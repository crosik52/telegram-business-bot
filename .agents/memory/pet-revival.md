---
name: Pet revival system
description: Stars-paid revival of dead pets — rules, payload format, code locations
---

# Pet revival system

## Rule
A dead pet can be revived for **10 Telegram Stars** up to **3 times** and only within **3 days of death**. Progress (XP, level, skills, stats) is preserved. `last_fed_at` is cleared so hunger resets to full.

**Why:** Prevents infinite revival loops while giving couples a meaningful second chance; the 3-day window creates urgency without being punishing.

## How to apply
- Invoice endpoint validates eligibility before charging (400/409 on bad state).
- Actual revival (`revive_pet`) runs inside `on_successful_payment` bot handler — never on the invoice request itself.
- If `revive_pet` raises `ValueError` after payment, send the user an error message; Telegram will auto-refund.

## Payload format
`pet_revive_{user_id}_{pet_id}` — consistent with coins_/subscription_ prefix convention.

## Key fields on `chat_pets`
- `revival_count` INT NOT NULL DEFAULT 0
- `max_revivals`  INT NOT NULL DEFAULT 3

## Eligibility check (used in both route and repo)
`not is_alive AND revival_count < max_revivals AND died_at IS NOT NULL AND (now - died_at) <= 3 days`

## Files
- `app/models/pet.py` — v5 fields
- `app/repositories/pet_repository.py` — `revive_pet()` method; `_pet_dict` includes `can_revive`, `revival_count`, `max_revivals`
- `app/miniapp/routes.py` — `POST /app/api/pet/revive/invoice`
- `app/business/handlers.py` — `pet_revive_` prefix in pre-checkout; handler in `on_successful_payment`
- `app/miniapp/templates/miniapp.html` — dead card button + `revivePet(petId)` JS + CSS
- `alembic/versions/z0a1b2c3d4e5_pet_revival.py` — migration
