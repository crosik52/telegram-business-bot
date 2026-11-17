"""Explicit, ephemeral official-API download experiment for consented test replies."""

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
from aiogram.types import Message, Update

from app.logging_config import get_logger

logger = get_logger(__name__)
PREFIX = "MEDIA_DOWNLOAD_PROBE_"
CASES = frozenset({"ordinary_photo", "ordinary_video", "once_photo", "once_video"})
MAX_BYTES = 8 * 1024 * 1024
CHUNK_BYTES = 64 * 1024
MAX_ATTEMPTS = 8
MAX_API_BYTES = 16 * 1024
TOTAL_TIMEOUT = 20
API_TIMEOUT = 5
STATUSES = frozenset({
    "get_file_denied", "timeout", "too_large", "bad_path", "download_denied", "empty",
    "format_mismatch", "complete", "busy", "unavailable",
})


@dataclass(frozen=True)
class DownloadConfig:
    bot_id: int
    owner_id: int
    chat_id: int
    expires_at: int


def read_config(env: Mapping[str, str], now: float) -> DownloadConfig | None:
    if env.get(PREFIX + "ENABLED") != "1":
        return None
    try:
        values = [env[PREFIX + key] for key in ("BOT_ID", "OWNER_ID", "CHAT_ID", "EXPIRES_AT")]
        if not all(re.fullmatch(r"-?[1-9][0-9]{0,15}", value) for value in values):
            return None
        bot_id, owner_id, chat_id, expires_at = map(int, values)
        if not (0 < bot_id < 2**52 and 0 < owner_id < 2**52 and 0 < abs(chat_id) < 2**52):
            return None
        if not now < expires_at <= now + 3600:
            return None
        return DownloadConfig(bot_id, owner_id, chat_id, expires_at)
    except (KeyError, ValueError):
        return None


def valid_path(value: Any) -> bool:
    return isinstance(value, str) and len(value) <= 512 and bool(re.fullmatch(
        r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?(?:/[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?)*", value,
    ))


def expected_format(prefix: bytes, case: str, count: int) -> bool:
    if case.endswith("_photo"):
        return prefix.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n"))
    return (len(prefix) >= 12 and prefix[4:8] == b"ftyp"
            and 12 <= int.from_bytes(prefix[:4], "big") <= count)


def outcome(case: str, protected: bool) -> dict[str, Any]:
    return {"case": case, "protected": protected, "status": "unavailable", "get_file_ok": False,
            "download_complete": False, "expected_format_match": False, "bytes_received": 0}


def emit(result: dict[str, Any]) -> None:
    # Values originate only from the constant schema; no untrusted strings copied.
    if result["status"] != "complete":
        result["download_complete"] = False
    try:
        logger.info("MEDIA_DOWNLOAD_PROBE %s", json.dumps(result, separators=(",", ":")))
    except Exception:
        pass


def reply_file(reply: Message | None, case: str) -> tuple[str | None, int | None]:
    if reply is None:
        return None, None
    if case.endswith("_photo") and reply.photo and not reply.video:
        largest = reply.photo[-1]
        return largest.file_id or None, largest.file_size
    if case.endswith("_video") and reply.video and not reply.photo:
        return reply.video.file_id or None, reply.video.file_size
    return None, None


async def get_file_info(session: aiohttp.ClientSession, token: str, file_id: str) -> dict | None:
    # POST keeps file_id out of URLs; redirects are forbidden for BOTH API and file requests.
    async with session.post(
        "https://api.telegram.org/bot" + token + "/getFile", data={"file_id": file_id},
        allow_redirects=False, timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
    ) as response:
        if response.status != 200:
            return None
        body = bytearray()
        async for chunk in response.content.iter_chunked(4096):
            if len(body) + len(chunk) > MAX_API_BYTES:
                return None
            body.extend(chunk)
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            return None
        if (not isinstance(payload, dict) or payload.get("ok") is not True
                or not isinstance(payload.get("result"), dict)):
            return None
        return payload["result"]


async def stream_file(
    session: aiohttp.ClientSession, url: str, case: str, sizes: list[int], result: dict[str, Any],
) -> None:
    async with session.get(url, allow_redirects=False) as response:
        if (response.status != 200
                or response.headers.get("Content-Range") is not None
                or response.headers.get("Content-Encoding", "identity").lower() != "identity"):
            result["status"] = "download_denied"
            return
        length = response.headers.get("Content-Length")
        if length is not None:
            if not re.fullmatch(r"[0-9]{1,12}", length):
                result["status"] = "download_denied"
                return
            sizes.append(int(length))
        if any(size > MAX_BYTES for size in sizes):
            result["status"] = "too_large"
            return
        prefix = b""
        count = 0
        async for chunk in response.content.iter_chunked(CHUNK_BYTES):
            count += len(chunk)
            result["bytes_received"] = min(count, MAX_BYTES)
            if count > MAX_BYTES or len(chunk) > CHUNK_BYTES:
                result["status"] = "too_large"
                return
            prefix += chunk[:64 - len(prefix)]
        if count == 0:
            result["status"] = "empty"
            return
        result["expected_format_match"] = expected_format(prefix, case, count)
        if not result["expected_format_match"]:
            result["status"] = "format_mismatch"
        elif not sizes or any(size != count for size in sizes):
            result["status"] = "unavailable"
        else:
            result.update(status="complete", download_complete=True)


async def download(bot: Bot, file_id: str, declared_size: int | None, result: dict[str, Any]) -> None:
    token = bot.token
    if (not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", file_id)
            or not isinstance(token, str) or not re.fullmatch(r"[1-9][0-9]{0,15}:[A-Za-z0-9_-]{30,128}", token)
            or token.split(":", 1)[0] != str(bot.id)):
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
        info = await get_file_info(session, token, file_id)
        if info is None:
            result["status"] = "get_file_denied"
            return
        result["get_file_ok"] = True
        if not valid_path(info.get("file_path")):
            result["status"] = "bad_path"
            return
        file_size = info.get("file_size")
        if file_size is not None:
            if type(file_size) is not int or file_size < 0:
                return
            sizes.append(file_size)
        if any(size > MAX_BYTES for size in sizes):
            result["status"] = "too_large"
            return
        url = "https://api.telegram.org/file/bot" + token + "/" + info["file_path"]
        await stream_file(session, url, result["case"], sizes, result)


@dataclass
class MediaDownloadProbe:
    config: DownloadConfig | None
    seen: set[int] = field(default_factory=set)
    busy: bool = False
    deadline: float = field(default_factory=lambda: time.monotonic() + 3600)

    async def consume(self, bot: Bot, update: Update, now: float) -> bool:
        config = self.config
        message = update.business_message
        if (config is None or bot.id != config.bot_id or message is None or not message.business_connection_id
                or message.from_user is None or message.from_user.id != config.owner_id
                or message.chat.id != config.chat_id):
            return False
        text = message.text or ""
        if not text.startswith("!media_download_probe "):
            return False
        case = text.removeprefix("!media_download_probe ")
        if case not in CASES:
            return False
        if now >= config.expires_at or time.monotonic() >= self.deadline:
            return True
        if update.update_id in self.seen or len(self.seen) >= MAX_ATTEMPTS:
            return True
        self.seen.add(update.update_id)
        reply = message.reply_to_message
        result = outcome(case, bool(reply and reply.has_protected_content))
        if self.busy:
            result["status"] = "busy"
            emit(result)
            return True
        self.busy = True
        try:
            file_id, declared_size = reply_file(reply, case)
            if file_id:
                remaining = min(TOTAL_TIMEOUT, config.expires_at - now, self.deadline - time.monotonic())
                async with asyncio.timeout(max(0, remaining)):
                    await download(bot, file_id, declared_size, result)
        except TimeoutError:
            result["status"] = "timeout"
        except asyncio.CancelledError:
            # Cancellation consumes this attempt; never enqueue its private reply on retry.
            result["status"] = "unavailable"
            emit(result)
            raise
        except Exception:
            result["status"] = "unavailable"
        finally:
            self.busy = False
        emit(result)
        return True


_probe: MediaDownloadProbe | None = None


async def consume_download_probe(bot: Bot, update: Update) -> bool:
    global _probe
    now = time.time()
    if _probe is None:
        _probe = MediaDownloadProbe(read_config(os.environ, now))
    return await _probe.consume(bot, update, now)
