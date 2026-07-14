"""Download Telegram media files and persist their bytes in the DB.

Self-destructing (view-once) media uses file_ids that expire within seconds of
the message disappearing.  By downloading immediately when the message arrives
we guarantee the bytes are available when the owner's delete notification is
assembled — even if the file_id has long since expired.
"""

from __future__ import annotations

import datetime as dt
import io

from aiogram import Bot
from aiogram.types import BufferedInputFile
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger
from app.models.media_cache import MediaCache

logger = get_logger(__name__)

# Skip files larger than this to keep DB size manageable.
_MAX_BYTES = 50 * 1024 * 1024  # 50 MB

# How long to keep cached media before purging (self-destructing media is
# consumed within seconds/minutes; 7 days is very generous).
_CACHE_TTL_DAYS = 7


async def download_and_cache(
    bot: Bot,
    session: AsyncSession,
    file_id: str,
    file_unique_id: str,
    media_type: str,
    max_bytes: int = _MAX_BYTES,
) -> bool:
    """Download *file_id* and cache its bytes.  Idempotent — safe to call twice.

    Returns True if the file is now in the cache (either just downloaded or
    was already there).  Returns False if the file was skipped (too large) or
    if the download failed.
    """
    # Already cached?  Nothing to do.
    existing = (
        await session.execute(
            select(MediaCache).where(MediaCache.file_unique_id == file_unique_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return True

    try:
        tg_file = await bot.get_file(file_id)
        if tg_file.file_size and tg_file.file_size > max_bytes:
            logger.info(
                "media_cache: skipping %s — size %s B exceeds %s B limit",
                file_unique_id,
                tg_file.file_size,
                max_bytes,
            )
            return False

        buf = io.BytesIO()
        await bot.download_file(tg_file.file_path, buf)
        data = buf.getvalue()

        try:
            session.add(
                MediaCache(
                    file_unique_id=file_unique_id,
                    file_id=file_id,
                    media_type=media_type,
                    file_data=data,
                    file_size=len(data),
                )
            )
            await session.flush()
            logger.debug(
                "media_cache: stored %s (%d B, type=%s)",
                file_unique_id,
                len(data),
                media_type,
            )
        except Exception:
            # Another concurrent call already inserted this row — that's fine.
            await session.rollback()
            logger.debug("media_cache: duplicate insert ignored for %s", file_unique_id)
        return True

    except Exception:
        logger.warning(
            "media_cache: failed to download %s (file_id=%s)",
            file_unique_id,
            file_id,
            exc_info=True,
        )
        return False


async def get_cached_bytes(
    session: AsyncSession,
    file_unique_id: str,
) -> bytes | None:
    """Return the raw cached bytes for *file_unique_id*, or None if not cached."""
    row = (
        await session.execute(
            select(MediaCache).where(MediaCache.file_unique_id == file_unique_id)
        )
    ).scalar_one_or_none()
    return row.file_data if row is not None else None


async def purge_old_messages(
    session: AsyncSession,
    max_age_days: int = 90,
) -> int:
    """Delete Message rows (+ their edit history via CASCADE) older than max_age_days."""
    from app.models.message import Message as DBMessage

    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=max_age_days)
    result = await session.execute(
        delete(DBMessage).where(DBMessage.sent_at < cutoff)
    )
    deleted = result.rowcount or 0
    await session.commit()
    if deleted:
        logger.info("messages: purged %d rows older than %d days", deleted, max_age_days)
    else:
        logger.debug("messages: nothing to purge (all rows within %d days)", max_age_days)
    return deleted


async def purge_old_media_cache(
    session: AsyncSession,
    max_age_days: int = _CACHE_TTL_DAYS,
) -> int:
    """Delete media_cache rows older than *max_age_days*.

    Returns the number of rows deleted.
    """
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=max_age_days)
    result = await session.execute(
        delete(MediaCache).where(MediaCache.created_at < cutoff)
    )
    deleted = result.rowcount or 0
    await session.commit()
    if deleted:
        logger.info("media_cache: purged %d rows older than %d days", deleted, max_age_days)
    else:
        logger.debug("media_cache: nothing to purge (all rows within %d days)", max_age_days)
    return deleted


def make_input_file(data: bytes, media_type: str) -> BufferedInputFile:
    """Wrap raw bytes in a BufferedInputFile with a sensible filename."""
    _EXTENSIONS = {
        "photo": "jpg",
        "video": "mp4",
        "voice": "ogg",
        "video_note": "mp4",
        "audio": "mp3",
        "document": "bin",
        "sticker": "webp",
        "animation": "gif",
    }
    ext = _EXTENSIONS.get(media_type, "bin")
    return BufferedInputFile(data, filename=f"media.{ext}")
