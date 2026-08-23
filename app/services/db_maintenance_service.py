"""Database maintenance helpers.

Functions shared across the cleanup background task and the admin panel.
The media-caching functions that previously lived here have been removed
(the media_cache table has been dropped; see startup migration in app/main.py).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger

logger = get_logger(__name__)


async def purge_old_messages(
    session: AsyncSession,
    max_age_days: int = 90,
    deleted_max_age_days: int = 90,
) -> int:
    """Delete Message rows (+ their edit history via CASCADE).

    Two passes:
    - Already-deleted messages (is_deleted=True): removed after *deleted_max_age_days*.
      Kept as long as regular messages so owners can still recover media/notifications.
    - All other messages: removed after *max_age_days*.
    """
    from app.models.message import Message as DBMessage  # noqa: PLC0415

    now = dt.datetime.now(dt.UTC)

    # Pass 1: deleted messages
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
    variant = "VACUUM FULL ANALYZE" if full else "VACUUM ANALYZE"
    async with engine.execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
        for tbl in table_names:
            await conn.execute(text(f"{variant} {tbl}"))
            logger.info("%s %s complete", variant, tbl)
