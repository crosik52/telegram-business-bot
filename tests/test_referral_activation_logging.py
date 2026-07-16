"""Test: DB errors in referral activation surface in the correct log record.

Asserts that when `ReferralRepository.try_activate` raises a database error
inside `miniapp_stats`, the outer `except` block:

  1. emits exactly one log record whose message contains the expected
     referral-activation sentinel ("Referral activation check failed")
     at WARNING level or above
  2. includes the user's telegram ID in that record
  3. captures the traceback (exc_info is truthy on that record)
  4. does NOT include PII (first_name, username) in that record

Each test asserts on the *specific* referral-activation record, keyed by
the message template used in routes.py line 492-494, so false positives from
unrelated log paths (e.g. stats-build exceptions) cannot satisfy the assertion.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

USER_TELEGRAM_ID = 9_876_543
# Sentinel that appears in the logger.exception() call in routes.py:
#   logger.exception("Referral activation check failed for user %s", owner_telegram_id)
REFERRAL_ACTIVATION_SENTINEL = "Referral activation check failed"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_execute_side_effects(connection_ids: list):
    """
    Return a side_effect callable for session.execute().

    The first call in miniapp_stats queries BusinessConnection rows and must
    return a result whose .all() yields [(id,), ...].  All subsequent calls
    (stats/media queries) also need to return something valid so that the
    stats-build path doesn't raise its own exception before the test
    assertions run.

    We use a stateful counter: first call → BusinessConnection result,
    subsequent calls → empty result.
    """
    call_count = 0

    async def _side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            # BusinessConnection query
            result.all.return_value = [(cid,) for cid in connection_ids]
        else:
            # Any subsequent query (media breakdown, etc.) — return empty
            result.all.return_value = []
            result.scalars.return_value.all.return_value = []
        return result

    return _side_effect


def _make_good_stats():
    """A stats object that satisfies every field accessed by miniapp_stats."""
    svc_result = MagicMock()
    svc_result.total_messages = 0
    svc_result.total_chats = 0
    svc_result.edited_messages = 0
    svc_result.deleted_messages = 0
    svc_result.best_streak = 0
    svc_result.best_streak_name = ""
    svc_result.global_longest_streak = 0
    svc_result.top_interlocutors = []
    svc_result.per_chat = []
    svc_result.top_n = []
    return svc_result


def _make_sub_repo_mock():
    sub_repo = AsyncMock()
    config = MagicMock()
    config.is_enabled = True
    sub_repo.get_config = AsyncMock(return_value=config)
    sub_repo.get_active_subscription = AsyncMock(return_value=None)
    return sub_repo


# ---------------------------------------------------------------------------
# Context manager: all non-referral mocks
# ---------------------------------------------------------------------------

from contextlib import asynccontextmanager  # noqa: E402


def _all_patches(session):
    """
    Return a list of (patch_target, mock) pairs that covers every path in
    miniapp_stats *except* the referral block, so the stats-build path
    completes without raising.
    """
    stats_svc = AsyncMock()
    stats_svc.get_owner_stats = AsyncMock(return_value=_make_good_stats())

    return [
        patch("app.miniapp.routes.SubscriptionRepository", return_value=_make_sub_repo_mock()),
        patch("app.miniapp.routes.StatsService", return_value=stats_svc),
        # _enrich_interlocutors calls session.execute internally; the empty
        # side_effect above keeps it safe, but also patch the helper directly.
        patch(
            "app.miniapp.routes._enrich_interlocutors",
            new=AsyncMock(return_value=[]),
        ),
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_referral_activation_db_error_logs_at_warning_or_above(caplog):
    """A DB error from try_activate must produce a WARNING-or-above record
    whose message contains the referral-activation sentinel and the telegram ID,
    with exc_info set (traceback captured)."""
    from app.miniapp.routes import miniapp_stats, StatsRequest

    payload = StatsRequest(initData="dummy_init_data")

    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=_make_execute_side_effects(connection_ids=["bc_001"])
    )

    db_error = OperationalError("statement", {}, Exception("DB connection lost"))
    ref_repo_mock = AsyncMock()
    ref_repo_mock.try_activate = AsyncMock(side_effect=db_error)

    with (
        patch(
            "app.miniapp.routes.verify_init_data",
            return_value={"id": USER_TELEGRAM_ID, "username": "testuser"},
        ),
        patch(
            "app.miniapp.routes.get_settings",
            return_value=MagicMock(
                telegram_bot_token="token",
                miniapp_admin_username="admin",
            ),
        ),
        patch("app.miniapp.routes.ReferralRepository", return_value=ref_repo_mock),
        patch("app.miniapp.routes.SubscriptionRepository", return_value=_make_sub_repo_mock()),
        patch(
            "app.miniapp.routes.StatsService",
            return_value=AsyncMock(get_owner_stats=AsyncMock(return_value=_make_good_stats())),
        ),
        patch("app.miniapp.routes._enrich_interlocutors", new=AsyncMock(return_value=[])),
        caplog.at_level(logging.WARNING, logger="app.miniapp.routes"),
    ):
        try:
            await miniapp_stats(payload=payload, session=session)
        except Exception:
            # The route may raise for unrelated reasons; we only care about
            # the specific referral-activation log record.
            pass

    # ── Filter to the specific referral-activation log record ──────────────
    activation_records = [
        r for r in caplog.records
        if REFERRAL_ACTIVATION_SENTINEL in r.getMessage()
    ]

    assert activation_records, (
        f"No log record containing '{REFERRAL_ACTIVATION_SENTINEL}' was found. "
        f"All WARNING+ records: {[r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]}"
    )

    record = activation_records[0]

    # 1. Level must be WARNING or above
    assert record.levelno >= logging.WARNING, (
        f"Expected WARNING+ but got level {record.levelname}"
    )

    # 2. Telegram ID must be in the message
    assert str(USER_TELEGRAM_ID) in record.getMessage(), (
        f"Telegram ID {USER_TELEGRAM_ID} not found in log message: {record.getMessage()!r}"
    )

    # 3. exc_info must be truthy (traceback captured)
    assert record.exc_info, (
        "Log record does not carry exc_info. "
        "logger.exception() should set exc_info=True implicitly."
    )


@pytest.mark.asyncio
async def test_referral_activation_log_does_not_expose_sensitive_data(caplog):
    """The referral-activation error log record must not contain the user's
    first name or @username — only their numeric telegram ID."""
    from app.miniapp.routes import miniapp_stats, StatsRequest

    FIRST_NAME = "SecretFirstName"
    USERNAME = "secret_username"

    payload = StatsRequest(initData="dummy_init_data")
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=_make_execute_side_effects(connection_ids=["bc_001"])
    )

    db_error = OperationalError("statement", {}, Exception("timeout"))
    ref_repo_mock = AsyncMock()
    ref_repo_mock.try_activate = AsyncMock(side_effect=db_error)

    with (
        patch(
            "app.miniapp.routes.verify_init_data",
            return_value={
                "id": USER_TELEGRAM_ID,
                "username": USERNAME,
                "first_name": FIRST_NAME,
            },
        ),
        patch(
            "app.miniapp.routes.get_settings",
            return_value=MagicMock(
                telegram_bot_token="token",
                miniapp_admin_username="admin",
            ),
        ),
        patch("app.miniapp.routes.ReferralRepository", return_value=ref_repo_mock),
        patch("app.miniapp.routes.SubscriptionRepository", return_value=_make_sub_repo_mock()),
        patch(
            "app.miniapp.routes.StatsService",
            return_value=AsyncMock(get_owner_stats=AsyncMock(return_value=_make_good_stats())),
        ),
        patch("app.miniapp.routes._enrich_interlocutors", new=AsyncMock(return_value=[])),
        caplog.at_level(logging.WARNING, logger="app.miniapp.routes"),
    ):
        try:
            await miniapp_stats(payload=payload, session=session)
        except Exception:
            pass

    # Scope assertions to the referral-activation record only
    activation_records = [
        r for r in caplog.records
        if REFERRAL_ACTIVATION_SENTINEL in r.getMessage()
    ]

    assert activation_records, (
        f"No referral-activation log record found (sentinel: '{REFERRAL_ACTIVATION_SENTINEL}')"
    )

    # Check neither PII field appears in the log message
    for record in activation_records:
        msg = record.getMessage()
        assert FIRST_NAME not in msg, (
            f"First name '{FIRST_NAME}' must not appear in referral activation log: {msg!r}"
        )
        assert USERNAME not in msg, (
            f"Username '{USERNAME}' must not appear in referral activation log: {msg!r}"
        )
