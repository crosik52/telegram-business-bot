"""PetRepository — shared virtual pet logic (v2).

Stats
-----
- Hunger  : 0-100, decays from last_fed_at (or born_at) over HUNGER_DECAY_HOURS
- Mood    : 0-100, decays from last interaction (play/cuddle/born) over MOOD_DECAY_HOURS
- XP      : cumulative, gained from all interactions
- Level   : 1-50, derived from XP via sqrt formula

Personality traits
------------------
playful   : play gives 2× mood, play cooldown → 3 h
lazy      : hunger decays 20 % slower, mood decays 20 % slower
gluttonous: feed costs 15 coins instead of 20, feed cooldown → 18 h
brave     : immune to streak-break death (only starvation kills)
shy       : cuddle gives 2× mood

Death
-----
- Hunger = 0                  → starvation
- No messages in 48 h (after grace) AND personality != brave → streak_broken

Security
--------
- All coin mutations use row-level locks (.with_for_update())
- Personality, XP and level are derived server-side only
"""

from __future__ import annotations

import datetime as dt
import json
import math
import random

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_connection import BusinessConnection
from app.models.message import Message
from app.models.pet import ChatPet
from app.models.wallet import UserWallet
from app.repositories.relationship_repository import RelationshipRepository

# ── Catalogue ─────────────────────────────────────────────────────────────────

SPECIES: dict[str, dict] = {
    "cat":     {"stages": ["🥚", "🐱", "🐱", "🐱", "😺"],  "label": "Котик"},
    "dog":     {"stages": ["🥚", "🐶", "🐶", "🐕", "🦮"],  "label": "Пёсик"},
    "rabbit":  {"stages": ["🥚", "🐰", "🐰", "🐇", "🐇"],  "label": "Зайка"},
    "hamster": {"stages": ["🥚", "🐹", "🐹", "🐹", "🐹"],  "label": "Хомяк"},
    "fox":     {"stages": ["🥚", "🦊", "🦊", "🦊", "🦊"],  "label": "Лисёнок"},
    "dragon":  {"stages": ["🥚", "🐣", "🐲", "🐲", "🐉"],  "label": "Дракон"},
    "penguin": {"stages": ["🥚", "🐧", "🐧", "🐧", "🐧"],  "label": "Пингвин"},
    "bear":    {"stages": ["🥚", "🐻", "🐻", "🐻", "🐻"],  "label": "Медведь"},
}

PERSONALITIES: dict[str, dict] = {
    "playful":    {"emoji": "🎮", "label": "Игривый"},
    "lazy":       {"emoji": "😴", "label": "Ленивый"},
    "gluttonous": {"emoji": "🍕", "label": "Обжора"},
    "brave":      {"emoji": "🦁", "label": "Храбрый"},
    "shy":        {"emoji": "🌸", "label": "Застенчивый"},
}

PET_NAMES = [
    "Пушок", "Мурзик", "Бублик", "Рыжик", "Снежок", "Барсик",
    "Пончик", "Печенька", "Карамель", "Зефир", "Кекс", "Плюша",
    "Батон", "Вафля", "Мармелад", "Сухарик", "Нугат", "Безе",
    "Круассан", "Тирамису", "Профитроль", "Эклер",
]

# ── Constants ─────────────────────────────────────────────────────────────────

FEED_COST              = 20    # coins (gluttonous: 15)
RENAME_COST            = 50    # coins
HUNGER_DECAY_HOURS     = 72    # lazy: 90
MOOD_DECAY_HOURS       = 36    # lazy: 45
FEED_COOLDOWN_HOURS    = 22    # gluttonous: 18
PLAY_COOLDOWN_HOURS    = 4     # playful: 3
CUDDLE_COOLDOWN_HOURS  = 1
STREAK_GRACE_HOURS     = 48

FEED_XP    = 15
PLAY_XP    = 25
CUDDLE_XP  = 10

PLAY_MOOD_GAIN   = 30   # playful: 45
CUDDLE_MOOD_GAIN = 15   # shy: 22

MAX_LEVEL = 50

# Play messages to send to the chat partner
PLAY_MESSAGES = [
    "🎾 {name} бросился ловить мяч и промахнулся… но очень старался!",
    "🎪 {name} крутился на месте так долго, что упал и заснул.",
    "🪀 {name} наблюдал за йо-йо полчаса с открытым ртом.",
    "🧸 {name} победил плюшевого мишку в честной схватке.",
    "🎠 {name} нашёл коробку и сидит в ней с довольным видом.",
    "🌀 {name} погнался за своим хвостом — и почти поймал!",
    "🎈 {name} лопнул воздушный шарик и испугался сам себя.",
    "🧩 {name} разобрал пазл. Собирать не стал — зачем?",
]

# ── Food catalogue ─────────────────────────────────────────────────────────────

FOOD_CATALOG: dict[str, dict] = {
    "kibble": {
        "name": "Корм",      "emoji": "🥣", "cost": 20,
        "xp_mult": 1.0, "mood_bonus": 0,
        "desc": "Обычная еда",
    },
    "fish": {
        "name": "Рыбка",     "emoji": "🐟", "cost": 40,
        "xp_mult": 1.5, "mood_bonus": 8,
        "desc": "+50% XP · настрой +8",
    },
    "steak": {
        "name": "Стейк",     "emoji": "🥩", "cost": 80,
        "xp_mult": 2.0, "mood_bonus": 15,
        "desc": "+100% XP · настрой +15",
    },
    "cake": {
        "name": "Тортик",    "emoji": "🎂", "cost": 150,
        "xp_mult": 3.0, "mood_bonus": 25,
        "desc": "+200% XP · настрой +25",
    },
    "divine": {
        "name": "Звёздный",  "emoji": "✨", "cost": 300,
        "xp_mult": 5.0, "mood_bonus": 40,
        "desc": "+400% XP · настрой +40",
    },
}

# ── Skill catalogue ────────────────────────────────────────────────────────────

SKILL_CATALOG: dict[str, dict] = {
    "xp_boost": {
        "name": "Умник", "emoji": "🧠",
        "desc": "+30% к XP за каждый уровень прокачки",
        "costs": [100, 250, 500],
        "max_level": 3,
    },
    "hunger_resist": {
        "name": "Сытость", "emoji": "🍽️",
        "desc": "Голод падает на 25% медленнее за уровень",
        "costs": [150, 350, 700],
        "max_level": 3,
    },
    "mood_resist": {
        "name": "Оптимизм", "emoji": "😊",
        "desc": "Настроение падает на 25% медленнее за уровень",
        "costs": [150, 350, 700],
        "max_level": 3,
    },
    "lucky_paw": {
        "name": "Удача", "emoji": "🍀",
        "desc": "15% шанс найти монеты во время игры",
        "costs": [200, 500],
        "max_level": 2,
    },
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _personality_hunger_hours(p: str) -> float:
    return HUNGER_DECAY_HOURS * 1.2 if p == "lazy" else HUNGER_DECAY_HOURS


def _personality_mood_hours(p: str) -> float:
    return MOOD_DECAY_HOURS * 1.25 if p == "lazy" else MOOD_DECAY_HOURS


def _personality_feed_cost(p: str) -> int:
    return 15 if p == "gluttonous" else FEED_COST


def _personality_feed_cooldown(p: str) -> float:
    return 18.0 if p == "gluttonous" else FEED_COOLDOWN_HOURS


def _personality_play_cooldown(p: str) -> float:
    return 3.0 if p == "playful" else PLAY_COOLDOWN_HOURS


def _personality_play_mood(p: str) -> int:
    return PLAY_MOOD_GAIN * 2 if p == "playful" else PLAY_MOOD_GAIN


def _personality_cuddle_mood(p: str) -> int:
    return CUDDLE_MOOD_GAIN * 2 if p == "shy" else CUDDLE_MOOD_GAIN


def _tz_aware(ts: dt.datetime) -> dt.datetime:
    """Ensure *ts* is timezone-aware (SQLite/aiosqlite may strip tzinfo)."""
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=dt.timezone.utc)


def _compute_hunger(pet: ChatPet, now: dt.datetime) -> int:
    ref = _tz_aware(pet.last_fed_at or pet.born_at)
    hours = max(0.0, (now - ref).total_seconds() / 3600)
    decay_h = _personality_hunger_hours(pet.personality)
    # hunger_resist skill: +25% decay time per level → hunger falls slower
    ups = _get_upgrades(pet)
    decay_h *= 1.0 + ups.get("hunger_resist", 0) * 0.25
    return max(0, round(100 - hours / decay_h * 100))


def _compute_mood(pet: ChatPet, now: dt.datetime) -> int:
    """Decay from the stored mood value since the last interaction timestamp.

    pet.mood stores the mood at the time of the last action.
    last_cuddled_at / last_played_at are REAL timestamps (not backdated),
    so cooldown checks remain independent and trustworthy.
    """
    ref = _tz_aware(pet.last_cuddled_at or pet.last_played_at or pet.born_at)
    hours = max(0.0, (now - ref).total_seconds() / 3600)
    decay_h = _personality_mood_hours(pet.personality)
    # mood_resist skill: +25% decay time per level → mood falls slower
    ups = _get_upgrades(pet)
    decay_h *= 1.0 + ups.get("mood_resist", 0) * 0.25
    return max(0, round(pet.mood - hours / decay_h * 100))


def _compute_stage(born_at: dt.datetime, now: dt.datetime) -> int:
    days = (now - _tz_aware(born_at)).days
    if days == 0: return 1
    if days <= 6: return 2
    if days <= 13: return 3
    if days <= 29: return 4
    return 5


def _compute_level(xp: int) -> int:
    """Level 1-50. Each level needs progressively more XP (sqrt curve)."""
    return min(MAX_LEVEL, math.isqrt(max(0, xp) // 8) + 1)


def _xp_for_next_level(level: int) -> int:
    """Total XP needed to reach the *next* level."""
    return ((level) ** 2) * 8


def _display_name(first, last, username, chat_id: int) -> str:
    parts = [p for p in (first, last) if p]
    if parts: return " ".join(parts)
    if username: return f"@{username}"
    return f"Собеседник {chat_id}"


def _pet_partner_id(pet: "ChatPet", uid: int) -> int:
    """Return the ID of the OTHER user in the shared pet pair."""
    return pet.user_b_id if pet.user_a_id == uid else pet.user_a_id


def _pet_dict(
    pet: "ChatPet",
    now: dt.datetime,
    rel_tier: str | None = None,
    viewer_id: int | None = None,
) -> dict:
    hunger = _compute_hunger(pet, now) if pet.is_alive else 0
    mood   = _compute_mood(pet, now)   if pet.is_alive else 0
    level  = _compute_level(pet.xp)
    next_xp = _xp_for_next_level(level)
    p_info  = PERSONALITIES.get(pet.personality, {"emoji": "❓", "label": "?"})
    from app.models.relationship import REL_XP_BONUS
    rel_bonus = REL_XP_BONUS.get(rel_tier, 1.0) if rel_tier else 1.0
    # Resolve the interlocutor name from the viewer's perspective.
    # If viewer is NOT the adopter (owner_telegram_id), the interlocutor is
    # the adopter — show partner_name stored at adoption time.
    if viewer_id is not None and viewer_id != pet.owner_telegram_id:
        interlocutor_name = pet.partner_name or f"Собеседник {pet.owner_telegram_id}"
    else:
        interlocutor_name = pet.interlocutor_name
    return {
        "id":               pet.id,
        "chat_id":          pet.chat_id,
        "pet_name":         pet.pet_name,
        "species":          pet.species,
        "stage":            _compute_stage(pet.born_at, now),
        "hunger":           hunger,
        "mood":             mood,
        "xp":               pet.xp,
        "level":            level,
        "xp_for_next":      next_xp,
        "personality":      pet.personality,
        "personality_emoji": p_info["emoji"],
        "personality_label": p_info["label"],
        "is_alive":         pet.is_alive,
        "interlocutor_name": interlocutor_name,
        "born_at":          pet.born_at.isoformat(),
        "last_fed_at":      pet.last_fed_at.isoformat() if pet.last_fed_at else None,
        "last_played_at":   pet.last_played_at.isoformat() if pet.last_played_at else None,
        "last_cuddled_at":  pet.last_cuddled_at.isoformat() if pet.last_cuddled_at else None,
        "died_at":          pet.died_at.isoformat() if pet.died_at else None,
        "death_cause":      pet.death_cause,
        "days_alive":       (now - _tz_aware(pet.born_at)).days,
        "total_feedings":   pet.total_feedings,
        "total_plays":      pet.total_plays,
        "total_cuddles":    pet.total_cuddles,
        "feed_streak":      pet.feed_streak,
        # Cooldown helpers (seconds remaining, 0 = ready)
        "feed_cost":        _personality_feed_cost(pet.personality),
        "play_cooldown_secs":   _cooldown_secs(pet.last_played_at, _personality_play_cooldown(pet.personality), now),
        "cuddle_cooldown_secs": _cooldown_secs(pet.last_cuddled_at, CUDDLE_COOLDOWN_HOURS, now),
        "feed_cooldown_secs":   _cooldown_secs(pet.last_fed_at, _personality_feed_cooldown(pet.personality), now),
        "upgrades":             _get_upgrades(pet),
        # Relationship XP bonus for this pet's chat
        "rel_tier":         rel_tier,
        "rel_bonus":        rel_bonus,
        # Revival
        "revival_count":    pet.revival_count,
        "max_revivals":     pet.max_revivals,
        "can_revive": (
            not pet.is_alive
            and pet.revival_count < pet.max_revivals
            and pet.died_at is not None
            and (now - _tz_aware(pet.died_at)).total_seconds() <= 3 * 86400
        ),
    }


def _cooldown_secs(last_at: dt.datetime | None, hours: float, now: dt.datetime) -> int:
    if last_at is None:
        return 0
    elapsed = (now - _tz_aware(last_at)).total_seconds()
    remaining = hours * 3600 - elapsed
    return max(0, int(remaining))


def _get_upgrades(pet: "ChatPet") -> dict:
    if not pet.upgrades:
        return {"xp_boost": 0, "hunger_resist": 0, "mood_resist": 0, "lucky_paw": 0}
    try:
        d = json.loads(pet.upgrades)
        return {k: int(d.get(k, 0)) for k in ("xp_boost", "hunger_resist", "mood_resist", "lucky_paw")}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {"xp_boost": 0, "hunger_resist": 0, "mood_resist": 0, "lucky_paw": 0}


def _set_upgrades(pet: "ChatPet", upgrades: dict) -> None:
    pet.upgrades = json.dumps(upgrades)


# ── Repository ────────────────────────────────────────────────────────────────

class PetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _get_conn_ids(self, owner_telegram_id: int) -> list[str]:
        result = await self._session.execute(
            select(BusinessConnection.business_connection_id).where(
                BusinessConnection.user_telegram_id == owner_telegram_id
            )
        )
        return [r[0] for r in result.all()]

    async def get_pets(self, owner_telegram_id: int) -> tuple[list[dict], list[dict]]:
        now = dt.datetime.now(dt.timezone.utc)
        conn_ids = await self._get_conn_ids(owner_telegram_id)

        # Fetch all pets where this user is either side of the pair
        pets: list[ChatPet] = list(
            (await self._session.execute(
                select(ChatPet)
                .where(
                    (ChatPet.user_a_id == owner_telegram_id)
                    | (ChatPet.user_b_id == owner_telegram_id)
                )
                .order_by(ChatPet.born_at.desc())
            )).scalars().all()
        )

        alive_pets = [p for p in pets if p.is_alive]
        changed = False

        if alive_pets and conn_ids:
            two_days_ago = now - dt.timedelta(hours=48)
            # Build the list of partner IDs (from the viewer's perspective)
            alive_partner_ids = [_pet_partner_id(p, owner_telegram_id) for p in alive_pets]

            # ── Side A: viewer's own BC — messages in the chat with each partner ──
            # Includes both viewer→partner and partner→viewer messages captured by
            # the viewer's bot connection.
            viewer_recent: set[int] = {
                r[0] for r in (
                    await self._session.execute(
                        select(Message.chat_id.distinct()).where(
                            Message.business_connection_id.in_(conn_ids),
                            Message.chat_id.in_(alive_partner_ids),
                            Message.chat_id != owner_telegram_id,
                            Message.sent_at >= two_days_ago,
                            Message.is_deleted.is_(False),
                        )
                    )
                ).all()
            }

            # ── Side B: partner's own BC — messages they sent to the viewer ────────
            # Covers the case where A's BC doesn't capture B's messages but B does
            # have their own BC connected.  One batched query across all partners.
            partner_active: set[int] = set()
            partner_bc_rows = (
                await self._session.execute(
                    select(
                        BusinessConnection.user_telegram_id,
                        BusinessConnection.business_connection_id,
                    ).where(
                        BusinessConnection.user_telegram_id.in_(alive_partner_ids)
                    )
                )
            ).all()
            if partner_bc_rows:
                all_partner_conn_ids = [r[1] for r in partner_bc_rows]
                conn_to_partner: dict[str, int] = {r[1]: r[0] for r in partner_bc_rows}
                active_conn_rows = (
                    await self._session.execute(
                        select(Message.business_connection_id.distinct()).where(
                            Message.business_connection_id.in_(all_partner_conn_ids),
                            Message.chat_id == owner_telegram_id,
                            Message.sent_at >= two_days_ago,
                            Message.is_deleted.is_(False),
                        )
                    )
                ).all()
                partner_active = {
                    conn_to_partner[r[0]]
                    for r in active_conn_rows
                    if r[0] in conn_to_partner
                }

            # Pet is "in contact" if EITHER side has recent messages in the chat
            recent_chats = viewer_recent | partner_active

            newly_dead: list[ChatPet] = []
            for pet in alive_pets:
                partner_id = _pet_partner_id(pet, owner_telegram_id)
                hunger = _compute_hunger(pet, now)
                if hunger == 0:
                    pet.is_alive = False
                    pet.death_cause = "starvation"
                    pet.died_at = now
                    changed = True
                    newly_dead.append(pet)
                elif partner_id not in recent_chats and pet.personality != "brave":
                    age_hours = (now - _tz_aware(pet.born_at)).total_seconds() / 3600
                    if age_hours > STREAK_GRACE_HOURS:
                        pet.is_alive = False
                        pet.death_cause = "streak_broken"
                        pet.died_at = now
                        changed = True
                        newly_dead.append(pet)

        if changed:
            await self._session.flush()
            # No mirrors to propagate — the single shared row is already dead.

        alive_out = [p for p in pets if p.is_alive]
        dead_out  = [p for p in pets if not p.is_alive][:3]

        # Relationship tier → XP bonus badge.
        # Only include the bonus when the partner still has an active BusinessConnection.
        rel_tier_map: dict[int, str] = {}
        if alive_out:
            rel_repo = RelationshipRepository(self._session)
            owner_rels = await rel_repo.get_for_user(owner_telegram_id)
            raw_map: dict[int, str] = {}
            for r in owner_rels:
                if r.status == "active":
                    partner = r.user_b_id if r.user_a_id == owner_telegram_id else r.user_a_id
                    raw_map[partner] = r.rel_type

            if raw_map:
                partner_ids = list(raw_map.keys())
                connected_ids: set[int] = {
                    r[0]
                    for r in (
                        await self._session.execute(
                            select(BusinessConnection.user_telegram_id).where(
                                BusinessConnection.user_telegram_id.in_(partner_ids),
                                BusinessConnection.is_enabled.is_(True),
                            )
                        )
                    ).all()
                }
                rel_tier_map = {k: v for k, v in raw_map.items() if k in connected_ids}

        pets_out = [
            _pet_dict(
                p, now,
                rel_tier=rel_tier_map.get(_pet_partner_id(p, owner_telegram_id)) if p.is_alive else None,
                viewer_id=owner_telegram_id,
            )
            for p in alive_out + dead_out
        ]

        # Available chats — exclude partners we already have an alive pet with
        alive_pet_chats: set[int] = {_pet_partner_id(p, owner_telegram_id) for p in alive_out}
        available_chats: list[dict] = []
        if conn_ids:
            two_days_ago = now - dt.timedelta(hours=48)
            activity_rows = (
                await self._session.execute(
                    select(Message.chat_id, func.count(Message.id).label("cnt"))
                    .where(
                        Message.business_connection_id.in_(conn_ids),
                        Message.chat_id != owner_telegram_id,
                        Message.sent_at >= two_days_ago,
                        Message.is_deleted.is_(False),
                    )
                    .group_by(Message.chat_id)
                    .order_by(func.count(Message.id).desc())
                    .limit(20)
                )
            ).all()
            candidate_ids = [r[0] for r in activity_rows if r[0] not in alive_pet_chats]
            counts = {r[0]: r[1] for r in activity_rows}

            if candidate_ids:
                mutual_rows = (
                    await self._session.execute(
                        select(BusinessConnection.user_telegram_id).where(
                            BusinessConnection.user_telegram_id.in_(candidate_ids)
                        )
                    )
                ).all()
                mutual_ids: set[int] = {r[0] for r in mutual_rows}
                candidate_ids = [cid for cid in candidate_ids if cid in mutual_ids]

            if candidate_ids:
                name_rows = (
                    await self._session.execute(
                        select(
                            Message.chat_id,
                            Message.sender_first_name,
                            Message.sender_last_name,
                            Message.sender_username,
                        )
                        .where(
                            Message.business_connection_id.in_(conn_ids),
                            Message.chat_id.in_(candidate_ids),
                            Message.sender_telegram_id != owner_telegram_id,
                            Message.sender_telegram_id.is_not(None),
                            Message.is_deleted.is_(False),
                        )
                        .distinct(Message.chat_id)
                        .order_by(Message.chat_id, Message.sent_at.desc())
                    )
                ).all()
                names: dict[int, str] = {
                    r[0]: _display_name(r[1], r[2], r[3], r[0]) for r in name_rows
                }
                for cid in candidate_ids:
                    available_chats.append({
                        "chat_id": cid,
                        "display_name": names.get(cid) or f"Собеседник {cid}",
                        "message_count": counts[cid],
                    })

        return pets_out, available_chats

    async def adopt(
        self,
        owner_telegram_id: int,
        chat_id: int,
        species: str,
        pet_name: str,
    ) -> dict:
        if species not in SPECIES:
            raise ValueError("invalid_species")

        pet_name = pet_name.strip()[:30] or random.choice(PET_NAMES)
        personality = random.choice(list(PERSONALITIES.keys()))

        # Partner must have the bot connected
        partner_conn = (
            await self._session.execute(
                select(BusinessConnection.business_connection_id).where(
                    BusinessConnection.user_telegram_id == chat_id
                ).limit(1)
            )
        ).scalar_one_or_none()
        if not partner_conn:
            raise ValueError("partner_not_connected")

        # Canonical pair key
        user_a_id = min(owner_telegram_id, chat_id)
        user_b_id = max(owner_telegram_id, chat_id)

        # Check for existing alive pet for this pair (shared row)
        existing = (
            await self._session.execute(
                select(ChatPet).where(
                    ChatPet.user_a_id == user_a_id,
                    ChatPet.user_b_id == user_b_id,
                    ChatPet.is_alive.is_(True),
                )
            )
        ).scalar_one_or_none()
        if existing:
            raise ValueError("pet_exists")

        # Resolve interlocutor name: how chat_id looks from owner's BC
        conn_ids = await self._get_conn_ids(owner_telegram_id)
        name_row = None
        if conn_ids:
            name_row = (
                await self._session.execute(
                    select(
                        Message.sender_first_name,
                        Message.sender_last_name,
                        Message.sender_username,
                    )
                    .where(
                        Message.business_connection_id.in_(conn_ids),
                        Message.chat_id == chat_id,
                        Message.is_outgoing.is_(False),
                        Message.is_deleted.is_(False),
                    )
                    .order_by(Message.sent_at.desc())
                    .limit(1)
                )
            ).first()
        interlocutor_name = (
            _display_name(name_row[0], name_row[1], name_row[2], chat_id)
            if name_row else f"Собеседник {chat_id}"
        )

        # Resolve partner_name: how owner looks from chat_id's BC
        b_conn_ids = await self._get_conn_ids(chat_id)
        owner_name_row = None
        if b_conn_ids:
            owner_name_row = (
                await self._session.execute(
                    select(
                        Message.sender_first_name,
                        Message.sender_last_name,
                        Message.sender_username,
                    )
                    .where(
                        Message.business_connection_id.in_(b_conn_ids),
                        Message.chat_id == owner_telegram_id,
                        Message.sender_telegram_id == owner_telegram_id,
                        Message.is_deleted.is_(False),
                    )
                    .order_by(Message.sent_at.desc())
                    .limit(1)
                )
            ).first()
        owner_display_name = (
            _display_name(owner_name_row[0], owner_name_row[1], owner_name_row[2], owner_telegram_id)
            if owner_name_row else f"Собеседник {owner_telegram_id}"
        )

        now = dt.datetime.now(dt.timezone.utc)
        pet = ChatPet(
            owner_telegram_id=owner_telegram_id,
            chat_id=chat_id,
            user_a_id=user_a_id,
            user_b_id=user_b_id,
            pet_name=pet_name,
            species=species,
            interlocutor_name=interlocutor_name,
            partner_name=owner_display_name,
            personality=personality,
            is_alive=True,
            born_at=now,
        )
        self._session.add(pet)
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            raise ValueError("pet_exists")

        return _pet_dict(pet, now, viewer_id=owner_telegram_id)

    async def feed(
        self,
        owner_telegram_id: int,
        pet_id: int,
        *,
        food_type: str = "kibble",
        feed_free: bool = False,
        xp_multiplier: float = 1.0,
    ) -> dict:
        food = FOOD_CATALOG.get(food_type) or FOOD_CATALOG["kibble"]
        now  = dt.datetime.now(dt.timezone.utc)
        pet  = await self._get_alive_pet(owner_telegram_id, pet_id)

        cooldown = _personality_feed_cooldown(pet.personality)
        if pet.last_fed_at:
            hours_since = (now - _tz_aware(pet.last_fed_at)).total_seconds() / 3600
            if hours_since < cooldown:
                raise ValueError("already_fed")

        # feed_free (subscription / boost perk) only waives the base kibble
        # cost.  Premium food always costs its own price to prevent a trivial
        # exploit where a subscriber passes food_type="divine" to get 5× XP
        # and +40 mood for 0 coins.
        cost = 0 if (feed_free and food_type == "kibble") else food["cost"]
        wallet = await self._lock_wallet(owner_telegram_id)
        if wallet is None:
            raise ValueError("insufficient_coins")
        if cost > 0 and wallet.balance < cost:
            raise ValueError("insufficient_coins")

        if cost > 0:
            wallet.balance    = max(0, wallet.balance - cost)
            wallet.total_spent = max(0, wallet.total_spent + cost)

        # Compute feed streak BEFORE updating last_fed_at
        prev_fed = pet.last_fed_at
        if prev_fed and (now - _tz_aware(prev_fed)).total_seconds() < 26 * 3600:
            pet.feed_streak += 1
        else:
            pet.feed_streak = 1

        # XP: food multiplier × xp_boost skill × subscription/shop multiplier × relationship bonus
        ups = _get_upgrades(pet)
        skill_xp_mult = 1.0 + ups.get("xp_boost", 0) * 0.30
        rel_repo      = RelationshipRepository(self._session)
        partner_id    = _pet_partner_id(pet, owner_telegram_id)
        rel_tier, rel_level = await rel_repo.get_active_bond(owner_telegram_id, partner_id)
        rel_mult      = rel_repo.rel_xp_multiplier(rel_tier, rel_level)
        total_mult    = food["xp_mult"] * skill_xp_mult * max(1.0, xp_multiplier) * rel_mult
        xp_gained     = round(FEED_XP * total_mult)

        pet.last_fed_at    = now
        pet.total_feedings += 1
        pet.xp             += xp_gained
        pet.level          = _compute_level(pet.xp)

        # Mood bonus from premium food (update ref timestamp so mood doesn't re-decay)
        current_mood = _compute_mood(pet, now)
        new_mood     = current_mood
        if food["mood_bonus"] > 0:
            new_mood = min(100, current_mood + food["mood_bonus"])
            pet.mood            = new_mood
            pet.last_cuddled_at = now

        await self._session.flush()
        return {
            "hunger":      100,
            "mood":        new_mood,
            "xp":          pet.xp,
            "level":       pet.level,
            "feed_streak": pet.feed_streak,
            "new_balance": wallet.balance,
            "feed_cost":   cost,
            "food_type":   food_type,
            "food_emoji":  food["emoji"],
            "xp_gained":   xp_gained,
            "rel_tier":    rel_tier,
            "rel_bonus":   rel_mult,
        }

    async def play(self, owner_telegram_id: int, pet_id: int, *, xp_multiplier: float = 1.0) -> dict:
        now = dt.datetime.now(dt.timezone.utc)
        pet = await self._get_alive_pet(owner_telegram_id, pet_id)

        cooldown = _personality_play_cooldown(pet.personality)
        if pet.last_played_at:
            hours_since = (now - _tz_aware(pet.last_played_at)).total_seconds() / 3600
            if hours_since < cooldown:
                raise ValueError("play_cooldown")

        mood_gain    = _personality_play_mood(pet.personality)
        current_mood = _compute_mood(pet, now)
        new_mood     = min(100, current_mood + mood_gain)

        pet.mood            = new_mood
        pet.last_played_at  = now
        pet.last_cuddled_at = now

        pet.total_plays += 1

        ups = _get_upgrades(pet)
        skill_xp_mult = 1.0 + ups.get("xp_boost", 0) * 0.30
        rel_repo      = RelationshipRepository(self._session)
        partner_id    = _pet_partner_id(pet, owner_telegram_id)
        rel_tier, rel_level = await rel_repo.get_active_bond(owner_telegram_id, partner_id)
        rel_mult      = rel_repo.rel_xp_multiplier(rel_tier, rel_level)
        xp_gained     = round(PLAY_XP * skill_xp_mult * max(1.0, xp_multiplier) * rel_mult)
        pet.xp       += xp_gained
        pet.level     = _compute_level(pet.xp)

        # Lucky Paw: chance to earn coins while playing
        coins_found = 0
        new_balance: int | None = None
        lucky_level = ups.get("lucky_paw", 0)
        if lucky_level > 0 and random.random() < lucky_level * 0.15:
            coins_found = random.randint(5 * lucky_level, 15 * lucky_level)
            wallet = await self._lock_wallet(owner_telegram_id)
            if wallet is not None:
                wallet.balance      = min(999_999, wallet.balance + coins_found)
                wallet.total_earned = max(0, wallet.total_earned + coins_found)
                new_balance         = wallet.balance

        play_msg = random.choice(PLAY_MESSAGES).format(name=pet.pet_name)
        await self._session.flush()
        return {
            "mood":        new_mood,
            "xp":          pet.xp,
            "level":       pet.level,
            "xp_gained":   xp_gained,
            "play_msg":    play_msg,
            "coins_found": coins_found,
            "new_balance": new_balance,
            "rel_tier":    rel_tier,
            "rel_bonus":   rel_mult,
        }

    async def cuddle(self, owner_telegram_id: int, pet_id: int, *, xp_multiplier: float = 1.0) -> dict:
        now = dt.datetime.now(dt.timezone.utc)
        pet = await self._get_alive_pet(owner_telegram_id, pet_id)

        if pet.last_cuddled_at:
            hours_since = (now - _tz_aware(pet.last_cuddled_at)).total_seconds() / 3600
            if hours_since < CUDDLE_COOLDOWN_HOURS:
                raise ValueError("cuddle_cooldown")

        mood_gain    = _personality_cuddle_mood(pet.personality)
        current_mood = _compute_mood(pet, now)
        new_mood     = min(100, current_mood + mood_gain)

        pet.mood            = new_mood
        pet.last_cuddled_at = now
        pet.total_cuddles   += 1

        ups = _get_upgrades(pet)
        skill_xp_mult = 1.0 + ups.get("xp_boost", 0) * 0.30
        rel_repo      = RelationshipRepository(self._session)
        partner_id    = _pet_partner_id(pet, owner_telegram_id)
        rel_tier, rel_level = await rel_repo.get_active_bond(owner_telegram_id, partner_id)
        rel_mult      = rel_repo.rel_xp_multiplier(rel_tier, rel_level)
        xp_gained     = round(CUDDLE_XP * skill_xp_mult * max(1.0, xp_multiplier) * rel_mult)
        pet.xp       += xp_gained
        pet.level     = _compute_level(pet.xp)

        await self._session.flush()
        return {
            "mood":      new_mood,
            "xp":        pet.xp,
            "level":     pet.level,
            "xp_gained": xp_gained,
            "rel_tier":  rel_tier,
            "rel_bonus": rel_mult,
        }

    async def rename(self, owner_telegram_id: int, pet_id: int, new_name: str) -> dict:
        now = dt.datetime.now(dt.timezone.utc)
        pet = await self._get_alive_pet(owner_telegram_id, pet_id)

        new_name = new_name.strip()[:30]
        if not new_name:
            raise ValueError("invalid_name")

        wallet = await self._lock_wallet(owner_telegram_id)
        if wallet is None or wallet.balance < RENAME_COST:
            raise ValueError("insufficient_coins")

        wallet.balance    = max(0, wallet.balance - RENAME_COST)
        wallet.total_spent = max(0, wallet.total_spent + RENAME_COST)
        pet.pet_name      = new_name

        await self._session.flush()
        return {"pet_name": pet.pet_name, "new_balance": wallet.balance}

    async def buy_upgrade(self, owner_telegram_id: int, pet_id: int, skill: str) -> dict:
        """Purchase one level of a skill upgrade for a pet."""
        if skill not in SKILL_CATALOG:
            raise ValueError("invalid_skill")

        pet = await self._get_alive_pet(owner_telegram_id, pet_id)
        ups = _get_upgrades(pet)
        current_level = ups.get(skill, 0)
        catalog       = SKILL_CATALOG[skill]

        if current_level >= catalog["max_level"]:
            raise ValueError("max_level_reached")

        cost   = catalog["costs"][current_level]
        wallet = await self._lock_wallet(owner_telegram_id)
        if wallet is None or wallet.balance < cost:
            raise ValueError("insufficient_coins")

        wallet.balance     = max(0, wallet.balance - cost)
        wallet.total_spent = max(0, wallet.total_spent + cost)

        ups[skill] = current_level + 1
        _set_upgrades(pet, ups)

        await self._session.flush()
        return {
            "skill":       skill,
            "new_level":   ups[skill],
            "new_balance": wallet.balance,
            "upgrades":    ups,
        }

    async def revive_pet(self, owner_telegram_id: int, pet_id: int) -> dict:
        """Revive a dead pet (called after successful 10-Star payment).

        Rules
        -----
        - Caller must be one of the pair owners (user_a_id or user_b_id).
        - revival_count < max_revivals (hard cap: 3).
        - Died no more than 3 days ago.
        - Resets hunger/mood to full, clears death metadata, increments revival_count.
        """
        now = dt.datetime.now(dt.timezone.utc)

        pet = (
            await self._session.execute(
                select(ChatPet)
                .where(
                    ChatPet.id == pet_id,
                    (ChatPet.user_a_id == owner_telegram_id)
                    | (ChatPet.user_b_id == owner_telegram_id),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()

        if not pet:
            raise ValueError("pet_not_found")
        if pet.is_alive:
            raise ValueError("pet_already_alive")
        if pet.revival_count >= pet.max_revivals:
            raise ValueError("no_revivals_left")
        if pet.died_at is None:
            raise ValueError("pet_not_dead")
        if (now - _tz_aware(pet.died_at)).total_seconds() > 3 * 86400:
            raise ValueError("revival_window_expired")

        # Restore the pet
        pet.is_alive     = True
        pet.death_cause  = None
        pet.died_at      = None
        pet.last_fed_at  = None       # hunger clock starts fresh
        pet.mood         = 100
        pet.revival_count += 1

        await self._session.flush()
        return _pet_dict(pet, now, viewer_id=owner_telegram_id)

    async def get_user_rank(self, owner_telegram_id: int) -> dict | None:
        """Return the current user's best alive pet rank (by XP DESC, id ASC).

        Ordering matches get_leaderboard so ranks are consistent even under ties.
        Returns None when the user has no alive pets.
        """
        now = dt.datetime.now(dt.timezone.utc)

        # Find the user's best alive pet using the same ordering as the leaderboard
        best = (
            await self._session.execute(
                select(ChatPet)
                .where(
                    (ChatPet.user_a_id == owner_telegram_id)
                    | (ChatPet.user_b_id == owner_telegram_id),
                    ChatPet.is_alive.is_(True),
                )
                .order_by(ChatPet.xp.desc(), ChatPet.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()

        if best is None:
            return None

        # Count alive pets globally that rank strictly above this one
        # under the same (xp DESC, id ASC) ordering:
        #   ranked above ↔ xp > best.xp  OR  (xp == best.xp AND id < best.id)
        above_count: int = (
            await self._session.execute(
                select(func.count()).where(
                    ChatPet.is_alive.is_(True),
                    (ChatPet.xp > best.xp)
                    | ((ChatPet.xp == best.xp) & (ChatPet.id < best.id)),
                )
            )
        ).scalar_one()

        rank = above_count + 1
        sp    = SPECIES.get(best.species, {})
        stage = _compute_stage(best.born_at, now)
        emoji = (sp.get("stages") or ["🐾"])[min(stage - 1, 4)]
        p_info = PERSONALITIES.get(best.personality, {})

        return {
            "pet_id":            best.id,
            "rank":              rank,
            "pet_name":          best.pet_name,
            "species_emoji":     emoji,
            "species_label":     sp.get("label", ""),
            "level":             _compute_level(best.xp),
            "xp":                best.xp,
            "days_alive":        (now - _tz_aware(best.born_at)).days,
            "personality_emoji": p_info.get("emoji", ""),
        }

    async def get_leaderboard(self, limit: int = 20) -> list[dict]:
        """Return top `limit` alive pets ordered by XP DESC, id ASC (tie-break)."""
        now  = dt.datetime.now(dt.timezone.utc)
        rows = list(
            (await self._session.execute(
                select(ChatPet)
                .where(ChatPet.is_alive.is_(True))
                .order_by(ChatPet.xp.desc(), ChatPet.id.asc())
                .limit(limit)
            )).scalars().all()
        )
        result: list[dict] = []
        for rank, pet in enumerate(rows, 1):
            sp    = SPECIES.get(pet.species, {})
            stage = _compute_stage(pet.born_at, now)
            emoji = (sp.get("stages") or ["🐾"])[min(stage - 1, 4)]
            p_info = PERSONALITIES.get(pet.personality, {})
            result.append({
                "pet_id":            pet.id,
                "rank":              rank,
                "pet_name":          pet.pet_name,
                "species_emoji":     emoji,
                "species_label":     sp.get("label", ""),
                "level":             _compute_level(pet.xp),
                "xp":                pet.xp,
                "days_alive":        (now - _tz_aware(pet.born_at)).days,
                "personality_emoji": p_info.get("emoji", ""),
            })
        return result

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _get_alive_pet(self, owner_telegram_id: int, pet_id: int) -> ChatPet:
        # with_for_update() prevents concurrent mutations to the same pet row
        # (XP, mood, level, upgrades).  Lock order: pet → wallet (consistent
        # across all callers) to avoid deadlocks.
        # Both users in the pair can interact with the shared pet.
        pet = (
            await self._session.execute(
                select(ChatPet)
                .where(
                    ChatPet.id == pet_id,
                    (ChatPet.user_a_id == owner_telegram_id)
                    | (ChatPet.user_b_id == owner_telegram_id),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not pet:
            raise ValueError("pet_not_found")
        if not pet.is_alive:
            raise ValueError("pet_is_dead")
        return pet

    async def _lock_wallet(self, owner_telegram_id: int) -> UserWallet | None:
        return (
            await self._session.execute(
                select(UserWallet)
                .where(UserWallet.owner_telegram_id == owner_telegram_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
