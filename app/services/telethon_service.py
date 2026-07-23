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

Entity cache
------------
Telethon resolves peers (user_id → access_hash) from its session cache.
A fresh session has an empty cache, causing "Could not find the input entity"
errors for users the account has never interacted with via MTProto.

We fix this in two complementary ways:
  1. At startup: call get_dialogs() to pre-populate the cache with recent contacts.
  2. Background task: run_until_disconnected() so Telethon processes live MTProto
     updates and caches every new peer it sees in real-time.
  3. Fallback: if a download still fails with an entity error, refresh dialogs
     and retry once before giving up.
"""

from __future__ import annotations

import asyncio
import io
import logging

logger = logging.getLogger(__name__)

# Populated by connect() / closed by disconnect()
_client = None
_client_lock = asyncio.Lock()
_update_task: asyncio.Task | None = None

# Errors that mean "entity not in cache yet" — trigger a dialog refresh + retry
_ENTITY_ERRORS = ("Could not find the input entity", "PeerUser", "PeerChat", "PeerChannel")


def _is_entity_error(exc: Exception) -> bool:
    msg = str(exc)
    return any(tag in msg for tag in _ENTITY_ERRORS)


async def _run_updates(client) -> None:
    """Background task: keep Telethon processing MTProto updates.

    This is what populates the entity cache automatically as new users
    send messages to the account owner.
    """
    try:
        await client.run_until_disconnected()
    except Exception as exc:
        logger.warning("Telethon update loop exited: %s", exc)


async def _refresh_entity_cache(client) -> None:
    """Fetch recent dialogs to populate the entity cache."""
    try:
        count = 0
        async for _ in client.iter_dialogs(limit=500):
            count += 1
        logger.info("Telethon: entity cache warmed up (%d dialogs)", count)
    except Exception as exc:
        logger.warning("Telethon: dialog prefetch failed: %s", exc)


async def connect(api_id: int, api_hash: str, session_str: str) -> None:
    """Connect and authorise the Telethon client.  Called once at startup."""
    global _client, _update_task
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    async with _client_lock:
        if _client is not None:
            return
        client = TelegramClient(StringSession(session_str), api_id, api_hash)
        try:
            await client.connect()
        except Exception as exc:
            logger.error(
                "Telethon: connect() failed (%s). "
                "If AuthKeyDuplicatedError — the same session is already running "
                "on another server. Use TELETHON_ENABLED=false to disable Telethon "
                "on this instance.",
                exc,
            )
            await client.disconnect()
            return
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

        # Warm up entity cache with recent dialogs so known contacts resolve immediately
        await _refresh_entity_cache(client)

        # Keep processing MTProto updates in the background so new peers are
        # cached as soon as they appear (no entity errors for first-time senders)
        _update_task = asyncio.create_task(_run_updates(client))
        _update_task.add_done_callback(
            lambda t: logger.info("Telethon update task finished")
        )


async def disconnect() -> None:
    """Disconnect the Telethon client.  Called at shutdown."""
    global _client, _update_task
    async with _client_lock:
        if _update_task is not None:
            _update_task.cancel()
            try:
                await _update_task
            except asyncio.CancelledError:
                pass
            _update_task = None
        if _client is not None:
            await _client.disconnect()
            _client = None
            logger.info("Telethon user-client disconnected")


def is_available() -> bool:
    """Return True when the client is connected and ready."""
    return _client is not None


async def _get_messages_with_retry(chat_id: int, message_id: int):
    """Call get_messages; on entity-cache miss, refresh dialogs and retry once."""
    client = _client
    if client is None:
        return None
    try:
        return await client.get_messages(chat_id, ids=message_id)
    except Exception as exc:
        if not _is_entity_error(exc):
            raise
        logger.info(
            "Telethon: entity cache miss for chat=%s — refreshing dialogs and retrying",
            chat_id,
        )
        await _refresh_entity_cache(client)
        # Retry after cache refresh
        return await client.get_messages(chat_id, ids=message_id)


async def is_view_once(chat_id: int, message_id: int) -> bool:
    """Return True if the message contains view-once (ttl_seconds > 0) media."""
    if _client is None:
        return False
    try:
        msg = await _get_messages_with_retry(chat_id, message_id)
        if msg is None or not msg.media:
            return False
        return bool(getattr(msg.media, "ttl_seconds", 0))
    except Exception:
        return False


async def download_view_once_only(chat_id: int, message_id: int) -> bytes | None:
    """Download media bytes ONLY if the message is view-once (ttl_seconds > 0).

    Returns None for regular media so the caller doesn't waste DB space.
    """
    if _client is None:
        return None
    try:
        msg = await _get_messages_with_retry(chat_id, message_id)
        if msg is None or not msg.media:
            return None
        if not getattr(msg.media, "ttl_seconds", 0):
            logger.debug(
                "Telethon: skipping non-view-once media chat=%s msg=%s", chat_id, message_id
            )
            return None
        buf = io.BytesIO()
        result = await _client.download_media(msg, file=buf)
        if result is None:
            return None
        data = buf.getvalue()
        logger.info(
            "Telethon: downloaded view-once %d B from chat=%s msg=%s",
            len(data), chat_id, message_id,
        )
        return data
    except Exception as exc:
        logger.warning(
            "Telethon: failed to download view-once chat=%s msg=%s: %s",
            chat_id, message_id, exc,
        )
        return None


async def download_message_media(chat_id: int, message_id: int) -> bytes | None:
    """Download media from *message_id* in *chat_id* via the user session.

    Returns raw bytes on success, None on any failure (not configured,
    message already expired, permission error, etc.).

    The caller is responsible for forwarding / caching the bytes.
    """
    if _client is None:
        return None
    try:
        msg = await _get_messages_with_retry(chat_id, message_id)
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
