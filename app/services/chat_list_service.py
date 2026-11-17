"""Bounded chat-list views, independent of statistics/share truncation."""
from __future__ import annotations

import time
from dataclasses import dataclass

from app.repositories.chat_list_repository import ChatListPreferences
from app.repositories.stats_repository import InterlocutorStat

# One app process; bounded owner buckets, no persisted request data or contents.
_buckets: dict[tuple[int, str], tuple[float, int]] = {}


def admit_chat_list(owner_id: int, operation: str) -> bool:
    now = time.monotonic()
    for key, (start, _) in list(_buckets.items()):
        if now - start >= 60:
            del _buckets[key]
    key = (owner_id, operation)
    if key not in _buckets and len(_buckets) >= 2048:
        return False
    start, count = _buckets.get(key, (now, 0))
    limit = 60 if operation == "list" else 30
    if count >= limit:
        return False
    _buckets[key] = (start, count + 1)
    return True


@dataclass(frozen=True)
class ChatListPage:
    rows: list[InterlocutorStat]
    total: int
    archive_count: int
    missing_archive_count: int


def chat_list_page(rows: list[InterlocutorStat], prefs: ChatListPreferences, *, view: str,
                   sort: str, query: str, offset: int, limit: int,
                   free_limit: int | None = None) -> ChatListPage:
    archived = set(prefs.archived)
    known = {row.chat_id for row in rows}
    selected = [row for row in rows if (row.chat_id in archived) == (view == "archived")]
    query = query.strip().casefold()
    if query:
        selected = [row for row in selected if query in row.display_name.casefold()
                    or query in (row.username or "").casefold()]
    if sort == "recent":
        selected.sort(key=lambda row: row.last_message_at.timestamp() if row.last_message_at else 0, reverse=True)
    elif sort == "streaks":
        selected.sort(key=lambda row: (row.streak_days, row.longest_streak), reverse=True)
    elif sort == "edited":
        selected.sort(key=lambda row: row.edited_count, reverse=True)
    else:
        selected.sort(key=lambda row: row.message_count, reverse=True)
    if view == "active":
        # Stable among pins: retain the selected stats ordering.
        selected.sort(key=lambda row: row.chat_id not in prefs.pinned)
    total = len(selected)
    if free_limit is not None:
        selected = selected[:free_limit]
    return ChatListPage(selected[offset:offset + limit], total, len(archived), len(archived - known))
