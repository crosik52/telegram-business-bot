"""Durable owner-only list preferences; message/history storage is never changed."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypeGuard

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_connection import BusinessConnection
from app.models.message import Message
from app.models.user_settings import UserSettings

MAX_PINS = 20
MAX_ARCHIVED = 1000


def valid_chat_id(value: object) -> TypeGuard[int]:
    return type(value) is int and 0 < value < 2**52


def clean_ids(values: object, limit: int) -> list[int]:
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(value for value in values if valid_chat_id(value)))[:limit]


@dataclass(frozen=True)
class ChatListPreferences:
    pinned: tuple[int, ...] = ()
    archived: tuple[int, ...] = ()


def preferences(row: UserSettings | None) -> ChatListPreferences:
    if row is None:
        return ChatListPreferences()
    archived = clean_ids(getattr(row, "archived_chat_ids", None), MAX_ARCHIVED)
    pinned = clean_ids(getattr(row, "pinned_chat_ids", None), MAX_PINS)
    legacy = row.pinned_chat_id
    if valid_chat_id(legacy) and legacy not in pinned and legacy not in archived:
        pinned = [legacy, *pinned][:MAX_PINS]
    return ChatListPreferences(tuple(value for value in pinned if value not in archived), tuple(archived))


class ChatListRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, owner_id: int) -> ChatListPreferences:
        row = await self.session.scalar(select(UserSettings).where(UserSettings.owner_telegram_id == owner_id))
        return preferences(row)

    async def _locked_settings(self, owner_id: int) -> UserSettings:
        dialect = self.session.get_bind().dialect.name
        insert = sqlite_insert if dialect == "sqlite" else pg_insert
        await self.session.execute(insert(UserSettings).values(owner_telegram_id=owner_id)
            .on_conflict_do_nothing(index_elements=[UserSettings.owner_telegram_id]))
        row = await self.session.scalar(select(UserSettings)
            .where(UserSettings.owner_telegram_id == owner_id).with_for_update()
            .execution_options(populate_existing=True))
        if row is None:
            raise RuntimeError("settings_unavailable")
        return row

    async def owns_chat(self, owner_id: int, chat_id: int) -> bool:
        # No admin or self shortcut: only this owner's stored Business history.
        result = await self.session.scalar(select(Message.id).join(BusinessConnection,
            BusinessConnection.business_connection_id == Message.business_connection_id)
            .where(BusinessConnection.user_telegram_id == owner_id,
                   BusinessConnection.is_blocked.is_(False), Message.chat_id == chat_id).limit(1))
        return result is not None

    async def change(self, owner_id: int, action: str, chat_id: int | None = None) -> ChatListPreferences:
        if action not in {"pin", "unpin", "archive", "restore", "restore_all", "unpin_all"}:
            raise ValueError("invalid_action")
        if action not in {"restore_all", "unpin_all"} and not valid_chat_id(chat_id):
            raise ValueError("invalid_chat")
        row = await self._locked_settings(owner_id)
        current = preferences(row)
        pinned, archived = list(current.pinned), list(current.archived)
        # Recovery remains possible even after a chat has left the stats cap/history.
        stored_recovery = ((action == "restore" and chat_id in archived) or
                           (action == "unpin" and chat_id in pinned))
        if action not in {"restore_all", "unpin_all"} and not stored_recovery:
            assert chat_id is not None
            if not await self.owns_chat(owner_id, chat_id):
                raise LookupError("chat_not_found")
        if action == "pin":
            assert chat_id is not None
            if chat_id in archived:
                raise ValueError("restore_first")
            if chat_id not in pinned:
                if len(pinned) >= MAX_PINS:
                    raise ValueError("pin_limit")
                pinned.append(chat_id)
        elif action == "unpin":
            pinned = [value for value in pinned if value != chat_id]
        elif action == "archive":
            assert chat_id is not None
            if chat_id not in archived:
                if len(archived) >= MAX_ARCHIVED:
                    raise ValueError("archive_limit")
                archived.append(chat_id)
            pinned = [value for value in pinned if value != chat_id]
        elif action == "restore":
            archived = [value for value in archived if value != chat_id]
        elif action == "restore_all":
            archived = []
        elif action == "unpin_all":
            pinned = []
        row.pinned_chat_ids, row.archived_chat_ids = pinned, archived
        # Transfer the historical paid pin once; never resurrect it after unpin.
        row.pinned_chat_id = None
        await self.session.flush()
        return ChatListPreferences(tuple(pinned), tuple(archived))
