"""Notes the business-account owner attaches to a contact."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class ContactNote(Base):
    """A free-text note about a specific contact in a business chat.

    Notes are append-only; there is no edit or delete for now so that the
    owner always has a timestamped audit trail.
    """

    __tablename__ = "contact_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_connection_id: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
    )
