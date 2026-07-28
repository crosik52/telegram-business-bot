"""Repository for Message / MessageEditHistory persistence and search."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.message import MediaType, Message, MessageEditHistory


@dataclass
class MessageFilters:
    """Search/filter criteria for browsing message history."""

    text_query: str | None = None
    username: str | None = None
    chat_id: int | None = None
    connection_ids: list[str] | None = None
    date_from: dt.datetime | None = None
    date_to: dt.datetime | None = None
    only_deleted: bool = False
    only_edited: bool = False
    only_media: bool = False
    only_text: bool = False
    media_type: MediaType | None = None
    include_deleted: bool = True


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _apply_filters(self, stmt: Select, filters: MessageFilters) -> Select:
        conditions = []

        if filters.text_query:
            like = f"%{filters.text_query}%"
            conditions.append(
                or_(Message.text.ilike(like), Message.caption.ilike(like))
            )
        if filters.username:
            like = f"%{filters.username.lstrip('@')}%"
            conditions.append(Message.sender_username.ilike(like))
        if filters.chat_id is not None:
            conditions.append(Message.chat_id == filters.chat_id)
        if filters.connection_ids is not None:
            conditions.append(Message.business_connection_id.in_(filters.connection_ids))
        if filters.date_from is not None:
            conditions.append(Message.sent_at >= filters.date_from)
        if filters.date_to is not None:
            conditions.append(Message.sent_at <= filters.date_to)
        if filters.only_deleted:
            conditions.append(Message.is_deleted.is_(True))
        elif not filters.include_deleted:
            conditions.append(Message.is_deleted.is_(False))
        if filters.only_edited:
            conditions.append(Message.is_edited.is_(True))
        if filters.only_media:
            conditions.append(Message.media_type != MediaType.NONE)
        if filters.only_text:
            conditions.append(Message.media_type == MediaType.NONE)
        if filters.media_type is not None:
            conditions.append(Message.media_type == filters.media_type)

        if conditions:
            stmt = stmt.where(and_(*conditions))
        return stmt

    async def search(
        self, filters: MessageFilters, *, page: int = 1, page_size: int = 25
    ) -> tuple[list[Message], int]:
        base_stmt = select(Message)
        base_stmt = self._apply_filters(base_stmt, filters)

        count_stmt = select(func.count()).select_from(base_stmt.subquery())
        total = (await self._session.execute(count_stmt)).scalar_one()

        stmt = (
            base_stmt.order_by(Message.sent_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .options(selectinload(Message.sender))
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all()), int(total)

    async def get_by_id(self, message_pk: int) -> Message | None:
        stmt = (
            select(Message)
            .where(Message.id == message_pk)
            .options(selectinload(Message.edit_history), selectinload(Message.sender))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_telegram_ids(
        self, business_connection_id: str, chat_id: int, message_id: int
    ) -> Message | None:
        stmt = select(Message).where(
            Message.business_connection_id == business_connection_id,
            Message.chat_id == chat_id,
            Message.message_id == message_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, message: Message) -> Message:
        self._session.add(message)
        await self._session.flush()
        return message

    async def record_edit(
        self,
        message: Message,
        *,
        new_text: str | None,
        new_caption: str | None,
        edited_at: dt.datetime,
        # Snapshot of media at the time of this edit. file_id is the
        # Telegram-permanent key; we store it so the audit trail is complete
        # and notifications can resend the file without downloading anything.
        snapshot_file_id: str | None = None,
        snapshot_media_type: str | None = None,
    ) -> MessageEditHistory:
        """Append an immutable edit-history row and update current fields.

        The message's ``original_text`` / ``original_caption`` are left
        untouched — only the "current" fields move forward.
        """

        history_entry = MessageEditHistory(
            message_id=message.id,
            text=new_text,
            caption=new_caption,
            file_id=snapshot_file_id,
            media_type=snapshot_media_type,
            edited_at=edited_at,
        )
        self._session.add(history_entry)

        message.text = new_text
        message.caption = new_caption
        message.is_edited = True
        message.last_edited_at = edited_at
        message.edit_count += 1

        await self._session.flush()
        return history_entry

    async def mark_deleted(self, message: Message, *, deleted_at: dt.datetime) -> Message:
        message.is_deleted = True
        message.deleted_at = deleted_at
        await self._session.flush()
        return message

    async def get_deleted(self, *, page: int = 1, page_size: int = 25) -> tuple[list[Message], int]:
        filters = MessageFilters(only_deleted=True)
        return await self.search(filters, page=page, page_size=page_size)

    async def distinct_chats(self) -> list[int]:
        stmt = select(Message.chat_id).distinct()
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]

    async def search_chats(
        self, query: str, *, limit: int = 100
    ) -> list[dict]:
        """Full-text search across all chats.

        Returns one row per matching chat, sorted by most-recent match:
          chat_id, match_count, last_match_at,
          latest_msg_id, latest_text, latest_caption, latest_media_type,
          sender_first_name, sender_last_name, sender_username
        """
        from sqlalchemy import text as _text  # noqa: PLC0415

        sql = _text("""
            WITH matches AS (
                SELECT id, chat_id, text, caption, media_type,
                       sender_first_name, sender_last_name, sender_username,
                       sent_at
                FROM messages
                WHERE text ILIKE :like OR caption ILIKE :like
            ),
            chat_stats AS (
                SELECT chat_id,
                       COUNT(*)       AS match_count,
                       MAX(sent_at)   AS last_match_at
                FROM matches
                GROUP BY chat_id
            ),
            latest_msg AS (
                SELECT DISTINCT ON (chat_id)
                       id, chat_id, text, caption, media_type,
                       sender_first_name, sender_last_name, sender_username
                FROM matches
                ORDER BY chat_id, sent_at DESC
            )
            SELECT cs.chat_id,
                   cs.match_count,
                   cs.last_match_at,
                   lm.id               AS latest_msg_id,
                   lm.text             AS latest_text,
                   lm.caption          AS latest_caption,
                   lm.media_type       AS latest_media_type,
                   lm.sender_first_name,
                   lm.sender_last_name,
                   lm.sender_username
            FROM   chat_stats cs
            JOIN   latest_msg lm ON cs.chat_id = lm.chat_id
            ORDER  BY cs.last_match_at DESC
            LIMIT  :limit
        """)
        result = await self._session.execute(sql, {"like": f"%{query}%", "limit": limit})
        return [dict(row._mapping) for row in result]
