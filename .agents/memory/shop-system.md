---
name: Shop system
description: Coin-spending shop tab — models, repository, routes, frontend
---

## Models
- `app/models/boost.py` — `UserBoost(id, owner_telegram_id, boost_type, purchased_at, expires_at)`
- `app/models/user_settings.py` — `UserSettings(id, owner_telegram_id, theme, frame, pinned_chat_id)`
- Both imported in `app/models/__init__.py`; created via `Base.metadata.create_all` at startup (no migrations needed)

## Repository
- `app/repositories/shop_repository.py` — ShopRepository
- Methods: `buy_double_xp`, `buy_theme`, `buy_frame`, `pin_chat`, `gift_coins`, `has_double_xp`, `get_shop_status`
- Double XP stacks by extending existing boost expiry, not creating duplicates

## Routes (in app/miniapp/routes.py)
- `POST /app/api/shop/status` — returns active_boosts, settings, prices
- `POST /app/api/shop/boost` — buys double_xp boost (boostType field)
- `POST /app/api/shop/theme` — buys/applies theme
- `POST /app/api/shop/frame` — buys/applies frame
- `POST /app/api/shop/pin-chat` — pins/unpins a chat (chatId field, null = unpin)
- `POST /app/api/shop/gift` — gifts coins to chat partner (chatId = recipient telegram_id)
- All use camelCase initData field (ShopStatusRequest pattern with resolved_init property)

## Double XP integration
- `_get_pet_sub_benefits()` in routes.py checks `ShopRepository.has_double_xp()` and multiplies xp_multiplier × 2.0

## Prices (in shop_repository.py constants)
- double_xp: 200🪙 / 24h
- pin_chat: 75🪙
- theme: 100🪙
- frame: 150🪙
- gift cost: 30🪙 → recipient gets 50🪙

**Why:** Stacking with subscription xp_multiplier multiplicatively means premium + boost = 4× XP total.

## Frontend (miniapp.html)
- 5th tab: `tab-shop` with accent color `#e11d48` (rose-red)
- SLIDER_STYLES.shop: `rgba(225,29,72,0.20)`
- `loadShop()` fetches wallet/shop-status/pet-list in parallel
- `renderShop()` renders balance bar + 5 category sections
- `openChatPickerModal()` reusable helper for pin/gift chat selection
- Pet card redesigned: centered hero emoji in floating circle, speech bubble, 2×2 button grid, 8px bars
- `petSpeech(p)` generates context-aware speech bubble text
