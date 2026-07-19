"""AI Relationship Analysis using Google Gemini.

Fetches the last N messages for a chat, computes raw stats locally,
then asks Gemini to produce a structured 10-section relationship report.
Results are cached in-memory per (owner_id, chat_id) for 2 hours.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import time
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import MediaType, Message

logger = logging.getLogger(__name__)

# ── In-memory result cache ────────────────────────────────────────────────────
_CACHE: dict[tuple[int, int], tuple[float, dict]] = {}
_CACHE_TTL = 7200  # 2 hours


def _cached(owner_id: int, chat_id: int) -> dict | None:
    entry = _CACHE.get((owner_id, chat_id))
    if entry and (time.time() - entry[0]) < _CACHE_TTL:
        return entry[1]
    return None


def _store(owner_id: int, chat_id: int, result: dict) -> None:
    _CACHE[(owner_id, chat_id)] = (time.time(), result)


# ── Message fetching ──────────────────────────────────────────────────────────

async def _fetch_messages(
    session: AsyncSession,
    chat_id: int,
    connection_ids: list[str],
    limit: int = 600,
) -> list[Message]:
    if not connection_ids:
        return []
    stmt = (
        select(Message)
        .where(
            Message.chat_id == chat_id,
            Message.business_connection_id.in_(connection_ids),
            Message.is_deleted.is_(False),
        )
        .order_by(Message.sent_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    msgs = list(result.scalars().all())
    msgs.reverse()  # chronological order
    return msgs


# ── Local stats (no AI needed) ────────────────────────────────────────────────

_MEDIA_LABEL: dict[str, str] = {
    "photo":            "фото",
    "video":            "видео",
    "voice":            "голосовое",
    "video_note":       "кружок",
    "sticker":          "стикер",
    "animation":        "GIF",
    "document":         "документ",
    "audio":            "аудио",
}


def _local_stats(msgs: list[Message]) -> dict:
    if not msgs:
        return {}

    user_msgs      = [m for m in msgs if m.is_outgoing]
    contact_msgs   = [m for m in msgs if not m.is_outgoing]

    def _words(m: Message) -> int:
        t = (m.text or "") + " " + (m.caption or "")
        return len(t.split())

    user_words    = sum(_words(m) for m in user_msgs)
    contact_words = sum(_words(m) for m in contact_msgs)

    # Initiates (who sent more "first messages" after a 3-hour gap)
    initiates_user = 0
    initiates_contact = 0
    for i, m in enumerate(msgs):
        if i == 0:
            if m.is_outgoing:
                initiates_user += 1
            else:
                initiates_contact += 1
            continue
        gap = (m.sent_at - msgs[i - 1].sent_at).total_seconds()
        if gap > 10800:  # 3 hours
            if m.is_outgoing:
                initiates_user += 1
            else:
                initiates_contact += 1

    # Media counts
    media_counts: dict[str, int] = {}
    for m in msgs:
        mt = m.media_type.value if hasattr(m.media_type, "value") else str(m.media_type)
        if mt and mt not in ("none", "NONE", ""):
            media_counts[mt] = media_counts.get(mt, 0) + 1

    # Date range
    first_at = msgs[0].sent_at.strftime("%d.%m.%Y") if msgs else "-"
    last_at  = msgs[-1].sent_at.strftime("%d.%m.%Y") if msgs else "-"
    days_span = max(1, (msgs[-1].sent_at - msgs[0].sent_at).days) if len(msgs) > 1 else 1

    return {
        "total_messages":       len(msgs),
        "user_messages":        len(user_msgs),
        "contact_messages":     len(contact_msgs),
        "user_words":           user_words,
        "contact_words":        contact_words,
        "initiates_user":       initiates_user,
        "initiates_contact":    initiates_contact,
        "media":                media_counts,
        "first_message_date":   first_at,
        "last_message_date":    last_at,
        "days_span":            days_span,
        "messages_per_day":     round(len(msgs) / days_span, 1),
    }


# ── Transcript builder ────────────────────────────────────────────────────────

def _build_transcript(msgs: list[Message], max_chars: int = 28_000) -> str:
    lines: list[str] = []
    total = 0
    for m in msgs:
        ts   = m.sent_at.strftime("%d.%m %H:%M")
        who  = "Вы" if m.is_outgoing else "Собеседник"
        text = (m.text or m.caption or "").strip()
        mt   = m.media_type.value if hasattr(m.media_type, "value") else str(m.media_type)
        if not text:
            text = f"[{_MEDIA_LABEL.get(mt, mt)}]"
        line = f"[{ts}] {who}: {text}"
        total += len(line)
        if total > max_chars:
            lines.append("... (старые сообщения пропущены)")
            break
        lines.append(line)
    return "\n".join(lines)


# ── Gemini call ───────────────────────────────────────────────────────────────

_SCHEMA = {
    "type": "object",
    "properties": {
        "general_score": {
            "type": "object",
            "properties": {
                "score":       {"type": "number"},
                "label":       {"type": "string"},
                "description": {"type": "string"},
            },
        },
        "balance": {
            "type": "object",
            "properties": {
                "initiates_first":            {"type": "string"},
                "avg_response_time_user":     {"type": "string"},
                "avg_response_time_contact":  {"type": "string"},
                "ends_conversation":          {"type": "string"},
                "ignores_more":               {"type": "string"},
                "longer_messages":            {"type": "string"},
            },
        },
        "interest": {
            "type": "object",
            "properties": {
                "probability": {"type": "number"},
                "indicators": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name":  {"type": "string"},
                            "value": {"type": "string"},
                            "level": {"type": "string"},
                        },
                    },
                },
            },
        },
        "style": {
            "type": "object",
            "properties": {
                "user_tags":    {"type": "array", "items": {"type": "string"}},
                "contact_tags": {"type": "array", "items": {"type": "string"}},
            },
        },
        "personality": {
            "type": "object",
            "properties": {
                "user_traits":    {"type": "array", "items": {"type": "string"}},
                "contact_traits": {"type": "array", "items": {"type": "string"}},
            },
        },
        "dynamics": {
            "type": "object",
            "properties": {
                "trend":             {"type": "string"},
                "trend_description": {"type": "string"},
                "activity_note":     {"type": "string"},
            },
        },
        "emotions": {
            "type": "object",
            "properties": {
                "positive":   {"type": "number"},
                "neutral":    {"type": "number"},
                "irritation": {"type": "number"},
                "sadness":    {"type": "number"},
                "support":    {"type": "number"},
            },
        },
        "red_flags":   {"type": "array", "items": {"type": "string"}},
        "green_flags": {"type": "array", "items": {"type": "string"}},
        "summary":     {"type": "string"},
    },
}

_SYSTEM_PROMPT = """Ты — аналитик отношений. Анализируй переписки в Telegram и выдавай точные, честные наблюдения на русском языке.

Правила:
- Не делай категоричных заявлений — говори о вероятностях и наблюдениях
- Будь конкретным, не банальным
- Анализируй только то, что видно в тексте
- Красные флаги — только реально заметные паттерны
- Зелёные флаги — только реально присутствующие позитивные знаки
- general_score: число от 1 до 10
- interest.probability: число от 0 до 100 (насколько вероятно, что собеседник заинтересован)
- emotions: 5 чисел, в сумме дающих ~100
- dynamics.trend: одно из "growing" | "stable" | "declining"
- balance.initiates_first: "user" | "contact" | "equal"
- Всё на русском языке"""


async def analyze(
    session: AsyncSession,
    owner_id: int,
    chat_id: int,
    connection_ids: list[str],
    contact_name: str = "Собеседник",
) -> dict:
    """Run AI analysis for a chat. Returns cached result if fresh."""
    cached = _cached(owner_id, chat_id)
    if cached:
        return cached

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")

    msgs = await _fetch_messages(session, chat_id, connection_ids)
    if not msgs:
        raise ValueError("no_messages")

    stats   = _local_stats(msgs)
    excerpt = _build_transcript(msgs)

    user_prompt = f"""Проанализируй переписку между пользователем («Вы») и их собеседником («Собеседник»).
Имя собеседника: {contact_name}

Базовая статистика (уже посчитана):
- Всего сообщений: {stats['total_messages']} (Вы: {stats['user_messages']}, Собеседник: {stats['contact_messages']})
- Слов: Вы — {stats['user_words']}, Собеседник — {stats['contact_words']}
- Инициировали разговор: Вы — {stats['initiates_user']} раз, Собеседник — {stats['initiates_contact']} раз
- Период: {stats['first_message_date']} — {stats['last_message_date']} ({stats['days_span']} дней)
- Среднее сообщений в день: {stats['messages_per_day']}

Переписка:
{excerpt}

Верни JSON по схеме."""

    import google.generativeai as genai  # noqa: PLC0415
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction=_SYSTEM_PROMPT,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.4,
        ),
    )

    import asyncio  # noqa: PLC0415
    response = await asyncio.to_thread(model.generate_content, user_prompt)
    raw = response.text.strip()

    # Strip markdown code fences if Gemini wrapped it
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    ai_data = json.loads(raw)

    result = {
        "stats":    stats,
        "ai":       ai_data,
        "analyzed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "message_count": len(msgs),
    }
    _store(owner_id, chat_id, result)
    return result
