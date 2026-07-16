"""FastAPI application entrypoint.

Wires together the Telegram webhook, health check, and admin dashboard into
a single ASGI app. Runs entirely in webhook mode — polling is never used.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.dashboard.routes import auth as dashboard_auth
from app.dashboard.routes import export as dashboard_export
from app.dashboard.routes import home as dashboard_home
from app.dashboard.routes import messages as dashboard_messages
from app.dashboard.routes import stats as dashboard_stats
from app.database.base import Base
from app.database.session import dispose_engine, get_engine
from app.logging_config import configure_logging, get_logger
from app.middlewares.logging_middleware import RequestLoggingMiddleware
from app.miniapp import routes as miniapp_routes
from app.routers import audio as audio_router
from app.routers import health, mockup_proxy, webhook

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)

_CLEANUP_INTERVAL_HOURS = 6
_STREAK_REMINDER_INTERVAL_MINUTES = 60
_MILESTONE_SWEEP_INTERVAL_MINUTES = 15
_MILESTONE_SWEEP_BATCH_SIZE = 50
_MILESTONE_SWEEP_MAX_BATCHES = 100  # safety cap: at most 5 000 rows per cycle
# After this many consecutive evaluation failures the sweep stops retrying a
# referral (circuit-breaker).  An operator can reset evaluation_failures to 0
# via SQL to re-enable processing.
_MILESTONE_SWEEP_MAX_FAILURES = 10


async def _streak_reminder_loop() -> None:
    """Background task: check for at-risk streaks and send bot DMs every hour."""
    await asyncio.sleep(120)  # Let the app fully start first
    while True:
        try:
            from app.business.dispatcher import get_bot
            from app.services.streak_notification_service import run_reminder_check
            bot = get_bot(settings)
            if bot:
                await run_reminder_check(bot)
        except Exception:
            logger.exception("Streak reminder check failed — will retry next cycle")
        await asyncio.sleep(_STREAK_REMINDER_INTERVAL_MINUTES * 60)


async def _milestone_sweep_loop() -> None:
    """Background task: retry milestone evaluation for referrals not yet checked.

    Picks up any referral that was activated (Phase 1 committed) but whose
    milestone evaluation (Phase 2) was skipped because the server restarted
    before ``evaluate_and_grant_milestones`` could run.  Runs every
    ``_MILESTONE_SWEEP_INTERVAL_MINUTES`` minutes.

    Within each cycle the loop keeps fetching batches of
    ``_MILESTONE_SWEEP_BATCH_SIZE`` rows until the backlog is exhausted or
    ``_MILESTONE_SWEEP_MAX_BATCHES`` batches have been processed (safety cap).
    A single log line summarises the total rows processed for the whole cycle
    rather than emitting one line per batch.
    """
    from app.database.session import get_db_session
    from app.repositories.referral_repository import ReferralRepository

    # Give the app a moment to be fully initialised before the first sweep.
    await asyncio.sleep(90)
    while True:
        try:
            total_processed = 0
            total_failed = 0
            batches_run = 0
            after_id = 0  # keyset cursor — advances monotonically through the table

            while batches_run < _MILESTONE_SWEEP_MAX_BATCHES:
                # Phase A: collect the next batch of unchecked IDs in a
                # short-lived session.  ``after_id`` ensures monotonic progress:
                # referrals whose evaluation failed (milestone_checked stays
                # False) are NOT re-selected within this cycle because their id
                # is already below the cursor.
                unchecked: list[tuple[int, int]] = []
                async for session in get_db_session():
                    repo = ReferralRepository(session)
                    unchecked = await repo.list_unchecked_referral_ids(
                        limit=_MILESTONE_SWEEP_BATCH_SIZE,
                        after_id=after_id,
                        max_failures=_MILESTONE_SWEEP_MAX_FAILURES,
                    )

                if not unchecked:
                    break  # backlog is clear

                batches_run += 1

                # Advance the keyset cursor past this batch before processing
                # so the next query starts from after the highest id we saw.
                after_id = unchecked[-1][0]

                # Phase B: evaluate milestones for each row in its own session
                # so that a single failure does not block the rest.
                # Exceptions are caught here and aggregated into a single
                # warning at the end of the cycle to avoid flooding logs.
                # On failure the per-row evaluation_failures counter is
                # incremented; once it reaches _MILESTONE_SWEEP_MAX_FAILURES
                # the row is excluded from future sweeps (circuit-breaker).
                failed_ids: list[int] = []
                for ref_id, referrer_id in unchecked:
                    try:
                        async for session in get_db_session():
                            repo = ReferralRepository(session)
                            await repo.evaluate_and_grant_milestones(referrer_id, ref_id)
                        total_processed += 1
                    except Exception as exc:  # noqa: BLE001
                        total_failed += 1
                        failed_ids.append(ref_id)
                        logger.debug(
                            "Milestone sweep: evaluation error for referral_id=%s: %s",
                            ref_id,
                            exc,
                        )
                        # Increment the per-row failure counter in its own
                        # session so a rollback of the evaluation session
                        # does not prevent the counter from being persisted.
                        try:
                            async for session in get_db_session():
                                repo = ReferralRepository(session)
                                await repo.increment_evaluation_failures(ref_id)
                        except Exception:  # noqa: BLE001
                            logger.debug(
                                "Milestone sweep: could not increment evaluation_failures "
                                "for referral_id=%s",
                                ref_id,
                            )

                # If the batch was smaller than the limit the table is exhausted.
                if len(unchecked) < _MILESTONE_SWEEP_BATCH_SIZE:
                    break

            capped = batches_run >= _MILESTONE_SWEEP_MAX_BATCHES
            if total_processed or total_failed:
                msg = (
                    "Milestone sweep: processed %d, failed %d referral(s)"
                    " in %d batch(es)%s"
                )
                args: list = [
                    total_processed,
                    total_failed,
                    batches_run,
                    " (safety cap reached — more rows remain)" if capped else "",
                ]
                if total_failed:
                    # Include a sample of failed IDs (up to 5) so operators can
                    # investigate without a per-row stack trace.
                    sample = failed_ids[:5]
                    msg += "; failed referral_ids (sample): %s"
                    args.append(sample)
                    logger.warning(msg, *args)
                else:
                    logger.info(msg, *args)
        except Exception:
            logger.exception("Milestone sweep loop error — will retry next cycle")

        await asyncio.sleep(_MILESTONE_SWEEP_INTERVAL_MINUTES * 60)


async def _cleanup_loop() -> None:
    """Background task: purge old media_cache and message rows every N hours."""
    from app.database.session import get_db_session
    from app.services.media_cache_service import purge_old_media_cache, purge_old_messages

    # Wait a bit after startup before first run so the app is fully ready.
    await asyncio.sleep(60)
    while True:
        try:
            async for session in get_db_session():
                await purge_old_media_cache(session)
            async for session in get_db_session():
                await purge_old_messages(session, max_age_days=90)
        except Exception:
            logger.exception("DB cleanup failed — will retry next cycle")
        await asyncio.sleep(_CLEANUP_INTERVAL_HOURS * 3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Telegram Business Bot (environment=%s)", settings.environment)

    if settings.is_sqlite:
        logger.warning(
            "Database is SQLite (%s). On platforms with ephemeral container "
            "filesystems (e.g. Railway without a mounted volume), the SQLite "
            "file is WIPED on every redeploy — this permanently loses all "
            "business connections, forcing users to reconnect the bot every "
            "time. Set DATABASE_URL to a persistent PostgreSQL instance to "
            "fix this.",
            settings.normalized_database_url,
        )
    else:
        logger.info("Database dialect: %s (persistent)", "postgresql" if settings.is_postgres else "other")

    # Ensure tables exist. Alembic migrations remain the source of truth for
    # schema evolution; this is a safety net for fresh deployments where
    # migrations haven't been run yet.
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Safe column additions for tables that already exist in production
        if not settings.is_sqlite:
            from sqlalchemy import text
            await conn.execute(text(
                "ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS owned_themes JSONB"
            ))
            await conn.execute(text(
                "ALTER TABLE referrals ADD COLUMN IF NOT EXISTS referred_first_name VARCHAR(128)"
            ))
            await conn.execute(text(
                "ALTER TABLE referrals ADD COLUMN IF NOT EXISTS referred_username VARCHAR(64)"
            ))
            await conn.execute(text(
                "ALTER TABLE referrals ADD COLUMN IF NOT EXISTS evaluation_failures INTEGER NOT NULL DEFAULT 0"
            ))

    if settings.webhook_base_url:
        from aiogram.types import MenuButtonWebApp, WebAppInfo

        from app.business.dispatcher import get_bot

        bot = get_bot(settings)
        webhook_url = settings.webhook_base_url.rstrip("/") + settings.webhook_path
        await bot.set_webhook(
            url=webhook_url,
            secret_token=settings.telegram_webhook_secret or None,
            allowed_updates=[
                "message",
                "pre_checkout_query",   # required: approve Stars payment before charge
                "callback_query",
                "inline_query",
                "chosen_inline_result",
                "business_connection",
                "business_message",
                "edited_business_message",
                "deleted_business_messages",
            ],
            drop_pending_updates=False,
        )
        logger.info("Telegram webhook set to %s", webhook_url)

        base_url = settings.webhook_base_url.rstrip("/")
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Статистика",
                web_app=WebAppInfo(url=base_url + "/app"),
            )
        )
        logger.info("Telegram chat menu button set to open mini app")
    else:
        logger.warning(
            "WEBHOOK_BASE_URL is not set — skipping automatic setWebhook call. "
            "Set it (or call setWebhook manually) before the bot can receive updates."
        )

    # ── Telethon user-client (view-once media) ────────────────────────────────
    if settings.telethon_enabled:
        from app.services import telethon_service
        await telethon_service.connect(
            settings.telegram_api_id,
            settings.telegram_api_hash,
            settings.telethon_session_str,
        )
    else:
        logger.info(
            "Telethon not configured (TELEGRAM_API_ID / TELEGRAM_API_HASH / "
            "TELETHON_SESSION_STR missing) — view-once media will fall back to "
            "Bot API only."
        )

    # ── Periodic DB cleanup task ──────────────────────────────────────────────
    cleanup_task = asyncio.create_task(_cleanup_loop())
    # ── Streak reminder background task ───────────────────────────────────────
    streak_task = asyncio.create_task(_streak_reminder_loop())
    # ── Referral milestone sweep (at-least-once guarantee) ───────────────────
    milestone_sweep_task = asyncio.create_task(_milestone_sweep_loop())

    yield

    cleanup_task.cancel()
    streak_task.cancel()
    milestone_sweep_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    try:
        await streak_task
    except asyncio.CancelledError:
        pass
    try:
        await milestone_sweep_task
    except asyncio.CancelledError:
        pass

    logger.info("Shutting down Telegram Business Bot")
    from app.services import telethon_service as _ts
    await _ts.disconnect()

    from app.business.dispatcher import close_bot
    await close_bot()
    await dispose_engine()


app = FastAPI(title="Telegram Business Bot", lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    max_age=settings.session_max_age_seconds,
    same_site="lax",
)
app.add_middleware(RequestLoggingMiddleware)

app.mount(
    "/static", StaticFiles(directory="app/dashboard/static"), name="static"
)

app.include_router(health.router)
app.include_router(mockup_proxy.router)
app.include_router(audio_router.router)
app.include_router(webhook.router)
app.include_router(dashboard_auth.router)
app.include_router(dashboard_home.router)
app.include_router(dashboard_messages.router)
app.include_router(dashboard_stats.router)
app.include_router(dashboard_export.router)
app.include_router(miniapp_routes.router)
