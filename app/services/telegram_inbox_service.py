"""At-least-once delivery with durable acknowledgement and bounded retries.

One application worker is required. Telegram sends and legacy payment/game
side effects are not transactional with the inbox and are not exactly-once.
"""

import asyncio
import datetime as dt

from aiogram.types import Update
from sqlalchemy import case, func, select, text, update

from app.business.dispatcher import get_bot, get_dispatcher
from app.config import get_settings
from app.database.session import get_session_factory
from app.logging_config import get_logger
from app.models.telegram_update import TelegramUpdate

logger = get_logger(__name__)
MAX_QUEUED_UPDATES = 256
MAX_ATTEMPTS = 5
PROCESSING_TIMEOUT_SECONDS = 60


class InboxFullError(Exception):
    pass


async def enqueue_update(bot_id: int, event: Update) -> None:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        # Serialise capacity checks + inserts across concurrent HTTP requests.
        if session.get_bind().dialect.name == "postgresql":
            await session.execute(text("SELECT pg_advisory_xact_lock(817260411)"))
        exists = await session.scalar(select(TelegramUpdate.id).where(
            TelegramUpdate.bot_id == bot_id, TelegramUpdate.update_id == event.update_id))
        if exists is not None:
            return
        queued = await session.scalar(select(func.count()).select_from(TelegramUpdate).where(
            TelegramUpdate.state != "done"))
        if (queued or 0) >= MAX_QUEUED_UPDATES:
            raise InboxFullError
        session.add(TelegramUpdate(bot_id=bot_id, update_id=event.update_id,
                                  # aiogram injects non-JSON Default sentinels into
                                  # LinkPreviewOptions. Persist wire fields only;
                                  # defaults are reconstructed when replaying.
                                  payload=event.model_dump(mode="json", by_alias=True,
                                                           exclude_none=True, exclude_unset=True)))
    # The commit above must complete before the webhook acknowledges the update.


async def recover_processing(bot_id: int) -> None:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await session.execute(update(TelegramUpdate).where(
            TelegramUpdate.bot_id == bot_id, TelegramUpdate.state == "processing"
        ).values(state=case((TelegramUpdate.attempts >= MAX_ATTEMPTS, "failed"), else_="pending"),
                 ready_at=dt.datetime.now(dt.UTC)))


async def process_next(bot, dispatcher) -> bool:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        row = await session.scalar(select(TelegramUpdate).where(
            TelegramUpdate.bot_id == bot.id,
            TelegramUpdate.state == "pending",
        ).order_by(TelegramUpdate.id).limit(1).with_for_update(skip_locked=True))
        if row is None:
            return False
        ready_at = row.ready_at
        if ready_at.tzinfo is None:
            ready_at = ready_at.replace(tzinfo=dt.UTC)
        if ready_at > dt.datetime.now(dt.UTC):
            return False
        row_id, update_id, payload = row.id, row.update_id, row.payload
        row.state = "processing"
        row.attempts += 1
        attempts = row.attempts
    try:
        event = Update.model_validate(payload, context={"bot": bot})
        async with asyncio.timeout(PROCESSING_TIMEOUT_SECONDS):
            await dispatcher.feed_update(bot, event)
    except Exception as exc:
        logger.error("Inbox processing failed update_id=%s attempt=%s error_type=%s",
                     update_id, attempts, type(exc).__name__)
        async with factory() as session, session.begin():
            await session.execute(update(TelegramUpdate).where(TelegramUpdate.id == row_id).values(
                state="failed" if attempts >= MAX_ATTEMPTS else "pending",
                ready_at=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=min(60, 2**attempts)),
            ))
        return True
    # Cancellation intentionally leaves processing state: restart recovery replays it.
    async with factory() as session, session.begin():
        await session.execute(update(TelegramUpdate).where(TelegramUpdate.id == row_id).values(
            state="done", payload=None))
    return True


async def run_inbox_worker() -> None:
    bot = get_bot(get_settings())
    dispatcher = get_dispatcher()
    recovered = False
    while True:
        try:
            if not recovered:
                await recover_processing(bot.id)
                recovered = True
            if not await process_next(bot, dispatcher):
                await asyncio.sleep(1)
        except Exception as exc:
            recovered = False
            logger.error("Inbox worker unavailable error_type=%s", type(exc).__name__)
            await asyncio.sleep(5)
