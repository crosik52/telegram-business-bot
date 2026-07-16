"""CRUD for RequiredChannel."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.required_channel import RequiredChannel


class ChannelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def get_all(self) -> list[RequiredChannel]:
        result = await self._db.execute(
            select(RequiredChannel).order_by(RequiredChannel.id)
        )
        return list(result.scalars().all())

    async def get_active(self) -> list[RequiredChannel]:
        result = await self._db.execute(
            select(RequiredChannel)
            .where(RequiredChannel.is_active.is_(True))
            .order_by(RequiredChannel.id)
        )
        return list(result.scalars().all())

    async def add(self, channel_username: str, channel_title: str | None = None) -> RequiredChannel:
        ch = RequiredChannel(
            channel_username=channel_username.strip(),
            channel_title=channel_title,
        )
        self._db.add(ch)
        await self._db.flush()
        return ch

    async def toggle(self, channel_id: int) -> RequiredChannel | None:
        ch = await self._db.get(RequiredChannel, channel_id)
        if ch:
            ch.is_active = not ch.is_active
            await self._db.flush()
        return ch

    async def delete(self, channel_id: int) -> bool:
        ch = await self._db.get(RequiredChannel, channel_id)
        if ch:
            await self._db.delete(ch)
            await self._db.flush()
            return True
        return False
