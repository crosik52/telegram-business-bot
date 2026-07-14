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
from app.routers import health, webhook

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)

_CLEANUP_INTERVAL_HOURS = 6


async def _cleanup_loop() -> None:
    """Background task: purge old media_cache rows every N hours."""
    from app.database.session import get_db_session

    from app.services.media_cache_service import purge_old_media_cache

    # Wait a bit after startup before first run so the app is fully ready.
    await asyncio.sleep(60)
    while True:
        try:
            async for session in get_db_session():
                await purge_old_media_cache(session)
        except Exception:
            logger.exception("media_cache cleanup failed — will retry next cycle")
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

    # ── Periodic DB cleanup task ──────────────────────────────────────────────
    cleanup_task = asyncio.create_task(_cleanup_loop())

    yield

    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass

    logger.info("Shutting down Telegram Business Bot")
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
app.include_router(webhook.router)
app.include_router(dashboard_auth.router)
app.include_router(dashboard_home.router)
app.include_router(dashboard_messages.router)
app.include_router(dashboard_stats.router)
app.include_router(dashboard_export.router)
app.include_router(miniapp_routes.router)
