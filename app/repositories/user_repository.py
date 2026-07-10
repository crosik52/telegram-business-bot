"""Repository for TelegramUser persistence."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import TelegramUser


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_telegram_id(self, telegram_user_id: int) -> TelegramUser | None:
        result = await self._session.execute(
            select(TelegramUser).where(
                TelegramUser.telegram_user_id == telegram_user_id
            )
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        telegram_user_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        is_bot: bool = False,
    ) -> TelegramUser:
        user = await self.get_by_telegram_id(telegram_user_id)
        now = dt.datetime.now(dt.UTC)
        if user is None:
            user = TelegramUser(
                telegram_user_id=telegram_user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                is_bot=is_bot,
                first_seen_at=now,
                last_seen_at=now,
            )
            self._session.add(user)
            await self._session.flush()
        else:
            user.username = username
            user.first_name = first_name
            user.last_name = last_name
            user.last_seen_at = now
        return user

    async def count(self) -> int:
        result = await self._session.execute(select(func.count(TelegramUser.id)))
        return int(result.scalar_one())
