"""Persistent cache for AI relationship analysis results.

One row per (owner_id, chat_id). Survives server restarts and Railway deploys,
preventing repeated Gemini API calls for the same chat within the TTL window.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, DateTime, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class AiAnalysisCache(Base):
    """Cached AI analysis result for one (owner, chat) pair."""

    __tablename__ = "ai_analysis_cache"
    __table_args__ = (
        Index("ix_ai_analysis_cache_owner_chat", "owner_id", "chat_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Full analysis result serialised as JSON text.
    result_json: Mapped[str] = mapped_column(Text, nullable=False)

    analyzed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: dt.datetime.now(dt.UTC),
    )
