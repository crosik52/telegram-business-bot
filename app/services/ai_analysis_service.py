"""AI Relationship Analysis using Google Gemini.

Fetches the last N messages for a chat, computes raw stats locally,
then asks Gemini to produce a structured 10-section relationship report.
Results are cached in the database per (owner_id, chat_id) for 24 hours
(survives Railway deploys) with a fast in-memory L1 layer on top.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import time
from typing import Any

from sqlalchemy import and_, delete as sa_delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_analysis_cache import AiAnalysisCache
from app.models.ai_analysis_daily_count import AiAnalysisDailyCount
from app.models.message import MediaType, Message

logger = logging.getLogger(__name__)

# ── Result cache — L1 in-memory + L2 database ────────────────────────────────
# L1 (in-memory): fast path, avoids repeated JSON parsing within same process.
# L2 (database):  survives Railway deploys; checked when L1 misses.
#
# Bump _PROMPT_VERSION whenever the system prompt or schema changes so that
# cached results built with the old prompt are automatically discarded.
_PROMPT_VERSION = "v2"  # bumped: red_flags prompt expanded
_CACHE: dict[tuple[int, int], tuple[float, dict]] = {}
_CACHE_TTL = 86400  # 24 hours
_L1_MAX_SIZE = int(os.environ.get("AI_L1_CACHE_MAX_SIZE", "500"))

def _l1_get(owner_id: int, chat_id: int) -> dict | None:
    entry = _CACHE.get((owner_id, chat_id))
    if entry and (time.time() - entry[0]) < _CACHE_TTL:
        result = entry[1]
        if result.get("prompt_version") == _PROMPT_VERSION:
            return result
    return None


def _l1_set(owner_id: int, chat_id: int, result: dict) -> None:
    key = (owner_id, chat_id)
    # Enforce max-size cap: evict the oldest entry before inserting a new key.
    if key not in _CACHE and len(_CACHE) >= _L1_MAX_SIZE:
        oldest_key = min(_CACHE, key=lambda k: _CACHE[k][0])
        _CACHE.pop(oldest_key, None)
    _CACHE[key] = (time.time(), result)


def _l1_evict_expired() -> int:
    """Remove all expired entries from the L1 in-memory cache.

    Scans the entire ``_CACHE`` dict and deletes every entry whose timestamp
    is older than ``_CACHE_TTL``.  Safe to call from a background loop because
    it snapshots the key list before iterating.

    Returns the number of entries evicted (useful for logging / tests).
    """
    now = time.time()
    expired = [
        key for key, (ts, _) in list(_CACHE.items())
        if (now - ts) >= _CACHE_TTL
    ]
    for key in expired:
        _CACHE.pop(key, None)
    if expired:
        logger.debug("L1 cache eviction: removed %d expired entries", len(expired))
    return len(expired)


async def _db_get(
    session: AsyncSession, owner_id: int, chat_id: int
) -> dict | None:
    """Return cached result from DB if it exists and is within TTL."""
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=_CACHE_TTL)
    row = await session.scalar(
        select(AiAnalysisCache).where(
            AiAnalysisCache.owner_id == owner_id,
            AiAnalysisCache.chat_id == chat_id,
            AiAnalysisCache.analyzed_at >= cutoff,
        )
    )
    if row is None:
        return None
    try:
        result = json.loads(row.result_json)
        if result.get("prompt_version") != _PROMPT_VERSION:
            return None  # stale — prompt changed, force re-analysis
        return result
    except Exception:
        return None


async def _db_set(
    session: AsyncSession, owner_id: int, chat_id: int, result: dict
) -> None:
    """Upsert analysis result into DB cache."""
    now = dt.datetime.now(dt.timezone.utc)
    stmt = (
        pg_insert(AiAnalysisCache)
        .values(
            owner_id=owner_id,
            chat_id=chat_id,
            result_json=json.dumps(result, ensure_ascii=False),
            analyzed_at=now,
        )
        .on_conflict_do_update(
            index_elements=["owner_id", "chat_id"],
            set_={"result_json": json.dumps(result, ensure_ascii=False), "analyzed_at": now},
        )
    )
    await session.execute(stmt)
    await session.commit()


async def invalidate_cache(
    session: AsyncSession, owner_id: int, chat_id: int
) -> None:
    """Delete the cached analysis for a single (owner_id, chat_id) pair.

    Called when messages are deleted for a chat so stale results are not
    served until the TTL expires.
    """
    _CACHE.pop((owner_id, chat_id), None)
    await session.execute(
        sa_delete(AiAnalysisCache).where(
            AiAnalysisCache.owner_id == owner_id,
            AiAnalysisCache.chat_id == chat_id,
        )
    )
    await session.commit()
    logger.debug(
        "Analysis cache invalidated for owner=%s chat=%s", owner_id, chat_id
    )


async def invalidate_cache_for_owner(
    session: AsyncSession, owner_id: int
) -> None:
    """Delete ALL cached analyses for an owner.

    Called when a BusinessConnection is revoked so no stale analyses remain
    across any of the owner's chats.
    """
    # Evict every matching L1 entry.
    stale_keys = [k for k in list(_CACHE) if k[0] == owner_id]
    for k in stale_keys:
        _CACHE.pop(k, None)
    await session.execute(
        sa_delete(AiAnalysisCache).where(AiAnalysisCache.owner_id == owner_id)
    )
    await session.commit()
    logger.debug(
        "Analysis cache invalidated for all chats of owner=%s (%d L1 entries evicted)",
        owner_id,
        len(stale_keys),
    )


# ── Per-user daily rate-limit (cost control on paid Gemini tier) ──────────────
# Counts are persisted in `ai_analysis_daily_counts` so they survive deploys.
DAILY_ANALYSIS_LIMIT = int(os.environ.get("AI_ANALYSIS_DAILY_LIMIT", "10"))


def _get_utc_date() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


async def _get_daily_count(session: AsyncSession, owner_id: int) -> int:
    """Return how many analyses the owner has run today (UTC)."""
    today = _get_utc_date()
    row = await session.scalar(
        select(AiAnalysisDailyCount).where(
            AiAnalysisDailyCount.owner_id == owner_id,
            AiAnalysisDailyCount.date == today,
        )
    )
    return row.count if row is not None else 0


async def _check_rate_limit(session: AsyncSession, owner_id: int) -> bool:
    """Return True if the user is within their daily limit, False if exceeded."""
    count = await _get_daily_count(session, owner_id)
    return count < DAILY_ANALYSIS_LIMIT


async def _increment_daily_count(session: AsyncSession, owner_id: int) -> None:
    """Atomically increment (or create) today's usage row in the DB."""
    today = _get_utc_date()
    stmt = (
        pg_insert(AiAnalysisDailyCount)
        .values(owner_id=owner_id, date=today, count=1)
        .on_conflict_do_update(
            index_elements=["owner_id", "date"],
            set_={"count": AiAnalysisDailyCount.count + 1},
        )
    )
    await session.execute(stmt)
    await session.commit()


async def get_remaining(session: AsyncSession, owner_id: int) -> int:
    """Return how many fresh AI analyses the user can still run today."""
    count = await _get_daily_count(session, owner_id)
    return max(0, DAILY_ANALYSIS_LIMIT - count)


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
- red_flags: ВСЕГДА найди хотя бы 1–2 наблюдения, если в переписке есть что-то настораживающее — даже незначительное. Примеры разного уровня:
  • лёгкие: односторонность инициативы, сухие короткие ответы, долгое игнорирование
  • средние: ревность, контроль, пассивная агрессия, резкие перепады тона
  • серьёзные: оскорбления, манипуляции, угрозы, полное игнорирование
  Если переписка действительно идеальна без единого тревожного знака — оставь пустым. Но не занижай намеренно.
- green_flags: реально присутствующие позитивные знаки (взаимность, поддержка, юмор, инициатива с обеих сторон и т.д.)

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
    """Run AI analysis for a chat. Returns cached result if fresh.

    Cache hierarchy:
      L1 — in-memory dict (fast, per-process, lost on restart)
      L2 — database row (survives restarts/deploys)
    Gemini API is only called when both caches miss.
    """
    # L1 check
    hit = _l1_get(owner_id, chat_id)
    if hit:
        return hit

    # L2 check — DB cache survives deploys
    db_hit = await _db_get(session, owner_id, chat_id)
    if db_hit:
        _l1_set(owner_id, chat_id, db_hit)  # warm L1 for next request
        return db_hit

    # Per-user daily rate-limit — cost control on paid Gemini tier.
    # Cached results bypass this check (they don't hit the API).
    if not await _check_rate_limit(session, owner_id):
        logger.warning(
            "Daily AI analysis limit (%d) reached for user %s",
            DAILY_ANALYSIS_LIMIT,
            owner_id,
        )
        raise ValueError("gemini_rate_limit")

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
    # Consume one daily slot before hitting the API (prevents concurrent
    # requests from all slipping past the check simultaneously).
    await _increment_daily_count(session, owner_id)

    # Working models verified against this API key (July 2026):
    #   gemini-3.5-flash-lite  — OK (primary)
    #   gemini-flash-lite-latest — OK (alias for current lite)
    #   gemini-3.5-flash       — 503 under high demand (temporary), falls through
    #   gemini-2.0-flash*      — 429 quota exhausted on free tier, last resort
    #   gemini-2.5-* / 1.5-*  — 404 "no longer available" on this key
    _MODELS = [
        "gemini-3.5-flash-lite",
        "gemini-flash-lite-latest",
        "gemini-3.5-flash",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
    ]
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
                        response_schema=_SCHEMA,
                    ),
                ),
                timeout=90.0,
            )
            break  # success — stop trying
        except asyncio.TimeoutError as exc:
            raise ValueError("gemini_timeout") from exc
        except Exception as exc:
            exc_str = str(exc)
            # Fall through to next model on quota exhaustion OR model deprecation (404 NOT_FOUND).
            if (
                "429" in exc_str
                or "503" in exc_str
                or "RESOURCE_EXHAUSTED" in exc_str
                or "UNAVAILABLE" in exc_str
                or "quota" in exc_str.lower()
                or "NOT_FOUND" in exc_str
                or "no longer available" in exc_str.lower()
            ):
                logger.warning("Gemini model %s unavailable (%s), trying next…", _model, exc_str[:120])
                last_exc = exc
                continue  # try fallback
            raise ValueError(f"gemini_error: {type(exc).__name__}: {exc}") from exc
    else:
        # All models exhausted / unavailable
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
        "stats":          stats,
        "ai":             ai_data,
        "analyzed_at":    dt.datetime.now(dt.timezone.utc).isoformat(),
        "message_count":  len(msgs),
        "prompt_version": _PROMPT_VERSION,
    }
    _l1_set(owner_id, chat_id, result)
    await _db_set(session, owner_id, chat_id, result)
    return result
