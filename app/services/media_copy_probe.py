"""Consented primary sendPhoto/sendVideo by Business reply file_id to the owner's DM."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from aiogram import Bot
from aiogram.types import Update

from app.logging_config import get_logger

logger = get_logger(__name__)
PREFIX = "MEDIA_COPY_PROBE_"
CASES = frozenset({"ordinary_photo", "ordinary_video", "once_photo", "once_video"})
STATUSES = frozenset({"api_accepted", "api_denied", "unknown_delivery", "unavailable", "busy", "bad_reply"})
CAPTION = "Save Log: synthetic media copy test."
MAX_ATTEMPTS = 8
MAX_RESPONSE_BYTES = 16 * 1024
TOTAL_TIMEOUT = 10
# Exact static descriptions only. Never retain or interpolate arbitrary API text.
ERROR_DESCRIPTIONS = {
    "Bad Request: can't use file of type SelfDestructingPhoto as Photo": "self_destructing_photo_as_photo",
    "Bad Request: can't use file of type SelfDestructingVideo as Video": "self_destructing_video_as_video",
    "Bad Request: wrong remote file identifier specified: can't unserialize it": "remote_file_unserialize",
    "Bad Request: wrong remote file identifier specified: can't unserialize it. Wrong last symbol": "remote_file_last_symbol",
    "Bad Request: invalid remote file identifier": "invalid_remote_file_id",
    "Bad Request: remote file identifier must be non-empty": "remote_file_empty",
    "Bad Request: can't use inputFileRemote": "remote_file_not_reusable",
    "Bad Request: file_id not specified": "file_id_missing",
    "Bad Request: wrong file_id or the file is temporarily unavailable": "file_id_unavailable",
    "Forbidden: bot was blocked by the user": "bot_blocked",
    "Forbidden: user is deactivated": "user_deactivated",
}
ERROR_REASONS = frozenset({*ERROR_DESCRIPTIONS.values(), "unknown_description"})


@dataclass(frozen=True)
class CopyConfig:
    bot_id: int
    owner_id: int
    chat_id: int
    expires_at: int


def read_config(env: Mapping[str, str], now: float) -> CopyConfig | None:
    if env.get(PREFIX + "ENABLED") != "1":
        return None
    try:
        values = [env[PREFIX + name] for name in ("BOT_ID", "OWNER_ID", "CHAT_ID", "EXPIRES_AT")]
        if not all(re.fullmatch(r"[1-9][0-9]{0,15}", value) for value in values):
            return None
        bot_id, owner_id, chat_id, expiry = map(int, values)
        if not all(0 < value < 2**52 for value in (bot_id, owner_id, chat_id)):
            return None
        if len({bot_id, owner_id, chat_id}) != 3 or not now < expiry <= now + 3600:
            return None
        return CopyConfig(bot_id, owner_id, chat_id, expiry)
    except (KeyError, ValueError):
        return None


def emit(result: dict[str, Any]) -> None:
    try:
        logger.info("MEDIA_COPY_PROBE %s", json.dumps(result, separators=(",", ":")))
    except Exception:
        pass


def valid_file_id(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,512}", value))


def capture_error(value: dict, result: dict[str, Any]) -> None:
    code = value.get("error_code")
    result["telegram_error_code"] = code if type(code) is int and 100 <= code <= 599 else None
    description = value.get("description")
    result["error_reason"] = (ERROR_DESCRIPTIONS.get(description, "unknown_description")
                              if isinstance(description, str) else "unknown_description")


def acknowledged(sent: dict, config: CopyConfig, case: str) -> bool:
    message_id = sent.get("message_id")
    chat = sent.get("chat")
    if (type(message_id) is not int or message_id <= 0 or not isinstance(chat, dict)
            or type(chat.get("id")) is not int or chat["id"] != config.owner_id or chat.get("type") != "private"
            or sent.get("has_protected_content") is not True):
        return False
    if case.endswith("_photo"):
        photo = sent.get("photo")
        return (isinstance(photo, list) and bool(photo) and isinstance(photo[-1], dict)
                and valid_file_id(photo[-1].get("file_id")) and not sent.get("video"))
    video = sent.get("video")
    return isinstance(video, dict) and valid_file_id(video.get("file_id")) and not sent.get("photo")


async def send_once(bot: Bot, config: CopyConfig, file_id: str, result: dict[str, Any]) -> None:
    token = bot.token
    if (not valid_file_id(file_id) or not isinstance(token, str)
            or not re.fullmatch(r"[1-9][0-9]{0,15}:[A-Za-z0-9_-]{30,128}", token)
            or token.split(":", 1)[0] != str(config.bot_id)):
        return
    # Source identity is the selected Telegram file_id; no source chat/message lookup.
    media_key = "photo" if result["method"] == "sendPhoto" else "video"
    body = {"chat_id": config.owner_id, media_key: file_id, "caption": CAPTION, "protect_content": True,
            "disable_notification": True, "allow_paid_broadcast": False}
    async with aiohttp.ClientSession(
        trust_env=False, auto_decompress=False, timeout=aiohttp.ClientTimeout(total=TOTAL_TIMEOUT),
        connector=aiohttp.TCPConnector(limit=1, ssl=True), cookie_jar=aiohttp.DummyCookieJar(),
        headers={"Accept-Encoding": "identity"},
    ) as session:
        # Once a write might be sent, failures cannot prove non-delivery. No retry.
        result["status"] = "unknown_delivery"
        async with session.post(
            "https://api.telegram.org/bot" + token + "/" + result["method"], json=body, allow_redirects=False,
        ) as response:
            status = response.status
            if type(status) is not int or not 100 <= status <= 599:
                return
            result["http_status"] = status
            if status != 200:
                result["error_reason"] = "unknown_description"
            if 400 <= status <= 499:
                result["status"] = "api_denied"
            if status != 200 and not 400 <= status <= 599:
                return
            data = bytearray()
            async for chunk in response.content.iter_chunked(4096):
                if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
                    return
                data.extend(chunk)
            try:
                value = json.loads(data)
            except (ValueError, TypeError):
                return
            if not isinstance(value, dict):
                return
            if value.get("ok") is False:
                capture_error(value, result)
                if status < 500:
                    result["status"] = "api_denied"
                return
            sent = value.get("result")
            if status == 200 and value.get("ok") is True and isinstance(sent, dict):
                if acknowledged(sent, config, result["case"]):
                    result.update(status="api_accepted", api_success=True)


@dataclass
class MediaCopyProbe:
    config: CopyConfig | None
    seen: set[int] = field(default_factory=set)
    busy: bool = False
    deadline: float = field(default_factory=lambda: time.monotonic() + 3600)

    async def consume(self, bot: Bot, update: Update, now: float) -> bool:
        config = self.config
        message = update.business_message
        if (config is None or bot.id != config.bot_id or message is None
                or not message.business_connection_id or message.chat.type != "private"
                or message.chat.id != config.chat_id or message.from_user is None
                or message.from_user.is_bot or message.from_user.id != config.owner_id):
            return False
        text = message.text or ""
        if not text.startswith("!media_copy_probe "):
            return False
        case = text.removeprefix("!media_copy_probe ")
        if case not in CASES:
            return False
        if (now >= config.expires_at or time.monotonic() >= self.deadline
                or update.update_id in self.seen or len(self.seen) >= MAX_ATTEMPTS):
            return True
        self.seen.add(update.update_id)
        reply = message.reply_to_message
        result = {"case": case, "protected": bool(reply and reply.has_protected_content),
                  "method": "sendPhoto" if case.endswith("_photo") else "sendVideo",
                  "status": "bad_reply", "api_success": False, "http_status": None,
                  "telegram_error_code": None, "error_reason": None}
        if (reply is None or reply.chat.id != config.chat_id or reply.chat.type != "private"
                or reply.date.timestamp() <= 0
                or (reply.business_connection_id is not None
                    and reply.business_connection_id != message.business_connection_id)
                or (case.endswith("_photo") and (not reply.photo or reply.video))
                or (case.endswith("_video") and (not reply.video or reply.photo))):
            emit(result)
            return True
        file_id = None
        if case.endswith("_photo") and reply.photo:
            file_id = reply.photo[-1].file_id
        elif case.endswith("_video") and reply.video:
            file_id = reply.video.file_id
        if not isinstance(file_id, str) or not valid_file_id(file_id):
            emit(result)
            return True
        if self.busy:
            result["status"] = "busy"
            emit(result)
            return True
        self.busy = True
        result["status"] = "unavailable"
        try:
            remaining = min(TOTAL_TIMEOUT, config.expires_at - now, self.deadline - time.monotonic())
            async with asyncio.timeout(max(0, remaining)):
                await send_once(bot, config, file_id, result)
        except asyncio.CancelledError:
            if not result["api_success"] and result["status"] != "api_denied":
                result["status"] = "unknown_delivery"
            emit(result)
            raise
        except Exception:
            if not result["api_success"] and result["status"] != "api_denied":
                result["status"] = "unknown_delivery"
        finally:
            self.busy = False
        emit(result)
        return True


_probe: MediaCopyProbe | None = None


async def consume_copy_probe(bot: Bot, update: Update) -> bool:
    global _probe
    now = time.time()
    if _probe is None:
        _probe = MediaCopyProbe(read_config(os.environ, now))
    return await _probe.consume(bot, update, now)
