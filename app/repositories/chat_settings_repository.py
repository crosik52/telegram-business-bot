"""Repository for per-chat notification settings."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_settings import ChatSettings


class ChatSettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, business_connection_id: str, chat_id: int) -> ChatSettings | None:
        stmt = select(ChatSettings).where(
            ChatSettings.business_connection_id == business_connection_id,
            ChatSettings.chat_id == chat_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_or_create(
        self, business_connection_id: str, chat_id: int
    ) -> ChatSettings:
        settings = await self.get(business_connection_id, chat_id)
        if settings is None:
            settings = ChatSettings(
                business_connection_id=business_connection_id,
                chat_id=chat_id,
            )
            self._session.add(settings)
            await self._session.flush()
        return settings

    async def set_muted_until(
        self,
        business_connection_id: str,
        chat_id: int,
        until: dt.datetime | None,
    ) -> ChatSettings:
        settings = await self.get_or_create(business_connection_id, chat_id)
        settings.muted_until = until
        await self._session.flush()
        return settings

    async def is_muted(self, business_connection_id: str, chat_id: int) -> bool:
        """Return True if the chat currently has an active mute."""
        settings = await self.get(business_connection_id, chat_id)
        if settings is None or settings.muted_until is None:
            return False
        return settings.muted_until > dt.datetime.now(dt.UTC)
