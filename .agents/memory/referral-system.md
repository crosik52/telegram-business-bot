---
name: Referral system
description: Architecture and key decisions for the referral program (models, activation flow, rewards, admin panel)
---

## Models
- `ReferralConfig` (singleton id=1) — `is_enabled`, `referrer_reward_days`, `referee_reward_days`, `min_account_age_days`, `max_referrals_per_day`, `milestones` (JSON list), `levels` (JSON list)
- `Referral` — `referrer_telegram_id`, `referred_telegram_id` (unique — one referrer per user), `status` (pending/active/fraud), `fraud_reason`, `activated_at`
- `ReferralRewardLog` — immutable audit log of every reward (welcome, per_activation, milestone); FK → referrals with SET NULL

## Deep-link flow
- `/start ref_<id>` parsed in `app/bot/commands.py` → `ReferralRepository.create_referral()` creates a **pending** referral
- Fraud checks at creation: self-referral, already-referred, circular, daily cap

## Activation trigger
- `app/miniapp/routes.py → /app/api/stats` — when a user with a pending referral first opens the mini-app **and has a business connection**, `try_activate()` fires
- Activation grants: referee welcome Premium, referrer per-activation Premium, milestone check (idempotent — checked via ReferralRewardLog)
- Referrer is notified via `bot.send_message` (best-effort, swallowed on failure)

## Premium grant
- `_grant_premium()` extends an existing active sub or creates a new one — does NOT deactivate prior sub, just extends `expires_at`

**Why:** Referral rewards should stack on top of paid subs, not replace them.

## Admin endpoints
All under `/app/api/admin/referral/` — use `_require_admin()` (no `settings` arg):
`stats`, `list`, `config`, `config/update`, `adjust`, `grant`

## Mini-app tab
- 6th tab: `data-tab="referral"`, color `#14b8a6` (teal)
- Shows: hero card with level/progress, ref link + copy/share buttons, stats row, two-sided reward hint, milestone roadmap with progress bars, recent referrals, reward history

## Admin tab
- switchTab branch: `else if (tab === "referral") loadReferralAdminTab()`
- Panel: stats + sparkline, top referrers leaderboard, manual grant form, paginated referral list with status adjustment, full config editor (milestones add/edit/delete)
