"""RelationshipRepository — bonds between mutually-connected bot users."""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.relationship import (
    GIFT_COOLDOWN_H,
    GIFT_COST,
    GIFT_TO_PARTNER,
    GIFT_XP,
    MAX_REL_LEVEL,
    REL_XP_BONUS,
    REQUEST_COST,
    TIER_ORDER,
    UPGRADE_COSTS,
    UPGRADE_MIN_LEVEL,
    XP_PER_LEVEL,
    Relationship,
)
from app.models.wallet import UserWallet

logger = logging.getLogger(__name__)


def _level_from_xp(xp: int) -> int:
    return min(MAX_REL_LEVEL, xp // XP_PER_LEVEL + 1)


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

    async def get_between(self, user1: int, user2: int) -> Relationship | None:
        """Active or pending relationship between two users."""
        a, b = self._pair(user1, user2)
        return (
            await self._session.execute(
                select(Relationship).where(
                    Relationship.user_a_id == a,
                    Relationship.user_b_id == b,
                    Relationship.status.in_(["pending", "active"]),
                )
            )
        ).scalar_one_or_none()

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
        """Count active marriages for daily bonus calculation."""
        rels = await self.get_for_user(user_id)
        return sum(
            1 for r in rels if r.rel_type == "married" and r.status == "active"
        )

    async def get_active_tier(self, user1: int, user2: int) -> str | None:
        """Return rel_type of the active relationship between user1 and user2, or None."""
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

    def rel_xp_multiplier(self, tier: str | None) -> float:
        """Return the pet XP multiplier for the given relationship tier (1.0 if none)."""
        return REL_XP_BONUS.get(tier, 1.0) if tier else 1.0

    def to_dict(self, rel: Relationship, viewer_id: int) -> dict:
        """Serialise a Relationship for API responses."""
        partner_id = (
            rel.user_b_id if viewer_id == rel.user_a_id else rel.user_a_id
        )
        last_gift = self._last_gift(rel, viewer_id)
        now = dt.datetime.now(dt.timezone.utc)
        gift_ready = last_gift is None or (
            now - last_gift
        ).total_seconds() >= GIFT_COOLDOWN_H * 3600
        xp_in_level = rel.xp % XP_PER_LEVEL
        can_upgrade = (
            rel.status == "active"
            and rel.rel_type != "married"
            and rel.level >= UPGRADE_MIN_LEVEL.get(rel.rel_type, 999)
        )
        return {
            "id":           rel.id,
            "partner_id":   partner_id,
            "rel_type":     rel.rel_type,
            "level":        rel.level,
            "xp":           rel.xp,
            "xp_in_level":  xp_in_level,
            "xp_pct":       round(xp_in_level / XP_PER_LEVEL * 100),
            "status":       rel.status,
            "initiator_id": rel.initiator_id,
            "is_initiator": rel.initiator_id == viewer_id,
            "gift_ready":   gift_ready,
            "can_upgrade":  can_upgrade,
            "upgrade_cost": UPGRADE_COSTS.get(rel.rel_type, 0),
            "accepted_at":  (
                rel.accepted_at.isoformat() if rel.accepted_at else None
            ),
        }

    # ── Mutations ─────────────────────────────────────────────────────────────

    async def send_request(
        self, requester_id: int, addressee_id: int
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

        wallet = await self._get_wallet(requester_id, lock=True)
        if wallet.balance < REQUEST_COST:
            raise ValueError("insufficient_funds")
        wallet.balance -= REQUEST_COST

        a, b = self._pair(requester_id, addressee_id)
        rel = Relationship(
            user_a_id=a,
            user_b_id=b,
            initiator_id=requester_id,
            rel_type="friends",
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

    async def gift(self, sender_id: int, partner_id: int) -> dict:
        """Daily gift: deduct GIFT_COST, add GIFT_TO_PARTNER to partner,
        and add GIFT_XP to both sides' relationship XP."""
        rel = await self.get_between(sender_id, partner_id)
        if not rel or rel.status != "active":
            raise ValueError("not_related")

        now  = dt.datetime.now(dt.timezone.utc)
        last = self._last_gift(rel, sender_id)
        if last and (now - last).total_seconds() < GIFT_COOLDOWN_H * 3600:
            secs = int(GIFT_COOLDOWN_H * 3600 - (now - last).total_seconds())
            raise ValueError(f"gift_cooldown:{secs}")

        sender_w = await self._get_wallet(sender_id,  lock=True)
        if sender_w.balance < GIFT_COST:
            raise ValueError("insufficient_funds")
        sender_w.balance -= GIFT_COST

        partner_w = await self._get_wallet(partner_id, lock=True)
        partner_w.balance += GIFT_TO_PARTNER

        rel.xp    += GIFT_XP
        rel.level  = _level_from_xp(rel.xp)
        self._set_last_gift(rel, sender_id, now)

        await self._session.flush()
        return {
            "new_xp":           rel.xp,
            "new_level":        rel.level,
            "new_balance":      sender_w.balance,
            "partner_received": GIFT_TO_PARTNER,
        }

    async def upgrade_tier(self, user_id: int, partner_id: int) -> Relationship:
        rel = await self.get_between(user_id, partner_id)
        if not rel or rel.status != "active":
            raise ValueError("not_related")

        cur = rel.rel_type
        if cur == "married":
            raise ValueError("already_max_tier")
        if rel.level < UPGRADE_MIN_LEVEL.get(cur, 999):
            raise ValueError(f"need_level_{UPGRADE_MIN_LEVEL[cur]}")

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
