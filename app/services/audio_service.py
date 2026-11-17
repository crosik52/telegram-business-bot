"""Bounded audio search/inline adapter; all network work uses DownloadService."""

from __future__ import annotations

import asyncio
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from app.logging_config import get_logger
from app.services.download_service import DownloadError, get_download_service, validate_video_url

logger = get_logger(__name__)
MAX_BYTES = 20 * 1024 * 1024
MAX_DURATION_SECS = 15 * 60
_CACHE_TTL = 600
PAGE_SIZE = 5
SEARCH_N = 15
MAX_RESULTS = 512
MAX_SESSIONS = 64
MAX_HISTORY_USERS = 256
MAX_FILE_IDS = 512
MAX_PRELOAD_BYTES = 32 * 1024 * 1024
MAX_PRELOAD_TASKS = 3


class CancelledByUser(RuntimeError):
    """Shared subprocess/context cancellation has completed."""


_file_id_cache: dict[str, str] = {}
_predownload_cache: dict[str, tuple[bytes, str, float]] = {}
_predownload_tasks: set[asyncio.Task] = set()
_predownload_by_key: dict[str, asyncio.Task] = {}


def get_predownloaded(key: str) -> tuple[bytes, str] | None:
    entry = _predownload_cache.get(key)
    if entry is None:
        return None
    data, filename, timestamp = entry
    if time.monotonic() - timestamp > _CACHE_TTL or get(key) is None:
        _predownload_cache.pop(key, None)
        return None
    return data, filename


def _store_predownloaded(key: str, data: bytes, filename: str) -> None:
    if len(data) > MAX_BYTES or len(data) > MAX_PRELOAD_BYTES or get(key) is None:
        return
    cutoff = time.monotonic() - _CACHE_TTL
    for stale in list(_predownload_cache):
        if _predownload_cache[stale][2] < cutoff or get(stale) is None:
            _predownload_cache.pop(stale, None)
    _predownload_cache.pop(key, None)
    while (
        _predownload_cache
        and sum(len(entry[0]) for entry in _predownload_cache.values()) + len(data)
        > MAX_PRELOAD_BYTES
    ):
        _predownload_cache.pop(next(iter(_predownload_cache)))
    _predownload_cache[key] = (data, filename, time.monotonic())


def start_predownload(key: str, url: str) -> None:
    result = get(key)
    if (
        result is None
        or result.url != url
        or key in _predownload_by_key
        or get_predownloaded(key) is not None
    ):
        return
    if len(_predownload_tasks) >= MAX_PRELOAD_TASKS:
        return

    async def run() -> None:
        try:
            data, filename = await stream_to_bytes(url)
            _store_predownloaded(key, data, filename)
        except Exception as error:
            logger.debug("Audio preload failed error_type=%s", type(error).__name__)

    try:
        task = asyncio.get_running_loop().create_task(run())
    except RuntimeError:
        return
    _predownload_tasks.add(task)
    _predownload_by_key[key] = task

    def finished(done: asyncio.Task) -> None:
        _predownload_tasks.discard(done)
        _predownload_by_key.pop(key, None)

    task.add_done_callback(finished)


async def shutdown_predownloads() -> None:
    tasks = list(_predownload_tasks)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _predownload_tasks.clear()
    _predownload_by_key.clear()
    _predownload_cache.clear()


def get_cached_file_id(url: str) -> str | None:
    return _file_id_cache.get(url)


def cache_file_id(url: str, file_id: str) -> None:
    validate_video_url(url, audio=True)
    if len(file_id) > 512:
        return
    if url not in _file_id_cache and len(_file_id_cache) >= MAX_FILE_IDS:
        _file_id_cache.pop(next(iter(_file_id_cache)))
    _file_id_cache[url] = file_id


HISTORY_SIZE = 7
_user_history: dict[int, list[str]] = {}  # user_id в†’ ordered list of keys (newest first)


def add_to_history(user_id: int, key: str) -> None:
    """Prepend key to user's history, evicting the oldest entry past HISTORY_SIZE."""
    if user_id not in _user_history and len(_user_history) >= MAX_HISTORY_USERS:
        _user_history.pop(next(iter(_user_history)))
    history = _user_history.setdefault(user_id, [])
    # Remove duplicate if present so the track bubbles to the top
    try:
        history.remove(key)
    except ValueError:
        pass
    history.insert(0, key)
    del history[HISTORY_SIZE:]


def get_history(user_id: int) -> list[str]:
    """Return the user's recent track keys, newest first."""
    return list(_user_history.get(user_id, []))


# в”Ђв”Ђ In-memory result cache в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ


class _Result:
    __slots__ = ("url", "title", "uploader", "duration", "bc_id", "chat_id", "ts", "session_key")

    def __init__(
        self, url: str, title: str, uploader: str, duration: int, bc_id: str, chat_id: int
    ) -> None:
        self.url = url
        self.title = title
        self.uploader = uploader
        self.duration = duration
        self.bc_id = bc_id
        self.chat_id = chat_id
        self.ts = time.monotonic()
        self.session_key = ""  # set after session is created


_cache: dict[str, _Result] = {}


def _evict() -> None:
    cutoff = time.monotonic() - _CACHE_TTL
    stale = [k for k, v in _cache.items() if v.ts < cutoff]
    for k in stale:
        del _cache[k]


def store(url: str, title: str, uploader: str, duration: int, bc_id: str, chat_id: int) -> str:
    """Persist a single result and return its 128-bit capability key."""
    _evict()
    validate_video_url(url, audio=True)
    if len(_cache) >= MAX_RESULTS:
        _cache.pop(next(iter(_cache)))
    title, uploader = title[:256], uploader[:128]
    key = uuid4().hex
    _cache[key] = _Result(url, title, uploader, duration, bc_id, chat_id)
    return key


def get(key: str) -> _Result | None:
    _evict()
    if not re.fullmatch(r"[a-f0-9]{32}", key):
        return None
    return _cache.get(key)


def set_session_key(key: str, sk: str) -> None:
    """Attach a session key to a cached result (called after session is created)."""
    r = _cache.get(key)
    if r:
        r.session_key = sk


# в”Ђв”Ђ Search-session cache (for pagination) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
# A session holds the ordered list of result-keys for an entire search query.
# Navigation callbacks reference the session key + page number.


class _Session:
    """Full results list for one !mp3 search вЂ” used for page navigation.

    Each entry: {"key": str, "title": str, "uploader": str, "duration": int}
    ``key`` maps into ``_cache`` for the actual download URL.
    """

    __slots__ = ("entries", "query", "ts")

    def __init__(self, entries: list[dict], query: str) -> None:
        self.entries = entries  # [{key, title, uploader, duration}, вЂ¦]
        self.query = query
        self.ts = time.monotonic()


_sessions: dict[str, _Session] = {}


def _evict_sessions() -> None:
    cutoff = time.monotonic() - _CACHE_TTL
    stale = [k for k, v in _sessions.items() if v.ts < cutoff]
    for k in stale:
        del _sessions[k]


def store_session(entries: list[dict], query: str) -> str:
    """Persist a search session and return its 128-bit session key."""
    _evict_sessions()
    if len(_sessions) >= MAX_SESSIONS:
        _sessions.pop(next(iter(_sessions)))
    entries, query = entries[:SEARCH_N], query[:200]
    sk = uuid4().hex
    _sessions[sk] = _Session(entries, query)
    return sk


def get_session(sk: str) -> _Session | None:
    _evict_sessions()
    return _sessions.get(sk)


# в”Ђв”Ђ Helpers в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ


def fmt_duration(secs: int | None) -> str:
    if not secs:
        return "?:??"
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# в”Ђв”Ђ Non-music keyword filter в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
# Titles containing any of these substrings (case-insensitive) are almost
# never the track itself вЂ” they're reactions, reviews, covers analysis, etc.
# Used as a last-resort filter when we fall back to plain ytsearch.

_NON_MUSIC_KEYWORDS: frozenset[str] = frozenset(
    {
        "СЂРµР°РєС†РёСЏ",
        "reaction",
        "СЂРµР°РєС‚",
        "react ",
        "РѕР±Р·РѕСЂ",
        "review ",
        "СЂР°Р·Р±РѕСЂ",
        "СЂР°Р·Р±РѕСЂРєР°",
        "СЃР»СѓС€Р°СЋ РІРїРµСЂРІС‹Рµ",
        "СЃР»СѓС€Р°РµРј РІРїРµСЂРІС‹Рµ",
        "РёСЃС‚РѕСЂРёСЏ СЃРѕР·РґР°РЅРёСЏ",
        "РёСЃС‚РѕСЂРёСЏ РїРµСЃРЅРё",
        "РёСЃС‚РѕСЂРёСЏ С‚СЂРµРєР°",
        "РѕР±СЉСЏСЃРЅРµРЅРёРµ",
        "explanation",
        "С‚СЂРёР±СЊСЋС‚",
        "tribute",
        "РїРѕРґР±РѕСЂРєР°",
        "compilation",
        "#shorts",
        "podcast",
        "РїРѕРґРєР°СЃС‚",
        "СЃС‚СЂРёРј",
        "stream",
        "live stream",
        "РёРЅС‚РµСЂРІСЊСЋ",
        "interview",
        "С‚РѕРї Р»СѓС‡С€РёС…",
        "С‚РѕРї РїРµСЃРµРЅ",
    }
)


def _is_music(title: str) -> bool:
    """Return False when a title strongly suggests a non-music video."""
    t = title.lower()
    return not any(kw in t for kw in _NON_MUSIC_KEYWORDS)


# в”Ђв”Ђ Search (sync, runs in executor) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ


async def search(query: str, n: int = SEARCH_N) -> list[dict]:
    if not isinstance(query, str) or not query.strip() or len(query) > 200:
        raise DownloadError("invalid_search")
    if not 1 <= n <= SEARCH_N:
        raise DownloadError("invalid_search_count")
    return await get_download_service().search(query.strip(), n=n)


async def stream_to_bytes(url: str) -> tuple[bytes, str]:
    async with get_download_service().media(url, mode="audio") as artifact:
        path = artifact.paths[0]
        if path.is_symlink() or not 0 < path.stat().st_size <= MAX_BYTES:
            raise DownloadError("audio_too_large")
        with path.open("rb") as source:
            data = source.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise DownloadError("audio_too_large")
        return data, "audio.mp3"


async def download(
    url: str,
    out_dir: str,
    *,
    fallback_title: str | None = None,
    fallback_uploader: str | None = None,
    cancel_flag: threading.Event | None = None,
) -> tuple[Path, str, str, int]:
    validate_video_url(url, audio=True)
    if cancel_flag is not None and cancel_flag.is_set():
        raise CancelledByUser
    destination = Path(out_dir)
    if not destination.is_dir() or destination.is_symlink():
        raise DownloadError("unsafe_output")
    output = destination / ("audio-" + uuid4().hex + ".mp3")

    async def copy(source_url: str) -> tuple[Path, str, str, int]:
        async with get_download_service().media(source_url, mode="audio") as artifact:
            source_path = artifact.paths[0]
            if source_path.is_symlink() or not 0 < source_path.stat().st_size <= MAX_BYTES:
                raise DownloadError("audio_too_large")
            copied = 0
            with source_path.open("rb") as source, output.open("xb") as target:
                while chunk := source.read(256 * 1024):
                    copied += len(chunk)
                    if copied > MAX_BYTES:
                        raise DownloadError("audio_too_large")
                    target.write(chunk)
                    await asyncio.sleep(0)
            return (
                output,
                (artifact.title or fallback_title or "Audio")[:256],
                (artifact.uploader or fallback_uploader or "")[:128],
                artifact.duration,
            )

    async def run() -> tuple[Path, str, str, int]:
        async with asyncio.timeout(150):
            try:
                return await copy(url)
            except DownloadError as error:
                if not fallback_title or str(error) not in {
                    "download_failed",
                    "audio_download_failed",
                    "audio_unavailable",
                    "extractor_failed",
                    "unsupported_media",
                }:
                    raise
                query = " ".join(filter(None, [fallback_uploader, fallback_title]))[:200]
                candidates = await search(query, n=5)
                for candidate in candidates:
                    if urlsplit(candidate["url"]).hostname in {
                        "soundcloud.com",
                        "www.soundcloud.com",
                    }:
                        return await copy(candidate["url"])
                raise DownloadError("audio_download_failed") from None

    job = asyncio.create_task(run())
    try:
        while not job.done():
            if cancel_flag is not None and cancel_flag.is_set():
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)
                raise CancelledByUser
            await asyncio.wait({job}, timeout=0.1)
        return await job
    except BaseException:
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
        output.unlink(missing_ok=True)
        raise
