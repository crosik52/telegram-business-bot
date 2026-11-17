"""Content-free conversation measures and bounded, owner-scoped read coordination."""
from __future__ import annotations

import asyncio
import datetime as dt
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from statistics import median
from typing import Any

SESSION_GAP_SECONDS = 10800
SAMPLE_LIMIT = 2000


@dataclass(frozen=True)
class ConversationPoint:
    id: int
    message_id: int
    sent_at: dt.datetime
    sender_telegram_id: int


def utc(value: dt.datetime) -> dt.datetime:
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value.astimezone(dt.UTC)


def calculate_conversation(
    points: Sequence[ConversationPoint], *, owner_id: int, chat_id: int,
    previous: ConversationPoint | None = None,
    window_start: dt.datetime | None = None, window_end: dt.datetime | None = None,
    sample_truncated: bool = False,
) -> dict[str, Any]:
    """Turns are side changes, not Telegram reply markup or evidence of reading."""
    unique: dict[int, ConversationPoint] = {}
    for point in points:
        if point.sender_telegram_id in (owner_id, chat_id):
            if point.message_id not in unique or unique[point.message_id].id < point.id:
                unique[point.message_id] = point
    rows = sorted(unique.values(), key=lambda p: (utc(p.sent_at), p.message_id, p.id))
    counts = {"you": 0, "contact": 0}
    starts = {"you": 0, "contact": 0}
    replies: dict[str, list[float]] = {"you": [], "contact": []}
    hourly: list[dict[str, Any]] = [{"hour": hour, "messages": {"you": 0, "contact": 0}} for hour in range(24)]
    weekdays: list[dict[str, Any]] = [{"weekday": day, "messages": {"you": 0, "contact": 0}} for day in range(7)]
    distribution: list[dict[str, Any]] = [{"max_seconds": bound, "counts": {"you": 0, "contact": 0}}
                    for bound in (60, 300, 1800, SESSION_GAP_SECONDS)]
    active: dict[str, set[dt.date]] = {"you": set(), "contact": set()}
    known_contexts = 0
    daily: dict[str, dict[str, Any]] = {}
    if window_start is not None and window_end is not None:
        day = utc(window_start).date()
        last_day = utc(window_end).date()
        while day <= last_day:
            midnight = dt.datetime.combine(day, dt.time(), tzinfo=dt.UTC)
            daily[day.isoformat()] = {
                "date": day.isoformat(),
                "partial": midnight < utc(window_start) or midnight + dt.timedelta(days=1) > utc(window_end),
                "outside_sample": bool(sample_truncated and rows and day < utc(rows[0].sent_at).date()),
                "sample_partial": bool(sample_truncated and rows and day == utc(rows[0].sent_at).date()),
                "messages": {"you": 0, "contact": 0},
                "reply_samples": {"you": [], "contact": []},
                "initiations": {"you": 0, "contact": 0},
            }
            day += dt.timedelta(days=1)
    prev = previous
    for point in rows:
        side = "you" if point.sender_telegram_id == owner_id else "contact"
        counts[side] += 1
        timestamp = utc(point.sent_at)
        hourly[timestamp.hour]["messages"][side] += 1
        weekdays[timestamp.weekday()]["messages"][side] += 1
        active[side].add(timestamp.date())
        bucket = daily.get(utc(point.sent_at).date().isoformat())
        if bucket is not None:
            bucket["messages"][side] += 1
        if prev is not None:
            known_contexts += 1
            gap = (utc(point.sent_at) - utc(prev.sent_at)).total_seconds()
            if gap > SESSION_GAP_SECONDS:
                starts[side] += 1
                if bucket is not None:
                    bucket["initiations"][side] += 1
            elif gap >= 0 and point.sender_telegram_id != prev.sender_telegram_id:
                replies[side].append(gap)
                for interval in distribution:
                    if gap <= interval["max_seconds"]:
                        interval["counts"][side] += 1
                        break
                if bucket is not None:
                    bucket["reply_samples"][side].append(gap)
        prev = point
    total = sum(counts.values())
    days = []
    for bucket in daily.values():
        samples = bucket.pop("reply_samples")
        bucket["replies"] = {
            side: {"count": len(values), "median_seconds": float(median(values)) if values else None}
            for side, values in samples.items()
        }
        days.append(bucket)
    return {
        "daily": days, "daily_timezone": "UTC",
        "hourly": hourly, "weekdays": weekdays, "reply_distribution": distribution,
        "activity": {"active_days": {"you": len(active["you"]), "contact": len(active["contact"]),
                                     "any": len(active["you"] | active["contact"])},
                     "known_initiation_contexts": known_contexts},
        "messages": counts, "total_messages": total, "initiations": starts,
        "shares": {side: round(value * 100 / total, 1) if total else None
                   for side, value in counts.items()},
        "replies": {side: {"count": len(values),
                           "median_seconds": float(median(values)) if values else None}
                    for side, values in replies.items()},
        "sample_start": utc(rows[0].sent_at).isoformat() if rows else None,
        "sample_end": utc(rows[-1].sent_at).isoformat() if rows else None,
        "left_edge_unknown": bool(rows and previous is None),
    }


class ConversationBusy(Exception):
    """No unbounded query queue is created when capacity is exhausted."""


CacheKey = tuple[int, int, int, tuple[str, ...]]


class ConversationReads:
    """One process: 128 small aggregate entries, two readers, finite owner buckets."""

    def __init__(self) -> None:
        self.cache: OrderedDict[CacheKey, tuple[float, dict[str, Any]]] = OrderedDict()
        self.pending: dict[CacheKey, asyncio.Future[dict[str, Any] | None]] = {}
        self.buckets: dict[int, tuple[float, int]] = {}

    def admit(self, owner_id: int) -> bool:
        now = time.monotonic()
        for owner, (started, _) in list(self.buckets.items()):
            if now - started >= 60:
                del self.buckets[owner]
        if owner_id not in self.buckets and len(self.buckets) >= 2048:
            return False
        started, count = self.buckets.get(owner_id, (now, 0))
        if count >= 30:
            return False
        self.buckets[owner_id] = (started, count + 1)
        return True

    async def read(self, key: CacheKey, loader: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        now = time.monotonic()
        for old, (created, _) in list(self.cache.items()):
            if now - created >= 60:
                del self.cache[old]
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key][1]
        if key in self.pending:
            result = await asyncio.shield(self.pending[key])
            if result is None:
                raise ConversationBusy
            return result
        if len(self.pending) >= 2:
            raise ConversationBusy
        future: asyncio.Future[dict[str, Any] | None] = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        try:
            result = await loader()
            self.cache[key] = (time.monotonic(), result)
            while len(self.cache) > 128:
                self.cache.popitem(last=False)
            future.set_result(result)
            return result
        finally:
            if not future.done():
                future.set_result(None)
            self.pending.pop(key, None)


conversation_reads = ConversationReads()
