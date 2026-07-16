"""Telethon user-client for downloading disappearing / view-once media.

The Telegram Bot API cannot download view-once (ttl_period / has_media_spoiler)
media — it returns an error because Telegram blocks getFile for such files.
A Telethon *user* client connected to the bot-owner's personal account can
fetch and download the same message via MTProto before the file expires.

Setup
-----
1. Obtain API credentials at https://my.telegram.org → "API development tools".
2. Run  ``python scripts/generate_telethon_session.py``  once to produce a
   StringSession string (logs you in interactively).
3. Store the three values as environment / Railway variables:
       TELEGRAM_API_ID      = 12345678
       TELEGRAM_API_HASH    = abcdef0123456789abcdef0123456789
       TELETHON_SESSION_STR = 1Abc...  (long base64-like string)

The service starts lazily: if those vars are absent the module is a no-op.
"""

from __future__ import annotations

import asyncio
import io
import logging

logger = logging.getLogger(__name__)

# Populated by connect() / closed by disconnect()
_client = None
_client_lock = asyncio.Lock()


async def connect(api_id: int, api_hash: str, session_str: str) -> None:
    """Connect and authorise the Telethon client.  Called once at startup."""
    global _client
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    async with _client_lock:
        if _client is not None:
            return
        client = TelegramClient(StringSession(session_str), api_id, api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            logger.error(
                "Telethon: session string is present but NOT authorised. "
                "Re-run scripts/generate_telethon_session.py and update "
                "TELETHON_SESSION_STR."
            )
            await client.disconnect()
            return
        _client = client
        me = await client.get_me()
        logger.info(
            "Telethon user-client connected as %s (id=%s)",
            getattr(me, "username", None) or getattr(me, "first_name", "?"),
            getattr(me, "id", "?"),
        )


async def disconnect() -> None:
    """Disconnect the Telethon client.  Called at shutdown."""
    global _client
    async with _client_lock:
        if _client is not None:
            await _client.disconnect()
            _client = None
            logger.info("Telethon user-client disconnected")


def is_available() -> bool:
    """Return True when the client is connected and ready."""
    return _client is not None


async def download_message_media(chat_id: int, message_id: int) -> bytes | None:
    """Download media from *message_id* in *chat_id* via the user session.

    Returns raw bytes on success, None on any failure (not configured,
    message already expired, permission error, etc.).

    The caller is responsible for forwarding / caching the bytes.
    """
    if _client is None:
        return None
    try:
        # get_messages returns a single Message or None
        msg = await _client.get_messages(chat_id, ids=message_id)
        if msg is None or not msg.media:
            logger.debug(
                "Telethon: no media found for chat=%s msg=%s", chat_id, message_id
            )
            return None

        buf = io.BytesIO()
        result = await _client.download_media(msg, file=buf)
        if result is None:
            logger.warning(
                "Telethon: download_media returned None for chat=%s msg=%s",
                chat_id, message_id,
            )
            return None

        data = buf.getvalue()
        logger.info(
            "Telethon: downloaded %d B from chat=%s msg=%s",
            len(data), chat_id, message_id,
        )
        return data

    except Exception as exc:
        logger.warning(
            "Telethon: failed to download chat=%s msg=%s: %s",
            chat_id, message_id, exc,
        )
        return None
