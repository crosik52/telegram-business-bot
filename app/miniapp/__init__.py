"""Per-user Telegram Mini App: personal communication statistics.

Unlike `app/dashboard/` (a single admin-only web dashboard protected by a
username/password login), this mini app is meant to be opened by *any*
connected user directly inside Telegram. It has no admin rights and shows
only that user's own data, identified via Telegram WebApp `initData`
(HMAC-signed by Telegram, verified server-side) — no separate login step.
"""
