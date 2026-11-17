"""Bounded short-lived capability URLs for Telegram inline audio fetches."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from starlette.types import Receive, Scope, Send

from app.services import audio_service
from app.services.download_service import DownloadError

router = APIRouter(tags=["audio"])
MAX_ACTIVE = 2
MAX_REQUESTS_PER_MINUTE = 120
_active = 0
_window = 0.0
_requests = 0


class LeasedAudioResponse(Response):
    """Retain admission until ASGI completes or cancels body transmission."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        global _active
        try:
            await super().__call__(scope, receive, send)
        finally:
            _active -= 1


@router.get("/audio/{key}")
async def stream_audio(key: str) -> Response:
    global _active, _window, _requests
    now = time.monotonic()
    if now - _window >= 60:
        _window, _requests = now, 0
    if _requests >= MAX_REQUESTS_PER_MINUTE:
        raise HTTPException(429, "Audio request limit reached", headers={"Retry-After": "60"})
    _requests += 1
    cached = audio_service.get(key)
    if cached is None:
        raise HTTPException(404, "Track not found or session expired")
    if _active >= MAX_ACTIVE:
        raise HTTPException(429, "Audio service busy", headers={"Retry-After": "5"})
    _active += 1
    transferred = False
    try:
        preloaded = audio_service.get_predownloaded(key)
        if preloaded is None:
            preloaded = await audio_service.stream_to_bytes(cached.url)
        data, _filename = preloaded
        if not 0 < len(data) <= audio_service.MAX_BYTES:
            raise DownloadError("audio_too_large")
        response = LeasedAudioResponse(
            data,
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": 'inline; filename="audio.mp3"',
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
        transferred = True
        return response
    except DownloadError:
        raise HTTPException(503, "Audio is unavailable", headers={"Retry-After": "5"}) from None
    except Exception:
        raise HTTPException(502, "Failed to fetch audio") from None
    finally:
        if not transferred:
            _active -= 1
