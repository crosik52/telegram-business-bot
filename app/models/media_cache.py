"""Cached media file bytes — persisted in DB so file_ids never expire.

Every business message that carries a downloadable file gets its bytes stored
here as soon as the message arrives.  When Telegram later delivers a delete
notification the bytes are already in the DB, so the owner receives the media
even if the original Telegram file_id has expired (self-destructing media).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Index, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class MediaCache(Base):
    """One row per unique media file (keyed by file_unique_id)."""

    __tablename__ = "media_cache"
    __table_args__ = (
        Index("ix_media_cache_file_unique_id", "file_unique_id", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Telegram permanent key — used for dedup; also stored in case we need to
    # re-download a newer version later.
    file_unique_id: Mapped[str] = mapped_column(String(255), nullable=False)
    file_id: Mapped[str] = mapped_column(String(512), nullable=False)
    media_type: Mapped[str] = mapped_column(String(32), nullable=False)

    # Raw file bytes — LargeBinary maps to bytea in PostgreSQL (up to 1 GB).
    file_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        nullable=False,
    )
