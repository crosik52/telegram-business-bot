---
name: Pet system v2
description: 8 species, 5 personalities, play/cuddle/rename, XP/level, mood, feed_free for premium
---

## Model columns (ChatPet)
- `mood` — stored float (0-100), decays from last cuddle/play timestamp
- `xp` / `level` — XP is additive; level = floor(sqrt(xp/10)), capped at 50
- `personality` — one of: playful, lazy, gluttonous, brave, shy (random at adopt)
- `last_played_at` / `last_cuddled_at` — real action timestamps (never backdated for mood math)

## Cooldowns
- Feed: personality-dependent (lazy = longer)
- Play: personality-dependent (~4h default)
- Cuddle: 1h fixed

## Personality multipliers
Each personality applies 2× multiplier to one stat gain (not 1.5×).

## Critical bug that was fixed
`play()` and `cuddle()` used to backdate timestamps to encode mood values — broke cooldowns. Fixed by storing actual mood in `pet.mood` column and keeping timestamps as real action times.

## Feed streak fix
`feed_streak` must be computed from `prev_fed = pet.last_fed_at` BEFORE updating `last_fed_at`. If you compute after, the streak always sees the new timestamp.

## Premium extensions
- `feed_free: bool = False` param on `feed()` — skips coin deduction when True
- `xp_multiplier: float = 1.0` param on `feed()`, `play()`, `cuddle()` — multiplies XP gained

## Migration chain
`9a8b7c6d5e4f` → `g1h2i3j4k5l6` (pet_system_v2) → `h2i3j4k5l6m7` (subscription) → `i3j4k5l6m7n8` (subscription_safety)
