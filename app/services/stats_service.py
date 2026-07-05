"""Statistics service — thin pass-through to the stats repository."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.stats_repository import DashboardStats, OwnerStats, StatsRepository


class StatsService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = StatsRepository(session)

    async def get_dashboard_stats(self) -> DashboardStats:
        return await self._repo.get_dashboard_stats()

    async def get_owner_stats(
        self, *, connection_ids: list[str], owner_telegram_id: int
    ) -> OwnerStats:
        return await self._repo.get_owner_stats(
            connection_ids=connection_ids, owner_telegram_id=owner_telegram_id
        )
