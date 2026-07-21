"""Download Telegram media files and persist their bytes in the DB.

Self-destructing (view-once) media uses file_ids that expire within seconds of
the message disappearing.  By downloading immediately when the message arrives
we guarantee the bytes are available when the owner's delete notification is
assembled — even if the file_id has long since expired.
"""

from __future__ import annotations

import datetime as dt
import io
import os

from aiogram import Bot
from aiogram.types import BufferedInputFile
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger
from app.models.media_cache import MediaCache

logger = get_logger(__name__)

# Skip files larger than this to keep DB size manageable.
# Override with MEDIA_CACHE_MAX_MB env var (default 10 MB).
_MAX_BYTES = int(os.environ.get("MEDIA_CACHE_MAX_MB", "10")) * 1024 * 1024

# How long to keep cached media before purging.
# Override with MEDIA_CACHE_TTL_DAYS env var (default 2 days).
_CACHE_TTL_DAYS = int(os.environ.get("MEDIA_CACHE_TTL_DAYS", "2"))


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


async def store_bytes(
    session: AsyncSession,
    file_unique_id: str,
    file_id: str | None,
    media_type: str,
    data: bytes,
) -> None:
    """Persist raw *data* bytes directly (e.g. from Telethon).  Idempotent."""
    existing = (
        await session.execute(
            select(MediaCache).where(MediaCache.file_unique_id == file_unique_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return
    try:
        session.add(
            MediaCache(
                file_unique_id=file_unique_id,
                file_id=file_id or "",
                media_type=media_type,
                file_data=data,
                file_size=len(data),
            )
        )
        await session.flush()
        logger.debug("media_cache: stored %d B via store_bytes (%s)", len(data), file_unique_id)
    except Exception:
        await session.rollback()
        logger.debug("media_cache: duplicate in store_bytes for %s", file_unique_id)


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
    deleted_max_age_days: int = 7,
) -> int:
    """Delete Message rows (+ their edit history via CASCADE).

    Two passes:
    - Already-deleted messages (is_deleted=True): removed after *deleted_max_age_days*.
      The owner already received the notification, so there's nothing to keep.
    - All other messages: removed after *max_age_days*.
    """
    from app.models.message import Message as DBMessage

    now = dt.datetime.now(dt.UTC)

    # Pass 1: deleted messages — purge aggressively (default 7 days)
    deleted_cutoff = now - dt.timedelta(days=deleted_max_age_days)
    r1 = await session.execute(
        delete(DBMessage).where(
            DBMessage.is_deleted.is_(True),
            DBMessage.deleted_at < deleted_cutoff,
        )
    )

    # Pass 2: old non-deleted messages
    old_cutoff = now - dt.timedelta(days=max_age_days)
    r2 = await session.execute(
        delete(DBMessage).where(DBMessage.sent_at < old_cutoff)
    )

    total = (r1.rowcount or 0) + (r2.rowcount or 0)
    await session.commit()
    if total:
        logger.info(
            "messages: purged %d deleted (<=%dd) + %d old (>%dd) rows",
            r1.rowcount or 0, deleted_max_age_days,
            r2.rowcount or 0, max_age_days,
        )
    return total


async def get_table_sizes(session: AsyncSession) -> dict:
    """Return sizes of all user tables + total DB size. PostgreSQL only."""
    from sqlalchemy import text  # noqa: PLC0415
    try:
        rows_result = await session.execute(text("""
            SELECT
                relname                                                      AS table_name,
                pg_size_pretty(pg_total_relation_size(relid))                AS total_size,
                pg_size_pretty(pg_relation_size(relid))                      AS table_size,
                pg_size_pretty(pg_total_relation_size(relid)
                               - pg_relation_size(relid))                    AS index_size,
                n_live_tup                                                   AS live_rows,
                n_dead_tup                                                   AS dead_rows,
                pg_total_relation_size(relid)                                AS bytes
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(relid) DESC
            LIMIT 20
        """))
        tables = [dict(r._mapping) for r in rows_result.all()]
        # Convert bytes to int for JSON (pg returns Decimal sometimes)
        for t in tables:
            t["bytes"] = int(t["bytes"])
            t["live_rows"] = int(t["live_rows"])
            t["dead_rows"] = int(t["dead_rows"])

        db_result = await session.execute(
            text("SELECT pg_size_pretty(pg_database_size(current_database())) AS db_size")
        )
        db_size = db_result.scalar() or "—"
        return {"tables": tables, "db_size": db_size}
    except Exception as exc:
        return {"tables": [], "db_size": "—", "error": str(exc)}


async def vacuum_tables(table_names: list[str], full: bool = False) -> None:
    """Run VACUUM [FULL] ANALYZE on the given tables (must be outside a transaction).

    ``full=True`` uses VACUUM FULL which rewrites the table and returns disk
    space to the OS.  It takes an exclusive lock — use only when the table can
    afford a brief write pause (e.g. just after a wipe).
    """
    from app.database.session import get_engine  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415
    engine = get_engine()
    # VACUUM cannot run inside a transaction → use AUTOCOMMIT isolation level
    variant = "VACUUM FULL ANALYZE" if full else "VACUUM ANALYZE"
    async with engine.execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
        for tbl in table_names:
            # Table name is an internal constant — safe to interpolate
            await conn.execute(text(f"{variant} {tbl}"))
            logger.info("%s %s complete", variant, tbl)


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
