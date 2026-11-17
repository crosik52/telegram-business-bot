"""Authenticated Telegram webhook with bounded payloads and safe errors."""

from __future__ import annotations

import hmac
import json

from aiogram.types import Update
from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import ValidationError

from app.business.dispatcher import get_bot
from app.config import get_settings
from app.logging_config import get_logger
from app.services.media_copy_probe import consume_copy_probe
from app.services.media_download_probe import consume_download_probe
from app.services.media_reply_probe import consume_probe
from app.services.media_reply_save import prepare_reply_save, save_reply_after_commit
from app.services.media_timer_probe import consume_timer_probe
from app.services.media_upload_probe import consume_upload_probe
from app.services.telegram_inbox_service import InboxFullError, enqueue_update

logger = get_logger(__name__)
router = APIRouter(tags=["webhook"])
MAX_UPDATE_BYTES = 1024 * 1024


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    settings = get_settings()
    try:
        settings.validate_webhook_security()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Webhook unavailable") from None
    if settings.telegram_webhook_secret and not hmac.compare_digest(
        (x_telegram_bot_api_secret_token or "").encode(),
        settings.telegram_webhook_secret.encode(),
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid secret token")

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_UPDATE_BYTES:
            raise HTTPException(status_code=413, detail="Update too large")
        body.extend(chunk)
    try:
        payload = json.loads(body)
        # Validate before touching Telegram or logging untrusted identifiers.
        update = Update.model_validate(payload)
    except (ValueError, ValidationError):
        raise HTTPException(status_code=400, detail="Invalid update") from None

    try:
        bot = get_bot(settings)
        if settings.telegram_webhook_secret and await consume_timer_probe(bot, update):
            return {"ok": True}
        if settings.telegram_webhook_secret and await consume_upload_probe(bot, update):
            return {"ok": True}
        if settings.telegram_webhook_secret and await consume_copy_probe(bot, update):
            return {"ok": True}
        if settings.telegram_webhook_secret and await consume_download_probe(bot, update):
            return {"ok": True}
        if settings.telegram_webhook_secret and consume_probe(bot.id, update, payload):
            return {"ok": True}
        candidate = None
        if settings.telegram_webhook_secret:
            try:
                candidate = prepare_reply_save(bot.id, update)
            except Exception:
                logger.warning("Optional reply save preparation unavailable")
        await enqueue_update(bot.id, candidate.archive_update if candidate is not None else update)
    except InboxFullError:
        raise HTTPException(status_code=503, detail="Update queue full", headers={"Retry-After": "5"}) from None
    except Exception as exc:
        logger.error("Telegram update persistence failed update_id=%s error_type=%s", update.update_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail="Update persistence failed") from None
    if candidate is not None:
        try:
            await save_reply_after_commit(bot, candidate)
        except Exception:
            # The ordinary reply is already committed. Optional copy failures must not replay it.
            logger.warning("Optional reply save handler unavailable after archive commit")
    return {"ok": True}
