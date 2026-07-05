"""Statistics service — thin pass-through to the stats repository."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.stats_repository import DashboardStats, StatsRepository


class StatsService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = StatsRepository(session)

    async def get_dashboard_stats(self) -> DashboardStats:
        return await self._repo.get_dashboard_stats()
