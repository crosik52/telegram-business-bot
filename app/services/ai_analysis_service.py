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
_CACHE_TTL = 86400  # 24 hours — free Gemini tier has tight daily token quota


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
    limit: int = 1000,
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


def _fmt_duration(seconds: float | None) -> str:
    """Format seconds into human-readable Russian string."""
    if seconds is None:
        return "—"
    s = int(seconds)
    if s < 60:
        return f"{s} сек"
    if s < 3600:
        return f"{s // 60} мин"
    hours = s // 3600
    mins  = (s % 3600) // 60
    return f"{hours} ч {mins} мин" if mins else f"{hours} ч"


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

    user_avg_words    = round(user_words    / max(len(user_msgs), 1), 1)
    contact_avg_words = round(contact_words / max(len(contact_msgs), 1), 1)

    # ── Initiates (who sent first after a 3-hour gap) ────────────────────────
    initiates_user    = 0
    initiates_contact = 0
    for i, m in enumerate(msgs):
        if i == 0:
            if m.is_outgoing: initiates_user += 1
            else:              initiates_contact += 1
            continue
        gap = (m.sent_at - msgs[i - 1].sent_at).total_seconds()
        if gap > 10800:
            if m.is_outgoing: initiates_user += 1
            else:              initiates_contact += 1

    # ── Ends conversation ────────────────────────────────────────────────────
    # Last message of each conversation block (separated by 3-hr gaps)
    user_ends    = 0
    contact_ends = 0
    for i in range(1, len(msgs)):
        gap = (msgs[i].sent_at - msgs[i - 1].sent_at).total_seconds()
        if gap > 10800:
            # msgs[i-1] ended the previous conversation
            if msgs[i - 1].is_outgoing: user_ends += 1
            else:                        contact_ends += 1
    # Last message of the final conversation
    if msgs[-1].is_outgoing: user_ends += 1
    else:                     contact_ends += 1

    # ── Average response times ───────────────────────────────────────────────
    user_resp_times:    list[float] = []
    contact_resp_times: list[float] = []
    for i in range(1, len(msgs)):
        gap = (msgs[i].sent_at - msgs[i - 1].sent_at).total_seconds()
        if gap > 10800:  # new conversation block, skip
            continue
        if msgs[i].is_outgoing and not msgs[i - 1].is_outgoing:
            user_resp_times.append(gap)
        elif not msgs[i].is_outgoing and msgs[i - 1].is_outgoing:
            contact_resp_times.append(gap)

    avg_user_resp    = (sum(user_resp_times)    / len(user_resp_times))    if user_resp_times    else None
    avg_contact_resp = (sum(contact_resp_times) / len(contact_resp_times)) if contact_resp_times else None

    # ── Media counts ─────────────────────────────────────────────────────────
    media_counts: dict[str, int] = {}
    for m in msgs:
        mt = m.media_type.value if hasattr(m.media_type, "value") else str(m.media_type)
        if mt and mt not in ("none", "NONE", ""):
            media_counts[mt] = media_counts.get(mt, 0) + 1

    # ── Date range ────────────────────────────────────────────────────────────
    first_at  = msgs[0].sent_at.strftime("%d.%m.%Y") if msgs else "-"
    last_at   = msgs[-1].sent_at.strftime("%d.%m.%Y") if msgs else "-"
    days_span = max(1, (msgs[-1].sent_at - msgs[0].sent_at).days) if len(msgs) > 1 else 1

    # ── Derived balance labels ───────────────────────────────────────────────
    def _winner(a: int | float, b: int | float, label_a: str, label_b: str) -> str:
        if a > b * 1.2:   return label_a
        if b > a * 1.2:   return label_b
        return "Поровну"

    balance_initiates    = _winner(initiates_user, initiates_contact, "Вы", "Собеседник")
    balance_ends         = _winner(user_ends, contact_ends, "Вы", "Собеседник")
    balance_longer       = _winner(user_avg_words, contact_avg_words, "Вы", "Собеседник")

    # Ignores = longer average response time
    if avg_user_resp is not None and avg_contact_resp is not None:
        balance_ignores = _winner(avg_user_resp, avg_contact_resp, "Вы", "Собеседник")
    else:
        balance_ignores = "—"

    return {
        "total_messages":          len(msgs),
        "user_messages":           len(user_msgs),
        "contact_messages":        len(contact_msgs),
        "user_words":              user_words,
        "contact_words":           contact_words,
        "user_avg_words":          user_avg_words,
        "contact_avg_words":       contact_avg_words,
        "initiates_user":          initiates_user,
        "initiates_contact":       initiates_contact,
        "media":                   media_counts,
        "first_message_date":      first_at,
        "last_message_date":       last_at,
        "days_span":               days_span,
        "messages_per_day":        round(len(msgs) / days_span, 1),
        # pre-computed balance
        "balance_initiates":       balance_initiates,
        "balance_ends":            balance_ends,
        "balance_longer":          balance_longer,
        "balance_ignores":         balance_ignores,
        "avg_response_time_user":  _fmt_duration(avg_user_resp),
        "avg_response_time_contact": _fmt_duration(avg_contact_resp),
    }


# ── Transcript builder ────────────────────────────────────────────────────────

def _build_transcript(msgs: list[Message], max_chars: int = 18_000) -> str:
    # Iterate newest-first so the char budget keeps RECENT messages.
    # After collecting, reverse back to chronological order for the AI.
    lines: list[str] = []
    total = 0
    for m in reversed(msgs):
        ts   = m.sent_at.strftime("%d.%m %H:%M")
        who  = "Вы" if m.is_outgoing else "Собеседник"
        text = (m.text or m.caption or "").strip()
        mt   = m.media_type.value if hasattr(m.media_type, "value") else str(m.media_type)
        if not text:
            text = f"[{_MEDIA_LABEL.get(mt, mt)}]"
        line = f"[{ts}] {who}: {text}"
        total += len(line)
        if total > max_chars:
            lines.append("... (ранние сообщения пропущены)")
            break
        lines.append(line)
    lines.reverse()  # chronological order for the AI
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

Шкала оценки (general_score.score) — число от 1 до 10, СТРОГО основанное на анализе:
- 1–2: серьёзные проблемы (конфликты, агрессия, игнорирование, явная односторонность)
- 3–4: слабое общение (сухие ответы, минимальный интерес, редкость контакта)
- 5–6: нейтральное/среднее (обычная переписка без ярких плюсов или минусов)
- 7–8: хорошее общение (взаимность, позитив, регулярность, интерес с обеих сторон)
- 9–10: отличное общение (глубокое взаимопонимание, высокая вовлечённость, тёплые эмоции)
ВАЖНО: НЕ ставь всем одинаковую оценку — анализируй реально и давай честный результат.

- interest.probability: число от 0 до 100 (насколько вероятно, что собеседник заинтересован)
- emotions: 5 чисел, в сумме дающих ~100
- dynamics.trend: одно из "growing" | "stable" | "declining"

ОБЯЗАТЕЛЬНО заполни все поля:
- balance.*: используй ТОЧНО предоставленные предвычисленные значения
- style.user_tags: 3–5 коротких тегов стиля общения пользователя (примеры: "краткий", "эмоциональный", "с юмором", "вдумчивый", "прямой")
- style.contact_tags: 3–5 коротких тегов стиля собеседника
- personality.user_traits: 3–5 черт характера пользователя по переписке (примеры: "открытый", "заботливый", "импульсивный", "сдержанный")
- personality.contact_traits: 3–5 черт характера собеседника
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

    msgs = await _fetch_messages(session, chat_id, connection_ids, limit=1000)
    if not msgs:
        raise ValueError("no_messages")

    stats   = _local_stats(msgs)
    excerpt = _build_transcript(msgs)

    user_prompt = f"""Проанализируй переписку между пользователем («Вы») и их собеседником («Собеседник»).
Имя собеседника: {contact_name}

Базовая статистика (уже посчитана):
- Всего сообщений: {stats['total_messages']} (Вы: {stats['user_messages']}, Собеседник: {stats['contact_messages']})
- Слов: Вы — {stats['user_words']}, Собеседник — {stats['contact_words']}
- Среднее слов в сообщении: Вы — {stats['user_avg_words']}, Собеседник — {stats['contact_avg_words']}
- Инициировали разговор: Вы — {stats['initiates_user']} раз, Собеседник — {stats['initiates_contact']} раз
- Период: {stats['first_message_date']} — {stats['last_message_date']} ({stats['days_span']} дней)
- Среднее сообщений в день: {stats['messages_per_day']}

Предвычисленные данные баланса (вставь ТОЧНО эти значения в соответствующие поля balance):
- balance.initiates_first = "{stats['balance_initiates']}"
- balance.ends_conversation = "{stats['balance_ends']}"
- balance.longer_messages = "{stats['balance_longer']}"
- balance.ignores_more = "{stats['balance_ignores']}"
- balance.avg_response_time_user = "{stats['avg_response_time_user']}"
- balance.avg_response_time_contact = "{stats['avg_response_time_contact']}"

Переписка:
{excerpt}

Верни JSON по схеме. ОБЯЗАТЕЛЬНО заполни style.user_tags, style.contact_tags, personality.user_traits, personality.contact_traits — минимум 3 значения в каждом."""

    import asyncio  # noqa: PLC0415
    from google import genai  # noqa: PLC0415
    from google.genai import types  # noqa: PLC0415

    client = genai.Client(api_key=api_key)

    full_prompt = (
        user_prompt
        + "\n\nОтвет верни СТРОГО в формате JSON без пояснений и markdown-обёрток."
    )
    # Try primary model first; if quota exhausted fall back to a model with a
    # separate free-tier quota pool (gemini-1.5-flash).
    _MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite", "gemini-1.5-flash"]
    response = None
    last_exc: Exception | None = None
    for _model in _MODELS:
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=_model,
                    contents=full_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=_SYSTEM_PROMPT,
                        temperature=0.4,
                        response_mime_type="application/json",
                    ),
                ),
                timeout=90.0,
            )
            break  # success — stop trying
        except asyncio.TimeoutError as exc:
            raise ValueError("gemini_timeout") from exc
        except Exception as exc:
            exc_str = str(exc)
            if "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str or "quota" in exc_str.lower():
                logger.warning("Gemini quota hit on %s, trying next model…", _model)
                last_exc = exc
                continue  # try fallback
            raise ValueError(f"gemini_error: {type(exc).__name__}: {exc}") from exc
    else:
        # All models exhausted quota
        raise ValueError("gemini_quota") from last_exc

    raw = response.text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw.strip())

    try:
        ai_data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            ai_data = json.loads(m.group())
        else:
            raise ValueError(f"bad_json: {raw[:300]}")

    result = {
        "stats":         stats,
        "ai":            ai_data,
        "analyzed_at":   dt.datetime.now(dt.timezone.utc).isoformat(),
        "message_count": len(msgs),
    }
    _store(owner_id, chat_id, result)
    return result
