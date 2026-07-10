"""QuestRepository — daily quest definitions, progress checks, and claim logic.

Security notes:
- Progress metrics (today_messages, today_chats, has_streak) MUST be computed
  server-side by the calling route, never accepted from the client.
- Claiming is idempotent-safe via the unique constraint on (owner_id, quest_id, date).
- Coin credit uses a row-level lock through WalletRepository._get_for_update.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.quest import DailyQuestCompletion
from app.models.wallet import UserWallet


# ── Quest catalogue (server-authoritative) ─────────────────────────────────
QUESTS: list[dict] = [
    {
        "id": "MSG_5",
        "emoji": "💬",
        "title": "5 сообщений за день",
        "desc": "Отправь 5+ сообщений сегодня в подключённых чатах",
        "reward": 25,
    },
    {
        "id": "CHAT_2",
        "emoji": "👥",
        "title": "Двойной чат",
        "desc": "Напиши в 2+ разных чата сегодня",
        "reward": 35,
    },
    {
        "id": "STREAK",
        "emoji": "🔥",
        "title": "Блюститель серии",
        "desc": "Пиши день за днём — сообщение вчера и сегодня",
        "reward": 50,
    },
]

QUEST_BY_ID: dict[str, dict] = {q["id"]: q for q in QUESTS}


def _check_progress(quest_id: str, *, today_messages: int, today_chats: int, has_streak: bool) -> bool:
    """Return True if the quest completion conditions are met."""
    if quest_id == "MSG_5":
        return today_messages >= 5
    if quest_id == "CHAT_2":
        return today_chats >= 2
    if quest_id == "STREAK":
        return has_streak
    return False


class QuestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _utc_today() -> dt.date:
        """Always use UTC date so quest windows match the UTC-based activity queries."""
        return dt.datetime.now(dt.timezone.utc).date()

    async def get_today_completions(self, owner_telegram_id: int) -> set[str]:
        """Return set of quest_ids already claimed today (UTC date)."""
        today = self._utc_today()
        result = await self._session.execute(
            select(DailyQuestCompletion.quest_id).where(
                DailyQuestCompletion.owner_telegram_id == owner_telegram_id,
                DailyQuestCompletion.quest_date == today,
            )
        )
        return {row[0] for row in result.all()}

    async def claim_quest(
        self,
        owner_telegram_id: int,
        quest_id: str,
        *,
        today_messages: int,
        today_chats: int,
        has_streak: bool,
    ) -> int:
        """Verify completion, record it, and credit coins.

        Returns the reward amount on success.
        Raises ValueError with a short code string on failure.
        """
        quest = QUEST_BY_ID.get(quest_id)
        if quest is None:
            raise ValueError("unknown_quest")

        today = self._utc_today()  # UTC — matches activity window queries

        # Idempotency check (pre-flight; the unique constraint is the true guard)
        existing = (
            await self._session.execute(
                select(DailyQuestCompletion).where(
                    DailyQuestCompletion.owner_telegram_id == owner_telegram_id,
                    DailyQuestCompletion.quest_id == quest_id,
                    DailyQuestCompletion.quest_date == today,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError("already_claimed")

        # Verify completion conditions (server-derived, not client-supplied)
        if not _check_progress(
            quest_id,
            today_messages=today_messages,
            today_chats=today_chats,
            has_streak=has_streak,
        ):
            raise ValueError("not_completed")

        # Record completion — unique constraint is the final race-condition guard
        completion = DailyQuestCompletion(
            owner_telegram_id=owner_telegram_id,
            quest_id=quest_id,
            quest_date=today,
            reward=quest["reward"],
        )
        self._session.add(completion)
        try:
            await self._session.flush()
        except IntegrityError:
            # Concurrent duplicate claim lost the race — treat as already_claimed
            await self._session.rollback()
            raise ValueError("already_claimed")

        # Credit coins via row-level lock (FOR UPDATE keeps accounting safe)
        wallet_result = await self._session.execute(
            select(UserWallet)
            .where(UserWallet.owner_telegram_id == owner_telegram_id)
            .with_for_update()
        )
        wallet = wallet_result.scalar_one_or_none()
        if wallet is None:
            # First-time wallet creation — guard against concurrent inserts
            new_wallet = UserWallet(owner_telegram_id=owner_telegram_id)
            self._session.add(new_wallet)
            try:
                await self._session.flush()
            except IntegrityError:
                await self._session.rollback()
                raise ValueError("already_claimed")
            wallet_result2 = await self._session.execute(
                select(UserWallet)
                .where(UserWallet.owner_telegram_id == owner_telegram_id)
                .with_for_update()
            )
            wallet = wallet_result2.scalar_one()

        reward = quest["reward"]
        wallet.balance = max(0, wallet.balance + reward)
        wallet.total_earned = max(0, wallet.total_earned + reward)
        await self._session.flush()

        return reward
