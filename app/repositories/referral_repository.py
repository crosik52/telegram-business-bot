"""ReferralRepository — full referral system data access layer."""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import case, func, select, update, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.referral import (
    DEFAULT_LEVELS,
    DEFAULT_MILESTONES,
    Referral,
    ReferralConfig,
    ReferralRewardLog,
)
from app.models.subscription import UserSubscription

logger = logging.getLogger(__name__)


def _level_for(count: int, levels: list) -> dict:
    """Return the level dict for a given activated-referral count."""
    matched = levels[0]
    for lv in levels:
        if count >= lv["min"]:
            matched = lv
    return matched


def _next_level(count: int, levels: list) -> dict | None:
    """Return the next level after current, or None if already max."""
    current = _level_for(count, levels)
    idx = next((i for i, lv in enumerate(levels) if lv["name"] == current["name"]), 0)
    if idx + 1 < len(levels):
        return levels[idx + 1]
    return None


def _next_milestone(count: int, milestones: list) -> dict | None:
    """Return the next unclaimed milestone, or None."""
    for m in sorted(milestones, key=lambda x: x["count"]):
        if m["count"] > count:
            return m
    return None


class ReferralRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    # ── Config ────────────────────────────────────────────────────────────────

    async def get_config(self) -> ReferralConfig:
        result = await self._db.execute(select(ReferralConfig).limit(1))
        cfg = result.scalar_one_or_none()
        if cfg is None:
            cfg = ReferralConfig(
                milestones=list(DEFAULT_MILESTONES),
                levels=list(DEFAULT_LEVELS),
            )
            self._db.add(cfg)
            await self._db.flush()
        return cfg

    async def update_config(self, **fields) -> ReferralConfig:
        cfg = await self.get_config()
        allowed = {
            "is_enabled", "referrer_reward_days", "referee_reward_days",
            "min_account_age_days", "max_referrals_per_day",
            "milestones", "levels",
        }
        for k, v in fields.items():
            if k in allowed:
                setattr(cfg, k, v)
        cfg.updated_at = dt.datetime.now(dt.timezone.utc)
        await self._db.flush()
        return cfg

    # ── Referral creation (called from /start deep-link handler) ─────────────

    async def create_referral(
        self,
        referrer_telegram_id: int,
        referred_telegram_id: int,
        referred_first_name: str | None = None,
        referred_username: str | None = None,
    ) -> tuple[Referral | None, str]:
        """Create a pending referral.

        Returns (referral, "ok") or (None, reason_code).
        Fraud checks are applied here.
        """
        cfg = await self.get_config()

        if not cfg.is_enabled:
            return None, "disabled"

        # Self-referral
        if referrer_telegram_id == referred_telegram_id:
            return None, "self_referral"

        # Already has a referrer
        existing = await self._db.execute(
            select(Referral).where(
                Referral.referred_telegram_id == referred_telegram_id,
                Referral.status != "fraud",
            )
        )
        if existing.scalar_one_or_none():
            return None, "already_referred"

        # Circular check: referred_telegram_id must not be referring referrer_telegram_id
        circular = await self._db.execute(
            select(Referral).where(
                Referral.referrer_telegram_id == referred_telegram_id,
                Referral.referred_telegram_id == referrer_telegram_id,
                Referral.status != "fraud",
            )
        )
        if circular.scalar_one_or_none():
            return None, "circular"

        # Daily cap for referrer
        today_start = dt.datetime.now(dt.timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        daily_count = await self._db.execute(
            select(func.count()).select_from(Referral).where(
                Referral.referrer_telegram_id == referrer_telegram_id,
                Referral.created_at >= today_start,
                Referral.status != "fraud",
            )
        )
        if (daily_count.scalar_one() or 0) >= cfg.max_referrals_per_day:
            return None, "daily_cap"

        ref = Referral(
            referrer_telegram_id=referrer_telegram_id,
            referred_telegram_id=referred_telegram_id,
            status="pending",
            referred_first_name=referred_first_name,
            referred_username=referred_username,
        )
        self._db.add(ref)
        await self._db.flush()
        logger.info(
            "Referral created: referrer=%s → referred=%s (id=%s)",
            referrer_telegram_id, referred_telegram_id, ref.id,
        )
        return ref, "ok"

    # ── Activation (called when referred user first opens mini-app with BC) ──

    async def try_activate(
        self, referred_telegram_id: int, has_business_connection: bool
    ) -> tuple[Referral | None, list[dict]]:
        """Try to activate a pending referral for the given user.

        Returns (referral, list_of_rewards_granted).
        If nothing to do, returns (None, []).

        Uses an atomic UPDATE WHERE status='pending' so that two concurrent
        calls for the same referred user can never both activate the referral:
        exactly one UPDATE will match (rowcount=1) and the other sees
        rowcount=0 and returns early.  This guard works for both SQLite and
        PostgreSQL without relying on SELECT … FOR UPDATE row-level locking.
        """
        if not has_business_connection:
            return None, []

        now = dt.datetime.now(dt.timezone.utc)

        # Atomic claim: only one concurrent transaction can flip status from
        # "pending" → "active"; the other will get rowcount == 0 and bail out.
        claim_result = await self._db.execute(
            update(Referral)
            .where(
                Referral.referred_telegram_id == referred_telegram_id,
                Referral.status == "pending",
            )
            .values(status="active", activated_at=now)
            .execution_options(synchronize_session="fetch")
        )
        if claim_result.rowcount == 0:
            return None, []

        # Load the row we just claimed so we can read referrer_id, ref.id, etc.
        ref_result = await self._db.execute(
            select(Referral).where(
                Referral.referred_telegram_id == referred_telegram_id,
                Referral.status == "active",
            )
        )
        ref = ref_result.scalar_one_or_none()
        if ref is None:
            # Should not happen, but be safe.
            return None, []

        cfg = await self.get_config()
        rewards: list[dict] = []

        # ── Referee welcome reward ────────────────────────────────────────────
        if cfg.referee_reward_days > 0:
            await self._grant_premium(referred_telegram_id, cfg.referee_reward_days)
            log = ReferralRewardLog(
                referral_id=ref.id,
                user_telegram_id=referred_telegram_id,
                reward_type="welcome",
                reward_value=str(cfg.referee_reward_days),
                label=f"+{cfg.referee_reward_days} дн. Premium (приветственный бонус)",
            )
            self._db.add(log)
            rewards.append({"user": referred_telegram_id, "type": "welcome", "days": cfg.referee_reward_days})

        # ── Per-activation reward for referrer ───────────────────────────────
        if cfg.referrer_reward_days > 0:
            await self._grant_premium(ref.referrer_telegram_id, cfg.referrer_reward_days)
            log2 = ReferralRewardLog(
                referral_id=ref.id,
                user_telegram_id=ref.referrer_telegram_id,
                reward_type="per_activation",
                reward_value=str(cfg.referrer_reward_days),
                label=f"+{cfg.referrer_reward_days} дн. Premium за приглашение",
            )
            self._db.add(log2)
            rewards.append({"user": ref.referrer_telegram_id, "type": "per_activation", "days": cfg.referrer_reward_days})

        await self._db.flush()
        return ref, rewards

    async def evaluate_and_grant_milestones(
        self,
        referrer_telegram_id: int,
        referral_id: int | None = None,
    ) -> list[dict]:
        """Evaluate and grant any newly-crossed milestone rewards for a referrer.

        MUST be called **after** the activation transaction has been committed
        so that ``_count_active`` reads the fully committed referral count —
        including any concurrent activations that finished at the same time.
        Calling this inside the activation transaction would produce a stale
        count (TOCTOU) and silently skip milestones that were crossed by
        concurrent activations.

        Concurrency safety
        ------------------
        * Uses ``<=`` (not ``==``) so every milestone threshold up to the
          current committed count is evaluated on each call.  If Session A and
          Session B each activate a different referred user and both call this
          method after their respective commits, Session B (which commits second
          and therefore sees the higher count) will attempt to grant all
          milestones ≤ count, while Session A will also attempt them — the
          partial unique index ``uq_milestone_reward_per_user`` rejects the
          duplicate via an IntegrityError caught inside a savepoint, ensuring
          each milestone is granted exactly once.

        Returns a list of reward dicts for newly granted milestones.
        """
        cfg = await self.get_config()
        active_count = await self._count_active(referrer_telegram_id)
        rewards: list[dict] = []

        for milestone in cfg.milestones:
            if milestone["count"] <= active_count:
                try:
                    async with self._db.begin_nested():
                        milestone_log = ReferralRewardLog(
                            referral_id=referral_id,
                            user_telegram_id=referrer_telegram_id,
                            reward_type="milestone",
                            reward_value=str(milestone["count"]),
                            label=milestone["label"],
                        )
                        self._db.add(milestone_log)
                        # flush first — unique index raises IntegrityError here
                        # if a concurrent session already inserted this grant.
                        await self._db.flush()
                        # Only reached when insert succeeded (no duplicate).
                        if milestone["type"] == "premium_days":
                            await self._grant_premium(
                                referrer_telegram_id, int(milestone["value"])
                            )
                    rewards.append({
                        "user": referrer_telegram_id,
                        "type": "milestone",
                        "milestone": milestone,
                    })
                except IntegrityError:
                    logger.info(
                        "Milestone count=%s already granted for referrer=%s "
                        "— concurrent duplicate skipped",
                        milestone["count"],
                        referrer_telegram_id,
                    )

        if rewards:
            await self._db.flush()

        # Mark this referral as milestone-checked regardless of whether any new
        # rewards were granted — so the background sweep won't re-process it.
        # We do this after the milestone loop so that a crash *inside* the loop
        # still leaves milestone_checked=False and the sweep can retry.
        if referral_id is not None:
            await self._db.execute(
                update(Referral)
                .where(Referral.id == referral_id)
                .values(milestone_checked=True)
                .execution_options(synchronize_session="fetch")
            )
            await self._db.flush()

        return rewards

    # ── Unchecked-milestone sweep (called by background loop) ─────────────────

    async def list_unchecked_referral_ids(
        self, limit: int = 50, after_id: int = 0, max_failures: int = 10
    ) -> list[tuple[int, int]]:
        """Return (referral_id, referrer_telegram_id) pairs for active referrals
        whose milestones have not yet been evaluated.

        Intended for the background sweep loop — each row is processed in a
        separate session so that one failure does not block others.

        ``after_id`` enables keyset pagination: pass the highest ``referral_id``
        seen in the previous batch so that each batch advances monotonically and
        a referral that fails (and therefore keeps ``milestone_checked=False``)
        is not re-selected within the same sweep cycle.

        ``max_failures`` caps the retry count: rows whose ``evaluation_failures``
        column has reached this threshold are excluded from the result.  They
        will no longer be retried by the sweep until an operator resets the
        counter.  This prevents a persistently-broken referral from causing
        infinite retries and log noise every cycle.
        """
        result = await self._db.execute(
            select(Referral.id, Referral.referrer_telegram_id)
            .where(
                Referral.status == "active",
                Referral.milestone_checked.is_(False),
                Referral.id > after_id,
                Referral.evaluation_failures < max_failures,
            )
            .order_by(Referral.id)
            .limit(limit)
        )
        return result.all()

    async def increment_evaluation_failures(self, referral_id: int) -> None:
        """Atomically increment the failure counter for a referral.

        Called by the background sweep when ``evaluate_and_grant_milestones``
        raises an exception.  Once the counter reaches the configured threshold
        (``max_failures`` in ``list_unchecked_referral_ids``), the sweep stops
        selecting that referral, preventing infinite retries for
        permanently-broken rows.
        """
        await self._db.execute(
            update(Referral)
            .where(Referral.id == referral_id)
            .values(evaluation_failures=Referral.evaluation_failures + 1)
            .execution_options(synchronize_session="fetch")
        )
        await self._db.flush()

    # ── User-facing stats ────────────────────────────────────────────────────

    async def get_user_stats(self, user_telegram_id: int, bot_username: str = "") -> dict:
        """Full stats for the referral tab in the mini-app."""
        cfg = await self.get_config()

        total_q = await self._db.execute(
            select(func.count()).select_from(Referral).where(
                Referral.referrer_telegram_id == user_telegram_id,
                Referral.status != "fraud",
            )
        )
        total_invited = total_q.scalar_one() or 0

        active_count = await self._count_active(user_telegram_id)
        pending_count = total_invited - active_count

        # Reward history for this user
        rewards_q = await self._db.execute(
            select(ReferralRewardLog)
            .where(ReferralRewardLog.user_telegram_id == user_telegram_id)
            .order_by(ReferralRewardLog.granted_at.desc())
            .limit(20)
        )
        reward_logs = rewards_q.scalars().all()

        # Recent referrals
        recent_q = await self._db.execute(
            select(Referral)
            .where(
                Referral.referrer_telegram_id == user_telegram_id,
                Referral.status != "fraud",
            )
            .order_by(Referral.created_at.desc())
            .limit(5)
        )
        recent = recent_q.scalars().all()

        # Level & progress
        level = _level_for(active_count, cfg.levels)
        next_lv = _next_level(active_count, cfg.levels)
        next_ms = _next_milestone(active_count, cfg.milestones)

        # Referral link
        ref_link = (
            f"https://t.me/{bot_username}?start=ref_{user_telegram_id}"
            if bot_username else f"ref_{user_telegram_id}"
        )

        return {
            "enabled": cfg.is_enabled,
            "ref_link": ref_link,
            "total_invited": total_invited,
            "active_count": active_count,
            "pending_count": pending_count,
            "level": level,
            "next_level": next_lv,
            "next_milestone": next_ms,
            "referrer_reward_days": cfg.referrer_reward_days,
            "referee_reward_days": cfg.referee_reward_days,
            "reward_logs": [
                {
                    "reward_type": r.reward_type,
                    "reward_value": r.reward_value,
                    "label": r.label,
                    "granted_at": r.granted_at.isoformat(),
                }
                for r in reward_logs
            ],
            "recent_referrals": [
                {
                    "referred_telegram_id": r.referred_telegram_id,
                    "referred_first_name": r.referred_first_name,
                    "referred_username": r.referred_username,
                    "status": r.status,
                    "created_at": r.created_at.isoformat(),
                    "activated_at": r.activated_at.isoformat() if r.activated_at else None,
                }
                for r in recent
            ],
            "milestones": cfg.milestones,
        }

    # ── Admin list ───────────────────────────────────────────────────────────

    async def admin_list(
        self,
        page: int = 1,
        page_size: int = 30,
        status_filter: str | None = None,
        search_id: int | None = None,
    ) -> dict:
        q = select(Referral)
        if status_filter:
            q = q.where(Referral.status == status_filter)
        if search_id:
            q = q.where(
                (Referral.referrer_telegram_id == search_id) |
                (Referral.referred_telegram_id == search_id)
            )

        count_q = select(func.count()).select_from(q.subquery())
        total = (await self._db.execute(count_q)).scalar_one() or 0

        rows_q = (
            q.order_by(Referral.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await self._db.execute(rows_q)).scalars().all()

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "referrals": [
                {
                    "id": r.id,
                    "referrer_telegram_id": r.referrer_telegram_id,
                    "referred_telegram_id": r.referred_telegram_id,
                    "referred_first_name": r.referred_first_name,
                    "referred_username": r.referred_username,
                    "status": r.status,
                    "fraud_reason": r.fraud_reason,
                    "created_at": r.created_at.isoformat(),
                    "activated_at": r.activated_at.isoformat() if r.activated_at else None,
                }
                for r in rows
            ],
        }

    async def admin_top_referrers(self, limit: int = 20) -> list[dict]:
        q = await self._db.execute(
            select(
                Referral.referrer_telegram_id,
                func.count().label("total"),
                func.sum(
                    case((Referral.status == "active", 1), else_=0)
                ).label("active"),
            )
            .where(Referral.status != "fraud")
            .group_by(Referral.referrer_telegram_id)
            .order_by(func.count().desc())
            .limit(limit)
        )
        rows = q.all()
        return [
            {
                "referrer_telegram_id": row.referrer_telegram_id,
                "total": row.total,
                "active": row.active or 0,
            }
            for row in rows
        ]

    async def admin_stats(self) -> dict:
        """Aggregated stats for admin analytics."""
        cfg = await self.get_config()
        now = dt.datetime.now(dt.timezone.utc)

        total_q = await self._db.execute(
            select(func.count()).select_from(Referral).where(Referral.status != "fraud")
        )
        total = total_q.scalar_one() or 0

        active_q = await self._db.execute(
            select(func.count()).select_from(Referral).where(Referral.status == "active")
        )
        active = active_q.scalar_one() or 0

        pending_q = await self._db.execute(
            select(func.count()).select_from(Referral).where(Referral.status == "pending")
        )
        pending = pending_q.scalar_one() or 0

        fraud_q = await self._db.execute(
            select(func.count()).select_from(Referral).where(Referral.status == "fraud")
        )
        fraud = fraud_q.scalar_one() or 0

        # Daily for last 7 days
        daily = []
        for i in range(6, -1, -1):
            day_start = (now - dt.timedelta(days=i)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            day_end = day_start + dt.timedelta(days=1)
            cnt_q = await self._db.execute(
                select(func.count()).select_from(Referral).where(
                    Referral.created_at >= day_start,
                    Referral.created_at < day_end,
                    Referral.status != "fraud",
                )
            )
            daily.append({
                "date": day_start.strftime("%d.%m"),
                "count": cnt_q.scalar_one() or 0,
            })

        top = await self.admin_top_referrers(10)

        activation_rate = round(active / total * 100, 1) if total > 0 else 0

        return {
            "enabled": cfg.is_enabled,
            "total": total,
            "active": active,
            "pending": pending,
            "fraud": fraud,
            "activation_rate": activation_rate,
            "daily": daily,
            "top_referrers": top,
        }

    # ── Admin manual actions ─────────────────────────────────────────────────

    async def admin_set_status(
        self, referral_id: int, status: str, reason: str = ""
    ) -> tuple[bool, "Referral | None"]:
        """Set status of a referral to 'active', 'pending', or 'fraud'.

        Returns ``(True, referral)`` on success or ``(False, None)`` when the
        referral does not exist.  The referral object is returned so callers can
        inspect ``referrer_telegram_id`` for subsequent milestone evaluation
        (Phase 2) without an extra query.
        """
        result = await self._db.execute(
            select(Referral).where(Referral.id == referral_id)
        )
        ref = result.scalar_one_or_none()
        if ref is None:
            return False, None
        ref.status = status
        if status == "fraud":
            ref.fraud_reason = reason or "admin"
        elif status == "active" and ref.activated_at is None:
            ref.activated_at = dt.datetime.now(dt.timezone.utc)
        await self._db.flush()
        return True, ref

    async def admin_grant_per_activation_rewards(
        self, ref: "Referral"
    ) -> list[dict]:
        """Grant per-activation (referrer) and welcome (referee) rewards for an
        admin-activated referral.

        This mirrors the reward logic inside ``try_activate`` so that referrals
        activated through the admin panel receive the same Premium grants as
        those activated through the normal mini-app flow.

        Idempotent: before granting each reward type this method checks whether
        a ``ReferralRewardLog`` row with the same ``referral_id`` and
        ``reward_type`` already exists.  If it does the grant is skipped, so
        calling this twice (e.g. an admin re-saves an already-active row) can
        never double-grant either reward.

        Must be called **after** the Phase 1 commit so the referral status is
        durably 'active' before rewards are written.

        Returns a list of reward dicts for the rewards that were actually granted.
        """
        cfg = await self.get_config()
        rewards: list[dict] = []

        # Which reward types are already logged for this referral?
        existing_q = await self._db.execute(
            select(ReferralRewardLog.reward_type).where(
                ReferralRewardLog.referral_id == ref.id,
                ReferralRewardLog.reward_type.in_(["welcome", "per_activation"]),
            )
        )
        already_granted = {row[0] for row in existing_q.all()}

        # ── Referee welcome reward ────────────────────────────────────────────
        if cfg.referee_reward_days > 0 and "welcome" not in already_granted:
            await self._grant_premium(ref.referred_telegram_id, cfg.referee_reward_days)
            self._db.add(ReferralRewardLog(
                referral_id=ref.id,
                user_telegram_id=ref.referred_telegram_id,
                reward_type="welcome",
                reward_value=str(cfg.referee_reward_days),
                label=f"+{cfg.referee_reward_days} дн. Premium (приветственный бонус)",
            ))
            rewards.append({
                "user": ref.referred_telegram_id,
                "type": "welcome",
                "days": cfg.referee_reward_days,
            })

        # ── Per-activation reward for referrer ───────────────────────────────
        if cfg.referrer_reward_days > 0 and "per_activation" not in already_granted:
            await self._grant_premium(ref.referrer_telegram_id, cfg.referrer_reward_days)
            self._db.add(ReferralRewardLog(
                referral_id=ref.id,
                user_telegram_id=ref.referrer_telegram_id,
                reward_type="per_activation",
                reward_value=str(cfg.referrer_reward_days),
                label=f"+{cfg.referrer_reward_days} дн. Premium за приглашение",
            ))
            rewards.append({
                "user": ref.referrer_telegram_id,
                "type": "per_activation",
                "days": cfg.referrer_reward_days,
            })

        if rewards:
            await self._db.flush()

        return rewards

    async def admin_grant_bonus(
        self, user_telegram_id: int, reward_type: str, reward_value: str, label: str
    ) -> dict:
        """Manually grant a reward (premium days or badge) to a user."""
        if reward_type == "premium_days":
            await self._grant_premium(user_telegram_id, int(reward_value))
        log = ReferralRewardLog(
            referral_id=None,
            user_telegram_id=user_telegram_id,
            reward_type=reward_type,
            reward_value=reward_value,
            label=label or f"Ручное начисление: {reward_value}",
        )
        self._db.add(log)
        await self._db.flush()
        return {"ok": True}

    # ── Helpers ──────────────────────────────────────────────────────────────

    async def _count_active(self, referrer_telegram_id: int) -> int:
        q = await self._db.execute(
            select(func.count()).select_from(Referral).where(
                Referral.referrer_telegram_id == referrer_telegram_id,
                Referral.status == "active",
            )
        )
        return q.scalar_one() or 0

    async def _grant_premium(self, user_telegram_id: int, days: int) -> None:
        """Extend or create an active subscription for the user.

        Concurrency safety
        ------------------
        The UPDATE uses a **DB-side arithmetic expression** rather than a
        Python-side mutation of a previously-read value.  This prevents the
        classic TOCTOU race where two concurrent sessions both read the same
        ``expires_at`` before either commits:

        * Buggy (SELECT → Python mutation → flush):
            Session A reads T, Session B reads T (stale),
            A commits T+7, B commits T+14 ← B's extension is lost.

        * Safe (SELECT id → atomic UPDATE SET expires_at = expires_at + Δ):
            A commits: DB T → T+7.
            B commits after A: DB reads T+7, writes T+21 ← correct.

        The DB-side expression is chosen per-dialect:
            * SQLite  : ``datetime(expires_at, '+N days')``
            * Others  : ``expires_at + timedelta(days=N)``   (PostgreSQL native)
        """
        now = dt.datetime.now(dt.timezone.utc)
        delta = dt.timedelta(days=days)

        # Find the ID of the most-future active subscription to extend.
        sub_id_q = await self._db.execute(
            select(UserSubscription.id).where(
                UserSubscription.user_telegram_id == user_telegram_id,
                UserSubscription.is_active.is_(True),
                UserSubscription.expires_at > now,
            ).order_by(UserSubscription.expires_at.desc()).limit(1)
        )
        sub_id = sub_id_q.scalar_one_or_none()

        if sub_id is not None:
            # Atomic UPDATE: the expression is evaluated server-side so that a
            # concurrent session that commits between our SELECT and this UPDATE
            # will have its change included rather than overwritten.
            dialect_name = self._db.sync_session.get_bind().dialect.name
            if dialect_name == "sqlite":
                new_expires = func.datetime(
                    UserSubscription.expires_at, f"+{days} days"
                )
            else:
                new_expires = UserSubscription.expires_at + delta

            await self._db.execute(
                update(UserSubscription)
                .where(UserSubscription.id == sub_id)
                .values(expires_at=new_expires)
                .execution_options(synchronize_session=False)
            )
        else:
            # No active subscription exists — create a fresh one.
            sub = UserSubscription(
                user_telegram_id=user_telegram_id,
                is_active=True,
                started_at=now,
                expires_at=now + delta,
                granted_by_admin=True,
                stars_paid=0,
            )
            self._db.add(sub)

        await self._db.flush()
