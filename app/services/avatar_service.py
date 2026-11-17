"""Bounded, server-side Telegram avatar download; no secret-bearing redirects."""

from __future__ import annotations

import asyncio
import time
from io import BytesIO

from fastapi import HTTPException

MAX_AVATAR_BYTES = 2 * 1024 * 1024
_download_slots = asyncio.Semaphore(4)
_window_start = 0.0
_requests = 0


class _AvatarBuffer(BytesIO):
    def write(self, data: bytes) -> int:
        if self.tell() + len(data) > MAX_AVATAR_BYTES:
            raise ValueError("Avatar exceeds size limit")
        return super().write(data)


def admit_avatar_request() -> None:
    # A bounded process-wide budget; no unbounded IP/user dictionary.
    global _window_start, _requests
    now = time.monotonic()
    if now - _window_start >= 60:
        _window_start, _requests = now, 0
    if _requests >= 120:
        raise HTTPException(429, "Avatar request limit reached", headers={"Retry-After": "60"})
    _requests += 1


async def download_avatar(bot, file_id: str) -> bytes:
    if _download_slots.locked():
        raise HTTPException(429, "Avatar service busy", headers={"Retry-After": "2"})
    async with _download_slots:
        async with asyncio.timeout(10):
            tg_file = await bot.get_file(file_id)
            if not tg_file.file_path or (tg_file.file_size or 0) > MAX_AVATAR_BYTES:
                raise ValueError("Avatar unavailable")
            with _AvatarBuffer() as buffer:
                await bot.download_file(tg_file.file_path, destination=buffer, timeout=8)
                data = buffer.getvalue()
                if not data.startswith(b"\xff\xd8\xff"):
                    raise ValueError("Unexpected avatar format")
                return data
