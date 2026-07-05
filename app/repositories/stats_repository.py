"""Repository for dashboard and per-owner statistics aggregation."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_connection import BusinessConnection
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


@dataclass
class InterlocutorStat:
    """Aggregated activity for a single chat (one interlocutor)."""

    chat_id: int
    display_name: str
    username: str | None
    message_count: int
    edited_count: int
    deleted_count: int
    last_message_at: dt.datetime | None


@dataclass
class OwnerStats:
    """Personal statistics for a single connection owner (mini app)."""

    owner_telegram_id: int
    total_messages: int
    total_chats: int
    edited_messages: int
    deleted_messages: int
    top_interlocutors: list[InterlocutorStat] = field(default_factory=list)


@dataclass
class AdminUserRow:
    """One connected owner, as shown in the super-admin mini app panel."""

    owner_telegram_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    connected_at: dt.datetime
    is_enabled: bool
    can_reply: bool
    notifications_enabled: bool
    is_blocked: bool
    total_messages: int
    total_chats: int
    edited_messages: int
    deleted_messages: int
    last_activity_at: dt.datetime | None


@dataclass
class AdminOverview:
    total_users: int
    users: list[AdminUserRow] = field(default_factory=list)


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

    async def get_owner_stats(
        self,
        *,
        connection_ids: list[str],
        owner_telegram_id: int,
        top_n: int = 10,
    ) -> OwnerStats:
        """Per-chat activity for one connection owner, for the mini app.

        Aggregated in Python rather than SQL: a personal Business account's
        message volume is small enough that fetching the (denormalized,
        already-indexed-by-chat) rows once and grouping in memory is simpler
        and avoids a fragile "most recent counterpart profile" subquery.
        """

        if not connection_ids:
            return OwnerStats(
                owner_telegram_id=owner_telegram_id,
                total_messages=0,
                total_chats=0,
                edited_messages=0,
                deleted_messages=0,
                top_interlocutors=[],
            )

        stmt = select(
            Message.chat_id,
            Message.sender_telegram_id,
            Message.sender_username,
            Message.sender_first_name,
            Message.sender_last_name,
            Message.is_edited,
            Message.is_deleted,
            Message.sent_at,
        ).where(Message.business_connection_id.in_(connection_ids))
        rows = (await self._session.execute(stmt)).all()

        total_messages = len(rows)
        edited_messages = sum(1 for r in rows if r.is_edited)
        deleted_messages = sum(1 for r in rows if r.is_deleted)

        chats: dict[int, dict] = {}
        for r in rows:
            chat = chats.setdefault(
                r.chat_id,
                {
                    "message_count": 0,
                    "edited_count": 0,
                    "deleted_count": 0,
                    "last_message_at": None,
                    "counterpart": None,
                    "counterpart_at": None,
                },
            )
            chat["message_count"] += 1
            if r.is_edited:
                chat["edited_count"] += 1
            if r.is_deleted:
                chat["deleted_count"] += 1
            if chat["last_message_at"] is None or (
                r.sent_at and r.sent_at > chat["last_message_at"]
            ):
                chat["last_message_at"] = r.sent_at

            if r.sender_telegram_id != owner_telegram_id and (
                chat["counterpart_at"] is None
                or (r.sent_at and r.sent_at > chat["counterpart_at"])
            ):
                chat["counterpart"] = (
                    r.sender_first_name,
                    r.sender_last_name,
                    r.sender_username,
                )
                chat["counterpart_at"] = r.sent_at

        top_interlocutors: list[InterlocutorStat] = []
        for chat_id, data in chats.items():
            first_name, last_name, username = data["counterpart"] or (None, None, None)
            name_parts = [p for p in (first_name, last_name) if p]
            display_name = (
                " ".join(name_parts)
                if name_parts
                else (f"@{username}" if username else f"Чат {chat_id}")
            )
            top_interlocutors.append(
                InterlocutorStat(
                    chat_id=chat_id,
                    display_name=display_name,
                    username=username,
                    message_count=data["message_count"],
                    edited_count=data["edited_count"],
                    deleted_count=data["deleted_count"],
                    last_message_at=data["last_message_at"],
                )
            )

        top_interlocutors.sort(key=lambda s: s.message_count, reverse=True)

        return OwnerStats(
            owner_telegram_id=owner_telegram_id,
            total_messages=total_messages,
            total_chats=len(chats),
            edited_messages=edited_messages,
            deleted_messages=deleted_messages,
            top_interlocutors=top_interlocutors[:top_n],
        )

    async def get_admin_overview(self) -> AdminOverview:
        """Every connected owner plus their aggregated activity, for the
        super-admin mini app panel.

        One row per distinct `user_telegram_id` (an owner can technically
        have more than one `BusinessConnection` row, e.g. after
        disconnect/reconnect) — settings and messages are aggregated across
        all of that owner's connection ids.
        """

        connections = (
            await self._session.execute(
                select(BusinessConnection).order_by(
                    BusinessConnection.connected_at.desc()
                )
            )
        ).scalars().all()

        by_owner: dict[int, list[BusinessConnection]] = {}
        for conn in connections:
            by_owner.setdefault(conn.user_telegram_id, []).append(conn)

        users: list[AdminUserRow] = []
        for owner_telegram_id, conns in by_owner.items():
            connection_ids = [c.business_connection_id for c in conns]
            latest = max(conns, key=lambda c: c.connected_at)

            stmt = select(
                Message.is_edited,
                Message.is_deleted,
                Message.chat_id,
                Message.sent_at,
            ).where(Message.business_connection_id.in_(connection_ids))
            rows = (await self._session.execute(stmt)).all()

            total_messages = len(rows)
            edited_messages = sum(1 for r in rows if r.is_edited)
            deleted_messages = sum(1 for r in rows if r.is_deleted)
            total_chats = len({r.chat_id for r in rows})
            last_activity_at = max(
                (r.sent_at for r in rows if r.sent_at is not None), default=None
            )

            users.append(
                AdminUserRow(
                    owner_telegram_id=owner_telegram_id,
                    username=latest.user_username,
                    first_name=latest.user_first_name,
                    last_name=latest.user_last_name,
                    connected_at=min(c.connected_at for c in conns),
                    is_enabled=any(c.is_enabled for c in conns),
                    can_reply=any(c.can_reply for c in conns),
                    notifications_enabled=all(c.notifications_enabled for c in conns),
                    is_blocked=any(c.is_blocked for c in conns),
                    total_messages=total_messages,
                    total_chats=total_chats,
                    edited_messages=edited_messages,
                    deleted_messages=deleted_messages,
                    last_activity_at=last_activity_at,
                )
            )

        users.sort(key=lambda u: u.total_messages, reverse=True)

        return AdminOverview(total_users=len(users), users=users)

    async def set_owner_settings(
        self,
        *,
        owner_telegram_id: int,
        notifications_enabled: bool | None = None,
        is_blocked: bool | None = None,
    ) -> int:
        """Apply admin-controlled settings to every connection owned by
        `owner_telegram_id`. Returns the number of connection rows updated.
        """

        connections = (
            await self._session.execute(
                select(BusinessConnection).where(
                    BusinessConnection.user_telegram_id == owner_telegram_id
                )
            )
        ).scalars().all()

        for conn in connections:
            if notifications_enabled is not None:
                conn.notifications_enabled = notifications_enabled
            if is_blocked is not None:
                conn.is_blocked = is_blocked

        return len(connections)
