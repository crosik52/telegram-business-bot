"""Opt-in, consented photo download and single protected multipart document upload."""
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
from app.services.media_copy_probe import CopyConfig, capture_error, valid_file_id
from app.services.media_download_probe import valid_path

logger = get_logger(__name__)
PREFIX = "MEDIA_UPLOAD_PROBE_"
CASES = frozenset({"ordinary_photo", "once_photo"})
MAX_BYTES = 8 * 1024 * 1024
CHUNK_BYTES = 64 * 1024
MAX_API_BYTES = 16 * 1024
MAX_ATTEMPTS = 4
TOTAL_TIMEOUT = 30
GET_FILE_TIMEOUT = 5
UPLOAD_TIMEOUT = 10
CAPTION = "Save Log: synthetic photo document test."
STAGES = frozenset({"precheck", "get_file", "download", "upload"})
STATUSES = frozenset({"bad_reply", "busy", "unavailable", "timeout", "get_file_denied", "bad_path",
                      "download_denied", "too_large", "empty", "format_mismatch", "size_mismatch",
                      "api_accepted", "api_denied", "unknown_delivery"})


def read_config(env: Mapping[str, str], now: float) -> CopyConfig | None:
    if env.get(PREFIX + "ENABLED") != "1":
        return None
    try:
        raw = [env[PREFIX + name] for name in ("BOT_ID", "OWNER_ID", "CHAT_ID", "EXPIRES_AT")]
        if not all(re.fullmatch(r"[1-9][0-9]{0,15}", value) for value in raw):
            return None
        bot_id, owner_id, chat_id, expiry = map(int, raw)
        if (not all(0 < value < 2**52 for value in (bot_id, owner_id, chat_id))
                or len({bot_id, owner_id, chat_id}) != 3 or not now < expiry <= now + 3600):
            return None
        return CopyConfig(bot_id, owner_id, chat_id, expiry)
    except (KeyError, ValueError):
        return None


def outcome(case: str, protected: bool) -> dict[str, Any]:
    return {"case": case, "protected": protected, "method": "sendDocument", "stage": "precheck",
            "status": "bad_reply", "bytes_received": 0, "get_file_ok": False,
            "download_complete": False, "expected_format_match": False, "api_success": False,
            "http_status": None, "telegram_error_code": None, "error_reason": None}


def emit(result: dict[str, Any]) -> None:
    try:
        logger.info("MEDIA_UPLOAD_PROBE %s", json.dumps(result, separators=(",", ":")))
    except Exception:
        pass


def reset_http(result: dict[str, Any]) -> None:
    result.update(http_status=None, telegram_error_code=None, error_reason=None)


async def api_json(session: aiohttp.ClientSession, token: str, method: str,
                   data: Any, result: dict[str, Any], *, upload: bool = False,
                   expected: tuple[CopyConfig, int, str] | None = None) -> dict | None:
    reset_http(result)
    if upload:
        # An initiated write may have delivered even if its response is lost.
        result["status"] = "unknown_delivery"
    async with session.post("https://api.telegram.org/bot" + token + "/" + method, data=data,
                            allow_redirects=False, timeout=aiohttp.ClientTimeout(
                                total=UPLOAD_TIMEOUT if upload else GET_FILE_TIMEOUT)) as response:
        status = response.status
        if type(status) is not int or not 100 <= status <= 599:
            return None
        result["http_status"] = status
        if status != 200:
            result["error_reason"] = "unknown_description"
            if 400 <= status <= 499:
                result["status"] = "api_denied" if upload else "get_file_denied"
        if status != 200 and not 400 <= status <= 599:
            if not upload:
                result["status"] = "get_file_denied"
            return None
        body = bytearray()
        async for chunk in response.content.iter_chunked(4096):
            if len(chunk) > 4096 or len(body) + len(chunk) > MAX_API_BYTES:
                return None
            body.extend(chunk)
        try:
            value = json.loads(body)
        except (ValueError, TypeError):
            return None
        if not isinstance(value, dict):
            return None
        if value.get("ok") is False:
            capture_error(value, result)
            if not upload or status < 500:
                result["status"] = "api_denied" if upload else "get_file_denied"
            return None
        if status == 200 and value.get("ok") is True and isinstance(value.get("result"), dict):
            if upload and expected is not None and acknowledged(value["result"], *expected):
                result.update(status="api_accepted", api_success=True)
            return value["result"]
        return None


async def read_photo(session: aiohttp.ClientSession, token: str, path: str, sizes: list[int],
                     buffer: bytearray, result: dict[str, Any]) -> str | None:
    result["stage"] = "download"
    reset_http(result)
    async with session.get("https://api.telegram.org/file/bot" + token + "/" + path,
                           allow_redirects=False) as response:
        if type(response.status) is int and 100 <= response.status <= 599:
            result["http_status"] = response.status
        if (response.status != 200 or response.headers.get("Content-Range") is not None
                or response.headers.get("Content-Encoding", "identity").lower() != "identity"):
            result["status"] = "download_denied"
            return None
        length = response.headers.get("Content-Length")
        if length is not None:
            if not re.fullmatch(r"[0-9]{1,12}", length):
                result["status"] = "download_denied"
                return None
            sizes.append(int(length))
        if any(size > MAX_BYTES for size in sizes):
            result["status"] = "too_large"
            return None
        async for chunk in response.content.iter_chunked(CHUNK_BYTES):
            count = len(buffer) + len(chunk)
            result["bytes_received"] = min(count, MAX_BYTES)
            if len(chunk) > CHUNK_BYTES or count > MAX_BYTES:
                result["status"] = "too_large"
                return None
            buffer.extend(chunk)
        if not buffer:
            result["status"] = "empty"
            return None
        if buffer.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif buffer.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        else:
            result["status"] = "format_mismatch"
            return None
        result["expected_format_match"] = True
        if not sizes or not any(size > 0 for size in sizes) or any(size != len(buffer) for size in sizes):
            result["status"] = "size_mismatch"
            return None
    # EOF, all sizes, signature, and response cleanup must finish before upload.
    result["download_complete"] = True
    return mime


def acknowledged(sent: dict, config: CopyConfig, size: int, mime: str) -> bool:
    chat, document = sent.get("chat"), sent.get("document")
    return (type(sent.get("message_id")) is int and sent["message_id"] > 0
            and isinstance(chat, dict) and type(chat.get("id")) is int
            and chat["id"] == config.owner_id and chat.get("type") == "private"
            and sent.get("has_protected_content") is True and isinstance(document, dict)
            and type(document.get("file_size")) is int and document["file_size"] == size
            and document.get("mime_type") == mime and valid_file_id(document.get("file_id"))
            and not sent.get("photo") and not sent.get("video"))


async def transfer(bot: Bot, config: CopyConfig, file_id: str, declared_size: int | None,
                   buffer: bytearray, result: dict[str, Any]) -> None:
    token = bot.token
    if (not isinstance(token, str) or not re.fullmatch(r"[1-9][0-9]{0,15}:[A-Za-z0-9_-]{30,128}", token)
            or token.split(":", 1)[0] != str(config.bot_id)):
        return
    sizes = []
    if declared_size is not None:
        if type(declared_size) is not int or declared_size < 0:
            return
        sizes.append(declared_size)
    if any(size > MAX_BYTES for size in sizes):
        result["status"] = "too_large"
        return
    async with aiohttp.ClientSession(
        trust_env=False, auto_decompress=False, timeout=aiohttp.ClientTimeout(total=TOTAL_TIMEOUT),
        connector=aiohttp.TCPConnector(limit=1, ssl=True), cookie_jar=aiohttp.DummyCookieJar(),
        headers={"Accept-Encoding": "identity"},
    ) as session:
        result["stage"] = "get_file"
        info = await api_json(session, token, "getFile", {"file_id": file_id}, result)
        if info is None:
            return
        result["get_file_ok"] = True
        path = info.get("file_path")
        if not isinstance(path, str) or not valid_path(path):
            result["status"] = "bad_path"
            return
        size = info.get("file_size")
        if size is not None:
            if type(size) is not int or size < 0:
                return
            sizes.append(size)
        if any(value > MAX_BYTES for value in sizes):
            result["status"] = "too_large"
            return
        mime = await read_photo(session, token, path, sizes, buffer, result)
        if mime is None:
            return
        form = aiohttp.FormData()
        for key, value in {"chat_id": str(config.owner_id), "document": "attach://test_photo",
                           "caption": CAPTION, "protect_content": "true", "disable_notification": "true",
                           "allow_paid_broadcast": "false", "disable_content_type_detection": "true"}.items():
            form.add_field(key, value)
        form.add_field("test_photo", bytes(buffer), filename="test-photo.jpg" if mime == "image/jpeg" else "test-photo.png",
                       content_type=mime)
        result["stage"] = "upload"
        reset_http(result)
        sent = await api_json(session, token, "sendDocument", form, result, upload=True,
                              expected=(config, len(buffer), mime))
        if sent is not None and acknowledged(sent, config, len(buffer), mime):
            result.update(status="api_accepted", api_success=True)


@dataclass
class MediaUploadProbe:
    config: CopyConfig | None
    seen: set[int] = field(default_factory=set)
    busy: bool = False
    deadline: float = field(default_factory=lambda: time.monotonic() + 3600)

    async def consume(self, bot: Bot, update: Update, now: float) -> bool:
        config, message = self.config, update.business_message
        if (config is None or bot.id != config.bot_id or message is None or not message.business_connection_id
                or message.chat.type != "private" or message.chat.id != config.chat_id or message.from_user is None
                or message.from_user.is_bot or message.from_user.id != config.owner_id):
            return False
        text = message.text or ""
        if not text.startswith("!media_upload_probe ") or text.removeprefix("!media_upload_probe ") not in CASES:
            return False
        case = text.removeprefix("!media_upload_probe ")
        if (now >= config.expires_at or time.monotonic() >= self.deadline or update.update_id in self.seen
                or len(self.seen) >= MAX_ATTEMPTS):
            return True
        self.seen.add(update.update_id)
        reply = message.reply_to_message
        result = outcome(case, bool(reply and reply.has_protected_content))
        if (reply is None or reply.chat.type != "private" or reply.chat.id != config.chat_id
                or reply.date.timestamp() <= 0 or not reply.photo or reply.video
                or any(getattr(reply, key, None) for key in (
                    "document", "animation", "audio", "voice", "video_note", "sticker", "paid_media", "live_photo"))
                or (reply.business_connection_id is not None
                    and reply.business_connection_id != message.business_connection_id)):
            emit(result)
            return True
        photo = reply.photo[-1]
        if not valid_file_id(photo.file_id):
            emit(result)
            return True
        if self.busy:
            result["status"] = "busy"
            emit(result)
            return True
        self.busy = True
        buffer = bytearray()
        result["status"] = "unavailable"
        try:
            remaining = min(TOTAL_TIMEOUT, config.expires_at - now, self.deadline - time.monotonic())
            async with asyncio.timeout(max(0, remaining)):
                await transfer(bot, config, photo.file_id, photo.file_size, buffer, result)
        except asyncio.CancelledError:
            failure(result, timeout=False)
            emit(result)
            raise
        except TimeoutError:
            failure(result, timeout=True)
        except Exception:
            failure(result, timeout=False)
        finally:
            try:
                buffer.clear()
            except Exception:
                pass
            finally:
                self.busy = False
        emit(result)
        return True


def failure(result: dict[str, Any], *, timeout: bool) -> None:
    if result["api_success"] or result["status"] in {"api_denied", "get_file_denied", "download_denied"}:
        return
    if result["stage"] == "upload" and result["status"] == "unknown_delivery":
        result["status"] = "unknown_delivery"
    else:
        result["status"] = "timeout" if timeout else "unavailable"


_probe: MediaUploadProbe | None = None


async def consume_upload_probe(bot: Bot, update: Update) -> bool:
    global _probe
    now = time.time()
    if _probe is None:
        _probe = MediaUploadProbe(read_config(os.environ, now))
    return await _probe.consume(bot, update, now)
