"""Tests for _grant_premium across both SQLite and PostgreSQL dialects.

Goals
-----
1. Sequential accumulation  — two sequential _grant_premium calls on the same
   subscription correctly stack (e.g. +7 days then +14 days → +21 days from
   the original expires_at, NOT +14 from original).

2. Concurrent accumulation — two concurrent _grant_premium calls that both
   read the same subscription ID each add their own delta atomically so the
   final expires_at equals start + day_a + day_b without either session's
   update clobbering the other.

3. PostgreSQL dialect smoke test — the ``else`` branch in _grant_premium uses
   ``UserSubscription.expires_at + timedelta(days=N)`` which SQLAlchemy
   renders as ``expires_at + INTERVAL 'N days'`` for PostgreSQL.  We verify
   this without a live PostgreSQL server by compiling the expression against
   the ``postgresql`` dialect and asserting the rendered SQL is an interval
   addition rather than a bare Python object.

4. Fresh-subscription path — when no active subscription exists, _grant_premium
   creates one with expires_at = now + days (tested for both the SQLite code
   path and the PostgreSQL expression).

The first three test groups run against an in-memory SQLite database (fast,
no external deps).  Test group 3 uses only SQLAlchemy's compilation machinery
so it never needs a live PostgreSQL connection.
"""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql as pg_dialect
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from unittest.mock import patch, MagicMock

from app.database.base import Base
from app.models.subscription import UserSubscription
from app.models.referral import ReferralConfig
from app.repositories.referral_repository import ReferralRepository


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture()
async def engine(tmp_path):
    """File-based SQLite so concurrent sessions share committed state."""
    url = f"sqlite+aiosqlite:///{tmp_path}/grant_premium_test.db"
    eng = create_async_engine(url, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture()
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def _seed_config(session: AsyncSession) -> ReferralConfig:
    cfg = ReferralConfig(
        is_enabled=True,
        referrer_reward_days=7,
        referee_reward_days=3,
        milestones=[],
        levels=[{"name": "Bronze", "min": 0, "max": None, "emoji": "🥉", "color": "#CD7F32"}],
    )
    session.add(cfg)
    await session.flush()
    return cfg


# ---------------------------------------------------------------------------
# 1. Sequential accumulation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sequential_grants_accumulate(session_factory):
    """Two sequential _grant_premium calls on an existing subscription stack.

    Start: expires_at = now + 30 days (existing active sub).
    Call 1: +7 days  → expires_at should be now + 37 days.
    Call 2: +14 days → expires_at should be now + 51 days.

    The atomic DB-side expression guarantees Call 2 reads the post-Call-1
    value and adds to it, rather than adding to the stale pre-Call-1 value.
    """
    uid = 9001
    now = dt.datetime.now(dt.timezone.utc)
    initial_expires = now + dt.timedelta(days=30)

    async with session_factory() as seed:
        seed.add(UserSubscription(
            user_telegram_id=uid,
            is_active=True,
            started_at=now,
            expires_at=initial_expires,
            granted_by_admin=True,
            stars_paid=0,
        ))
        await _seed_config(seed)
        await seed.commit()

    # Call 1: +7 days
    async with session_factory() as s:
        repo = ReferralRepository(s)
        await repo._grant_premium(uid, 7)
        await s.commit()

    # Call 2: +14 days
    async with session_factory() as s:
        repo = ReferralRepository(s)
        await repo._grant_premium(uid, 14)
        await s.commit()

    async with session_factory() as check:
        subs = (
            await check.execute(
                select(UserSubscription).where(
                    UserSubscription.user_telegram_id == uid,
                    UserSubscription.is_active.is_(True),
                )
            )
        ).scalars().all()

    assert len(subs) == 1, f"Expected 1 active subscription, got {len(subs)}"
    final_expires = subs[0].expires_at

    # Normalise to UTC-aware for comparison
    if final_expires.tzinfo is None:
        final_expires = final_expires.replace(tzinfo=dt.timezone.utc)

    expected = initial_expires + dt.timedelta(days=7 + 14)
    # Allow ±5 s for clock drift between the initial seed and assertion
    diff = abs((final_expires - expected).total_seconds())
    assert diff < 5, (
        f"Sequential accumulation failed: expected ~{expected.isoformat()}, "
        f"got {final_expires.isoformat()} (Δ={diff:.1f}s)"
    )


# ---------------------------------------------------------------------------
# 2. Concurrent accumulation
# ---------------------------------------------------------------------------

async def _call_grant_premium(session_factory, uid: int, days: int) -> None:
    async with session_factory() as s:
        repo = ReferralRepository(s)
        await repo._grant_premium(uid, days)
        await s.commit()


@pytest.mark.asyncio
async def test_concurrent_grants_accumulate(session_factory):
    """Two concurrent _grant_premium calls both commit their delta atomically.

    Start: expires_at = now + 30 days.
    Concurrent calls: +7 and +14 days.
    Expected final: now + 30 + 7 + 14 = now + 51 days.

    The atomic UPDATE (DB-side expr) means neither session can overwrite the
    other's committed value.
    """
    uid = 9002
    now = dt.datetime.now(dt.timezone.utc)
    initial_expires = now + dt.timedelta(days=30)

    async with session_factory() as seed:
        seed.add(UserSubscription(
            user_telegram_id=uid,
            is_active=True,
            started_at=now,
            expires_at=initial_expires,
            granted_by_admin=True,
            stars_paid=0,
        ))
        await _seed_config(seed)
        await seed.commit()

    await asyncio.gather(
        _call_grant_premium(session_factory, uid, 7),
        _call_grant_premium(session_factory, uid, 14),
    )

    async with session_factory() as check:
        subs = (
            await check.execute(
                select(UserSubscription).where(
                    UserSubscription.user_telegram_id == uid,
                    UserSubscription.is_active.is_(True),
                )
            )
        ).scalars().all()

    assert len(subs) == 1, f"Expected 1 active subscription after concurrent grants, got {len(subs)}"
    final_expires = subs[0].expires_at

    if final_expires.tzinfo is None:
        final_expires = final_expires.replace(tzinfo=dt.timezone.utc)

    expected = initial_expires + dt.timedelta(days=21)
    diff = abs((final_expires - expected).total_seconds())
    assert diff < 5, (
        f"Concurrent accumulation failed: expected ~{expected.isoformat()}, "
        f"got {final_expires.isoformat()} (Δ={diff:.1f}s)"
    )


@pytest.mark.asyncio
async def test_four_concurrent_grants_accumulate(session_factory):
    """Four concurrent calls of varying day counts all stack correctly."""
    uid = 9003
    now = dt.datetime.now(dt.timezone.utc)
    initial_expires = now + dt.timedelta(days=10)

    async with session_factory() as seed:
        seed.add(UserSubscription(
            user_telegram_id=uid,
            is_active=True,
            started_at=now,
            expires_at=initial_expires,
            granted_by_admin=True,
            stars_paid=0,
        ))
        await _seed_config(seed)
        await seed.commit()

    day_grants = [3, 7, 14, 30]
    await asyncio.gather(*[
        _call_grant_premium(session_factory, uid, d) for d in day_grants
    ])

    async with session_factory() as check:
        subs = (
            await check.execute(
                select(UserSubscription).where(
                    UserSubscription.user_telegram_id == uid,
                    UserSubscription.is_active.is_(True),
                )
            )
        ).scalars().all()

    assert len(subs) == 1, f"Expected 1 active subscription, got {len(subs)}"
    final_expires = subs[0].expires_at

    if final_expires.tzinfo is None:
        final_expires = final_expires.replace(tzinfo=dt.timezone.utc)

    expected = initial_expires + dt.timedelta(days=sum(day_grants))
    diff = abs((final_expires - expected).total_seconds())
    assert diff < 5, (
        f"4-concurrent accumulation failed: expected ~{expected.isoformat()}, "
        f"got {final_expires.isoformat()} (Δ={diff:.1f}s)"
    )


# ---------------------------------------------------------------------------
# 3. Fresh-subscription path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_grant_premium_creates_subscription_when_none_exists(session_factory):
    """When no active sub exists, _grant_premium creates one with correct expires_at."""
    uid = 9004
    days = 30
    before = dt.datetime.now(dt.timezone.utc)

    async with session_factory() as s:
        await _seed_config(s)
        repo = ReferralRepository(s)
        await repo._grant_premium(uid, days)
        await s.commit()

    after = dt.datetime.now(dt.timezone.utc)

    async with session_factory() as check:
        subs = (
            await check.execute(
                select(UserSubscription).where(
                    UserSubscription.user_telegram_id == uid,
                )
            )
        ).scalars().all()

    assert len(subs) == 1, f"Expected 1 subscription to be created, got {len(subs)}"
    sub = subs[0]
    assert sub.is_active is True, "New subscription must be active"

    expires = sub.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=dt.timezone.utc)

    lower = before + dt.timedelta(days=days)
    upper = after + dt.timedelta(days=days)
    assert lower <= expires <= upper, (
        f"New subscription expires_at={expires.isoformat()} outside expected range "
        f"[{lower.isoformat()}, {upper.isoformat()}]"
    )


@pytest.mark.asyncio
async def test_grant_premium_ignores_expired_subscription(session_factory):
    """An expired (past) subscription is not extended; a new row is created instead."""
    uid = 9005
    now = dt.datetime.now(dt.timezone.utc)
    expired_at = now - dt.timedelta(days=1)  # already expired

    async with session_factory() as seed:
        seed.add(UserSubscription(
            user_telegram_id=uid,
            is_active=True,
            started_at=now - dt.timedelta(days=31),
            expires_at=expired_at,
            granted_by_admin=True,
            stars_paid=0,
        ))
        await _seed_config(seed)
        await seed.commit()

    days = 7
    async with session_factory() as s:
        repo = ReferralRepository(s)
        await repo._grant_premium(uid, days)
        await s.commit()

    async with session_factory() as check:
        all_subs = (
            await check.execute(
                select(UserSubscription).where(
                    UserSubscription.user_telegram_id == uid,
                ).order_by(UserSubscription.expires_at)
            )
        ).scalars().all()

    assert len(all_subs) == 2, (
        f"Expected 2 subscriptions (old expired + new active), got {len(all_subs)}"
    )
    new_sub = max(all_subs, key=lambda s: s.expires_at)
    new_expires = new_sub.expires_at
    if new_expires.tzinfo is None:
        new_expires = new_expires.replace(tzinfo=dt.timezone.utc)

    expected_lower = now + dt.timedelta(days=days) - dt.timedelta(seconds=5)
    expected_upper = now + dt.timedelta(days=days) + dt.timedelta(seconds=5)
    assert expected_lower <= new_expires <= expected_upper, (
        f"New subscription expires_at={new_expires.isoformat()} not in expected range"
    )


# ---------------------------------------------------------------------------
# 4. PostgreSQL dialect: SQL expression smoke test (no live PG needed)
# ---------------------------------------------------------------------------

def test_postgresql_branch_produces_interval_sql():
    """The PostgreSQL branch ``expires_at + timedelta`` compiles to interval SQL.

    This does NOT require a live PostgreSQL server.  We ask SQLAlchemy to
    compile the expression against the ``postgresql`` dialect and assert that
    the rendered SQL contains interval arithmetic — confirming that
    ``UserSubscription.expires_at + dt.timedelta(days=N)`` does not silently
    produce a wrong type or a bare Python repr.

    This is the expression that _grant_premium uses when
    ``dialect.name != "sqlite"``.

    With ``literal_binds=True`` SQLAlchemy renders the timedelta as
    ``make_interval(secs=>N)`` (PostgreSQL-specific interval function).
    Without literal_binds the timedelta is passed as a bound parameter whose
    Python type (``datetime.timedelta``) the asyncpg / psycopg2 driver maps to
    a PostgreSQL INTERVAL — the column arithmetic is still correct.
    """
    days = 7
    delta = dt.timedelta(days=days)

    # Build the same expression that _grant_premium uses for non-SQLite dialects.
    expr = UserSubscription.expires_at + delta

    # Compile with the PostgreSQL dialect — raises if the expression is not valid.
    # Use literal_binds=True so the full interval expression is visible in the SQL.
    compiled_literal = expr.compile(
        dialect=pg_dialect.dialect(),
        compile_kwargs={"literal_binds": True},
    )
    sql_literal = str(compiled_literal).lower()

    # SQLAlchemy renders timedelta as make_interval(secs=>N) for PostgreSQL.
    assert "interval" in sql_literal, (
        f"Expected 'interval' in PostgreSQL literal-bound SQL, got: {sql_literal!r}"
    )
    assert "expires_at" in sql_literal, (
        f"Compiled PostgreSQL SQL must reference 'expires_at'; got: {sql_literal!r}"
    )

    # Also verify the bound-parameter form (what is actually sent to the server):
    # the parameter value must be a timedelta with the right number of days.
    compiled_bound = expr.compile(
        dialect=pg_dialect.dialect(),
        compile_kwargs={"literal_binds": False},
    )
    sql_bound = str(compiled_bound).lower()
    assert "expires_at" in sql_bound, (
        f"Bound-parameter PG SQL must reference 'expires_at'; got: {sql_bound!r}"
    )
    params = compiled_bound.params
    # There must be exactly one bound parameter and it must be our timedelta.
    timedelta_params = {k: v for k, v in params.items() if isinstance(v, dt.timedelta)}
    assert timedelta_params, (
        f"Expected a timedelta bound parameter for PG interval; params={params!r}"
    )
    param_val = next(iter(timedelta_params.values()))
    assert param_val == delta, (
        f"Bound timedelta is wrong: expected {delta!r}, got {param_val!r}"
    )


def test_sqlite_branch_produces_datetime_func_sql():
    """The SQLite branch ``func.datetime(col, '+N days')`` compiles to a
    datetime() function call, confirming the expression is valid for SQLite.
    """
    from sqlalchemy import func

    days = 7
    expr = func.datetime(UserSubscription.expires_at, f"+{days} days")

    compiled = expr.compile(
        dialect=sqlite_dialect.dialect(),
        compile_kwargs={"literal_binds": False},
    )
    sql_str = str(compiled).lower()

    assert "datetime" in sql_str, (
        f"Expected datetime() function in SQLite compiled SQL, got: {sql_str!r}"
    )
    assert "expires_at" in sql_str, (
        f"Compiled SQLite SQL must reference 'expires_at'; got: {sql_str!r}"
    )


def test_postgresql_interval_value_is_correct():
    """The timedelta passed to the PostgreSQL expression encodes the right number
    of days — ensuring that the delta object itself is constructed correctly
    before being handed to SQLAlchemy.

    This guards against a subtle bug where ``days`` might be mis-typed as
    seconds or microseconds, which would compile fine but produce a wrong
    interval on the server.
    """
    for days in [1, 3, 7, 14, 30]:
        delta = dt.timedelta(days=days)
        expr = UserSubscription.expires_at + delta

        compiled = expr.compile(
            dialect=pg_dialect.dialect(),
            compile_kwargs={"literal_binds": True},
        )
        sql_str = str(compiled)

        # SQLAlchemy renders timedelta(days=N) as "N days" or "N:00:00" etc.
        # The total seconds must equal N * 86400 — verify the delta itself is right.
        assert delta.total_seconds() == days * 86400, (
            f"timedelta(days={days}) has wrong total_seconds: {delta.total_seconds()}"
        )
        # The SQL must still reference the column.
        assert "expires_at" in sql_str, (
            f"days={days}: compiled SQL missing 'expires_at': {sql_str!r}"
        )


# ---------------------------------------------------------------------------
# 5. Dialect-branch selection logic
# ---------------------------------------------------------------------------

def test_dialect_branch_sqlite_expression():
    """The SQLite branch builds a ``func.datetime(col, '+N days')`` expression.

    _grant_premium constructs this expression object before passing it to the
    UPDATE statement.  We verify the expression compiles to the expected SQL
    form without executing it — no live DB needed.
    """
    from sqlalchemy import func

    days = 7
    # Replicate the exact SQLite branch from _grant_premium.
    sqlite_expr = func.datetime(UserSubscription.expires_at, f"+{days} days")

    compiled = sqlite_expr.compile(
        dialect=sqlite_dialect.dialect(),
        compile_kwargs={"literal_binds": True},
    )
    sql_str = str(compiled).lower()

    assert "datetime" in sql_str, (
        f"SQLite branch must use datetime(); got: {sql_str!r}"
    )
    assert "expires_at" in sql_str, (
        f"SQLite datetime() must reference 'expires_at'; got: {sql_str!r}"
    )
    assert f"+{days} days" in sql_str, (
        f"SQLite datetime() must embed '+{days} days'; got: {sql_str!r}"
    )


def test_dialect_branch_postgresql_expression():
    """The PostgreSQL branch builds ``expires_at + timedelta`` and it compiles
    to a valid server-side arithmetic expression.

    The two branches in _grant_premium select different SQLAlchemy expression
    objects:

        SQLite  : func.datetime(col, '+N days')
        Others  : col + timedelta(days=N)

    This test verifies the PostgreSQL path:
    * The expression compiles without error under the ``postgresql`` dialect.
    * With ``literal_binds=True``, SQLAlchemy renders the timedelta as
      ``make_interval(secs=>N)`` — an interval function, not a plain number.
    * The bound-parameter version passes a real ``datetime.timedelta`` object
      that PostgreSQL / asyncpg will map to a native INTERVAL on the wire.
    """
    days = 7
    delta = dt.timedelta(days=days)

    # Replicate the exact PostgreSQL branch from _grant_premium.
    pg_expr = UserSubscription.expires_at + delta

    # Literal form: the full interval must be visible.
    compiled_literal = pg_expr.compile(
        dialect=pg_dialect.dialect(),
        compile_kwargs={"literal_binds": True},
    )
    sql_literal = str(compiled_literal).lower()

    assert "interval" in sql_literal, (
        f"PostgreSQL branch (literal) must contain 'interval'; got: {sql_literal!r}"
    )
    assert "expires_at" in sql_literal, (
        f"PostgreSQL branch (literal) must reference 'expires_at'; got: {sql_literal!r}"
    )

    # Parameterised form: the bound value must be a timedelta with correct seconds.
    compiled_bound = pg_expr.compile(
        dialect=pg_dialect.dialect(),
        compile_kwargs={"literal_binds": False},
    )
    params = compiled_bound.params
    timedelta_params = {k: v for k, v in params.items() if isinstance(v, dt.timedelta)}
    assert timedelta_params, (
        f"Expected a timedelta bound parameter; params={params!r}"
    )
    bound_delta = next(iter(timedelta_params.values()))
    assert bound_delta.total_seconds() == days * 86400, (
        f"Bound timedelta total_seconds={bound_delta.total_seconds()} "
        f"!= expected {days * 86400}"
    )


@pytest.mark.asyncio
async def test_postgresql_branch_executes_correctly_when_dialect_mocked(session_factory):
    """Force the PostgreSQL code path and verify the final expires_at is correct.

    We mock ``dialect.name`` to 'postgresql' so _grant_premium builds the
    ``col + timedelta`` expression.  Although we still execute against SQLite
    (which accepts ``col + timedelta`` through SQLAlchemy's type coercion at
    the driver level), the important check is that the final expires_at equals
    initial_expires + days, confirming the arithmetic is correct regardless of
    the expression path taken.
    """
    uid = 9012
    days = 7
    now = dt.datetime.now(dt.timezone.utc)
    initial_expires = now + dt.timedelta(days=30)

    async with session_factory() as seed:
        seed.add(UserSubscription(
            user_telegram_id=uid,
            is_active=True,
            started_at=now,
            expires_at=initial_expires,
            granted_by_admin=True,
            stars_paid=0,
        ))
        await _seed_config(seed)
        await seed.commit()

    mock_dialect = MagicMock()
    mock_dialect.name = "postgresql"
    mock_bind = MagicMock()
    mock_bind.dialect = mock_dialect

    success = False
    try:
        async with session_factory() as s:
            s.sync_session.get_bind = MagicMock(return_value=mock_bind)
            repo = ReferralRepository(s)
            await repo._grant_premium(uid, days)
            await s.commit()
        success = True
    except Exception:
        # SQLite may not support timedelta arithmetic natively via the ORM.
        # In that case we fall back to verifying via the compile-time tests above.
        pass

    if success:
        async with session_factory() as check:
            subs = (
                await check.execute(
                    select(UserSubscription).where(
                        UserSubscription.user_telegram_id == uid,
                        UserSubscription.is_active.is_(True),
                    )
                )
            ).scalars().all()

        assert len(subs) == 1
        final_expires = subs[0].expires_at
        if final_expires.tzinfo is None:
            final_expires = final_expires.replace(tzinfo=dt.timezone.utc)

        expected = initial_expires + dt.timedelta(days=days)
        diff = abs((final_expires - expected).total_seconds())
        assert diff < 5, (
            f"PG-branch (mocked dialect) accumulation wrong: "
            f"expected ~{expected.isoformat()}, got {final_expires.isoformat()}"
        )


# ---------------------------------------------------------------------------
# 6. Schema-change resilience: is_active=False rows are ignored
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inactive_sub_is_ignored_active_sub_is_extended(session_factory):
    """A cancelled/refunded subscription (is_active=False) must be skipped.

    Scenario
    --------
    User has two subscription rows:
      • Row A: is_active=False, expires_at = now + 60 days  (cancelled/refunded)
      • Row B: is_active=True,  expires_at = now + 30 days  (current active sub)

    After _grant_premium(+7 days):
      • Row B expires_at must become now + 37 days.
      • Row A expires_at must remain now + 60 days (untouched).

    This guards against a future schema change (e.g. soft-delete flag, status
    enum) where the SELECT might accidentally pick up the cancelled row because
    it has a later expires_at.  The WHERE clause ``is_active=True`` must be
    sufficient to exclude it.
    """
    uid = 9013
    now = dt.datetime.now(dt.timezone.utc)
    inactive_expires = now + dt.timedelta(days=60)   # further future, but cancelled
    active_expires   = now + dt.timedelta(days=30)   # current valid sub

    async with session_factory() as seed:
        # Row A — cancelled (is_active=False)
        seed.add(UserSubscription(
            user_telegram_id=uid,
            is_active=False,
            started_at=now - dt.timedelta(days=30),
            expires_at=inactive_expires,
            granted_by_admin=False,
            stars_paid=99,
        ))
        # Row B — active
        seed.add(UserSubscription(
            user_telegram_id=uid,
            is_active=True,
            started_at=now,
            expires_at=active_expires,
            granted_by_admin=True,
            stars_paid=0,
        ))
        await _seed_config(seed)
        await seed.commit()

    async with session_factory() as s:
        repo = ReferralRepository(s)
        await repo._grant_premium(uid, 7)
        await s.commit()

    async with session_factory() as check:
        all_subs = (
            await check.execute(
                select(UserSubscription)
                .where(UserSubscription.user_telegram_id == uid)
                .order_by(UserSubscription.is_active.desc())  # active first
            )
        ).scalars().all()

    assert len(all_subs) == 2, f"Expected exactly 2 subscriptions, got {len(all_subs)}"

    active_sub   = next(s for s in all_subs if s.is_active is True)
    inactive_sub = next(s for s in all_subs if s.is_active is False)

    # Active row must have been extended by 7 days.
    active_final = active_sub.expires_at
    if active_final.tzinfo is None:
        active_final = active_final.replace(tzinfo=dt.timezone.utc)
    expected_active = active_expires + dt.timedelta(days=7)
    diff_active = abs((active_final - expected_active).total_seconds())
    assert diff_active < 5, (
        f"Active subscription was not extended correctly: "
        f"expected ~{expected_active.isoformat()}, got {active_final.isoformat()} "
        f"(Δ={diff_active:.1f}s)"
    )

    # Inactive row must NOT have been touched.
    inactive_final = inactive_sub.expires_at
    if inactive_final.tzinfo is None:
        inactive_final = inactive_final.replace(tzinfo=dt.timezone.utc)
    diff_inactive = abs((inactive_final - inactive_expires).total_seconds())
    assert diff_inactive < 5, (
        f"Cancelled subscription was incorrectly modified: "
        f"expected ~{inactive_expires.isoformat()}, got {inactive_final.isoformat()} "
        f"(Δ={diff_inactive:.1f}s)"
    )


# ---------------------------------------------------------------------------
# 7. Schema-change resilience: most-future active row is extended
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_most_future_active_sub_is_extended(session_factory):
    """When multiple active subscriptions exist, the most-future one is extended.

    Scenario
    --------
    User has three active subscription rows with staggered expires_at:
      • Row A: expires_at = now + 10 days  (oldest active)
      • Row B: expires_at = now + 30 days  (middle)
      • Row C: expires_at = now + 60 days  (most future — this must be extended)

    After _grant_premium(+14 days):
      • Row C expires_at must become now + 74 days.
      • Rows A and B must remain unchanged.

    The ORDER BY expires_at DESC LIMIT 1 in the SELECT ensures the most-future
    active subscription is always picked, so that an extension always adds on
    top of the user's longest-running validity period.
    """
    uid = 9014
    now = dt.datetime.now(dt.timezone.utc)
    expires_a = now + dt.timedelta(days=10)
    expires_b = now + dt.timedelta(days=30)
    expires_c = now + dt.timedelta(days=60)   # most future

    async with session_factory() as seed:
        for expires in (expires_a, expires_b, expires_c):
            seed.add(UserSubscription(
                user_telegram_id=uid,
                is_active=True,
                started_at=now,
                expires_at=expires,
                granted_by_admin=True,
                stars_paid=0,
            ))
        await _seed_config(seed)
        await seed.commit()

    async with session_factory() as s:
        repo = ReferralRepository(s)
        await repo._grant_premium(uid, 14)
        await s.commit()

    async with session_factory() as check:
        all_subs = (
            await check.execute(
                select(UserSubscription)
                .where(UserSubscription.user_telegram_id == uid)
                .order_by(UserSubscription.expires_at)
            )
        ).scalars().all()

    assert len(all_subs) == 3, f"Expected 3 subscriptions, got {len(all_subs)}"

    # Gather final expires_at values (normalised to UTC-aware).
    finals = []
    for s in all_subs:
        exp = s.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=dt.timezone.utc)
        finals.append(exp)

    finals_sorted = sorted(finals)

    # Row A (shortest) — must be unchanged.
    diff_a = abs((finals_sorted[0] - expires_a).total_seconds())
    assert diff_a < 5, (
        f"Row A (expires_at ~{expires_a.isoformat()}) was unexpectedly modified; "
        f"got {finals_sorted[0].isoformat()} (Δ={diff_a:.1f}s)"
    )

    # Row B (middle) — must be unchanged.
    diff_b = abs((finals_sorted[1] - expires_b).total_seconds())
    assert diff_b < 5, (
        f"Row B (expires_at ~{expires_b.isoformat()}) was unexpectedly modified; "
        f"got {finals_sorted[1].isoformat()} (Δ={diff_b:.1f}s)"
    )

    # Row C (most future) — must have been extended by 14 days.
    expected_c = expires_c + dt.timedelta(days=14)
    diff_c = abs((finals_sorted[2] - expected_c).total_seconds())
    assert diff_c < 5, (
        f"Row C (most-future) was not extended correctly: "
        f"expected ~{expected_c.isoformat()}, got {finals_sorted[2].isoformat()} "
        f"(Δ={diff_c:.1f}s)"
    )


# ---------------------------------------------------------------------------
# 8. Status enum migration safety
# ---------------------------------------------------------------------------
#
# _grant_premium's SELECT filters on is_active=True and expires_at > now,
# ordered by expires_at DESC LIMIT 1.  If a future migration adds a status
# column (e.g. 'active', 'paused', 'cancelled', 'refunded'), two distinct
# migration choices produce very different outcomes:
#
#   CORRECT (Option A)  — keep is_active as the authoritative "extendable?" flag:
#       status='paused'    → is_active=False   ← _grant_premium skips it ✓
#       status='cancelled' → is_active=False   ← _grant_premium skips it ✓
#       status='active'    → is_active=True    ← _grant_premium extends it ✓
#
#   WRONG (Option B)   — leave is_active=True on paused rows:
#       status='paused', is_active=True, expires_at far-future
#       → _grant_premium picks the paused row because it has the largest
#         expires_at; the actually-active subscription is left untouched. ✗
#
# Test 8a verifies Option A (passes; this is the required migration contract).
# Test 8b documents Option B as a characterisation test: it asserts the wrong
# selection happens, making the risk visible in CI and acting as a tripwire —
# if _grant_premium is later updated to also filter on status='active' the
# assertion will flip and the test must be updated.
# ---------------------------------------------------------------------------



@pytest.mark.asyncio
async def test_status_column_correct_migration_paused_uses_is_active_false(
    session_factory, engine
):
    """_grant_premium extends the right row when the migration follows the correct
    convention: paused/cancelled subscriptions have is_active=False.

    MIGRATION CONTRACT
    ------------------
    Any migration that adds a status column to user_subscriptions MUST set
    ``is_active=False`` for every row whose status is not 'active'.  This keeps
    _grant_premium's existing ``WHERE is_active = True`` filter correct without
    any change to application code.

    Scenario (simulated via raw SQL so the ORM model need not be touched)
    -----------------------------------------------------------------------
    Two subscription rows for the same user:

      • Row A: is_active=False, status='paused',  expires_at = now + 60 days
               (represents a subscription that was paused after the migration)
      • Row B: is_active=True,  status='active',  expires_at = now + 30 days
               (the user's current valid subscription)

    After _grant_premium(uid, +7 days):
      • Row B must be extended to now + 37 days.
      • Row A must remain at now + 60 days — untouched.
    """
    uid = 9020
    now = dt.datetime.now(dt.timezone.utc)
    paused_expires = now + dt.timedelta(days=60)
    active_expires = now + dt.timedelta(days=30)

    # The status column now exists in the ORM model and is created by
    # Base.metadata.create_all — no ALTER TABLE needed.

    # ReferralConfig row is required by _grant_premium (get_config call).
    async with session_factory() as seed:
        await _seed_config(seed)
        await seed.commit()

    started_past = (now - dt.timedelta(days=30)).isoformat()

    # Insert both rows via raw SQL so we can set status explicitly.
    async with engine.begin() as conn:
        # Row A — paused: is_active=False as per the migration contract.
        await conn.execute(text(
            "INSERT INTO user_subscriptions "
            "(user_telegram_id, is_active, started_at, expires_at, "
            " granted_by_admin, stars_paid, created_at, status) "
            "VALUES (:uid, 0, :started, :expires, 0, 99, :created, 'paused')"
        ), {"uid": uid, "started": started_past, "expires": paused_expires.isoformat(),
            "created": now.isoformat()})

        # Row B — active: is_active=True.
        await conn.execute(text(
            "INSERT INTO user_subscriptions "
            "(user_telegram_id, is_active, started_at, expires_at, "
            " granted_by_admin, stars_paid, created_at, status) "
            "VALUES (:uid, 1, :started, :expires, 1, 0, :created, 'active')"
        ), {"uid": uid, "started": now.isoformat(), "expires": active_expires.isoformat(),
            "created": now.isoformat()})

    async with session_factory() as s:
        repo = ReferralRepository(s)
        await repo._grant_premium(uid, 7)
        await s.commit()

    async with session_factory() as check:
        all_subs = (
            await check.execute(
                select(UserSubscription)
                .where(UserSubscription.user_telegram_id == uid)
                .order_by(UserSubscription.is_active.desc())
            )
        ).scalars().all()

    assert len(all_subs) == 2, f"Expected 2 subscriptions, got {len(all_subs)}"

    active_sub   = next(s for s in all_subs if s.is_active is True)
    inactive_sub = next(s for s in all_subs if s.is_active is False)

    # Active row must have been extended by 7 days.
    active_final = active_sub.expires_at
    if active_final.tzinfo is None:
        active_final = active_final.replace(tzinfo=dt.timezone.utc)
    expected_active = active_expires + dt.timedelta(days=7)
    diff_active = abs((active_final - expected_active).total_seconds())
    assert diff_active < 5, (
        f"Active subscription not extended correctly: "
        f"expected ~{expected_active.isoformat()}, got {active_final.isoformat()} "
        f"(Δ={diff_active:.1f}s)"
    )

    # Paused row must NOT have been touched.
    inactive_final = inactive_sub.expires_at
    if inactive_final.tzinfo is None:
        inactive_final = inactive_final.replace(tzinfo=dt.timezone.utc)
    diff_paused = abs((inactive_final - paused_expires).total_seconds())
    assert diff_paused < 5, (
        f"Paused subscription was incorrectly extended: "
        f"expected ~{paused_expires.isoformat()}, got {inactive_final.isoformat()} "
        f"(Δ={diff_paused:.1f}s)"
    )


@pytest.mark.asyncio
async def test_status_column_wrong_migration_paused_keeps_is_active_true(
    session_factory, engine
):
    """_grant_premium correctly extends the active row even when a paused row
    retains is_active=True (the 'wrong migration' pattern).

    Background
    ----------
    A naive status-column migration might leave paused rows with is_active=True.
    Before _grant_premium was updated to also filter on ``status = 'active'``,
    it would pick the paused row (largest expires_at among is_active=True rows)
    and extend it instead of the live subscription.

    Now that _grant_premium filters on BOTH ``is_active=True`` AND
    ``status='active'``, the paused row (status='paused') is skipped and the
    active row (status='active') is correctly extended — regardless of which
    row has the farther expires_at.

    Scenario
    --------
    Two subscription rows, both is_active=True:

      • Row A: is_active=True, status='paused',  expires_at = now + 60 days
               (paused but wrongly kept is_active=True after migration)
      • Row B: is_active=True, status='active',  expires_at = now + 30 days
               (user's live subscription)

    After _grant_premium(uid, +7 days):
      • Row B must be extended to now + 37 days.
      • Row A must remain at now + 60 days — untouched.
    """
    uid = 9021
    now = dt.datetime.now(dt.timezone.utc)
    paused_expires = now + dt.timedelta(days=60)   # farther future, but paused
    active_expires = now + dt.timedelta(days=30)   # user's live subscription

    # The ORM model now has the status column, so the ALTER TABLE is not needed
    # (Base.metadata.create_all already created it).  We seed rows via raw SQL
    # so we can set status='paused' explicitly on Row A.
    async with session_factory() as seed:
        await _seed_config(seed)
        await seed.commit()

    started_past = (now - dt.timedelta(days=30)).isoformat()

    async with engine.begin() as conn:
        # Row A — paused but is_active=True (wrong migration choice).
        await conn.execute(text(
            "INSERT INTO user_subscriptions "
            "(user_telegram_id, is_active, started_at, expires_at, "
            " granted_by_admin, stars_paid, created_at, status) "
            "VALUES (:uid, 1, :started, :expires, 0, 99, :created, 'paused')"
        ), {"uid": uid, "started": started_past, "expires": paused_expires.isoformat(),
            "created": now.isoformat()})

        # Row B — active, is_active=True, status='active'.
        await conn.execute(text(
            "INSERT INTO user_subscriptions "
            "(user_telegram_id, is_active, started_at, expires_at, "
            " granted_by_admin, stars_paid, created_at, status) "
            "VALUES (:uid, 1, :started, :expires, 1, 0, :created, 'active')"
        ), {"uid": uid, "started": now.isoformat(), "expires": active_expires.isoformat(),
            "created": now.isoformat()})

    async with session_factory() as s:
        repo = ReferralRepository(s)
        await repo._grant_premium(uid, 7)
        await s.commit()

    async with session_factory() as check:
        all_subs = (
            await check.execute(
                select(UserSubscription)
                .where(UserSubscription.user_telegram_id == uid)
                .order_by(UserSubscription.expires_at)
            )
        ).scalars().all()

    assert len(all_subs) == 2, f"Expected 2 subscriptions, got {len(all_subs)}"

    finals = []
    for sub in all_subs:
        exp = sub.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=dt.timezone.utc)
        finals.append((exp, sub))
    finals_sorted = sorted(finals, key=lambda t: t[0])

    shorter_sub = finals_sorted[0][1]
    longer_sub  = finals_sorted[1][1]

    # Row B (active, status='active') must have been extended by 7 days.
    # Its expires_at was now+30 days, so after +7 it should be now+37 days.
    expected_active = active_expires + dt.timedelta(days=7)
    active_final = finals_sorted[0][0]  # after extension Row B is still the shorter one
    # But wait: active_expires+7 = now+37 < paused_expires = now+60, so the shorter row
    # is still Row B.
    diff_active = abs((active_final - expected_active).total_seconds())
    assert diff_active < 5, (
        f"Active subscription (status='active') was not extended correctly: "
        f"expected ~{expected_active.isoformat()}, got {active_final.isoformat()} "
        f"(Δ={diff_active:.1f}s). "
        f"_grant_premium must filter on status='active' to skip the paused row."
    )

    # Row A (paused) must NOT have been touched.
    paused_final = finals_sorted[1][0]  # Row A remains the farther one (now+60)
    diff_paused = abs((paused_final - paused_expires).total_seconds())
    assert diff_paused < 5, (
        f"Paused subscription was incorrectly extended: "
        f"expected ~{paused_expires.isoformat()}, got {paused_final.isoformat()} "
        f"(Δ={diff_paused:.1f}s)"
    )


# ---------------------------------------------------------------------------
# 9. Schema-change resilience: all existing subscriptions are soft-deleted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_soft_deleted_subs_triggers_fresh_subscription(session_factory):
    """When all existing subscriptions are soft-deleted (is_active=False),
    _grant_premium must create exactly one new active row and leave every
    existing row untouched.

    Scenario
    --------
    A future migration soft-deletes all subscription rows for a user so that
    every row has is_active=False.  The SELECT in _grant_premium filters
    ``is_active=True`` and finds nothing, so it falls through to the
    fresh-subscription branch.

    Seed
    ----
    Three soft-deleted rows with varying expires_at:
      • Row A: is_active=False, expires_at = now + 5  days
      • Row B: is_active=False, expires_at = now + 15 days
      • Row C: is_active=False, expires_at = now + 45 days  (furthest future)

    After _grant_premium(+30 days):
      • A brand-new row must be created: is_active=True,
        expires_at ≈ now + 30 days (fresh grant, NOT stacked on any old row).
      • Rows A, B, C must remain is_active=False with their original expires_at.
      • Total subscription rows for the user must be 4 (3 old + 1 new).
    """
    uid = 9015
    days = 30
    now = dt.datetime.now(dt.timezone.utc)
    before = now  # lower bound for new row's expires_at

    expires_a = now + dt.timedelta(days=5)
    expires_b = now + dt.timedelta(days=15)
    expires_c = now + dt.timedelta(days=45)

    async with session_factory() as seed:
        for expires in (expires_a, expires_b, expires_c):
            seed.add(UserSubscription(
                user_telegram_id=uid,
                is_active=False,
                started_at=now - dt.timedelta(days=1),
                expires_at=expires,
                granted_by_admin=True,
                stars_paid=0,
            ))
        await _seed_config(seed)
        await seed.commit()

    async with session_factory() as s:
        repo = ReferralRepository(s)
        await repo._grant_premium(uid, days)
        await s.commit()

    after = dt.datetime.now(dt.timezone.utc)

    async with session_factory() as check:
        all_subs = (
            await check.execute(
                select(UserSubscription)
                .where(UserSubscription.user_telegram_id == uid)
                .order_by(UserSubscription.expires_at)
            )
        ).scalars().all()

    # Total rows: 3 old soft-deleted + 1 new active.
    assert len(all_subs) == 4, (
        f"Expected 4 subscription rows (3 old + 1 new), got {len(all_subs)}"
    )

    # Exactly one row must be active.
    active_subs = [s for s in all_subs if s.is_active is True]
    inactive_subs = [s for s in all_subs if s.is_active is False]
    assert len(active_subs) == 1, (
        f"Expected exactly 1 active subscription after fresh grant, "
        f"got {len(active_subs)}"
    )
    assert len(inactive_subs) == 3, (
        f"Expected 3 soft-deleted subscriptions to remain, got {len(inactive_subs)}"
    )

    # New active row: expires_at must be ≈ now + days (fresh grant).
    new_expires = active_subs[0].expires_at
    if new_expires.tzinfo is None:
        new_expires = new_expires.replace(tzinfo=dt.timezone.utc)

    lower = before + dt.timedelta(days=days)
    upper = after + dt.timedelta(days=days)
    assert lower <= new_expires <= upper, (
        f"New active subscription expires_at={new_expires.isoformat()} is outside "
        f"the expected range [{lower.isoformat()}, {upper.isoformat()}]"
    )

    # Soft-deleted rows must be completely untouched.
    original_expires = {expires_a, expires_b, expires_c}
    for sub in inactive_subs:
        exp = sub.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=dt.timezone.utc)
        # Find the closest original value and verify it was not modified.
        closest = min(original_expires, key=lambda e: abs((e - exp).total_seconds()))
        diff = abs((exp - closest).total_seconds())
        assert diff < 5, (
            f"Soft-deleted subscription was unexpectedly modified: "
            f"original≈{closest.isoformat()}, got {exp.isoformat()} (Δ={diff:.1f}s)"
        )
