"""Repository for note reminders."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.note_reminder import NoteReminder


class NoteReminderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def create(
        self,
        owner_telegram_id: int,
        note_text: str,
        event_at: dt.datetime | None,
        advance_minutes: int,
    ) -> NoteReminder:
        remind_at = (
            event_at - dt.timedelta(minutes=advance_minutes)
            if event_at is not None
            else dt.datetime.now(dt.timezone.utc)
        )
        reminder = NoteReminder(
            owner_telegram_id=owner_telegram_id,
            note_text=note_text,
            event_at=event_at,
            remind_at=remind_at,
            advance_minutes=advance_minutes,
        )
        self._db.add(reminder)
        await self._db.flush()
        return reminder

    async def get_due(self, now: dt.datetime) -> list[NoteReminder]:
        """Return all unsent reminders whose remind_at has passed."""
        result = await self._db.execute(
            select(NoteReminder).where(
                NoteReminder.remind_at <= now,
                NoteReminder.is_sent.is_(False),
            )
        )
        return list(result.scalars().all())

    async def mark_sent(self, reminder_id: int) -> None:
        await self._db.execute(
            update(NoteReminder)
            .where(NoteReminder.id == reminder_id)
            .values(is_sent=True)
            .execution_options(synchronize_session=False)
        )
        await self._db.flush()
