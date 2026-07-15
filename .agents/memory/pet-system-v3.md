---
name: Pet system v3
description: Full pet system overhaul — food shop, skill tree, leaderboard, tabbed UI, upgrades column
---

## Overview
Pet system v3 adds a food shop, skill upgrades, leaderboard, and tabbed card UI on top of v2.

## DB
- New column `chat_pets.upgrades String(400)` — JSON dict e.g. `{"xp_boost":1,"hunger_resist":0,...}`
- Migration: `l6m7n8o9p0q1_pet_system_v3.py`

## Food catalog (5 items, kibble→divine)
- `kibble` 20🪙 1×XP, `fish` 40🪙 1.5×, `steak` 80🪙 2×, `cake` 150🪙 3×, `divine` 300🪙 5×
- Premium food also adds mood bonus (+8…+40) and updates last_cuddled_at
- `feed()` accepts `food_type: str = "kibble"` param

## Skill catalog (4 skills)
- `xp_boost`: +30% XP per level, max 3, costs [100,250,500]
- `hunger_resist`: +25% hunger decay time per level, max 3, costs [150,350,700]
- `mood_resist`: +25% mood decay time per level, max 3, costs [150,350,700]
- `lucky_paw`: 15% × level chance to earn coins during play, max 2, costs [200,500]

## Repository helpers
- `_get_upgrades(pet)` / `_set_upgrades(pet, dict)` — JSON parse/serialize to `pet.upgrades`
- `_compute_hunger` / `_compute_mood` — apply skill bonus multipliers to decay time
- `buy_upgrade(owner_id, pet_id, skill)` — deducts cost, increments level in upgrades JSON
- `get_leaderboard(limit=20)` — top alive pets by XP, returns rank/emoji/level/days_alive

## Routes
- `POST /app/api/pet/feed` — now accepts `foodType` field (default "kibble")
- `POST /app/api/pet/upgrade` — `PetUpgradeRequest(initData, petId, skill)`
- `POST /app/api/pet/leaderboard` — reuses `StatsRequest`, returns `{leaderboard: [...]}`

## Frontend JS (miniapp.html)
- `petActiveTabs = {}` — maps petId → active tab name ('info'|'food'|'skills'|'top')
- `petLeaderboard` — cached globally, loaded lazily on first 🏆 tab open
- `switchPetTab(petId, tab)` — show/hide panes WITHOUT re-rendering
- `doPetFeed(petId, foodType, btn)` — handles all food purchases
- `doPetAction(petId, action, btn)` — play/cuddle; shows lucky_paw coin toast
- `doPetUpgrade(petId, skill, btn)` — skill purchase
- `loadLeaderboard()` — fetch + cache, updates ALL `.pet-tab-pane[data-tab="top"]`

## Pet card structure
Hero (avatar + speech + name + badges) → tab row → 4 panes:
- Info: XP bar, hunger/mood bars, achievement badges, 2×2 action grid (Покормить/Поиграть/Обнять/Имя)
- Еда: 5 food items in 2-col grid (last item full-width if odd), all disabled on cooldown
- Навыки: 4 skill rows with progress dots and upgrade button
- Топ: lazy-loaded leaderboard, top 20

**Why:** Each skill has unique effect calling into _compute_hunger/_compute_mood or play() — consistent with server-derived stat model. petActiveTabs persists across _refreshPets() calls so tab state is retained after any action.
