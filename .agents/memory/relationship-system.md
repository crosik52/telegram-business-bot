---
name: Relationship system
description: Durable design decisions for the relationship/couple economy (streaks, gifts, quests, anniversaries, postcards)
---
# Relationship system — durable decisions

- **Every relationship economy mutation (gift, quest claim, anniversary credit, tier upgrade) must lock the relationship row FOR UPDATE before any validation**, then lock the two wallets in normalized user-id order (`_get_wallets_ordered`). **Why:** validation on an unlocked row lets concurrent requests double-credit/double-charge; unordered wallet locks deadlock on reciprocal actions. Review FAILed twice on this.
- Gift catalogue / rewards are server-authoritative: client sends only an id; cost/xp/tier-gating always come from server constants. (Same principle as wallet-security memory.)
- `relationships.meta` is a schemaless JSON Text blob (streak/week counters/anniversary keys) so new mechanics avoid migrations — but it **must be reset to None when a broken row is reused** for a new relationship, or old streak/anniversary state leaks.
- Streak = both partners gift on same UTC date; milestones are one-shot per streak run (list in meta). Anniversaries are processed lazily on /list open, idempotent via a `YYYY-MM` key checked again under the row lock.
- Level cap raise (5→10) required backfilling stored `level` from xp in the migration; stored levels don't self-heal. Upgrade thresholds: friends 3 (intentionally accessible), dating 7.
- Postcard images (`!card`): Pillow + bundled Inter fonts — **Inter has no emoji glyphs**, keep emoji out of image text; always fall back to the text card on render failure.
