"""RelationshipRepository — bonds between mutually-connected bot users."""
from __future__ import annotations

import datetime as dt
import json
import logging

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_connection import BusinessConnection
from app.models.relationship import (
    ANNIVERSARY_MONTH_BONUS,
    ANNIVERSARY_YEAR_BONUS,
    COUPLE_QUESTS,
    GIFT_COOLDOWN_H,
    GIFT_TYPES,
    LEVEL_PERK_STEP,
    LEVEL_TITLES,
    MAX_REL_LEVEL,
    REL_XP_BONUS,
    REQUEST_COST,
    STREAK_MILESTONES,
    STREAK_XP_BONUS_CAP,
    STREAK_XP_BONUS_PER_DAY,
    TIER_ORDER,
    UPGRADE_COSTS,
    UPGRADE_MIN_GIFTS,
    UPGRADE_MIN_LEVEL,
    XP_PER_LEVEL,
    Relationship,
)
from app.models.wallet import UserWallet

logger = logging.getLogger(__name__)


def _level_from_xp(xp: int) -> int:
    return min(MAX_REL_LEVEL, xp // XP_PER_LEVEL + 1)


def _title_for(rel_type: str, level: int) -> str:
    """Highest bracket title whose min_level <= level."""
    brackets = LEVEL_TITLES.get(rel_type, LEVEL_TITLES["friends"])
    title = brackets[0][1]
    for min_lvl, t in brackets:
        if level >= min_lvl:
            title = t
    return title


def _week_key(now: dt.datetime) -> str:
    iso = now.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _load_meta(rel: Relationship) -> dict:
    if not rel.meta:
        return {}
    try:
        return json.loads(rel.meta)
    except Exception:
        return {}


def _save_meta(rel: Relationship, meta: dict) -> None:
    rel.meta = json.dumps(meta, ensure_ascii=False)


class RelationshipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Pair helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _pair(a: int, b: int) -> tuple[int, int]:
        return (min(a, b), max(a, b))

    def _last_gift(self, rel: Relationship, user_id: int) -> dt.datetime | None:
        return rel.last_gift_a if user_id == rel.user_a_id else rel.last_gift_b

    def _set_last_gift(
        self, rel: Relationship, user_id: int, ts: dt.datetime
    ) -> None:
        if user_id == rel.user_a_id:
            rel.last_gift_a = ts
        else:
            rel.last_gift_b = ts

    # ── Queries ───────────────────────────────────────────────────────────────

    async def get_between(
        self, user1: int, user2: int, *, lock: bool = False
    ) -> Relationship | None:
        """Active or pending relationship between two users.

        With lock=True the row is selected FOR UPDATE so all subsequent
        validation (cooldowns, meta counters, claims) happens under the lock —
        required for every economy mutation to avoid double-credits.
        """
        a, b = self._pair(user1, user2)
        q = select(Relationship).where(
            Relationship.user_a_id == a,
            Relationship.user_b_id == b,
            Relationship.status.in_(["pending", "active"]),
        )
        if lock:
            q = q.with_for_update()
        return (await self._session.execute(q)).scalar_one_or_none()

    async def _get_wallets_ordered(
        self, uid1: int, uid2: int
    ) -> tuple[UserWallet, UserWallet]:
        """Lock both wallets in normalized id order to prevent deadlocks,
        returning them as (wallet_for_uid1, wallet_for_uid2)."""
        first, second = sorted((uid1, uid2))
        w_first  = await self._get_wallet(first,  lock=True)
        w_second = await self._get_wallet(second, lock=True)
        return (w_first, w_second) if uid1 == first else (w_second, w_first)

    async def get_for_user(self, user_id: int) -> list[Relationship]:
        """All active + pending relationships for a user."""
        return list(
            (
                await self._session.execute(
                    select(Relationship).where(
                        or_(
                            Relationship.user_a_id == user_id,
                            Relationship.user_b_id == user_id,
                        ),
                        Relationship.status.in_(["pending", "active"]),
                    )
                )
            )
            .scalars()
            .all()
        )

    async def count_marriages(self, user_id: int) -> int:
        """Count active marriages where the partner still has an enabled
        BusinessConnection. A disconnected partner must not keep generating
        the marriage daily bonus."""
        rels = await self.get_for_user(user_id)
        active_marriages = [
            r for r in rels if r.rel_type == "married" and r.status == "active"
        ]
        if not active_marriages:
            return 0
        partner_ids = [
            r.user_b_id if r.user_a_id == user_id else r.user_a_id
            for r in active_marriages
        ]
        connected: set[int] = {
            r[0]
            for r in (
                await self._session.execute(
                    select(BusinessConnection.user_telegram_id).where(
                        BusinessConnection.user_telegram_id.in_(partner_ids),
                        BusinessConnection.is_enabled.is_(True),
                    )
                )
            ).all()
        }
        return sum(1 for pid in partner_ids if pid in connected)

    async def get_active_tier(self, user1: int, user2: int) -> str | None:
        """Return rel_type of the active relationship between user1 and user2, or None.

        Returns None if either party no longer has an active BusinessConnection
        (i.e. the partner has disconnected the bot), so the XP bonus silently
        drops to 1.0 rather than persisting after a disconnect.
        """
        for uid in (user1, user2):
            connected = (
                await self._session.execute(
                    select(BusinessConnection.id).where(
                        BusinessConnection.user_telegram_id == uid,
                        BusinessConnection.is_enabled.is_(True),
                    ).limit(1)
                )
            ).scalar_one_or_none()
            if connected is None:
                return None

        a, b = self._pair(user1, user2)
        return (
            await self._session.execute(
                select(Relationship.rel_type).where(
                    Relationship.user_a_id == a,
                    Relationship.user_b_id == b,
                    Relationship.status == "active",
                )
            )
        ).scalar_one_or_none()

    async def get_active_bond(
        self, user1: int, user2: int
    ) -> tuple[str | None, int]:
        """(rel_type, level) of the active relationship, or (None, 1).

        Same BusinessConnection gating as get_active_tier; level feeds the
        per-level pet-XP perk in rel_xp_multiplier().
        """
        for uid in (user1, user2):
            connected = (
                await self._session.execute(
                    select(BusinessConnection.id).where(
                        BusinessConnection.user_telegram_id == uid,
                        BusinessConnection.is_enabled.is_(True),
                    ).limit(1)
                )
            ).scalar_one_or_none()
            if connected is None:
                return None, 1

        a, b = self._pair(user1, user2)
        row = (
            await self._session.execute(
                select(Relationship.rel_type, Relationship.level).where(
                    Relationship.user_a_id == a,
                    Relationship.user_b_id == b,
                    Relationship.status == "active",
                )
            )
        ).first()
        return (row[0], row[1] or 1) if row else (None, 1)

    def rel_xp_multiplier(self, tier: str | None, level: int = 1) -> float:
        """Pet XP multiplier for the given relationship tier + level perk."""
        if not tier:
            return 1.0
        base = REL_XP_BONUS.get(tier, 1.0)
        return base + max(0, level - 1) * LEVEL_PERK_STEP

    @staticmethod
    def _next_anniversary(accepted_at: dt.datetime, now: dt.datetime) -> tuple[int, str] | None:
        """(days_until, label) of the next month/year anniversary, or None."""
        if accepted_at.tzinfo is None:
            accepted_at = accepted_at.replace(tzinfo=dt.timezone.utc)
        months = (now.year - accepted_at.year) * 12 + (now.month - accepted_at.month)
        for add in (0, 1, 2):
            m = months + add
            if m <= 0:
                continue
            y, mo = divmod(accepted_at.month - 1 + m, 12)
            try:
                target = accepted_at.replace(year=accepted_at.year + y, month=mo + 1)
            except ValueError:  # e.g. Jan 31 → Feb: clamp to end of month
                target = accepted_at.replace(year=accepted_at.year + y, month=mo + 2, day=1) - dt.timedelta(days=1)
            if target.date() >= now.date():
                days = (target.date() - now.date()).days
                label = f"{m // 12} г." if m % 12 == 0 else f"{m} мес."
                return days, label
        return None

    def to_dict(self, rel: Relationship, viewer_id: int) -> dict:
        """Serialise a Relationship for API responses."""
        partner_id = (
            rel.user_b_id if viewer_id == rel.user_a_id else rel.user_a_id
        )
        last_gift = self._last_gift(rel, viewer_id)
        now = dt.datetime.now(dt.timezone.utc)
        if last_gift is not None and last_gift.tzinfo is None:
            # SQLite/aiosqlite may strip timezone info; treat as UTC.
            last_gift = last_gift.replace(tzinfo=dt.timezone.utc)
        gift_ready = last_gift is None or (
            now - last_gift
        ).total_seconds() >= GIFT_COOLDOWN_H * 3600
        xp_in_level = rel.xp % XP_PER_LEVEL
        category = getattr(rel, "category", "romantic") or "romantic"
        meta = _load_meta(rel)
        _min_gifts = UPGRADE_MIN_GIFTS.get(rel.rel_type, 0)
        _by_a = meta.get("gifts_by_a", 0)
        _by_b = meta.get("gifts_by_b", 0)
        _both_gifted_enough = (
            _min_gifts == 0
            or (_by_a >= _min_gifts and _by_b >= _min_gifts)
        )
        can_upgrade = (
            rel.status == "active"
            and rel.rel_type != "married"
            and rel.level >= UPGRADE_MIN_LEVEL.get(rel.rel_type, 999)
            and category == "romantic"
            and _both_gifted_enough
        )
        streak = meta.get("streak", {})
        next_anniv = (
            self._next_anniversary(rel.accepted_at, now)
            if rel.accepted_at and rel.status == "active" else None
        )
        # streak XP bonus currently in effect
        streak_bonus = min(
            streak.get("days", 0) * STREAK_XP_BONUS_PER_DAY, STREAK_XP_BONUS_CAP
        )
        # 24-hour postcard cooldown tracked per user side in meta
        _postcard_key = "last_postcard_a" if viewer_id == rel.user_a_id else "last_postcard_b"
        _last_postcard_raw = meta.get(_postcard_key)
        if _last_postcard_raw:
            try:
                _lp = dt.datetime.fromisoformat(_last_postcard_raw)
                if _lp.tzinfo is None:
                    _lp = _lp.replace(tzinfo=dt.timezone.utc)
                postcard_ready = (now - _lp).total_seconds() >= 86400
            except Exception:
                postcard_ready = True
        else:
            postcard_ready = True

        return {
            "id":           rel.id,
            "partner_id":   partner_id,
            "rel_type":     rel.rel_type,
            "category":     category,
            "level":        rel.level,
            "max_level":    MAX_REL_LEVEL,
            "title":        _title_for(rel.rel_type, rel.level),
            "xp":           rel.xp,
            "xp_in_level":  xp_in_level,
            "xp_pct":       round(xp_in_level / XP_PER_LEVEL * 100),
            "status":       rel.status,
            "initiator_id": rel.initiator_id,
            "is_initiator": rel.initiator_id == viewer_id,
            "gift_ready":   gift_ready,
            "postcard_ready": postcard_ready,
            "can_upgrade":  can_upgrade,
            "upgrade_cost": UPGRADE_COSTS.get(rel.rel_type, 0),
            "accepted_at":  (
                rel.accepted_at.isoformat() if rel.accepted_at else None
            ),
            "streak_days":     streak.get("days", 0),
            "streak_best":     streak.get("best", 0),
            "streak_bonus_pct": round(streak_bonus * 100),
            "total_gifts":     meta.get("totals", {}).get("gifts", 0),
            "total_spent":     meta.get("totals", {}).get("spent", 0),
            "total_postcards": meta.get("totals", {}).get("postcards", 0),
            "next_anniv_days": next_anniv[0] if next_anniv else None,
            "next_anniv_label": next_anniv[1] if next_anniv else None,
        }

    # ── Mutations ─────────────────────────────────────────────────────────────

    async def send_request(
        self,
        requester_id: int,
        addressee_id: int,
        category: str = "romantic",
    ) -> Relationship:
        if requester_id == addressee_id:
            raise ValueError("cannot_self_request")
        existing = await self.get_between(requester_id, addressee_id)
        if existing:
            raise ValueError(
                "already_related"
                if existing.status == "active"
                else "request_pending"
            )

        # Validate category
        if category not in ("friendship", "romantic"):
            category = "romantic"

        wallet = await self._get_wallet(requester_id, lock=True)
        if wallet.balance < REQUEST_COST:
            raise ValueError("insufficient_funds")
        wallet.balance -= REQUEST_COST

        a, b = self._pair(requester_id, addressee_id)

        # The UniqueConstraint on (user_a_id, user_b_id) allows only one row
        # per pair ever.  If a previous request was declined or broken, reuse
        # that row (UPDATE) instead of attempting a new INSERT that would
        # violate the constraint.
        broken = (
            await self._session.execute(
                select(Relationship).where(
                    Relationship.user_a_id == a,
                    Relationship.user_b_id == b,
                    Relationship.status == "broken",
                )
            )
        ).scalar_one_or_none()

        if broken:
            broken.initiator_id = requester_id
            broken.rel_type     = "friends"
            broken.category     = category
            broken.level        = 1
            broken.xp           = 0
            broken.status       = "pending"
            broken.accepted_at  = None
            broken.last_gift_a  = None
            broken.last_gift_b  = None
            broken.meta         = None  # old streak/quest/anniversary state must not leak
            broken.created_at   = dt.datetime.now(dt.timezone.utc)
            await self._session.flush()
            return broken

        rel = Relationship(
            user_a_id=a,
            user_b_id=b,
            initiator_id=requester_id,
            rel_type="friends",
            category=category,
            level=1,
            xp=0,
            status="pending",
            created_at=dt.datetime.now(dt.timezone.utc),
        )
        self._session.add(rel)
        await self._session.flush()
        return rel

    async def respond(
        self, viewer_id: int, partner_id: int, accept: bool
    ) -> Relationship:
        rel = await self.get_between(viewer_id, partner_id)
        if not rel or rel.status != "pending":
            raise ValueError("no_pending_request")
        if rel.initiator_id == viewer_id:
            raise ValueError("cannot_respond_own_request")

        if accept:
            rel.status   = "active"
            rel.accepted_at = dt.datetime.now(dt.timezone.utc)
        else:
            rel.status = "broken"

        await self._session.flush()
        return rel

    async def cancel_request(self, user_id: int, partner_id: int) -> None:
        """Cancel own pending request and refund coins."""
        rel = await self.get_between(user_id, partner_id)
        if not rel or rel.status != "pending" or rel.initiator_id != user_id:
            raise ValueError("no_own_pending_request")
        wallet = await self._get_wallet(user_id, lock=True)
        wallet.balance += REQUEST_COST
        rel.status = "broken"
        await self._session.flush()

    async def gift(
        self, sender_id: int, partner_id: int, gift_id: str = "rose"
    ) -> dict:
        """Send a gift from the catalogue.  Cost/XP are ALWAYS taken from
        GIFT_TYPES server-side — the client only picks the id.

        Also advances the couple streak (a streak day = both partners gifted
        on the same UTC date), weekly quest counters, and lifetime totals.

        Atomicity guarantee: sender debit, partner credit, and any streak
        milestone bonuses all happen in one session transaction.
        """
        gift_def = GIFT_TYPES.get(gift_id)
        if gift_def is None:
            raise ValueError("unknown_gift")

        # Lock the relationship row first: all cooldown/streak/meta validation
        # must happen under this lock to prevent concurrent double-gifts.
        rel = await self.get_between(sender_id, partner_id, lock=True)
        if not rel or rel.status != "active":
            raise ValueError("not_related")

        # tier gate for premium gifts
        if TIER_ORDER.index(rel.rel_type) < TIER_ORDER.index(gift_def["min_tier"]):
            raise ValueError("gift_tier_locked")

        now  = dt.datetime.now(dt.timezone.utc)
        last = self._last_gift(rel, sender_id)
        if last:
            # SQLite/aiosqlite may return naive datetimes; normalise to UTC.
            if last.tzinfo is None:
                last = last.replace(tzinfo=dt.timezone.utc)
            if (now - last).total_seconds() < GIFT_COOLDOWN_H * 3600:
                secs = int(GIFT_COOLDOWN_H * 3600 - (now - last).total_seconds())
                raise ValueError(f"gift_cooldown:{secs}")

        cost, to_partner = gift_def["cost"], gift_def["to_partner"]

        # Wallets locked in normalized id order (deadlock-safe for reciprocal gifts)
        sender_w, partner_w = await self._get_wallets_ordered(sender_id, partner_id)
        if sender_w.balance < cost:
            raise ValueError("insufficient_funds")
        sender_w.balance -= cost
        partner_w.balance += to_partner

        # ── meta: streak / weekly counters / totals ──────────────────────────
        meta   = _load_meta(rel)
        today  = now.date().isoformat()
        wk     = _week_key(now)
        week   = meta.get("week", {})
        if week.get("key") != wk:
            week = {"key": wk, "gifts": 0, "mutual": 0, "spent": 0, "claimed": []}
        week["gifts"] = week.get("gifts", 0) + 1
        week["spent"] = week.get("spent", 0) + cost

        side_key  = "gift_date_a" if sender_id == rel.user_a_id else "gift_date_b"
        other_key = "gift_date_b" if sender_id == rel.user_a_id else "gift_date_a"
        meta[side_key] = today

        streak = meta.get("streak", {"days": 0, "best": 0, "last_mutual": None, "milestones": []})
        streak_advanced = False
        milestone_coins = 0
        if meta.get(other_key) == today and streak.get("last_mutual") != today:
            # both partners gifted today → advance (or reset-and-start) streak
            prev = streak.get("last_mutual")
            if prev == (now.date() - dt.timedelta(days=1)).isoformat():
                streak["days"] = streak.get("days", 0) + 1
            else:
                streak["days"] = 1
            streak["last_mutual"] = today
            streak["best"] = max(streak.get("best", 0), streak["days"])
            streak_advanced = True
            week["mutual"] = week.get("mutual", 0) + 1
            # milestone bonus — once per milestone per streak run
            hit = streak["days"]
            done: list = streak.get("milestones", [])
            if hit in STREAK_MILESTONES and hit not in done:
                milestone_coins = STREAK_MILESTONES[hit]
                sender_w.balance  += milestone_coins
                partner_w.balance += milestone_coins
                done.append(hit)
                streak["milestones"] = done
        elif streak.get("last_mutual") and streak["last_mutual"] < (
            now.date() - dt.timedelta(days=1)
        ).isoformat():
            # streak broken by a gap ≥ 2 days
            streak["days"] = 0
            streak["milestones"] = []

        totals = meta.get("totals", {"gifts": 0, "spent": 0})
        totals["gifts"] = totals.get("gifts", 0) + 1
        totals["spent"] = totals.get("spent", 0) + cost

        # Per-side gift counters used by upgrade_tier() to ensure both
        # partners participated before allowing a tier upgrade.
        gifts_side_key = "gifts_by_a" if sender_id == rel.user_a_id else "gifts_by_b"
        meta[gifts_side_key] = meta.get(gifts_side_key, 0) + 1

        meta["streak"], meta["week"], meta["totals"] = streak, week, totals
        _save_meta(rel, meta)

        # ── XP with streak bonus ──────────────────────────────────────────────
        bonus = min(streak.get("days", 0) * STREAK_XP_BONUS_PER_DAY, STREAK_XP_BONUS_CAP)
        xp_gain = round(gift_def["xp"] * (1 + bonus))
        rel.xp    += xp_gain
        rel.level  = _level_from_xp(rel.xp)
        self._set_last_gift(rel, sender_id, now)

        await self._session.flush()
        return {
            "gift":             {"id": gift_id, **gift_def},
            "xp_gained":        xp_gain,
            "new_xp":           rel.xp,
            "new_level":        rel.level,
            "new_title":        _title_for(rel.rel_type, rel.level),
            "new_balance":      sender_w.balance,
            "partner_received": to_partner,
            "streak_days":      streak.get("days", 0),
            "streak_advanced":  streak_advanced,
            "milestone_coins":  milestone_coins,
        }

    # ── Couple quests ─────────────────────────────────────────────────────────

    def quests_for(self, rel: Relationship) -> list[dict]:
        """Weekly quest list with live progress from meta counters."""
        now  = dt.datetime.now(dt.timezone.utc)
        meta = _load_meta(rel)
        week = meta.get("week", {})
        if week.get("key") != _week_key(now):
            week = {"gifts": 0, "mutual": 0, "spent": 0, "claimed": []}
        counter_map = {"gifts_week": week.get("gifts", 0),
                       "mutual_week": week.get("mutual", 0),
                       "spent_week": week.get("spent", 0)}
        out = []
        for qid, q in COUPLE_QUESTS.items():
            progress = min(counter_map.get(q["counter"], 0), q["target"])
            out.append({
                "id": qid, "title": q["title"], "desc": q["desc"],
                "target": q["target"], "progress": progress,
                "reward_coins": q["reward_coins"], "reward_xp": q["reward_xp"],
                "done": progress >= q["target"],
                "claimed": qid in week.get("claimed", []),
            })
        return out

    async def claim_quest(
        self, user_id: int, partner_id: int, quest_id: str
    ) -> dict:
        """Claim a completed weekly quest — rewards BOTH partners."""
        q = COUPLE_QUESTS.get(quest_id)
        if q is None:
            raise ValueError("unknown_quest")
        # Relationship lock BEFORE reading claim state — prevents double-claims.
        rel = await self.get_between(user_id, partner_id, lock=True)
        if not rel or rel.status != "active":
            raise ValueError("not_related")

        now  = dt.datetime.now(dt.timezone.utc)
        meta = _load_meta(rel)
        week = meta.get("week", {})
        if week.get("key") != _week_key(now):
            raise ValueError("quest_not_done")
        counter_map = {"gifts_week": week.get("gifts", 0),
                       "mutual_week": week.get("mutual", 0),
                       "spent_week": week.get("spent", 0)}
        if counter_map.get(q["counter"], 0) < q["target"]:
            raise ValueError("quest_not_done")
        if quest_id in week.get("claimed", []):
            raise ValueError("quest_already_claimed")

        w1, w2 = await self._get_wallets_ordered(user_id, partner_id)
        w1.balance += q["reward_coins"]
        w2.balance += q["reward_coins"]
        rel.xp   += q["reward_xp"]
        rel.level = _level_from_xp(rel.xp)

        week.setdefault("claimed", []).append(quest_id)
        meta["week"] = week
        _save_meta(rel, meta)
        await self._session.flush()
        return {
            "reward_coins": q["reward_coins"],
            "reward_xp":    q["reward_xp"],
            "new_balance":  w1.balance,
            "new_xp":       rel.xp,
            "new_level":    rel.level,
        }

    # ── Anniversaries ─────────────────────────────────────────────────────────

    async def process_anniversary(self, rel: Relationship) -> dict | None:
        """If today is a month/year anniversary not yet congratulated,
        credit both partners and return {label, coins}.  Otherwise None.
        Called from the /list route so it fires on next mini-app open."""
        if rel.status != "active" or not rel.accepted_at:
            return None
        acc = rel.accepted_at
        if acc.tzinfo is None:
            acc = acc.replace(tzinfo=dt.timezone.utc)
        now = dt.datetime.now(dt.timezone.utc)
        months = (now.year - acc.year) * 12 + (now.month - acc.month)
        if months <= 0:
            return None
        # clamp anniversary day for short months (Jan 31 → Feb 28)
        import calendar
        anniv_day = min(acc.day, calendar.monthrange(now.year, now.month)[1])
        if now.day != anniv_day:
            return None

        key = f"{now.year}-{now.month:02d}"
        # Quick unlocked pre-check (avoids locking on every /list call) …
        if _load_meta(rel).get("anniv", {}).get("last") == key:
            return None

        # … then re-fetch FOR UPDATE and re-validate under the lock so two
        # concurrent /list calls can't both credit the bonus.
        rel = (
            await self._session.execute(
                select(Relationship)
                .where(Relationship.id == rel.id, Relationship.status == "active")
                .with_for_update()
            )
        ).scalar_one_or_none()
        if rel is None:
            return None
        meta = _load_meta(rel)
        anniv = meta.get("anniv", {})
        if anniv.get("last") == key:
            return None

        is_year = months % 12 == 0
        coins = ANNIVERSARY_YEAR_BONUS if is_year else ANNIVERSARY_MONTH_BONUS
        label = f"{months // 12} г. вместе" if is_year else f"{months} мес. вместе"

        w1, w2 = await self._get_wallets_ordered(rel.user_a_id, rel.user_b_id)
        w1.balance += coins
        w2.balance += coins

        anniv["last"] = key
        meta["anniv"] = anniv
        _save_meta(rel, meta)
        await self._session.flush()
        return {"label": label, "coins": coins}

    async def change_category(
        self, user_id: int, partner_id: int, new_category: str
    ) -> Relationship:
        """Switch relationship category between 'friendship' and 'romantic'.

        If switching to 'friendship' and rel_type is not 'friends', rel_type is
        reset to 'friends' (level/xp/streak preserved).
        """
        if new_category not in ("friendship", "romantic"):
            raise ValueError("invalid_category")
        rel = await self.get_between(user_id, partner_id)
        if not rel or rel.status != "active":
            raise ValueError("not_related")
        rel.category = new_category
        if new_category == "friendship" and rel.rel_type != "friends":
            rel.rel_type = "friends"
            rel.initiator_id = user_id
        await self._session.flush()
        return rel

    async def upgrade_tier(self, user_id: int, partner_id: int) -> Relationship:
        # Relationship lock BEFORE validation so two concurrent upgrades can't
        # both pass the tier/level checks and double-charge the wallet.
        rel = await self.get_between(user_id, partner_id, lock=True)
        if not rel or rel.status != "active":
            raise ValueError("not_related")

        # Friendship category cannot have romantic tiers
        if getattr(rel, "category", "romantic") == "friendship":
            raise ValueError("friendship_no_upgrade")

        cur = rel.rel_type
        if cur == "married":
            raise ValueError("already_max_tier")
        if rel.level < UPGRADE_MIN_LEVEL.get(cur, 999):
            raise ValueError(f"need_level_{UPGRADE_MIN_LEVEL[cur]}")

        # Both partners must have sent at least UPGRADE_MIN_GIFTS[cur] gifts
        # so that one side can't spam gifts alone to force a tier upgrade.
        min_gifts = UPGRADE_MIN_GIFTS.get(cur, 0)
        if min_gifts > 0:
            meta = _load_meta(rel)
            by_a = meta.get("gifts_by_a", 0)
            by_b = meta.get("gifts_by_b", 0)
            # Determine which counter belongs to the initiator vs partner
            if user_id == rel.user_a_id:
                own_gifts, partner_gifts = by_a, by_b
            else:
                own_gifts, partner_gifts = by_b, by_a
            if own_gifts < min_gifts:
                raise ValueError(f"need_gifts_{min_gifts}")
            if partner_gifts < min_gifts:
                raise ValueError("partner_not_active")

        cost   = UPGRADE_COSTS[cur]
        wallet = await self._get_wallet(user_id, lock=True)
        if wallet.balance < cost:
            raise ValueError("insufficient_funds")
        wallet.balance -= cost

        rel.rel_type     = TIER_ORDER[TIER_ORDER.index(cur) + 1]
        rel.level        = 1
        rel.xp           = 0
        rel.initiator_id = user_id
        rel.last_gift_a  = None
        rel.last_gift_b  = None

        # Reset per-side gift counters for the new tier so requirements
        # are re-evaluated fresh if the couple ever tries another upgrade.
        meta = _load_meta(rel)
        meta.pop("gifts_by_a", None)
        meta.pop("gifts_by_b", None)
        _save_meta(rel, meta)

        await self._session.flush()
        return rel

    async def break_rel(self, user_id: int, partner_id: int) -> Relationship:
        """Break an active or pending relationship. Returns the relationship
        (with its pre-break ``rel_type``) so the caller can react (e.g. notify
        the other party when a marriage ends)."""
        rel = await self.get_between(user_id, partner_id)
        if not rel:
            raise ValueError("not_related")
        rel.status = "broken"
        await self._session.flush()
        return rel

    async def get_user_rank(self, user_id: int) -> dict | None:
        """Return the current user's best active relationship rank (by XP DESC, id ASC).

        Ordering matches get_leaderboard so ranks are consistent even under ties.
        Returns None when the user has no active relationships.
        """
        from app.models.user import TelegramUser  # local import to avoid circulars

        # Find the user's best active relationship using the same ordering as the leaderboard
        best = (
            await self._session.execute(
                select(Relationship)
                .where(
                    Relationship.status == "active",
                    (Relationship.user_a_id == user_id) | (Relationship.user_b_id == user_id),
                )
                .order_by(Relationship.xp.desc(), Relationship.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()

        if best is None:
            return None

        # Count how many active relationships rank strictly above this one
        # using the same (xp DESC, id ASC) ordering:
        #   ranked above ↔ xp > best.xp  OR  (xp == best.xp AND id < best.id)
        above_count: int = (
            await self._session.execute(
                select(func.count()).where(
                    Relationship.status == "active",
                    (Relationship.xp > best.xp)
                    | ((Relationship.xp == best.xp) & (Relationship.id < best.id)),
                )
            )
        ).scalar_one()

        rank = above_count + 1

        # Fetch partner name
        partner_id = best.user_b_id if best.user_a_id == user_id else best.user_a_id
        partner_user = (
            await self._session.execute(
                select(TelegramUser).where(TelegramUser.telegram_user_id == partner_id)
            )
        ).scalar_one_or_none()

        if partner_user:
            parts = [p for p in (partner_user.first_name, partner_user.last_name) if p]
            partner_name = " ".join(parts) or partner_user.username or f"#{partner_id}"
        else:
            partner_name = f"#{partner_id}"

        # Also fetch own name
        own_user = (
            await self._session.execute(
                select(TelegramUser).where(TelegramUser.telegram_user_id == user_id)
            )
        ).scalar_one_or_none()
        if own_user:
            parts = [p for p in (own_user.first_name, own_user.last_name) if p]
            own_name = " ".join(parts) or own_user.username or f"#{user_id}"
        else:
            own_name = f"#{user_id}"

        tier_emoji = {"friends": "🤝", "dating": "💕", "married": "💍"}
        user_a_name = own_name if best.user_a_id == user_id else partner_name
        user_b_name = partner_name if best.user_a_id == user_id else own_name

        return {
            "rel_id":      best.id,
            "rank":        rank,
            "user_a_name": user_a_name,
            "user_b_name": user_b_name,
            "rel_type":    best.rel_type,
            "tier_emoji":  tier_emoji.get(best.rel_type, "💫"),
            "level":       best.level,
            "xp":          best.xp,
        }

    async def get_leaderboard(self, limit: int = 20) -> list[dict]:
        """Top *limit* active relationships ordered by XP DESC, id ASC (tie-break)."""
        from app.models.user import TelegramUser  # local import to avoid circulars

        rows = list(
            (
                await self._session.execute(
                    select(Relationship)
                    .where(Relationship.status == "active")
                    .order_by(Relationship.xp.desc(), Relationship.id.asc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return []

        user_ids: set[int] = set()
        for r in rows:
            user_ids.add(r.user_a_id)
            user_ids.add(r.user_b_id)

        name_map: dict[int, str] = {}
        for u in (
            await self._session.execute(
                select(TelegramUser).where(
                    TelegramUser.telegram_user_id.in_(user_ids)
                )
            )
        ).scalars():
            parts = [p for p in (u.first_name, u.last_name) if p]
            name_map[u.telegram_user_id] = (
                " ".join(parts) or u.username or f"#{u.telegram_user_id}"
            )

        tier_emoji = {"friends": "🤝", "dating": "💕", "married": "💍"}
        result = []
        for i, r in enumerate(rows, 1):
            result.append(
                {
                    "rel_id":      r.id,
                    "rank":        i,
                    "user_a_name": name_map.get(r.user_a_id, f"#{r.user_a_id}"),
                    "user_b_name": name_map.get(r.user_b_id, f"#{r.user_b_id}"),
                    "rel_type":    r.rel_type,
                    "tier_emoji":  tier_emoji.get(r.rel_type, "💫"),
                    "level":       r.level,
                    "xp":          r.xp,
                }
            )
        return result

    # ── Wallet helper ─────────────────────────────────────────────────────────

    async def _get_wallet(self, user_id: int, *, lock: bool = False) -> UserWallet:
        q = select(UserWallet).where(UserWallet.owner_telegram_id == user_id)
        if lock:
            q = q.with_for_update()
        wallet = (await self._session.execute(q)).scalar_one_or_none()
        if not wallet:
            wallet = UserWallet(owner_telegram_id=user_id, balance=0)
            self._session.add(wallet)
            await self._session.flush()
        return wallet
