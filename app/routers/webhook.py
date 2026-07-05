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
    logger.info(
        "Received Telegram update update_id=%s", payload.get("update_id")
    )

    bot = get_bot(settings)
    dispatcher = get_dispatcher()

    try:
        update = Update.model_validate(payload, context={"bot": bot})
        await dispatcher.feed_update(bot, update)
    except Exception:
        logger.exception("Error while processing Telegram update")
        # Always return 200 to Telegram so it doesn't endlessly retry a
        # payload we can't process, but the error is fully logged above.
        return {"ok": False}

    return {"ok": True}
