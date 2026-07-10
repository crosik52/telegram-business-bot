"""In-memory rolling-window panic tracker for rapid message deletions.

Detects when a contact deletes a suspicious number of messages in a short
time window — commonly called a "panic delete" (cleaning the conversation
before the other side notices).

One shared ``PanicTracker`` instance lives for the lifetime of the process.
Because aiogram dispatches handlers sequentially within a single process,
no additional locking is required.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

# ── Defaults ────────────────────────────────────────────────────────────────

WINDOW_SECONDS: int = 60    # rolling window for counting deletions
THRESHOLD: int = 3          # deletions within window to qualify as panic
COOLDOWN_SECONDS: int = 120 # min seconds between panic alerts for same chat


# ── Internal state ───────────────────────────────────────────────────────────

@dataclass
class _ChatState:
    timestamps: list[dt.datetime] = field(default_factory=list)
    last_alert_at: dt.datetime | None = None


# ── Tracker ──────────────────────────────────────────────────────────────────

class PanicTracker:
    """Rolling-window deletion tracker.

    Usage::

        tracker = PanicTracker()   # once, at module level
        should_alert, total = tracker.record(chat_key, deleted_count)
    """

    def __init__(
        self,
        window: int = WINDOW_SECONDS,
        threshold: int = THRESHOLD,
        cooldown: int = COOLDOWN_SECONDS,
    ) -> None:
        self._window = window
        self._threshold = threshold
        self._cooldown = cooldown
        self._state: dict[str, _ChatState] = defaultdict(_ChatState)

    def record(
        self,
        key: str,
        count: int,
        now: dt.datetime | None = None,
    ) -> tuple[bool, int]:
        """Record *count* new deletions for *key*.

        Returns ``(should_alert, total_in_window)``.

        ``should_alert`` is ``True`` only when:
        - the running total in the rolling window reaches the threshold, AND
        - the cooldown period since the last alert for this key has elapsed.

        Callers are responsible for deciding *how* to surface the alert; this
        method is purely stateful bookkeeping.
        """
        if now is None:
            now = dt.datetime.now(dt.UTC)

        state = self._state[key]
        cutoff = now - dt.timedelta(seconds=self._window)

        # Purge timestamps outside the window.
        state.timestamps = [t for t in state.timestamps if t >= cutoff]

        # Append one timestamp per deleted message.
        state.timestamps.extend([now] * count)

        total = len(state.timestamps)
        if total < self._threshold:
            return False, total

        # Threshold met — check cooldown before firing again.
        if state.last_alert_at is not None:
            elapsed = (now - state.last_alert_at).total_seconds()
            if elapsed < self._cooldown:
                return False, total

        state.last_alert_at = now
        return True, total

    def clear(self, key: str) -> None:
        """Remove accumulated state for a key (e.g. after connection removal)."""
        self._state.pop(key, None)
