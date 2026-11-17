"""Explicit own-neutral timed-media experiment; bounded official API and protected document output."""
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
from app.services import media_upload_probe as shared
from app.services.media_copy_probe import CopyConfig, valid_file_id
from app.services.media_download_probe import valid_path

logger = get_logger(__name__)
PREFIX = "MEDIA_TIMER_PROBE_"
CASES = frozenset({"timer_photo", "timer_video"})
MAX_BYTES, CHUNK_BYTES = shared.MAX_BYTES, shared.CHUNK_BYTES
MAX_API_BYTES = shared.MAX_API_BYTES
MAX_ATTEMPTS, TOTAL_TIMEOUT = shared.MAX_ATTEMPTS, shared.TOTAL_TIMEOUT
STAGES, STATUSES = shared.STAGES, shared.STATUSES
CAPTION = "Save Log: synthetic timer media document test."
MP4_BRANDS = frozenset({b"isom", b"iso2", b"iso3", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"avc1", b"M4V "})


def read_config(env: Mapping[str, str], now: float) -> CopyConfig | None:
    keys = ("ENABLED", "BOT_ID", "OWNER_ID", "CHAT_ID", "EXPIRES_AT")
    return shared.read_config({shared.PREFIX + key: env.get(PREFIX + key, "") for key in keys}, now)


def emit(result: dict[str, Any]) -> None:
    try:
        logger.info("MEDIA_TIMER_PROBE %s", json.dumps(result, separators=(",", ":")))
    except Exception:
        pass


def media_format(buffer: bytearray, case: str) -> tuple[str, str] | None:
    if case == "timer_photo":
        if buffer.startswith(b"\xff\xd8\xff"):
            return "image/jpeg", "test-photo.jpg"
        if buffer.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png", "test-photo.png"
        return None
    if case == "timer_video" and len(buffer) >= 16 and buffer[4:8] == b"ftyp":
        box_size = int.from_bytes(buffer[:4], "big")
        if 16 <= box_size <= len(buffer) and box_size % 4 == 0 and bytes(buffer[8:12]) in MP4_BRANDS:
            return "video/mp4", "test-video.mp4"
    return None


async def read_media(session: aiohttp.ClientSession, token: str, path: str, sizes: list[int],
                     buffer: bytearray, case: str, result: dict[str, Any]) -> tuple[str, str] | None:
    result["stage"] = "download"
    shared.reset_http(result)
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
        detected = media_format(buffer, case)
        if detected is None:
            result["status"] = "format_mismatch"
            return None
        mime, filename = detected
        result["expected_format_match"] = True
        if not sizes or not any(size > 0 for size in sizes) or any(size != len(buffer) for size in sizes):
            result["status"] = "size_mismatch"
            return None
    # EOF, all sizes, signature, and response cleanup must finish before upload.
    result["download_complete"] = True
    return mime, filename



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
        info = await shared.api_json(session, token, "getFile", {"file_id": file_id}, result)
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
        detected = await read_media(session, token, path, sizes, buffer, result["case"], result)
        if detected is None:
            return
        mime, filename = detected
        form = aiohttp.FormData()
        for key, value in {"chat_id": str(config.owner_id), "document": "attach://test_media",
                           "caption": CAPTION, "protect_content": "true", "disable_notification": "true",
                           "allow_paid_broadcast": "false", "disable_content_type_detection": "true"}.items():
            form.add_field(key, value)
        form.add_field("test_media", bytes(buffer), filename=filename,
                       content_type=mime)
        result["stage"] = "upload"
        shared.reset_http(result)
        sent = await shared.api_json(session, token, "sendDocument", form, result, upload=True,
                              expected=(config, len(buffer), mime))
        if sent is not None and shared.acknowledged(sent, config, len(buffer), mime):
            result.update(status="api_accepted", api_success=True)


@dataclass
class MediaTimerProbe:
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
        if not text.startswith("!media_timer_probe ") or text.removeprefix("!media_timer_probe ") not in CASES:
            return False
        case = text.removeprefix("!media_timer_probe ")
        if (now >= config.expires_at or time.monotonic() >= self.deadline or update.update_id in self.seen
                or len(self.seen) >= MAX_ATTEMPTS):
            return True
        self.seen.add(update.update_id)
        reply = message.reply_to_message
        result = shared.outcome(case, bool(reply and reply.has_protected_content))
        if (reply is None or reply.chat.type != "private" or reply.chat.id != config.chat_id
                or reply.date.timestamp() <= 0
                or (case == "timer_photo" and (not reply.photo or reply.video))
                or (case == "timer_video" and (not reply.video or reply.photo))
                or any(getattr(reply, key, None) for key in (
                    "document", "animation", "audio", "voice", "video_note", "sticker", "paid_media", "live_photo"))
                or (reply.business_connection_id is not None
                    and reply.business_connection_id != message.business_connection_id)):
            emit(result)
            return True
        media = reply.photo[-1] if case == "timer_photo" and reply.photo else reply.video
        if media is None or not valid_file_id(media.file_id):
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
                await transfer(bot, config, media.file_id, media.file_size, buffer, result)
        except asyncio.CancelledError:
            shared.failure(result, timeout=False)
            emit(result)
            raise
        except TimeoutError:
            shared.failure(result, timeout=True)
        except Exception:
            shared.failure(result, timeout=False)
        finally:
            try:
                buffer.clear()
            except Exception:
                pass
            finally:
                self.busy = False
        emit(result)
        return True


_probe: MediaTimerProbe | None = None


async def consume_timer_probe(bot: Bot, update: Update) -> bool:
    global _probe
    now = time.time()
    if _probe is None:
        _probe = MediaTimerProbe(read_config(os.environ, now))
    return await _probe.consume(bot, update, now)
