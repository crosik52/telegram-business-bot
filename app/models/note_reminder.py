"""Scheduled reminders for owner notes."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class NoteReminder(Base):
    """One reminder row per note × advance-time selection.

    The background loop queries rows where ``remind_at <= now`` and
    ``is_sent = False``, sends a DM to the owner, then flips ``is_sent``.
    """

    __tablename__ = "note_reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Who to notify
    owner_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    # Snapshot of the note text (so the reminder works even if the note is deleted)
    note_text: Mapped[str] = mapped_column(Text, nullable=False)

    # The actual event datetime (UTC), parsed from the note text
    event_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # When to fire the reminder = event_at − advance_minutes
    remind_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    # How many minutes before the event to remind (for display in the reminder message)
    advance_minutes: Mapped[int] = mapped_column(Integer, nullable=False)

    # Has the reminder DM already been sent?
    is_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.timezone.utc),
    )
