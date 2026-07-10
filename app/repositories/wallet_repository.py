"""WalletRepository — coin balance, daily claim, and casino game outcomes.

Security notes:
- All mutation methods acquire a row-level lock (SELECT … FOR UPDATE) before
  reading balance/cooldown state, preventing concurrent lost-update races.
- Balance and totals are clamped to ≥ 0 defensively before every flush.
- streak_days supplied by callers must already be server-derived (routes are
  responsible for fetching it from StatsService — never accept from client).
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wallet import UserWallet

# ── Constants ──────────────────────────────────────────────────────────────
DAILY_BASE = 50
DAILY_STREAK_BONUS_PER_DAY = 2
DAILY_STREAK_BONUS_MAX = 50
DAILY_COOLDOWN_HOURS = 20           # allows slightly-early next-day claims

SLOT_COST = 10
FLIP_MIN_BET = 10
FLIP_MAX_BET = 500

SLOT_SYMBOLS = ["🍒", "🍋", "🍊", "🍇", "⭐", "💎"]
SLOT_WEIGHTS  = [35,   25,   20,   12,    6,    2]   # sum = 100

SLOT_THREE_PAYOUTS: dict[str, int] = {
    "🍒": 5,
    "🍋": 10,
    "🍊": 15,
    "🍇": 30,
    "⭐": 80,
    "💎": 500,
}
SLOT_TWO_PAYOUT = 5


# ── Internal helpers ────────────────────────────────────────────────────────
def _pick_symbol() -> str:
    total = sum(SLOT_WEIGHTS)
    r = random.randrange(total)
    cumulative = 0
    for sym, wt in zip(SLOT_SYMBOLS, SLOT_WEIGHTS):
        cumulative += wt
        if r < cumulative:
            return sym
    return SLOT_SYMBOLS[0]


def _clamp_wallet(wallet: UserWallet) -> None:
    """Defensive: ensure no field goes below zero (belt-and-suspenders)."""
    wallet.balance      = max(0, wallet.balance)
    wallet.total_earned = max(0, wallet.total_earned)
    wallet.total_spent  = max(0, wallet.total_spent)


# ── Result dataclasses ──────────────────────────────────────────────────────
@dataclass
class SlotResult:
    reels: list[str]
    payout: int
    net: int
    is_jackpot: bool
    new_balance: int


@dataclass
class FlipResult:
    server_side: str
    won: bool
    amount_change: int
    new_balance: int


@dataclass
class DailyClaimResult:
    earned: int
    base: int
    streak_bonus: int
    new_balance: int


# ── Repository ──────────────────────────────────────────────────────────────
class WalletRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Read (no lock needed) ──────────────────────────────────────────────
    async def get_or_create(self, owner_telegram_id: int) -> UserWallet:
        """Fetch wallet for reading.  Does NOT acquire a lock."""
        result = await self.session.execute(
            select(UserWallet).where(UserWallet.owner_telegram_id == owner_telegram_id)
        )
        wallet = result.scalar_one_or_none()
        if wallet is None:
            wallet = UserWallet(owner_telegram_id=owner_telegram_id)
            self.session.add(wallet)
            await self.session.flush()
        return wallet

    # ── Locked read for mutations ──────────────────────────────────────────
    async def _get_for_update(self, owner_telegram_id: int) -> UserWallet:
        """Fetch wallet with a row-level lock (SELECT … FOR UPDATE).

        Prevents concurrent requests from racing through balance/cooldown
        checks and producing lost updates.
        """
        result = await self.session.execute(
            select(UserWallet)
            .where(UserWallet.owner_telegram_id == owner_telegram_id)
            .with_for_update()
        )
        wallet = result.scalar_one_or_none()
        if wallet is None:
            # INSERT is protected by the unique constraint; flush creates the row
            # and lets subsequent operations lock it within the same transaction.
            wallet = UserWallet(owner_telegram_id=owner_telegram_id)
            self.session.add(wallet)
            await self.session.flush()
            # Re-lock the newly inserted row
            result2 = await self.session.execute(
                select(UserWallet)
                .where(UserWallet.owner_telegram_id == owner_telegram_id)
                .with_for_update()
            )
            wallet = result2.scalar_one()
        return wallet

    # ── Daily claim status (read-only helper) ─────────────────────────────
    def daily_claim_status(self, wallet: UserWallet) -> tuple[bool, int]:
        """Return (can_claim, seconds_until_next_claim)."""
        if wallet.last_daily_claim is None:
            return True, 0
        now  = dt.datetime.now(dt.timezone.utc)
        last = wallet.last_daily_claim
        if last.tzinfo is None:
            last = last.replace(tzinfo=dt.timezone.utc)
        elapsed  = (now - last).total_seconds()
        cooldown = DAILY_COOLDOWN_HOURS * 3600
        if elapsed >= cooldown:
            return True, 0
        return False, int(cooldown - elapsed)

    # ── Daily claim (mutation) ─────────────────────────────────────────────
    async def claim_daily(
        self, owner_telegram_id: int, streak_days: int = 0
    ) -> DailyClaimResult:
        """Claim the daily reward.  streak_days MUST be server-derived by the
        caller — never pass a client-supplied value directly."""
        streak_days = max(0, streak_days)   # defensive clamp
        wallet = await self._get_for_update(owner_telegram_id)

        can_claim, wait = self.daily_claim_status(wallet)
        if not can_claim:
            raise ValueError(f"not_yet:{wait}")

        bonus  = min(streak_days * DAILY_STREAK_BONUS_PER_DAY, DAILY_STREAK_BONUS_MAX)
        earned = DAILY_BASE + bonus

        wallet.balance      += earned
        wallet.total_earned += earned
        wallet.last_daily_claim = dt.datetime.now(dt.timezone.utc)
        _clamp_wallet(wallet)
        await self.session.flush()

        return DailyClaimResult(
            earned=earned, base=DAILY_BASE,
            streak_bonus=bonus, new_balance=wallet.balance,
        )

    # ── Slots spin (mutation) ──────────────────────────────────────────────
    async def spin_slots(self, owner_telegram_id: int) -> SlotResult:
        wallet = await self._get_for_update(owner_telegram_id)

        if wallet.balance < SLOT_COST:
            raise ValueError("insufficient_balance")

        wallet.balance      -= SLOT_COST
        wallet.total_spent  += SLOT_COST

        reels  = [_pick_symbol() for _ in range(3)]
        payout = 0
        if reels[0] == reels[1] == reels[2]:
            payout = SLOT_THREE_PAYOUTS.get(reels[0], 0)
        elif reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
            payout = SLOT_TWO_PAYOUT

        if payout > 0:
            wallet.balance      += payout
            wallet.total_earned += payout

        _clamp_wallet(wallet)
        await self.session.flush()

        return SlotResult(
            reels=reels, payout=payout, net=payout - SLOT_COST,
            is_jackpot=(reels == ["💎", "💎", "💎"]),
            new_balance=wallet.balance,
        )

    # ── Admin: set balance (mutation) ─────────────────────────────────────
    async def admin_set_balance(
        self, owner_telegram_id: int, new_balance: int
    ) -> int:
        """Set a user's coin balance directly (admin action).

        Adjusts total_earned / total_spent to keep accounting consistent.
        Returns the resulting balance.
        """
        new_balance = max(0, min(new_balance, 10_000_000))
        wallet = await self._get_for_update(owner_telegram_id)
        diff = new_balance - wallet.balance
        wallet.balance = new_balance
        if diff > 0:
            wallet.total_earned += diff
        elif diff < 0:
            wallet.total_spent += abs(diff)
        _clamp_wallet(wallet)
        await self.session.flush()
        return wallet.balance

    # ── Coin flip (mutation) ───────────────────────────────────────────────
    async def flip_coin(
        self, owner_telegram_id: int, bet: int, choice: str
    ) -> FlipResult:
        if choice not in ("heads", "tails"):
            raise ValueError("invalid_choice")

        wallet = await self._get_for_update(owner_telegram_id)

        bet = max(FLIP_MIN_BET, min(bet, FLIP_MAX_BET, wallet.balance))
        if wallet.balance < bet:
            raise ValueError("insufficient_balance")

        server_side = random.choice(("heads", "tails"))
        won         = server_side == choice

        if won:
            wallet.balance      += bet
            wallet.total_earned += bet
            amount_change = bet
        else:
            wallet.balance     -= bet
            wallet.total_spent += bet
            amount_change = -bet

        _clamp_wallet(wallet)
        await self.session.flush()

        return FlipResult(
            server_side=server_side, won=won,
            amount_change=amount_change, new_balance=wallet.balance,
        )
