"""Opt-in metadata-only Business reply diagnostic; no Telegram calls or persistence."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from aiogram.types import Update

from app.logging_config import get_logger

logger = get_logger(__name__)
MAX_RESULTS = 32
MAX_DURATION_SECONDS = 3600
CASES = frozenset({
    "ordinary_photo", "ordinary_video", "once_photo", "once_video", "timer_photo", "timer_video",
})
MEDIA_FIELDS = (
    "photo", "video", "voice", "video_note", "audio", "document", "animation", "sticker",
    "live_photo", "paid_media",
)
HINT_FIELDS = frozenset({
    "ttl", "ttl_seconds", "ttl_period", "is_view_once", "view_once", "self_destruct_type",
    "self_destruct_time", "self_destruct", "is_self_destruct",
})
ENV_PREFIX = "MEDIA_REPLY_PROBE_"


@dataclass(frozen=True)
class ProbeConfig:
    bot_id: int
    owner_id: int
    chat_id: int
    expires_at: int


def _identifier(value: str, *, signed: bool = False) -> int:
    if not re.fullmatch(r"-?[1-9][0-9]{0,15}" if signed else r"[1-9][0-9]{0,15}", value):
        raise ValueError
    parsed = int(value)
    if abs(parsed) >= 2**52:
        raise ValueError
    return parsed


def read_config(env: Mapping[str, str], now: float) -> ProbeConfig | None:
    """Invalid, incomplete, expired, or excessive settings disable the diagnostic."""
    if env.get(ENV_PREFIX + "ENABLED") != "1":
        return None
    try:
        bot_id = _identifier(env[ENV_PREFIX + "BOT_ID"])
        owner_id = _identifier(env[ENV_PREFIX + "OWNER_ID"])
        chat_id = _identifier(env[ENV_PREFIX + "CHAT_ID"], signed=True)
        expiry_raw = env[ENV_PREFIX + "EXPIRES_AT"]
        if not re.fullmatch(r"[1-9][0-9]{0,10}", expiry_raw):
            return None
        expires_at = int(expiry_raw)
        if not now < expires_at <= now + MAX_DURATION_SECONDS:
            return None
    except (KeyError, ValueError):
        return None
    return ProbeConfig(bot_id, owner_id, chat_id, expires_at)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _has_file_id(value: Any) -> bool:
    if isinstance(value, list):
        return any(_has_file_id(item) for item in value)
    media = _mapping(value)
    file_id = media.get("file_id")
    return isinstance(file_id, str) and bool(file_id)


def _media_summary(value: Any) -> dict[str, Any]:
    source = _mapping(value)
    media_summary = {
        name: {"present": name in source, "file_id_present": _has_file_id(source.get(name))}
        for name in MEDIA_FIELDS
    }
    hints = bool(HINT_FIELDS.intersection(source))
    for name in MEDIA_FIELDS:
        values = source.get(name)
        for item in values if isinstance(values, list) else [values]:
            hints = hints or bool(HINT_FIELDS.intersection(_mapping(item)))
    return {
        "media": media_summary,
        "protected": source.get("has_protected_content") is True,
        "ttl_or_view_once_hint": hints,
    }


def summarize(payload: Mapping[str, Any], case: str) -> dict[str, Any]:
    """Build a constant-key schema; never copy raw values, names, IDs, or text."""
    if case not in CASES:
        raise ValueError
    message = _mapping(payload.get("business_message"))
    reply = message.get("reply_to_message")
    external = message.get("external_reply")
    return {
        "case": case,
        "reply_present": isinstance(reply, dict),
        "external_reply_present": isinstance(external, dict),
        "reply_inaccessible": isinstance(reply, dict) and reply.get("date") == 0,
        "message": _media_summary(message),
        "reply": _media_summary(reply),
        "external_reply": _media_summary(external),
    }


@dataclass
class MediaReplyProbe:
    config: ProbeConfig | None
    seen: set[int] = field(default_factory=set)
    ready_reported: bool = False
    deadline_monotonic: float = field(default_factory=lambda: time.monotonic() + MAX_DURATION_SECONDS)

    @staticmethod
    def _emit(result: dict[str, Any]) -> None:
        try:
            logger.info("MEDIA_REPLY_PROBE %s", json.dumps(result, separators=(",", ":")))
        except Exception:
            # A logging failure must not fall back to raw inbox persistence.
            pass

    def consume(self, bot_id: int, update: Update, payload: Mapping[str, Any], now: float) -> bool:
        """Return True only for an exact, scoped probe; never enqueue consumed probes."""
        config = self.config
        message = update.business_message
        expired = config is not None and (now >= config.expires_at or time.monotonic() >= self.deadline_monotonic)
        if config is not None and bot_id == config.bot_id and not expired and not self.ready_reported:
            self.ready_reported = True
            self._emit({"status": "ready"})
        if (config is None or bot_id != config.bot_id or message is None
                or not message.business_connection_id or message.from_user is None
                or message.from_user.id != config.owner_id or message.chat.id != config.chat_id):
            return False
        text = message.text
        if not isinstance(text, str) or not text.startswith("!media_probe "):
            return False
        case = text.removeprefix("!media_probe ")
        if case not in CASES:
            return False
        # Once scoped, retries, expired probes, and quota excess are acknowledged
        # without logging or sending the raw reply into the durable inbox.
        if expired or update.update_id in self.seen or len(self.seen) >= MAX_RESULTS:
            return True
        self.seen.add(update.update_id)
        try:
            result = summarize(payload, case)
        except Exception:
            # Neither arbitrary values nor exception text enter logs/errors.
            result = {"case": case, "status": "unavailable"}
        self._emit(result)
        return True


_probe: MediaReplyProbe | None = None


def consume_probe(bot_id: int, update: Update, payload: Mapping[str, Any]) -> bool:
    """Load env once per process; changing settings requires a deliberate restart."""
    global _probe
    now = time.time()
    if _probe is None:
        _probe = MediaReplyProbe(read_config(os.environ, now))
    return _probe.consume(bot_id, update, payload, now)
