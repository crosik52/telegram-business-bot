---
name: Subscription system
description: Telegram Stars subscription — models, repo, payment flow, admin panel, DB safety
---

## Key files
- `app/models/subscription.py` — `SubscriptionConfig` (singleton) + `UserSubscription`
- `app/repositories/subscription_repository.py` — get_config, update_config, activate, grant, revoke, list_subscribers
- `alembic/versions/h2i3j4k5l6m7_subscription.py` — creates tables (down_revision=g1h2i3j4k5l6)
- `alembic/versions/i3j4k5l6m7n8_subscription_safety.py` — adds idempotency + one-active-sub constraints

## Payment flow (Telegram Stars / XTR)
1. Frontend calls `POST /app/api/subscription/invoice`
2. Backend calls `bot.create_invoice_link(currency="XTR", provider_token="", ...)`
3. Frontend receives `invoice_link` and calls `tg.openInvoice(link, callback)`
4. Telegram shows native Stars payment sheet inside the Mini App
5. Bot receives `@router.pre_checkout_query()` → must answer `ok=True` before payment settles
6. Bot receives `@router.message(F.successful_payment)` → calls `activate()`

**Why `create_invoice_link` + `tg.openInvoice()` (not `send_invoice`):** Stars payments from inside Mini Apps require `openInvoice()` for in-app UX; `send_invoice` would send to the user's DM chat instead.

## Payment idempotency (important)
`activate()` checks `payment_charge_id` before inserting — returns existing row if already processed. DB-level enforcement:
- `UNIQUE INDEX uix_user_subscriptions_charge_id ON user_subscriptions (payment_charge_id) WHERE payment_charge_id IS NOT NULL`
- `UNIQUE INDEX uix_user_subscriptions_one_active ON user_subscriptions (user_telegram_id) WHERE is_active = true`

**Why:** Telegram can retransmit `successful_payment`. Without idempotency guard, a user gets double-subscribed (or worse, their valid sub is overwritten).

## Benefits keys (allow-list enforced in admin endpoint)
`daily_multiplier`, `daily_bonus_coins`, `pet_feed_free`, `xp_multiplier`, `max_pets_bonus`

## Integration with wallet + pets
- `claim_daily()` accepts `premium_multiplier` and `premium_bonus` — applied server-side only after streak calculation
- `pet.feed()` accepts `feed_free: bool` and `xp_multiplier: float`
- `pet.play()` / `pet.cuddle()` accept `xp_multiplier: float`
- Routes compute benefits via `_get_pet_sub_benefits()` helper — never from client params

## Subscription status in wallet/info
`wallet_info` now returns `subscription: {...}` nested in its response. `renderCasino()` reads `walletData.subscription` — no second API call needed.

## Admin panel tab
`⭐ Подписка` tab in admin.html — config form (enable toggle, price, duration, title, description, benefits sliders), grant form, active subscribers list with revoke buttons.
