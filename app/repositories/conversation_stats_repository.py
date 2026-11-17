"""Owner-scoped metadata reads; never load message content or create schema/data."""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import case, func, literal, or_, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_connection import BusinessConnection
from app.models.message import Message
from app.services.conversation_stats_service import (
    SAMPLE_LIMIT,
    ConversationBusy,
    ConversationPoint,
    calculate_conversation,
)


class ConversationStatsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def prepare(self, owner_id: int, chat_id: int) -> tuple[str, ...]:
        if self.session.get_bind().dialect.name == "postgresql":
            await self.session.execute(text("SET LOCAL statement_timeout = '3000ms'"))
        connections = tuple(sorted((await self.session.execute(
            select(BusinessConnection.business_connection_id).where(
                BusinessConnection.user_telegram_id == owner_id,
                BusinessConnection.is_blocked.is_(False),
            ).limit(65)
        )).scalars().all()))
        if len(connections) > 64:
            raise ConversationBusy
        if not connections:
            return ()
        found = (await self.session.execute(select(Message.id).where(
            Message.business_connection_id.in_(connections), Message.chat_id == chat_id,
        ).limit(1))).first()
        return connections if found is not None else ()

    async def read(self, *, owner_id: int, chat_id: int, connections: tuple[str, ...], days: int) -> dict[str, Any]:
        end = dt.datetime.now(dt.UTC)
        start = end - dt.timedelta(days=days)
        known = Message.sender_telegram_id.in_((owner_id, chat_id))
        canonical = select(
            Message.id, Message.message_id, Message.sent_at, Message.sender_telegram_id,
            Message.is_placeholder,
            func.row_number().over(partition_by=Message.message_id, order_by=(
                Message.is_placeholder.asc(), case((known, 0), else_=1), Message.id.desc(),
            )).label("copy_rank"),
        ).where(
            Message.business_connection_id.in_(connections), Message.chat_id == chat_id,
            Message.sent_at >= start, Message.sent_at <= end,
        ).subquery()
        genuine = canonical.c.is_placeholder.is_(False)
        direction = canonical.c.sender_telegram_id.in_((owner_id, chat_id))
        rows = (await self.session.execute(select(
            canonical.c.id, canonical.c.message_id, canonical.c.sent_at, canonical.c.sender_telegram_id,
        ).where(canonical.c.copy_rank == 1, genuine, direction).order_by(
            canonical.c.sent_at.desc(), canonical.c.message_id.desc(), canonical.c.id.desc(),
        ).limit(SAMPLE_LIMIT + 1))).all()
        truncated = len(rows) > SAMPLE_LIMIT
        points = [ConversationPoint(*row) for row in rows[:SAMPLE_LIMIT]]
        previous = ConversationPoint(*rows[SAMPLE_LIMIT]) if truncated else None
        if points and not truncated:
            earliest = points[-1]
            earlier = select(
                Message.id, Message.message_id, Message.sent_at, Message.sender_telegram_id,
                func.row_number().over(partition_by=Message.message_id,
                                       order_by=Message.id.desc()).label("copy_rank"),
            ).where(
                Message.business_connection_id.in_(connections), Message.chat_id == chat_id,
                Message.is_placeholder.is_(False), known,
                Message.sent_at <= earliest.sent_at,
                Message.message_id.not_in([point.message_id for point in points]),
            ).subquery()
            previous_row = (await self.session.execute(select(
                earlier.c.id, earlier.c.message_id, earlier.c.sent_at, earlier.c.sender_telegram_id,
            ).where(
                earlier.c.copy_rank == 1,
                tuple_(earlier.c.sent_at, earlier.c.message_id, earlier.c.id) <
                tuple_(literal(earliest.sent_at), literal(earliest.message_id), literal(earliest.id)),
            ).order_by(earlier.c.sent_at.desc(), earlier.c.message_id.desc(), earlier.c.id.desc()).limit(1))).first()
            previous = ConversationPoint(*previous_row) if previous_row else None
        exclusions = (await self.session.execute(select(
            func.count().filter(canonical.c.is_placeholder.is_(True)),
            func.count().filter(genuine, or_(canonical.c.sender_telegram_id.is_(None), ~direction)),
        ).where(canonical.c.copy_rank == 1))).one()
        return {
            **calculate_conversation(points, owner_id=owner_id, chat_id=chat_id, previous=previous,
                                     window_start=start, window_end=end, sample_truncated=truncated),
            "chat_id": chat_id, "period_days": days, "window_start": start.isoformat(),
            "window_end": end.isoformat(), "truncated": truncated, "sample_limit": SAMPLE_LIMIT,
            "excluded_placeholders": int(exclusions[0]), "excluded_unknown_senders": int(exclusions[1]),
            "includes_deleted": True, "session_gap_seconds": 10800,
        }
