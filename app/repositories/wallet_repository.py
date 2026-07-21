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

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_connection import BusinessConnection
from app.models.relationship import MARRIAGE_DAILY_BONUS, Relationship
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

MINES_GRID_SIZE = 25
MINES_MIN_COUNT = 3
MINES_MAX_COUNT = 15
MINES_BET_MIN   = 10
MINES_BET_MAX   = 500
MINES_HOUSE_EDGE = 0.97   # 3 % edge

CRASH_BET_MIN   = 10
CRASH_BET_MAX   = 500
CRASH_HOUSE_EDGE = 0.99   # 1 % edge

# ── Per-process game sessions (single Railway instance → fine) ──────────────
_mines_sessions: dict[int, dict] = {}   # owner_id → session
_crash_sessions: dict[int, dict] = {}   # owner_id → session
_crash_history:  list[dict]      = []   # global crash history (all users, last 200)
_live_events:    list[dict]      = []   # recent cashout/mine/crash events (last 100)


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


def _mines_multiplier(mines: int, revealed: int) -> float:
    """Expected payout multiplier with house edge for `revealed` safe cells."""
    if revealed == 0:
        return 1.0
    safe = MINES_GRID_SIZE - mines
    p = 1.0
    for i in range(revealed):
        p *= (safe - i) / (MINES_GRID_SIZE - i)
    return round(MINES_HOUSE_EDGE / p, 2)


def _generate_crash_point() -> float:
    """Generate crash multiplier. Distribution: ~50 % crash at ≤ 2×, house edge ~1 %."""
    r = random.random()
    if r < 0.01:
        r = 0.01        # clamp so result ≤ 99 with 99 % edge
    return round(max(1.0, CRASH_HOUSE_EDGE / r), 2)


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
class MinesStartResult:
    grid_size:  int
    mines_count: int
    safe_count: int


@dataclass
class MinesRevealResult:
    is_mine:         bool
    revealed_indices: list[int]
    mines_indices:   list[int]   # non-empty only on mine hit
    revealed_count:  int
    multiplier:      float
    potential_payout: int
    new_balance:     int


@dataclass
class MinesCashoutResult:
    payout:        int
    multiplier:    float
    revealed_count: int
    new_balance:   int
    mines_indices: list  # positions of all mines (for FOMO reveal)


@dataclass
class CrashStartResult:
    ok:          bool
    new_balance: int
    crash_at:    float


@dataclass
class CrashCashoutResult:
    won:        bool
    crash_at:   float
    multiplier: float
    payout:     int
    new_balance: int


@dataclass
class DailyClaimResult:
    earned: int
    base: int
    streak_bonus: int
    new_balance: int
    premium_multiplier: float = 1.0
    premium_bonus: int = 0
    marriage_bonus: int = 0
    marriage_count: int = 0


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
        self,
        owner_telegram_id: int,
        streak_days: int = 0,
        premium_multiplier: float = 1.0,
        premium_bonus: int = 0,
    ) -> DailyClaimResult:
        """Claim the daily reward.  streak_days MUST be server-derived by the
        caller — never pass a client-supplied value directly.
        premium_multiplier and premium_bonus are applied after streak calculation.

        The marriage bonus is computed atomically *after* the wallet row-level
        lock is acquired (SELECT … FOR UPDATE), so two concurrent claim_daily
        calls for the same user will serialize at the lock and only the first
        will pass the can_claim check — preventing the bonus from being
        double-applied.
        """
        streak_days = max(0, streak_days)   # defensive clamp
        wallet = await self._get_for_update(owner_telegram_id)

        can_claim, wait = self.daily_claim_status(wallet)
        if not can_claim:
            raise ValueError(f"not_yet:{wait}")

        # Count active marriages inside the locked transaction so a concurrent
        # claim_daily cannot observe a stale count before the cooldown is set.
        # Only count marriages where the partner still has an enabled
        # BusinessConnection — a disconnected partner must not keep generating
        # the daily bonus.
        connected_partners = (
            select(BusinessConnection.user_telegram_id)
            .where(BusinessConnection.is_enabled.is_(True))
            .scalar_subquery()
        )
        marriage_count_row = await self.session.execute(
            select(func.count()).select_from(Relationship).where(
                or_(
                    and_(
                        Relationship.user_a_id == owner_telegram_id,
                        Relationship.user_b_id.in_(connected_partners),
                    ),
                    and_(
                        Relationship.user_b_id == owner_telegram_id,
                        Relationship.user_a_id.in_(connected_partners),
                    ),
                ),
                Relationship.rel_type == "married",
                Relationship.status == "active",
            )
        )
        _marriage_count = marriage_count_row.scalar() or 0
        _marriage_bonus = MARRIAGE_DAILY_BONUS * _marriage_count

        bonus        = min(streak_days * DAILY_STREAK_BONUS_PER_DAY, DAILY_STREAK_BONUS_MAX)
        base_earned  = DAILY_BASE + bonus
        earned       = round(base_earned * max(1.0, premium_multiplier)) + max(0, premium_bonus) + _marriage_bonus

        wallet.balance      += earned
        wallet.total_earned += earned
        wallet.last_daily_claim = dt.datetime.now(dt.timezone.utc)
        _clamp_wallet(wallet)
        await self.session.flush()

        return DailyClaimResult(
            earned=earned,
            base=DAILY_BASE,
            streak_bonus=bonus,
            new_balance=wallet.balance,
            premium_multiplier=premium_multiplier,
            premium_bonus=premium_bonus,
            marriage_bonus=_marriage_bonus,
            marriage_count=_marriage_count,
        )

    # ── Slots spin (mutation) ──────────────────────────────────────────────
    async def spin_slots(self, owner_telegram_id: int, bet: int = SLOT_COST) -> SlotResult:
        bet = max(SLOT_COST, min(bet, 100))   # clamp to allowed range
        wallet = await self._get_for_update(owner_telegram_id)

        if wallet.balance < bet:
            raise ValueError("insufficient_balance")

        wallet.balance      -= bet
        wallet.total_spent  += bet

        reels  = [_pick_symbol() for _ in range(3)]
        payout = 0
        if reels[0] == reels[1] == reels[2]:
            base = SLOT_THREE_PAYOUTS.get(reels[0], 0)
            payout = int(base * bet / SLOT_COST)
        elif reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
            payout = int(SLOT_TWO_PAYOUT * bet / SLOT_COST)

        if payout > 0:
            wallet.balance      += payout
            wallet.total_earned += payout

        _clamp_wallet(wallet)
        await self.session.flush()

        return SlotResult(
            reels=reels, payout=payout, net=payout - bet,
            is_jackpot=(reels == ["💎", "💎", "💎"]),
            new_balance=wallet.balance,
        )

    # ── Admin: adjust balance (add / subtract) ────────────────────────────
    async def admin_adjust_balance(
        self, owner_telegram_id: int, delta: int
    ) -> int:
        """Add (delta > 0) or subtract (delta < 0) coins from a user's wallet.

        Balance is clamped to [0, 10_000_000].  Keeps total_earned / total_spent
        accounting consistent.  Returns the resulting balance.
        """
        wallet = await self._get_for_update(owner_telegram_id)
        new_balance = max(0, min(wallet.balance + delta, 10_000_000))
        diff = new_balance - wallet.balance
        wallet.balance = new_balance
        if diff > 0:
            wallet.total_earned += diff
        elif diff < 0:
            wallet.total_spent += abs(diff)
        _clamp_wallet(wallet)
        await self.session.flush()
        return wallet.balance

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

    # ── Mines (mutation) ──────────────────────────────────────────────────
    async def mines_start(
        self, owner_telegram_id: int, bet: int, mines_count: int, first_name: str = "Игрок"
    ) -> "MinesStartResult":
        bet         = max(MINES_BET_MIN, min(bet, MINES_BET_MAX))
        mines_count = max(MINES_MIN_COUNT, min(mines_count, MINES_MAX_COUNT))

        wallet = await self._get_for_update(owner_telegram_id)
        if wallet.balance < bet:
            raise ValueError("insufficient_balance")

        wallet.balance     -= bet
        wallet.total_spent += bet
        _clamp_wallet(wallet)
        await self.session.flush()

        positions = list(range(MINES_GRID_SIZE))
        random.shuffle(positions)
        mines_set = set(positions[:mines_count])

        _mines_sessions[owner_telegram_id] = {
            "name": first_name[:14],
            "bet": bet,
            "mines": mines_set,
            "revealed": [],
            "mines_count": mines_count,
        }
        return MinesStartResult(
            grid_size=MINES_GRID_SIZE,
            mines_count=mines_count,
            safe_count=MINES_GRID_SIZE - mines_count,
        )

    async def mines_reveal(
        self, owner_telegram_id: int, cell_index: int
    ) -> "MinesRevealResult":
        sess = _mines_sessions.get(owner_telegram_id)
        if not sess:
            raise ValueError("no_active_game")
        if not (0 <= cell_index < MINES_GRID_SIZE):
            raise ValueError("invalid_cell")
        if cell_index in sess["revealed"]:
            raise ValueError("already_revealed")

        is_mine = cell_index in sess["mines"]

        if is_mine:
            bet        = sess["bet"]
            mines_list = sorted(sess["mines"])
            revealed   = list(sess["revealed"])
            _live_events.append({
                "game": "mines", "type": "mine",
                "name": sess.get("name", "Игрок"), "bet": bet,
                "ts": dt.datetime.now(dt.timezone.utc),
            })
            if len(_live_events) > 100:
                del _live_events[:-100]
            del _mines_sessions[owner_telegram_id]
            wallet = await self._get_for_update(owner_telegram_id)
            return MinesRevealResult(
                is_mine=True,
                revealed_indices=revealed,
                mines_indices=mines_list,
                revealed_count=len(revealed),
                multiplier=0.0,
                potential_payout=0,
                new_balance=wallet.balance,
            )

        sess["revealed"].append(cell_index)
        revealed_count = len(sess["revealed"])
        mult   = _mines_multiplier(sess["mines_count"], revealed_count)
        payout = round(sess["bet"] * mult)
        wallet = await self._get_for_update(owner_telegram_id)
        return MinesRevealResult(
            is_mine=False,
            revealed_indices=list(sess["revealed"]),
            mines_indices=[],
            revealed_count=revealed_count,
            multiplier=mult,
            potential_payout=payout,
            new_balance=wallet.balance,
        )

    async def mines_cashout(self, owner_telegram_id: int) -> "MinesCashoutResult":
        sess = _mines_sessions.get(owner_telegram_id)
        if not sess:
            raise ValueError("no_active_game")
        if not sess["revealed"]:
            raise ValueError("no_cells_revealed")

        revealed_count = len(sess["revealed"])
        mult       = _mines_multiplier(sess["mines_count"], revealed_count)
        payout     = round(sess["bet"] * mult)
        mines_list = sorted(sess["mines"])   # capture before deletion
        _live_events.append({
            "game": "mines", "type": "cashout",
            "name": sess.get("name", "Игрок"), "bet": sess["bet"],
            "mult": mult, "payout": payout,
            "ts": dt.datetime.now(dt.timezone.utc),
        })
        if len(_live_events) > 100:
            del _live_events[:-100]
        del _mines_sessions[owner_telegram_id]

        wallet = await self._get_for_update(owner_telegram_id)
        wallet.balance      += payout
        wallet.total_earned += payout
        _clamp_wallet(wallet)
        await self.session.flush()

        return MinesCashoutResult(
            payout=payout, multiplier=mult,
            revealed_count=revealed_count, new_balance=wallet.balance,
            mines_indices=mines_list,
        )

    # ── Crash (mutation) ──────────────────────────────────────────────────
    async def crash_start(
        self, owner_telegram_id: int, bet: int, first_name: str = "Игрок"
    ) -> "CrashStartResult":
        bet = max(CRASH_BET_MIN, min(bet, CRASH_BET_MAX))

        wallet = await self._get_for_update(owner_telegram_id)
        if wallet.balance < bet:
            raise ValueError("insufficient_balance")

        wallet.balance     -= bet
        wallet.total_spent += bet
        _clamp_wallet(wallet)
        await self.session.flush()

        crash_at = _generate_crash_point()
        _crash_sessions[owner_telegram_id] = {
            "name": first_name[:14],
            "bet": bet, "crash_at": crash_at,
            "started_at": dt.datetime.now(dt.timezone.utc),
        }
        return CrashStartResult(ok=True, new_balance=wallet.balance, crash_at=crash_at)

    async def crash_cashout(
        self, owner_telegram_id: int, multiplier: float
    ) -> "CrashCashoutResult":
        sess = _crash_sessions.get(owner_telegram_id)
        if not sess:
            raise ValueError("no_active_game")

        crash_at   = sess["crash_at"]
        bet        = sess["bet"]
        multiplier = max(1.0, min(round(multiplier, 2), 200.0))
        del _crash_sessions[owner_telegram_id]

        won = multiplier <= crash_at

        wallet = await self._get_for_update(owner_telegram_id)
        if won:
            payout = round(bet * multiplier)
            wallet.balance      += payout
            wallet.total_earned += payout
        else:
            payout = 0
        _clamp_wallet(wallet)
        await self.session.flush()

        # Record in global history (keep last 200 entries)
        _now = dt.datetime.now(dt.timezone.utc)
        _crash_history.append({"crash_at": crash_at, "ts": _now})
        _live_events.append({
            "game": "crash",
            "type": "crash_won" if won else "crash_lost",
            "name": sess.get("name", "Игрок"), "bet": bet,
            "mult": multiplier if won else round(crash_at, 2),
            "payout": payout,
            "ts": _now,
        })
        if len(_live_events) > 100:
            del _live_events[:-100]
        if len(_crash_history) > 200:
            del _crash_history[:-200]

        return CrashCashoutResult(
            won=won, crash_at=crash_at, multiplier=multiplier,
            payout=payout, new_balance=wallet.balance,
        )

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


# ── Live players (module-level, no DB needed) ────────────────────────────────

def get_live_players() -> dict:
    """Return current active game sessions and recent events (last 20 s).

    Called by the public /app/api/wallet/live_players endpoint.
    No authentication required — only anonymised names and bets are exposed.
    """
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=20)

    active = []
    for sess in list(_mines_sessions.values()):
        active.append({
            "game":     "mines",
            "name":     sess.get("name", "Игрок"),
            "bet":      sess["bet"],
            "revealed": len(sess.get("revealed", [])),
            "mines_count": sess.get("mines_count", 5),
        })
    for sess in list(_crash_sessions.values()):
        active.append({
            "game": "crash",
            "name": sess.get("name", "Игрок"),
            "bet":  sess["bet"],
        })

    recent = [
        {k: v for k, v in e.items() if k != "ts"}
        for e in _live_events
        if e["ts"] >= cutoff
    ]

    return {"active": active, "recent": recent}
