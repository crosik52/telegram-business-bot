---
name: Nav redesign — 5-tab architecture
description: Bottom nav collapsed from 7 to 5 tabs; content migrated to inner sub-tab bars
---

## What changed

Bottom nav reduced from 7 tabs (stats/casino/quests/donate/shop/interact/giveaway) to 5:
| Tab | data-tab | Inner sub-tabs |
|-----|----------|---------------|
| Стата | `stats` | Общая / Топ / Детально (stats-pill-bar) |
| Игры | `casino` | Игры / Задания / Рейтинг |
| Магазин | `shop` | Монеты / Подписка |
| Связи | `interact` | — |
| Конкурс | `giveaway` | Розыгрыш / Рефералы |

`quests` and `donate` tabs REMOVED from nav; their content moved inside Casino and Shop/Giveaway.

## Key JS variables added

- `let statsSubTab = "general"` — controls stats pill bar
- `let casinoSubTab = "games"` — controls casino inner tabs
- `let shopSubTab = "coins"` — controls shop inner tabs
- `let giveawaySubTab = "contest"` — controls giveaway inner tabs

## Key functions added

- `switchStatsSubTab(tab)` — toggles stats pill sections, shows/hides `.stats-section[data-section]`
- `switchCasinoSubTab(tab)` — re-calls `renderCasino()` with updated `casinoSubTab`
- `switchShopSubTab(tab)` — re-calls `renderShop()`; "subscription" sub-tab calls `renderSubscriptionSection()`
- `switchGiveawaySubTab(tab)` — re-calls `renderGiveawayTab()`; "referrals" sub-tab calls `renderReferralSection()`
- `_casinoTabBar()`, `_shopTabBar()`, `_giveawayTabBar()` — return the inner tab bar HTML strings

## Data loading changes

- `loadGiveaway()` now parallel-fetches `/app/api/giveaway` + `/app/api/referral/info` (if not yet loaded)
- `loadQuestsLeaderboard()` no longer touches `questContent` div; triggers lazy from casino sub-tab
- `renderQuestsTab()` now just calls `renderCasino()` if the active casino sub-tab is tasks/rating
- `_applyClaimedLocally()` now calls `renderCasino()` instead of writing to `questContent`
- `loadWalletForDonate()` is dead code (kept as no-op stub)

## Premium gate / subscription deep-links

- `attachPremiumGateHandlers` → sets `shopSubTab = "subscription"` then `switchMainTab("shop")`
- AI VIP button → same: `shopSubTab='subscription'; switchMainTab('shop')`
- `_refreshWalletAfterSub()` → calls `renderShop()` instead of `renderDonate()`

## CSS classes added

- `.inner-tab-bar` — sticky row of inner tabs inside casino/shop/giveaway
- `.inner-tab` / `.inner-tab.active`
- `.stats-pill-bar` — sticky stats pill row at top of stats section
- `.stats-pill` / `.stats-pill.active` (blue highlight)

**Why:** User approved the 5-tab canvas mockup. The old 7-tab layout was too cramped on mobile.
**How to apply:** When adding new content to any of these tabs, add a new inner sub-tab button and handle it in the corresponding `render*` function using the sub-tab variable.
