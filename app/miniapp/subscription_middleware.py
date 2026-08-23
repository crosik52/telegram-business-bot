"""Starlette middleware that enforces the channel-subscription gate for every
mini-app API endpoint.

Why middleware instead of per-endpoint Depends():
- A single registration automatically covers all current *and future* routes.
- It prevents accidental omissions when new endpoints are added.
- The per-route _assert_subscribed calls remain as a defence-in-depth layer.

Gate behaviour
--------------
For every ``/app/api/*`` request (GET or POST) that is *not* explicitly
excluded, the middleware:

1. Extracts the Telegram ``initData`` from the request (body JSON or query
   param).
2. Verifies the signature and derives the user-id.
3. Checks channel membership via the subscription-cache / Telegram API.
4. **Returns 403** when the user isn't subscribed.
5. **Returns 503** (fail-closed) when *required channels are configured* but
   the membership check throws an exception.  An unreachable Telegram API
   must never silently grant access.
6. Passes the request through when:
   - No required channels are configured.
   - The user is confirmed subscribed.
   - init_data is absent or unparseable (the route's own auth will reject it).
"""

from __future__ import annotations

import json as _json
import logging
import time as _time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# ── Paths that bypass the gate ────────────────────────────────────────────────

# The stats endpoint is the gate-discovery response: it tells the frontend
# WHICH channels are required (and is the mechanism that triggers the overlay).
# Admin routes handle their own auth entirely.
_EXCLUDED_EXACT: frozenset[str] = frozenset(
    {
        "/app/api/stats",  # gate-discovery; returns subscription_gate payload
    }
)

_EXCLUDED_PREFIXES: tuple[str, ...] = (
    "/app/api/admin/",    # admin endpoints have their own auth layer
    "/app/api/payments",  # Telegram Stars webhook callbacks (no user init_data)
    "/app/api/avatar/",   # public image proxy; no Telegram init_data present
)

# ── Tri-state check result ─────────────────────────────────────────────────────
# True  → subscribed (or no gate configured)
# False → not subscribed
# None  → check failed while channels are configured → fail closed
_SUBSCRIBED = True
_NOT_SUBSCRIBED = False
_CHECK_FAILED = None  # type: ignore[assignment]


async def _subscription_status(
    user_id: int,
) -> bool | None:
    """Return the tri-state subscription result for *user_id*.

    Opens its own short-lived DB session so the middleware does not share the
    session with the route handler (avoids transaction ordering surprises).

    Returns:
        True   – subscribed or no gate active
        False  – not subscribed
        None   – error AND channels are configured (caller should return 503)
    """
    from app.database.session import get_session_factory as _sf  # noqa: PLC0415
    from app.repositories.channel_repository import ChannelRepository  # noqa: PLC0415
    from app.services.channel_subscription_service import (  # noqa: PLC0415
        get_unsubscribed_channels,
    )
    from app.business.dispatcher import get_bot  # noqa: PLC0415
    from app.config import get_settings  # noqa: PLC0415
    from app.miniapp.routes import _sub_cache, _SUB_CACHE_TTL  # noqa: PLC0415

    now = _time.monotonic()
    cached = _sub_cache.get(user_id)
    if cached and (now - cached[0]) < _SUB_CACHE_TTL:
        return cached[1]

    factory = _sf()
    async with factory() as session:
        try:
            active = await ChannelRepository(session).get_active()
            if not active:
                _sub_cache[user_id] = (now, True)
                return True

            bot = get_bot(get_settings())
            if bot is None:
                # Bot unavailable → if channels are configured, fail closed.
                return None

            unsub = await get_unsubscribed_channels(bot, user_id, active)
            ok = len(unsub) == 0
            _sub_cache[user_id] = (now, ok)
            return ok

        except Exception:
            logger.exception(
                "subscription_gate middleware: check failed for user %s — failing closed",
                user_id,
            )
            # We attempted the check while channels may be configured.
            # Fail closed rather than silently granting access.
            return None


class SubscriptionGateMiddleware(BaseHTTPMiddleware):
    """HTTP middleware that enforces channel-subscription for all mini-app APIs."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # ── Only gate /app/api/* paths ─────────────────────────────────────
        if not path.startswith("/app/api/"):
            return await call_next(request)

        # ── Excluded paths bypass the gate ────────────────────────────────
        if path in _EXCLUDED_EXACT:
            return await call_next(request)
        if any(path.startswith(p) for p in _EXCLUDED_PREFIXES):
            return await call_next(request)

        # ── Extract init_data ─────────────────────────────────────────────
        init_data = await _extract_init_data(request)
        if not init_data:
            # init_data absent on a gated route → block immediately.
            # All genuinely public or webhook routes are in the exclusion lists
            # above; anything reaching this point must be an authenticated call.
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing Telegram init_data"},
            )

        # ── Verify Telegram signature → get user_id ───────────────────────
        from app.config import get_settings as _gs  # noqa: PLC0415
        from app.miniapp.auth import verify_init_data  # noqa: PLC0415

        try:
            settings = _gs()
            user = verify_init_data(init_data, settings.telegram_bot_token)
        except Exception:
            user = None

        if user is None:
            # Invalid signature; let the route handle it (returns 401).
            return await call_next(request)

        user_id = int(user["id"])

        # ── Check subscription ────────────────────────────────────────────
        status = await _subscription_status(user_id)

        if status is False:
            return JSONResponse(
                status_code=403,
                content={"detail": {"subscription_gate": True,
                                    "message": "Subscribe to required channels"}},
            )

        if status is None:
            # Channels configured but check failed → fail closed.
            return JSONResponse(
                status_code=503,
                content={"detail": "subscription_check_unavailable"},
            )

        # status is True → user is subscribed, proceed normally.
        return await call_next(request)


async def _extract_init_data(request: Request) -> str | None:
    """Pull the Telegram init_data string out of the request.

    For POST requests the body is JSON; for GET requests it is a query param.
    Starlette caches ``request.body()`` on ``request._body`` so the route
    handler can still read the body after the middleware has consumed it.
    """
    method = request.method.upper()

    if method == "GET":
        # Common query-param names used by GET endpoints in this app
        for key in ("initData", "init_data"):
            val = request.query_params.get(key)
            if val:
                return val
        return None

    if method == "POST":
        try:
            raw = await request.body()
            if not raw:
                return None
            data = _json.loads(raw)
            for key in ("initData", "init_data", "resolved_init"):
                val = data.get(key)
                if val and isinstance(val, str):
                    return val
        except Exception:
            pass
        return None

    return None
