"""Verification of Telegram Mini App `initData`.

Telegram signs the data passed to a Web App with an HMAC-SHA256 derived
from the bot token, per the official algorithm:
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

This lets us trust the identity of whoever opened the mini app without any
separate login step — the signature proves Telegram itself vouches for the
`user` payload.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from app.logging_config import get_logger

logger = get_logger(__name__)

_DEFAULT_MAX_AGE_SECONDS = 24 * 60 * 60


def verify_init_data(
    init_data: str, bot_token: str, *, max_age_seconds: int = _DEFAULT_MAX_AGE_SECONDS
) -> dict | None:
    """Validate `initData` and return the embedded Telegram user dict, or None."""

    if not init_data:
        return None

    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        logger.warning("Malformed initData string received")
        return None

    parsed = dict(pairs)
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    computed_hash = hmac.new(
        secret_key, data_check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        logger.warning("initData signature verification failed")
        return None

    auth_date = parsed.get("auth_date")
    if auth_date is not None:
        try:
            if time.time() - int(auth_date) > max_age_seconds:
                logger.warning("initData is stale (auth_date too old)")
                return None
        except ValueError:
            pass

    user_raw = parsed.get("user")
    if not user_raw:
        return None

    try:
        user = json.loads(user_raw)
    except json.JSONDecodeError:
        logger.warning("initData 'user' field was not valid JSON")
        return None

    if not isinstance(user, dict) or "id" not in user:
        return None

    return user
