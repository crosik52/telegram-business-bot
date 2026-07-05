"""Repository for dashboard statistics aggregation."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import MediaType, Message
from app.models.user import TelegramUser


@dataclass
class MediaTypeCount:
    media_type: str
    count: int


@dataclass
class DashboardStats:
    total_messages: int
    total_users: int
    edited_messages: int
    deleted_messages: int
    media_messages: int
    text_messages: int
    media_breakdown: list[MediaTypeCount]


class StatsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_dashboard_stats(self) -> DashboardStats:
        total_messages = (
            await self._session.execute(select(func.count(Message.id)))
        ).scalar_one()

        total_users = (
            await self._session.execute(select(func.count(TelegramUser.id)))
        ).scalar_one()

        edited_messages = (
            await self._session.execute(
                select(func.count(Message.id)).where(Message.is_edited.is_(True))
            )
        ).scalar_one()

        deleted_messages = (
            await self._session.execute(
                select(func.count(Message.id)).where(Message.is_deleted.is_(True))
            )
        ).scalar_one()

        media_messages = (
            await self._session.execute(
                select(func.count(Message.id)).where(
                    Message.media_type != MediaType.NONE
                )
            )
        ).scalar_one()

        text_messages = total_messages - media_messages

        breakdown_result = await self._session.execute(
            select(Message.media_type, func.count(Message.id))
            .where(Message.media_type != MediaType.NONE)
            .group_by(Message.media_type)
        )
        media_breakdown = [
            MediaTypeCount(media_type=row[0].value, count=row[1])
            for row in breakdown_result.all()
        ]

        return DashboardStats(
            total_messages=int(total_messages),
            total_users=int(total_users),
            edited_messages=int(edited_messages),
            deleted_messages=int(deleted_messages),
            media_messages=int(media_messages),
            text_messages=int(text_messages),
            media_breakdown=media_breakdown,
        )
