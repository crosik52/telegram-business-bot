"""Audio streaming endpoint for inline bot results.

GET /audio/{key}  — streams an audio track by its cache key.
Telegram downloads this URL when the user picks an inline result,
so the audio lands directly in the chat (no DM needed).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.services import audio_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["audio"])


@router.get("/audio/{key}")
async def stream_audio(key: str) -> Response:
    cached = audio_service.get(key)
    if cached is None:
        raise HTTPException(status_code=404, detail="Track not found or session expired")

    try:
        audio_bytes, filename = await audio_service.stream_to_bytes(cached.url)
    except Exception as exc:
        logger.warning("audio endpoint: stream failed key=%s: %s", key, exc)
        raise HTTPException(status_code=502, detail="Failed to fetch audio") from exc

    ext = filename.rsplit(".", 1)[-1].lower()
    content_type = "audio/mpeg" if ext == "mp3" else "audio/mp4"

    logger.info("audio endpoint: served key=%s title=%r size=%dKB",
                key, cached.title, len(audio_bytes) // 1024)
    return Response(
        content=audio_bytes,
        media_type=content_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
