"""Versioned media-save policy stored in the reserved ShopConfig namespace."""
from __future__ import annotations

import copy
import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_connection import BusinessConnection
from app.models.shop_config import DEFAULT_SHOP_CONFIG, ShopConfig

SCHEMA_VERSION = 1
MAX_OWNERS = 256
MIN_BYTES, MAX_BYTES = 64 * 1024, 8 * 1024 * 1024
MIN_ATTEMPTS, MAX_ATTEMPTS = 1, 20
MIN_COOLDOWN, MAX_COOLDOWN = 35, 3600


@dataclass(frozen=True)
class MediaLimits:
    max_bytes: int = MAX_BYTES
    attempts_per_hour: int = MAX_ATTEMPTS
    cooldown_seconds: int = MIN_COOLDOWN


@dataclass(frozen=True)
class OwnerPolicy:
    enabled: bool
    limits: MediaLimits


@dataclass(frozen=True)
class MediaPolicySnapshot:
    bot_id: int
    revision: int
    enabled: bool
    defaults: MediaLimits
    owners: Mapping[int, OwnerPolicy]
    blocked_owners: frozenset[int]

    def effective(self, owner_id: int) -> OwnerPolicy:
        configured = self.owners.get(owner_id, OwnerPolicy(True, self.defaults))
        return OwnerPolicy(
            self.enabled and configured.enabled and owner_id not in self.blocked_owners,
            configured.limits,
        )


@dataclass(frozen=True)
class ConnectedOwner:
    owner_id: int
    display_name: str | None
    username: str | None
    connection_count: int
    is_enabled: bool
    can_reply: bool
    is_blocked: bool
    updated_at: dt.datetime


class RevisionConflictError(Exception):
    def __init__(self, current_revision: int) -> None:
        self.current_revision = current_revision


def validate_limits(max_bytes: Any, attempts: Any, cooldown: Any) -> MediaLimits:
    if any(type(value) is not int for value in (max_bytes, attempts, cooldown)):
        raise ValueError("invalid_limits")
    if not MIN_BYTES <= max_bytes <= MAX_BYTES:
        raise ValueError("invalid_max_bytes")
    if not MIN_ATTEMPTS <= attempts <= MAX_ATTEMPTS:
        raise ValueError("invalid_attempts_per_hour")
    if not MIN_COOLDOWN <= cooldown <= MAX_COOLDOWN:
        raise ValueError("invalid_cooldown_seconds")
    return MediaLimits(max_bytes, attempts, cooldown)


def _bot_payload(items: dict, bot_id: int) -> dict:
    if "_media_save" not in items:
        return {}
    root = items["_media_save"]
    if (not isinstance(root, dict) or type(root.get("schemaVersion")) is not int
            or root.get("schemaVersion") != SCHEMA_VERSION
            or not isinstance(root.get("bots"), dict)):
        raise ValueError("invalid_media_policy")
    if str(bot_id) not in root["bots"]:
        return {}
    raw = root["bots"][str(bot_id)]
    if not isinstance(raw, dict):
        raise ValueError("invalid_media_policy")
    return copy.deepcopy(raw)


def parse_snapshot(items: dict, bot_id: int, blocked: frozenset[int] = frozenset()) -> MediaPolicySnapshot:
    raw = _bot_payload(items, bot_id)
    revision = raw.get("revision", 0)
    enabled = raw.get("enabled", True)
    defaults_raw = raw.get("defaults", {})
    if type(revision) is not int or revision < 0 or type(enabled) is not bool:
        raise ValueError("invalid_media_policy")
    if not isinstance(defaults_raw, dict):
        raise ValueError("invalid_media_policy")
    defaults = validate_limits(
        defaults_raw.get("maxBytes", MAX_BYTES),
        defaults_raw.get("attemptsPerHour", MAX_ATTEMPTS),
        defaults_raw.get("cooldownSeconds", MIN_COOLDOWN),
    )
    owners_raw = raw.get("owners", {})
    if not isinstance(owners_raw, dict) or len(owners_raw) > MAX_OWNERS:
        raise ValueError("invalid_media_policy")
    owners: dict[int, OwnerPolicy] = {}
    for owner_key, value in owners_raw.items():
        if not isinstance(owner_key, str) or not owner_key.isdigit() or not isinstance(value, dict):
            raise ValueError("invalid_media_policy")
        owner_id = int(owner_key)
        if not 1 < owner_id < 2**52 or type(value.get("enabled", True)) is not bool:
            raise ValueError("invalid_media_policy")
        owners[owner_id] = OwnerPolicy(
            value.get("enabled", True),
            validate_limits(
                value.get("maxBytes", defaults.max_bytes),
                value.get("attemptsPerHour", defaults.attempts_per_hour),
                value.get("cooldownSeconds", defaults.cooldown_seconds),
            ),
        )
    return MediaPolicySnapshot(bot_id, revision, enabled, defaults, MappingProxyType(owners), blocked)


class MediaSaveSettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _row(self, *, for_update: bool = False) -> ShopConfig:
        stmt = select(ShopConfig).where(ShopConfig.id == 1)
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = ShopConfig(id=1, items=copy.deepcopy(DEFAULT_SHOP_CONFIG))
            self._session.add(row)
            await self._session.flush()
        return row

    async def connected_owners(self, bot_id: int) -> list[ConnectedOwner]:
        rows = (await self._session.execute(
            select(BusinessConnection)
            .where(BusinessConnection.bot_id == bot_id)
            .order_by(BusinessConnection.updated_at.desc(), BusinessConnection.id.desc())
        )).scalars().all()
        grouped: dict[int, list[BusinessConnection]] = {}
        for row in rows:
            grouped.setdefault(row.user_telegram_id, []).append(row)
        result = []
        for owner_id, conns in grouped.items():
            latest = conns[0]
            result.append(ConnectedOwner(
                owner_id, latest.user_first_name, latest.user_username, len(conns),
                any(c.is_enabled for c in conns),
                any(c.is_enabled and c.can_reply for c in conns),
                any(c.is_blocked for c in conns), max(c.updated_at for c in conns),
            ))
        return result

    async def read(self, bot_id: int) -> MediaPolicySnapshot:
        row = await self._row()
        owners = await self.connected_owners(bot_id)
        blocked = frozenset(owner.owner_id for owner in owners if owner.is_blocked)
        return parse_snapshot(row.items or DEFAULT_SHOP_CONFIG, bot_id, blocked)

    async def update(
        self, *, bot_id: int, expected_revision: int, owner_id: int | None,
        enabled: bool, limits: MediaLimits,
    ) -> MediaPolicySnapshot:
        if type(expected_revision) is not int or expected_revision < 0 or type(enabled) is not bool:
            raise ValueError("invalid_media_policy")
        row = await self._row(for_update=True)
        items = copy.deepcopy(row.items or DEFAULT_SHOP_CONFIG)
        current = parse_snapshot(items, bot_id)
        if current.revision != expected_revision:
            raise RevisionConflictError(current.revision)
        if owner_id is not None:
            exists = await self._session.scalar(select(BusinessConnection.id).where(
                BusinessConnection.bot_id == bot_id,
                BusinessConnection.user_telegram_id == owner_id,
            ).limit(1))
            if exists is None:
                raise LookupError("owner_not_connected_to_current_bot")
        root = items.get("_media_save")
        if not isinstance(root, dict):
            root = {}
        root = copy.deepcopy(root)
        root["schemaVersion"] = SCHEMA_VERSION
        bots = root.get("bots")
        if not isinstance(bots, dict):
            bots = {}
        bots = copy.deepcopy(bots)
        raw = _bot_payload(items, bot_id)
        raw["revision"] = current.revision + 1
        if owner_id is None:
            raw.update(enabled=enabled, defaults={
                "maxBytes": limits.max_bytes,
                "attemptsPerHour": limits.attempts_per_hour,
                "cooldownSeconds": limits.cooldown_seconds,
            })
            raw.setdefault("owners", {})
        else:
            owners = copy.deepcopy(raw.get("owners", {}))
            if len(owners) >= MAX_OWNERS and str(owner_id) not in owners:
                raise ValueError("owner_capacity")
            owners[str(owner_id)] = {
                "enabled": enabled,
                "maxBytes": limits.max_bytes,
                "attemptsPerHour": limits.attempts_per_hour,
                "cooldownSeconds": limits.cooldown_seconds,
            }
            raw["owners"] = owners
            raw.setdefault("enabled", current.enabled)
            raw.setdefault("defaults", {
                "maxBytes": current.defaults.max_bytes,
                "attemptsPerHour": current.defaults.attempts_per_hour,
                "cooldownSeconds": current.defaults.cooldown_seconds,
            })
        bots[str(bot_id)] = raw
        root["bots"] = bots
        items["_media_save"] = root
        row.items = items
        await self._session.flush()
        return await self.read(bot_id)
