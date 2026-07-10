"""PetRepository — shared virtual pet logic.

A ChatPet is tied to one (owner, chat_id) relationship.
It dies if:
  - Hunger reaches 0  (starvation after ~3 days without feeding)
  - No messages to/from that chat in the last 48 h after a 48-h grace period
    (streak broken)

Security notes:
  - Feed cost is debited via row-level lock (_get_for_update pattern inline).
  - Interlocutor name is derived server-side from Message.sender_*, not trusted
    from the client.
"""

from __future__ import annotations

import datetime as dt
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
    "cat":     {"stages": ["🥚", "🐱", "🐱", "🐱", "😺"], "label": "Котик"},
    "dog":     {"stages": ["🥚", "🐶", "🐶", "🐕", "🦮"], "label": "Пёсик"},
    "rabbit":  {"stages": ["🥚", "🐰", "🐰", "🐇", "🐇"], "label": "Зайка"},
    "hamster": {"stages": ["🥚", "🐹", "🐹", "🐹", "🐹"], "label": "Хомяк"},
    "fox":     {"stages": ["🥚", "🦊", "🦊", "🦊", "🦊"], "label": "Лисёнок"},
}

PET_NAMES = [
    "Пушок", "Мурзик", "Бублик", "Рыжик", "Снежок", "Барсик",
    "Пончик", "Печенька", "Карамель", "Зефир", "Кекс", "Плюша",
    "Батон", "Вафля", "Мармелад", "Сухарик",
]

FEED_COST = 20          # coins per feeding
HUNGER_DECAY_HOURS = 72  # hours from full (100) to 0
FEED_COOLDOWN_HOURS = 22  # minimum hours between feedings
STREAK_GRACE_HOURS = 48   # new pets get a 48-h window before streak-death kicks in


# ── Helpers ───────────────────────────────────────────────────────────────────

def _compute_hunger(pet: ChatPet, now: dt.datetime) -> int:
    """Current hunger 0-100.  Full = 100, decreases from last-fed (or born) time."""
    ref = pet.last_fed_at or pet.born_at
    hours_hungry = max(0.0, (now - ref).total_seconds() / 3600)
    return max(0, round(100 - hours_hungry / HUNGER_DECAY_HOURS * 100))


def _compute_stage(born_at: dt.datetime, now: dt.datetime) -> int:
    days = (now - born_at).days
    if days == 0:   return 1  # Egg
    if days <= 6:   return 2  # Baby
    if days <= 13:  return 3  # Child
    if days <= 29:  return 4  # Teen
    return 5                  # Adult


def _display_name(first: str | None, last: str | None, username: str | None, chat_id: int) -> str:
    parts = [p for p in (first, last) if p]
    if parts:
        return " ".join(parts)
    if username:
        return f"@{username}"
    return f"Собеседник {chat_id}"


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
        """Return (pet_dicts, available_chat_dicts).

        Side-effect: marks pets dead (starvation / streak) and flushes if needed.
        """
        now = dt.datetime.now(dt.timezone.utc)
        conn_ids = await self._get_conn_ids(owner_telegram_id)

        # Load all pets for this owner (newest first)
        pets: list[ChatPet] = list(
            (
                await self._session.execute(
                    select(ChatPet)
                    .where(ChatPet.owner_telegram_id == owner_telegram_id)
                    .order_by(ChatPet.born_at.desc())
                )
            ).scalars().all()
        )

        alive_pets = [p for p in pets if p.is_alive]
        changed = False

        if alive_pets and conn_ids:
            two_days_ago = now - dt.timedelta(hours=48)
            alive_chat_ids = [p.chat_id for p in alive_pets]

            recent_chats: set[int] = {
                r[0]
                for r in (
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
                elif pet.chat_id not in recent_chats:
                    # Only kill from streak break after grace period
                    age_hours = (now - pet.born_at).total_seconds() / 3600
                    if age_hours > STREAK_GRACE_HOURS:
                        pet.is_alive = False
                        pet.death_cause = "streak_broken"
                        pet.died_at = now
                        changed = True

        if changed:
            await self._session.flush()

        # Build output list (alive first, then up to 3 most recent dead)
        alive_out = [p for p in pets if p.is_alive]
        dead_out = [p for p in pets if not p.is_alive][:3]
        pets_out: list[dict] = []
        for pet in alive_out + dead_out:
            days_alive = (now - pet.born_at).days
            pets_out.append(
                {
                    "id": pet.id,
                    "chat_id": pet.chat_id,
                    "pet_name": pet.pet_name,
                    "species": pet.species,
                    "stage": _compute_stage(pet.born_at, now),
                    "hunger": _compute_hunger(pet, now) if pet.is_alive else 0,
                    "is_alive": pet.is_alive,
                    "interlocutor_name": pet.interlocutor_name,
                    "born_at": pet.born_at.isoformat(),
                    "last_fed_at": pet.last_fed_at.isoformat() if pet.last_fed_at else None,
                    "died_at": pet.died_at.isoformat() if pet.died_at else None,
                    "death_cause": pet.death_cause,
                    "days_alive": days_alive,
                }
            )

        # Available chats — active in last 48 h, no alive pet already
        alive_pet_chats: set[int] = {p.chat_id for p in alive_out}
        available_chats: list[dict] = []
        if conn_ids:
            two_days_ago = now - dt.timedelta(hours=48)
            activity_rows = (
                await self._session.execute(
                    select(Message.chat_id, func.count(Message.id).label("cnt"))
                    .where(
                        Message.business_connection_id.in_(conn_ids),
                        Message.chat_id != owner_telegram_id,  # exclude self-chat
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

            # Keep only partners who have also connected the bot
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
                # Get display name from the most recent message sent BY the
                # interlocutor (sender_telegram_id != owner) — works correctly
                # even for rows where is_outgoing was incorrectly stored as False.
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
                    available_chats.append(
                        {
                            "chat_id": cid,
                            "display_name": names.get(cid) or f"Собеседник {cid}",
                            "message_count": counts[cid],
                        }
                    )

        return pets_out, available_chats

    async def adopt(
        self,
        owner_telegram_id: int,
        chat_id: int,
        species: str,
        pet_name: str,
    ) -> dict:
        """Create a new pet.  Raises ValueError on business-logic errors."""
        if species not in SPECIES:
            raise ValueError("invalid_species")

        pet_name = pet_name.strip()[:30] or random.choice(PET_NAMES)

        # Verify the chat partner has also connected the bot
        partner_conn = (
            await self._session.execute(
                select(BusinessConnection.business_connection_id).where(
                    BusinessConnection.user_telegram_id == chat_id
                ).limit(1)
            )
        ).scalar_one_or_none()
        if not partner_conn:
            raise ValueError("partner_not_connected")

        # Check no alive pet for this chat already
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

        # Derive interlocutor name server-side
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
            if name_row
            else f"Собеседник {chat_id}"
        )

        now = dt.datetime.now(dt.timezone.utc)
        pet = ChatPet(
            owner_telegram_id=owner_telegram_id,
            chat_id=chat_id,
            pet_name=pet_name,
            species=species,
            interlocutor_name=interlocutor_name,
            is_alive=True,
            born_at=now,
        )
        self._session.add(pet)
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            raise ValueError("pet_exists")

        return {
            "id": pet.id,
            "pet_name": pet.pet_name,
            "species": pet.species,
            "stage": 1,
            "hunger": 100,
            "is_alive": True,
            "interlocutor_name": interlocutor_name,
            "days_alive": 0,
        }

    async def feed(self, owner_telegram_id: int, pet_id: int) -> dict:
        """Feed the pet, deducting FEED_COST coins.  Returns new hunger + balance."""
        now = dt.datetime.now(dt.timezone.utc)

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

        if pet.last_fed_at:
            hours_since = (now - pet.last_fed_at).total_seconds() / 3600
            if hours_since < FEED_COOLDOWN_HOURS:
                raise ValueError("already_fed")

        # Deduct coins — row-level lock
        wallet = (
            await self._session.execute(
                select(UserWallet)
                .where(UserWallet.owner_telegram_id == owner_telegram_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if wallet is None or wallet.balance < FEED_COST:
            raise ValueError("insufficient_coins")

        wallet.balance = max(0, wallet.balance - FEED_COST)
        wallet.total_spent = max(0, wallet.total_spent + FEED_COST)

        # Feed
        pet.last_fed_at = now
        await self._session.flush()

        return {"hunger": 100, "new_balance": wallet.balance}
