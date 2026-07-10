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
import math
import random

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_connection import BusinessConnection
from app.models.message import Message
from app.models.pet import ChatPet
from app.models.wallet import UserWallet

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
    return 45 if p == "playful" else PLAY_MOOD_GAIN


def _personality_cuddle_mood(p: str) -> int:
    return 22 if p == "shy" else CUDDLE_MOOD_GAIN


def _compute_hunger(pet: ChatPet, now: dt.datetime) -> int:
    ref = pet.last_fed_at or pet.born_at
    hours = max(0.0, (now - ref).total_seconds() / 3600)
    decay_h = _personality_hunger_hours(pet.personality)
    return max(0, round(100 - hours / decay_h * 100))


def _compute_mood(pet: ChatPet, now: dt.datetime) -> int:
    ref = pet.last_cuddled_at or pet.last_played_at or pet.born_at
    hours = max(0.0, (now - ref).total_seconds() / 3600)
    decay_h = _personality_mood_hours(pet.personality)
    return max(0, round(100 - hours / decay_h * 100))


def _compute_stage(born_at: dt.datetime, now: dt.datetime) -> int:
    days = (now - born_at).days
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


def _pet_dict(pet: ChatPet, now: dt.datetime) -> dict:
    hunger = _compute_hunger(pet, now) if pet.is_alive else 0
    mood   = _compute_mood(pet, now)   if pet.is_alive else 0
    level  = _compute_level(pet.xp)
    next_xp = _xp_for_next_level(level)
    p_info  = PERSONALITIES.get(pet.personality, {"emoji": "❓", "label": "?"})
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
        "interlocutor_name": pet.interlocutor_name,
        "born_at":          pet.born_at.isoformat(),
        "last_fed_at":      pet.last_fed_at.isoformat() if pet.last_fed_at else None,
        "last_played_at":   pet.last_played_at.isoformat() if pet.last_played_at else None,
        "last_cuddled_at":  pet.last_cuddled_at.isoformat() if pet.last_cuddled_at else None,
        "died_at":          pet.died_at.isoformat() if pet.died_at else None,
        "death_cause":      pet.death_cause,
        "days_alive":       (now - pet.born_at).days,
        "total_feedings":   pet.total_feedings,
        "total_plays":      pet.total_plays,
        "total_cuddles":    pet.total_cuddles,
        "feed_streak":      pet.feed_streak,
        # Cooldown helpers (seconds remaining, 0 = ready)
        "feed_cost":        _personality_feed_cost(pet.personality),
        "play_cooldown_secs":   _cooldown_secs(pet.last_played_at, _personality_play_cooldown(pet.personality), now),
        "cuddle_cooldown_secs": _cooldown_secs(pet.last_cuddled_at, CUDDLE_COOLDOWN_HOURS, now),
        "feed_cooldown_secs":   _cooldown_secs(pet.last_fed_at, _personality_feed_cooldown(pet.personality), now),
    }


def _cooldown_secs(last_at: dt.datetime | None, hours: float, now: dt.datetime) -> int:
    if last_at is None:
        return 0
    elapsed = (now - last_at).total_seconds()
    remaining = hours * 3600 - elapsed
    return max(0, int(remaining))


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

        pets: list[ChatPet] = list(
            (await self._session.execute(
                select(ChatPet)
                .where(ChatPet.owner_telegram_id == owner_telegram_id)
                .order_by(ChatPet.born_at.desc())
            )).scalars().all()
        )

        alive_pets = [p for p in pets if p.is_alive]
        changed = False

        if alive_pets and conn_ids:
            two_days_ago = now - dt.timedelta(hours=48)
            alive_chat_ids = [p.chat_id for p in alive_pets]

            recent_chats: set[int] = {
                r[0] for r in (
                    await self._session.execute(
                        select(Message.chat_id.distinct()).where(
                            Message.business_connection_id.in_(conn_ids),
                            Message.chat_id.in_(alive_chat_ids),
                            Message.chat_id != owner_telegram_id,
                            Message.sent_at >= two_days_ago,
                            Message.is_deleted.is_(False),
                        )
                    )
                ).all()
            }

            for pet in alive_pets:
                hunger = _compute_hunger(pet, now)
                if hunger == 0:
                    pet.is_alive = False
                    pet.death_cause = "starvation"
                    pet.died_at = now
                    changed = True
                elif pet.chat_id not in recent_chats and pet.personality != "brave":
                    age_hours = (now - pet.born_at).total_seconds() / 3600
                    if age_hours > STREAK_GRACE_HOURS:
                        pet.is_alive = False
                        pet.death_cause = "streak_broken"
                        pet.died_at = now
                        changed = True

        if changed:
            await self._session.flush()

        alive_out = [p for p in pets if p.is_alive]
        dead_out  = [p for p in pets if not p.is_alive][:3]

        pets_out = [_pet_dict(p, now) for p in alive_out + dead_out]

        # Available chats
        alive_pet_chats: set[int] = {p.chat_id for p in alive_out}
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

        partner_conn = (
            await self._session.execute(
                select(BusinessConnection.business_connection_id).where(
                    BusinessConnection.user_telegram_id == chat_id
                ).limit(1)
            )
        ).scalar_one_or_none()
        if not partner_conn:
            raise ValueError("partner_not_connected")

        existing = (
            await self._session.execute(
                select(ChatPet).where(
                    ChatPet.owner_telegram_id == owner_telegram_id,
                    ChatPet.chat_id == chat_id,
                    ChatPet.is_alive.is_(True),
                )
            )
        ).scalar_one_or_none()
        if existing:
            raise ValueError("pet_exists")

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

        now = dt.datetime.now(dt.timezone.utc)
        pet = ChatPet(
            owner_telegram_id=owner_telegram_id,
            chat_id=chat_id,
            pet_name=pet_name,
            species=species,
            interlocutor_name=interlocutor_name,
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

        # Mirror pet for partner
        mirror_created = False
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

        b_existing = (
            await self._session.execute(
                select(ChatPet).where(
                    ChatPet.owner_telegram_id == chat_id,
                    ChatPet.chat_id == owner_telegram_id,
                    ChatPet.is_alive.is_(True),
                )
            )
        ).scalar_one_or_none()

        if not b_existing:
            mirror = ChatPet(
                owner_telegram_id=chat_id,
                chat_id=owner_telegram_id,
                pet_name=pet_name,
                species=species,
                interlocutor_name=owner_display_name,
                personality=personality,
                is_alive=True,
                born_at=now,
            )
            try:
                async with self._session.begin_nested():
                    self._session.add(mirror)
                    await self._session.flush()
                mirror_created = True
            except IntegrityError:
                pass

        return {**_pet_dict(pet, now), "mirror_created": mirror_created}

    async def feed(self, owner_telegram_id: int, pet_id: int) -> dict:
        now = dt.datetime.now(dt.timezone.utc)
        pet = await self._get_alive_pet(owner_telegram_id, pet_id)

        cooldown = _personality_feed_cooldown(pet.personality)
        if pet.last_fed_at:
            hours_since = (now - pet.last_fed_at).total_seconds() / 3600
            if hours_since < cooldown:
                raise ValueError("already_fed")

        cost = _personality_feed_cost(pet.personality)
        wallet = await self._lock_wallet(owner_telegram_id)
        if wallet is None or wallet.balance < cost:
            raise ValueError("insufficient_coins")

        wallet.balance    = max(0, wallet.balance - cost)
        wallet.total_spent = max(0, wallet.total_spent + cost)

        pet.last_fed_at   = now
        pet.total_feedings += 1
        pet.xp            += FEED_XP
        pet.level         = _compute_level(pet.xp)
        # Feed streak: if fed within 26 h of previous feeding
        if pet.last_fed_at and (now - pet.last_fed_at).total_seconds() < 26 * 3600:
            pet.feed_streak += 1
        else:
            pet.feed_streak = 1

        await self._session.flush()
        return {
            "hunger":      100,
            "mood":        _compute_mood(pet, now),
            "xp":          pet.xp,
            "level":       pet.level,
            "feed_streak": pet.feed_streak,
            "new_balance": wallet.balance,
            "feed_cost":   cost,
        }

    async def play(self, owner_telegram_id: int, pet_id: int) -> dict:
        now = dt.datetime.now(dt.timezone.utc)
        pet = await self._get_alive_pet(owner_telegram_id, pet_id)

        cooldown = _personality_play_cooldown(pet.personality)
        if pet.last_played_at:
            hours_since = (now - pet.last_played_at).total_seconds() / 3600
            if hours_since < cooldown:
                raise ValueError("play_cooldown")

        mood_gain = _personality_play_mood(pet.personality)
        current_mood = _compute_mood(pet, now)
        new_mood = min(100, current_mood + mood_gain)

        pet.last_played_at = now
        # Mood is stored implicitly via last_played_at/last_cuddled_at
        # We use last_cuddled_at as the primary mood reference; update it too
        # only if the resulting mood would be higher than current cuddled-at
        # — just set last_played_at and compute dynamically.
        pet.total_plays += 1
        pet.xp          += PLAY_XP
        pet.level        = _compute_level(pet.xp)

        # If new_mood would be > what play alone provides (because of recent cuddle),
        # we store the effective reset point so mood reads correctly.
        # We persist mood as an offset from "now minus the equivalent decay fraction".
        if new_mood >= 100:
            # Full mood — set reference to now (both played and cuddled = now)
            pet.last_played_at  = now
            pet.last_cuddled_at = now
        else:
            # Set last_played_at to a time that produces exactly new_mood on read
            decay_h = _personality_mood_hours(pet.personality)
            fraction_gone = 1.0 - new_mood / 100.0
            hours_offset = fraction_gone * decay_h
            pet.last_played_at  = now - dt.timedelta(hours=hours_offset)
            pet.last_cuddled_at = pet.last_played_at  # cuddle was earlier, use play ref

        play_msg = random.choice(PLAY_MESSAGES).format(name=pet.pet_name)
        await self._session.flush()
        return {
            "mood":      new_mood,
            "xp":        pet.xp,
            "level":     pet.level,
            "play_msg":  play_msg,
        }

    async def cuddle(self, owner_telegram_id: int, pet_id: int) -> dict:
        now = dt.datetime.now(dt.timezone.utc)
        pet = await self._get_alive_pet(owner_telegram_id, pet_id)

        if pet.last_cuddled_at:
            hours_since = (now - pet.last_cuddled_at).total_seconds() / 3600
            if hours_since < CUDDLE_COOLDOWN_HOURS:
                raise ValueError("cuddle_cooldown")

        mood_gain = _personality_cuddle_mood(pet.personality)
        current_mood = _compute_mood(pet, now)
        new_mood = min(100, current_mood + mood_gain)

        decay_h = _personality_mood_hours(pet.personality)
        fraction_gone = 1.0 - new_mood / 100.0
        hours_offset = fraction_gone * decay_h
        pet.last_cuddled_at = now - dt.timedelta(hours=hours_offset)

        pet.total_cuddles += 1
        pet.xp            += CUDDLE_XP
        pet.level          = _compute_level(pet.xp)

        await self._session.flush()
        return {
            "mood":  new_mood,
            "xp":    pet.xp,
            "level": pet.level,
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

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _get_alive_pet(self, owner_telegram_id: int, pet_id: int) -> ChatPet:
        pet = (
            await self._session.execute(
                select(ChatPet).where(
                    ChatPet.id == pet_id,
                    ChatPet.owner_telegram_id == owner_telegram_id,
                )
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
