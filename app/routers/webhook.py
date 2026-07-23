"""Telegram webhook endpoint.

Receives updates from Telegram via HTTPS webhook (polling is never used),
verifies the optional secret token, and feeds the update into aiogram's
dispatcher for processing.
"""

from __future__ import annotations

from aiogram.types import Update
from fastapi import APIRouter, Header, HTTPException, Request, status

from app.business.dispatcher import get_bot, get_dispatcher
from app.config import get_settings
from app.logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["webhook"])


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    settings = get_settings()

    if settings.telegram_webhook_secret:
        if x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
            logger.warning("Rejected webhook request with invalid secret token")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid secret token"
            )

    payload = await request.json()
    update_id = payload.get("update_id")
    update_type = next(
        (k for k in payload if k not in ("update_id",)), "unknown"
    )
    logger.info("Received Telegram update update_id=%s type=%s", update_id, update_type)

    # ── Raw logging for media messages (helps diagnose view-once delivery) ──
    _bm = payload.get("business_message") or payload.get("edited_business_message")
    if _bm:
        _has_photo = bool(_bm.get("photo"))
        _has_video = bool(_bm.get("video"))
        _has_doc   = bool(_bm.get("document"))
        _has_voice = bool(_bm.get("voice"))
        _has_vn    = bool(_bm.get("video_note"))
        _text      = bool(_bm.get("text"))
        _spoiler   = _bm.get("has_media_spoiler")
        _ttl       = (
            (_bm.get("photo") or [{}])[-1].get("file_id", "")
            if _has_photo else ""
        )
        _keys = list(_bm.keys())
        logger.info(
            "RAW business_message chat=%s msg=%s | photo=%s video=%s doc=%s "
            "voice=%s vn=%s text=%s spoiler=%s | keys=%s",
            _bm.get("chat", {}).get("id"),
            _bm.get("message_id"),
            _has_photo, _has_video, _has_doc,
            _has_voice, _has_vn, _text, _spoiler,
            _keys,
        )

    bot = get_bot(settings)
    dispatcher = get_dispatcher()

    try:
        update = Update.model_validate(payload, context={"bot": bot})
        await dispatcher.feed_update(bot, update)
    except Exception:
        logger.exception("Error while processing Telegram update payload=%s", payload)
        # Always return 200 to Telegram so it doesn't endlessly retry a
        # payload we can't process, but the error is fully logged above.
        return {"ok": False}

    return {"ok": True}
