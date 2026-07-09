---
name: Wallet security patterns
description: Coin economy design rules — locking, reward derivation, constraints
---

## Rule: All wallet mutations use row-level locks

`WalletRepository._get_for_update()` acquires `SELECT ... FOR UPDATE` before any read-check-modify-write (balance check, cooldown check). Use `_get_for_update()` for claim_daily, spin_slots, flip_coin. Use `get_or_create()` (no lock) only for read-only wallet_info.

**Why:** Async FastAPI can process concurrent requests in the same event loop. Without a row-level lock, two simultaneous `/slots` calls both pass the `balance >= SLOT_COST` check and deduct twice, producing lost updates.

**How to apply:** Any new mutation method in WalletRepository must call `_get_for_update(owner_telegram_id)` at the top, not `get_or_create`.

## Rule: Streak days must be server-derived for reward calculations

`wallet_claim_daily` route queries `BusinessConnection` → `StatsService.get_owner_stats()` to get `best_streak`. The `ClaimDailyRequest` does NOT have a `streak_days` field.

**Why:** Client-controlled reward parameters are an economy exploit vector — any user could send `streak_days=99999` to claim maximum bonus every day.

**How to apply:** If any future reward that depends on user-level data is added, derive it server-side before passing to the repository.

## Rule: DB CHECK constraints on wallet fields

Migration `f1e2d3c4b5a6` adds `balance >= 0`, `total_earned >= 0`, `total_spent >= 0` constraints. Repository also calls `_clamp_wallet()` before every flush as belt-and-suspenders.
