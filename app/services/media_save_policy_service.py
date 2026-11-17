"""Process-local immutable media-save policy cache backed by ShopConfig."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.database.session import get_session_factory
from app.repositories.media_save_settings_repository import (
    MediaPolicySnapshot,
    MediaSaveSettingsRepository,
    OwnerPolicy,
)


@dataclass(frozen=True)
class AppliedPolicy:
    snapshot: MediaPolicySnapshot
    owner: OwnerPolicy


class MediaSavePolicyService:
    def __init__(self) -> None:
        self._snapshots: dict[int, MediaPolicySnapshot] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._generations: dict[int, int] = {}

    async def load(self, bot_id: int) -> MediaPolicySnapshot | None:
        existing = self._snapshots.get(bot_id)
        if existing is not None:
            return existing
        lock = self._locks.setdefault(bot_id, asyncio.Lock())
        async with lock:
            existing = self._snapshots.get(bot_id)
            if existing is not None:
                return existing
            generation = self.generation(bot_id)
            try:
                factory = get_session_factory()
                async with factory() as session:
                    snapshot = await MediaSaveSettingsRepository(session).read(bot_id)
            except Exception:
                return None
            return self.apply(snapshot, expected_generation=generation)

    async def effective(self, bot_id: int, owner_id: int) -> AppliedPolicy | None:
        snapshot = await self.load(bot_id)
        return None if snapshot is None else AppliedPolicy(snapshot, snapshot.effective(owner_id))

    def apply(
        self, snapshot: MediaPolicySnapshot, *, allow_same_revision: bool = False,
        expected_generation: int | None = None,
    ) -> MediaPolicySnapshot | None:
        current = self._snapshots.get(snapshot.bot_id)
        if expected_generation is not None and expected_generation != self.generation(snapshot.bot_id):
            return current
        if current is not None and (
            snapshot.revision < current.revision
            or (snapshot.revision == current.revision and not allow_same_revision)
        ):
            return current
        self._snapshots[snapshot.bot_id] = snapshot
        self._generations[snapshot.bot_id] = self.generation(snapshot.bot_id) + 1
        return snapshot

    def generation(self, bot_id: int) -> int:
        return self._generations.get(bot_id, 0)

    def snapshot(self, bot_id: int) -> MediaPolicySnapshot | None:
        return self._snapshots.get(bot_id)

    def invalidate(self, bot_id: int) -> None:
        self._snapshots.pop(bot_id, None)
        self._generations[bot_id] = self.generation(bot_id) + 1

    def reset(self) -> None:
        self._snapshots.clear()
        self._locks.clear()
        self._generations.clear()


policy_service = MediaSavePolicyService()
