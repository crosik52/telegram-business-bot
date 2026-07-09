"""WalletRepository — coin balance, daily claim, and casino game outcomes."""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wallet import UserWallet

# ── Constants ──────────────────────────────────────────────────────────────
DAILY_BASE = 50
DAILY_STREAK_BONUS_PER_DAY = 2
DAILY_STREAK_BONUS_MAX = 50
DAILY_COOLDOWN_HOURS = 20          # allow slightly early next-day claims

SLOT_COST = 10
FLIP_MIN_BET = 10
FLIP_MAX_BET = 500

SLOT_SYMBOLS = ["🍒", "🍋", "🍊", "🍇", "⭐", "💎"]
SLOT_WEIGHTS  = [35,   25,   20,   12,    6,    2]   # sum = 100

# Payouts for exact 3-of-a-kind (gross coins returned, before deducting cost)
SLOT_THREE_PAYOUTS: dict[str, int] = {
    "🍒": 5,
    "🍋": 10,
    "🍊": 15,
    "🍇": 30,
    "⭐": 80,
    "💎": 500,
}
SLOT_TWO_PAYOUT = 5   # any pair


# ── Helper ─────────────────────────────────────────────────────────────────
def _pick_symbol() -> str:
    total = sum(SLOT_WEIGHTS)
    r = random.randrange(total)
    cumulative = 0
    for sym, wt in zip(SLOT_SYMBOLS, SLOT_WEIGHTS):
        cumulative += wt
        if r < cumulative:
            return sym
    return SLOT_SYMBOLS[0]


# ── Result dataclasses ──────────────────────────────────────────────────────
@dataclass
class SlotResult:
    reels: list[str]
    payout: int                    # gross coins returned (0 = no win)
    net: int                       # payout - SLOT_COST (can be negative)
    is_jackpot: bool
    new_balance: int


@dataclass
class FlipResult:
    server_side: str               # "heads" or "tails"
    won: bool
    amount_change: int             # positive = won, negative = lost
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

    async def get_or_create(self, owner_telegram_id: int) -> UserWallet:
        result = await self.session.execute(
            select(UserWallet).where(UserWallet.owner_telegram_id == owner_telegram_id)
        )
        wallet = result.scalar_one_or_none()
        if wallet is None:
            wallet = UserWallet(owner_telegram_id=owner_telegram_id)
            self.session.add(wallet)
            await self.session.flush()
        return wallet

    def daily_claim_status(self, wallet: UserWallet) -> tuple[bool, int]:
        """Return (can_claim, seconds_until_next_claim)."""
        if wallet.last_daily_claim is None:
            return True, 0
        now = dt.datetime.now(dt.timezone.utc)
        last = wallet.last_daily_claim
        if last.tzinfo is None:
            last = last.replace(tzinfo=dt.timezone.utc)
        elapsed = (now - last).total_seconds()
        cooldown = DAILY_COOLDOWN_HOURS * 3600
        if elapsed >= cooldown:
            return True, 0
        return False, int(cooldown - elapsed)

    async def claim_daily(
        self, wallet: UserWallet, streak_days: int = 0
    ) -> DailyClaimResult:
        can_claim, wait = self.daily_claim_status(wallet)
        if not can_claim:
            raise ValueError(f"not_yet:{wait}")

        bonus = min(streak_days * DAILY_STREAK_BONUS_PER_DAY, DAILY_STREAK_BONUS_MAX)
        earned = DAILY_BASE + bonus

        wallet.balance += earned
        wallet.total_earned += earned
        wallet.last_daily_claim = dt.datetime.now(dt.timezone.utc)
        await self.session.flush()

        return DailyClaimResult(
            earned=earned,
            base=DAILY_BASE,
            streak_bonus=bonus,
            new_balance=wallet.balance,
        )

    async def spin_slots(self, wallet: UserWallet) -> SlotResult:
        if wallet.balance < SLOT_COST:
            raise ValueError("insufficient_balance")

        wallet.balance -= SLOT_COST
        wallet.total_spent += SLOT_COST

        reels = [_pick_symbol() for _ in range(3)]
        payout = 0

        if reels[0] == reels[1] == reels[2]:
            payout = SLOT_THREE_PAYOUTS.get(reels[0], 0)
        elif reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
            payout = SLOT_TWO_PAYOUT

        if payout > 0:
            wallet.balance += payout
            wallet.total_earned += payout

        await self.session.flush()

        return SlotResult(
            reels=reels,
            payout=payout,
            net=payout - SLOT_COST,
            is_jackpot=(reels == ["💎", "💎", "💎"]),
            new_balance=wallet.balance,
        )

    async def flip_coin(
        self, wallet: UserWallet, bet: int, choice: str
    ) -> FlipResult:
        if choice not in ("heads", "tails"):
            raise ValueError("invalid_choice")
        bet = max(FLIP_MIN_BET, min(bet, FLIP_MAX_BET, wallet.balance))
        if wallet.balance < bet:
            raise ValueError("insufficient_balance")

        server_side = random.choice(("heads", "tails"))
        won = server_side == choice

        if won:
            wallet.balance += bet
            wallet.total_earned += bet
            amount_change = bet
        else:
            wallet.balance -= bet
            wallet.total_spent += bet
            amount_change = -bet

        await self.session.flush()

        return FlipResult(
            server_side=server_side,
            won=won,
            amount_change=amount_change,
            new_balance=wallet.balance,
        )
