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
from collections import OrderedDict

logger = logging.getLogger(__name__)

# Populated by connect() / closed by disconnect()
_client = None
_client_lock = asyncio.Lock()
_update_task: asyncio.Task | None = None

# Errors that mean "entity not in cache yet" — trigger a dialog refresh + retry
_ENTITY_ERRORS = ("Could not find the input entity", "PeerUser", "PeerChat", "PeerChannel")

# ── View-once proactive cache ─────────────────────────────────────────────────
# Keyed by (chat_id, message_id) → (media_type_str, bytes).
# Populated by the NewMessage event handler BEFORE the user opens the media.
# Consumed (popped) by dot-save in handlers.py.
_view_once_cache: OrderedDict[tuple[int, int], tuple[str, bytes]] = OrderedDict()
_VIEW_ONCE_CACHE_MAX = 200  # keep at most 200 entries in memory


def _media_type_from_msg(msg) -> str:
    """Detect media type string from a Telethon message object."""
    try:
        from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
        media = msg.media
        if isinstance(media, MessageMediaPhoto):
            return "photo"
        if isinstance(media, MessageMediaDocument):
            doc = media.document
            attr_names = {type(a).__name__ for a in doc.attributes}
            attr_map = {type(a).__name__: a for a in doc.attributes}
            if "DocumentAttributeVideo" in attr_names:
                if getattr(attr_map["DocumentAttributeVideo"], "round_message", False):
                    return "video_note"
                return "video"
            if "DocumentAttributeVoice" in attr_names:
                return "voice"
            if "DocumentAttributeAudio" in attr_names:
                return "audio"
    except Exception:
        pass
    return "document"


async def _view_once_event_handler(event) -> None:
    """Telethon NewMessage handler — proactively captures view-once media.

    Fires for every incoming message on the owner's account via MTProto.
    If the message has ttl_seconds > 0 (view-once / self-destructing), we
    download the bytes immediately — before the user opens it — and stash
    them in _view_once_cache keyed by (chat_id, message_id).

    dot-save in handlers.py calls pop_view_once_bytes() to retrieve them.
    """
    global _view_once_cache
    msg = event.message
    if not msg or not msg.media:
        return
    if not getattr(msg.media, "ttl_seconds", 0):
        return  # not view-once

    chat_id = event.chat_id
    message_id = msg.id
    media_type = _media_type_from_msg(msg)

    try:
        buf = io.BytesIO()
        result = await event.client.download_media(msg, file=buf)
        if result is None:
            logger.warning(
                "Telethon view-once listener: download returned None "
                "chat=%s msg=%s", chat_id, message_id,
            )
            return
        data = buf.getvalue()
        _view_once_cache[(chat_id, message_id)] = (media_type, data)
        # Evict oldest entry if cache is full
        while len(_view_once_cache) > _VIEW_ONCE_CACHE_MAX:
            _view_once_cache.popitem(last=False)
        logger.info(
            "Telethon: ✓ captured view-once %s %d B (chat=%s msg=%s)",
            media_type, len(data), chat_id, message_id,
        )
    except Exception as exc:
        logger.warning(
            "Telethon: view-once capture failed chat=%s msg=%s: %s",
            chat_id, message_id, exc,
        )


def pop_view_once_bytes(chat_id: int, message_id: int) -> tuple[str, bytes] | None:
    """Return and remove cached view-once bytes, or None if not found."""
    return _view_once_cache.pop((chat_id, message_id), None)


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
    """Connect and authorise the Telethon client.  Called once at startup.

    On AuthKeyDuplicatedError (Railway rolling deploy overlap) we retry up to 3
    times with increasing delays — the old container's disconnect() usually
    completes within 10–20 s.
    """
    global _client, _update_task
    from telethon import TelegramClient
    from telethon.errors import AuthKeyDuplicatedError
    from telethon.sessions import StringSession

    async with _client_lock:
        if _client is not None:
            return

        _retry_delays = [10, 20, 30]  # seconds between retries
        for attempt, delay in enumerate([0] + _retry_delays):
            if delay:
                logger.info(
                    "Telethon: waiting %ds before retry %d/3 (session conflict)",
                    delay, attempt,
                )
                await asyncio.sleep(delay)

            client = TelegramClient(StringSession(session_str), api_id, api_hash)
            try:
                await client.connect()
                break  # success
            except AuthKeyDuplicatedError as exc:
                await client.disconnect()
                if attempt < len(_retry_delays):
                    logger.warning(
                        "Telethon: AuthKeyDuplicatedError on attempt %d — "
                        "old container still running, will retry",
                        attempt + 1,
                    )
                    continue
                logger.error(
                    "Telethon: AuthKeyDuplicatedError — session permanently revoked "
                    "by Telegram after %d attempts. "
                    "Regenerate TELETHON_SESSION_STR via /session-gen. "
                    "To avoid this: set Railway deployment strategy to 'Recreate' "
                    "(stop old container before starting new one).",
                    attempt + 1,
                )
                return
            except Exception as exc:
                await client.disconnect()
                logger.error(
                    "Telethon: connect() failed (%s). "
                    "If AuthKeyDuplicatedError — use TELETHON_ENABLED=false on dev.",
                    exc,
                )
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

        # Register proactive view-once capture handler BEFORE run_until_disconnected
        # so it fires for every incoming message with ttl_seconds > 0.
        from telethon import events
        client.add_event_handler(
            _view_once_event_handler,
            events.NewMessage(incoming=True),
        )
        logger.info("Telethon: view-once listener registered")

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
    """Call get_messages with progressive fallbacks for entity-cache misses.

    Strategy:
      1. Normal call (uses session entity cache).
      2. InputPeerUser(id, 0) — bypasses Telethon's client-side cache check;
         Telegram server accepts access_hash=0 for the account owner's own chats.
      3. Wait 3 s so the background MTProto update loop can cache the peer,
         then refresh dialogs and retry.
    """
    from telethon.tl.types import InputPeerUser

    client = _client
    if client is None:
        return None

    # Attempt 1 — standard (entity in session cache)
    try:
        return await client.get_messages(chat_id, ids=message_id)
    except Exception as exc:
        if not _is_entity_error(exc):
            raise
        logger.info("Telethon: entity cache miss chat=%s — trying InputPeerUser(id,0)", chat_id)

    # Attempt 2 — bypass client-side entity resolution with access_hash=0
    try:
        peer = InputPeerUser(user_id=chat_id, access_hash=0)
        return await client.get_messages(peer, ids=message_id)
    except Exception as exc:
        if not _is_entity_error(exc) and "PEER_ID_INVALID" not in str(exc):
            raise
        logger.info(
            "Telethon: InputPeerUser(0) rejected chat=%s — waiting 3s for MTProto sync",
            chat_id,
        )

    # Attempt 3 — give the MTProto update loop time, refresh dialogs, final try
    await asyncio.sleep(3)
    await _refresh_entity_cache(client)
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
