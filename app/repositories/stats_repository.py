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
    streak_days: int = 0
    longest_streak: int = 0
    mutual_connected: bool = False


@dataclass
class OwnerStats:
    """Personal statistics for a single connection owner (mini app)."""

    owner_telegram_id: int
    total_messages: int
    total_chats: int
    edited_messages: int
    deleted_messages: int
    best_streak: int = 0
    best_streak_name: str = ""
    global_longest_streak: int = 0
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
                    "active_dates": set(),
                },
            )
            chat["message_count"] += 1
            if r.is_edited:
                chat["edited_count"] += 1
            if r.is_deleted:
                chat["deleted_count"] += 1
            if r.sent_at:
                chat["active_dates"].add(r.sent_at.date())
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

        mutual_owner_ids = await self._get_active_owner_telegram_ids()

        top_interlocutors: list[InterlocutorStat] = []
        for chat_id, data in chats.items():
            first_name, last_name, username = data["counterpart"] or (None, None, None)
            name_parts = [p for p in (first_name, last_name) if p]
            display_name = (
                " ".join(name_parts)
                if name_parts
                else (f"@{username}" if username else f"Чат {chat_id}")
            )
            current_streak = _calculate_streak(data["active_dates"])
            longest = _calculate_longest_streak(data["active_dates"])
            top_interlocutors.append(
                InterlocutorStat(
                    chat_id=chat_id,
                    display_name=display_name,
                    username=username,
                    message_count=data["message_count"],
                    edited_count=data["edited_count"],
                    deleted_count=data["deleted_count"],
                    last_message_at=data["last_message_at"],
                    streak_days=current_streak,
                    longest_streak=longest,
                    mutual_connected=chat_id in mutual_owner_ids,
                )
            )

        top_interlocutors.sort(key=lambda s: s.message_count, reverse=True)

        best_streak = max((s.streak_days for s in top_interlocutors), default=0)
        global_longest_streak = max((s.longest_streak for s in top_interlocutors), default=0)
        best_streak_holder = max(
            top_interlocutors, key=lambda s: s.streak_days, default=None
        )
        best_streak_name = best_streak_holder.display_name if best_streak_holder and best_streak_holder.streak_days >= 2 else ""

        return OwnerStats(
            owner_telegram_id=owner_telegram_id,
            total_messages=total_messages,
            total_chats=len(chats),
            edited_messages=edited_messages,
            deleted_messages=deleted_messages,
            best_streak=best_streak,
            best_streak_name=best_streak_name,
            global_longest_streak=global_longest_streak,
            top_interlocutors=top_interlocutors[:top_n],
        )

    async def get_owner_activity(
        self, *, connection_ids: list[str], days: int = 90
    ) -> dict[str, int]:
        """Message counts per calendar day for the last `days` days, for the
        personal activity heatmap in the mini app."""

        if not connection_ids:
            return {}

        since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
        stmt = (
            select(func.date(Message.sent_at), func.count(Message.id))
            .where(
                Message.business_connection_id.in_(connection_ids),
                Message.sent_at >= since,
            )
            .group_by(func.date(Message.sent_at))
        )
        rows = (await self._session.execute(stmt)).all()
        return {str(row[0]): int(row[1]) for row in rows}

    async def get_admin_growth(self, *, days: int = 30) -> dict[str, dict[str, int]]:
        """Daily new-connection counts and message volume for the admin
        analytics chart."""

        since = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)

        conn_stmt = (
            select(
                func.date(BusinessConnection.connected_at),
                func.count(func.distinct(BusinessConnection.user_telegram_id)),
            )
            .where(BusinessConnection.connected_at >= since)
            .group_by(func.date(BusinessConnection.connected_at))
        )
        conn_rows = (await self._session.execute(conn_stmt)).all()

        msg_stmt = (
            select(func.date(Message.sent_at), func.count(Message.id))
            .where(Message.sent_at >= since)
            .group_by(func.date(Message.sent_at))
        )
        msg_rows = (await self._session.execute(msg_stmt)).all()

        return {
            "connections_by_day": {str(r[0]): int(r[1]) for r in conn_rows},
            "messages_by_day": {str(r[0]): int(r[1]) for r in msg_rows},
        }

    async def owner_has_chat(
        self, *, connection_ids: list[str], chat_id: int
    ) -> bool:
        """Whether any message exists in one of these connections for the
        given chat — i.e. the caller actually has this counterpart in their
        own message history (authorization check before sending a game)."""

        if not connection_ids:
            return False

        result = await self._session.execute(
            select(Message.id)
            .where(
                Message.business_connection_id.in_(connection_ids),
                Message.chat_id == chat_id,
            )
            .limit(1)
        )
        return result.first() is not None

    async def _get_active_owner_telegram_ids(self) -> set[int]:
        """Telegram user ids of everyone who currently has an active
        (non-blocked) business connection to the bot.

        Used to detect "mutual" connections: a chat counterpart is only
        offered in-chat games if they are a bot user too, since games are
        delivered by sending a dice-type message via *that counterpart's*
        business connection.
        """

        rows = (
            await self._session.execute(
                select(BusinessConnection.user_telegram_id).where(
                    BusinessConnection.is_blocked.is_(False)
                )
            )
        ).all()
        return {row[0] for row in rows}

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

        # One batched aggregate query instead of one query per owner.
        all_conn_ids = [c.business_connection_id for c in connections]
        stats_by_conn: dict[str, tuple[int, int, int, int, dt.datetime | None]] = {}
        if all_conn_ids:
            agg_stmt = (
                select(
                    Message.business_connection_id,
                    func.count(Message.id).label("total"),
                    func.count(Message.id).filter(
                        Message.is_edited.is_(True)
                    ).label("edited"),
                    func.count(Message.id).filter(
                        Message.is_deleted.is_(True)
                    ).label("deleted"),
                    func.count(Message.chat_id.distinct()).label("chats"),
                    func.max(Message.sent_at).label("last_at"),
                )
                .where(Message.business_connection_id.in_(all_conn_ids))
                .group_by(Message.business_connection_id)
            )
            for row in (await self._session.execute(agg_stmt)).all():
                stats_by_conn[row.business_connection_id] = (
                    row.total,
                    row.edited,
                    row.deleted,
                    row.chats,
                    row.last_at,
                )

        users: list[AdminUserRow] = []
        for owner_telegram_id, conns in by_owner.items():
            latest = max(conns, key=lambda c: c.connected_at)

            total_messages = edited_messages = deleted_messages = total_chats = 0
            last_activity_at: dt.datetime | None = None
            for conn in conns:
                s = stats_by_conn.get(conn.business_connection_id)
                if s:
                    total_messages += s[0]
                    edited_messages += s[1]
                    deleted_messages += s[2]
                    total_chats += s[3]
                    if s[4] and (last_activity_at is None or s[4] > last_activity_at):
                        last_activity_at = s[4]

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


def _calculate_longest_streak(active_dates: set[dt.date]) -> int:
    """Find the longest consecutive-day streak ever recorded, regardless of recency."""
    if not active_dates:
        return 0
    sorted_dates = sorted(active_dates)
    longest = 1
    current = 1
    for i in range(1, len(sorted_dates)):
        if (sorted_dates[i] - sorted_dates[i - 1]).days == 1:
            current += 1
            if current > longest:
                longest = current
        else:
            current = 1
    return longest


def _calculate_streak(active_dates: set[dt.date]) -> int:
    """Consecutive-day messaging streak, ending today or yesterday.

    A streak is "alive" only if the most recent active day was today or
    yesterday (grace period so the streak doesn't reset mid-timezone-day);
    otherwise it has already been broken and the streak is 0.
    """

    if not active_dates:
        return 0

    today = dt.datetime.now(dt.UTC).date()
    most_recent = max(active_dates)
    if (today - most_recent).days > 1:
        return 0

    streak = 0
    cursor = most_recent
    while cursor in active_dates:
        streak += 1
        cursor -= dt.timedelta(days=1)

    return streak
