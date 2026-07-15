"""Statistics service — thin pass-through to the stats repository."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.stats_repository import (
    AdminOverview,
    DashboardStats,
    OwnerStats,
    StatsRepository,
)


class StatsService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = StatsRepository(session)

    async def get_dashboard_stats(self) -> DashboardStats:
        return await self._repo.get_dashboard_stats()

    async def get_owner_stats(
        self, *, connection_ids: list[str], owner_telegram_id: int, top_n: int = 15
    ) -> OwnerStats:
        return await self._repo.get_owner_stats(
            connection_ids=connection_ids,
            owner_telegram_id=owner_telegram_id,
            top_n=top_n,
        )

    async def get_admin_overview(self) -> AdminOverview:
        return await self._repo.get_admin_overview()

    async def get_owner_activity(
        self, *, connection_ids: list[str], days: int = 90
    ) -> dict[str, int]:
        return await self._repo.get_owner_activity(
            connection_ids=connection_ids, days=days
        )

    async def get_admin_growth(self, *, days: int = 30) -> dict[str, dict[str, int]]:
        return await self._repo.get_admin_growth(days=days)

    async def owner_has_chat(
        self, *, connection_ids: list[str], chat_id: int
    ) -> bool:
        return await self._repo.owner_has_chat(
            connection_ids=connection_ids, chat_id=chat_id
        )

    async def set_owner_settings(
        self,
        *,
        owner_telegram_id: int,
        notifications_enabled: bool | None = None,
        is_blocked: bool | None = None,
    ) -> int:
        return await self._repo.set_owner_settings(
            owner_telegram_id=owner_telegram_id,
            notifications_enabled=notifications_enabled,
            is_blocked=is_blocked,
        )
