"""AnalysisResult — persisted AI analysis cache.

Stores Gemini analysis results keyed by (owner_id, chat_id) so they survive
deploys. TTL is enforced at read-time in the service layer.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, DateTime, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    # Full JSON blob of the analysis result (stats + ai + metadata)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)

    # UTC timestamp of when the analysis was stored
    stored_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: dt.datetime.now(dt.timezone.utc),
    )

    __table_args__ = (
        Index("ix_analysis_results_owner_chat", "owner_id", "chat_id", unique=True),
    )
