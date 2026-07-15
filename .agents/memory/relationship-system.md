---
name: Relationship system
description: 3-tier relationship bonds between mutually-connected bot users, with daily gifts, tier upgrades, and the !card chat command
---

## Architecture
- **Model**: `app/models/relationship.py` — `Relationship` table, pair normalised as `user_a_id < user_b_id`, `UniqueConstraint("user_a_id","user_b_id","uq_relationship_pair")`
- **Migration**: `alembic/versions/n8o9p0q1r2s3_relationships.py` (down_revision `m7n8o9p0q1r2`)
- **Repository**: `app/repositories/relationship_repository.py` — `RelationshipRepository`
- **Routes**: 7 endpoints under `/app/api/relationships/{list,request,respond,cancel,gift,upgrade,break}`
- **Chat command**: `!card` / `!открытка [текст]` in `app/business/commands.py`

## Economy constants (in `app/models/relationship.py`)
- REQUEST_COST 50🪙, GIFT_COST 50🪙, GIFT_TO_PARTNER 40🪙, GIFT_XP 100, GIFT_COOLDOWN_H 20
- UPGRADE_COSTS `{friends:300, dating:1000}`, UPGRADE_MIN_LEVEL `{friends:3, dating:5}`
- MARRIAGE_DAILY_BONUS 100🪙 per active marriage (added in daily claim route)
- XP_PER_LEVEL 200, MAX_REL_LEVEL 5

## Tiers
1. `friends` (💛) — request costs 50🪙
2. `dating` (❤️) — upgrade from friends level 3+, costs 300🪙
3. `married` (💍) — upgrade from dating level 5, costs 1000🪙

## Stats integration
- `top_interlocutors` in stats response now includes `"relationship"` field per contact (via `_enrich_interlocutors`)
- Relationships fetched alongside stats in `loadStats` Promise.all — stored in `lastRelationships` JS dict keyed by partner_id

## !card command
- Only works if active relationship exists between owner and chat contact
- Sends card visible IN the business chat (uses `business_connection_id`)
- Tier-specific templates + optional custom text: `!card С днём рождения!`
- `_build_card(rel_type, custom_text)` helper in commands.py

**Why pair normalisation matters:** Without min/max enforcement, A→B and B→A create two rows. The `_pair()` static method in the repository enforces this for every write and read.
