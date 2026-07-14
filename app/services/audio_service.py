"""Audio search & download service for the !mp3 command.

Flow
----
1. Owner types ``!mp3 название`` in a business chat.
2. Bot searches YouTube (top-5) and shows an inline-keyboard with results.
3. Owner taps a result → callback handler downloads the MP3 and replaces the
   search message with the audio file via edit_message_media.

Search results are held in a lightweight in-memory dict with a 10-minute TTL.
Each entry is keyed by an 8-char hex token used in callback_data.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from functools import partial
from pathlib import Path
from uuid import uuid4

from app.logging_config import get_logger

logger = get_logger(__name__)

MAX_BYTES         = 48 * 1024 * 1024   # 48 MB — Telegram audio cap
MAX_DURATION_SECS = 15 * 60            # 15 minutes — skip albums / long mixes
_CACHE_TTL        = 600                # 10 minutes


# ── In-memory result cache ────────────────────────────────────────────────────

class _Result:
    __slots__ = ("url", "title", "uploader", "duration", "bc_id", "chat_id", "ts")

    def __init__(self, url: str, title: str, uploader: str, duration: int,
                 bc_id: str, chat_id: int) -> None:
        self.url      = url
        self.title    = title
        self.uploader = uploader
        self.duration = duration
        self.bc_id    = bc_id
        self.chat_id  = chat_id
        self.ts       = time.monotonic()


_cache: dict[str, _Result] = {}


def _evict() -> None:
    cutoff = time.monotonic() - _CACHE_TTL
    stale = [k for k, v in _cache.items() if v.ts < cutoff]
    for k in stale:
        del _cache[k]


def store(url: str, title: str, uploader: str, duration: int,
          bc_id: str, chat_id: int) -> str:
    """Persist a search result and return its 8-char key."""
    _evict()
    key = uuid4().hex[:8]
    _cache[key] = _Result(url, title, uploader, duration, bc_id, chat_id)
    return key


def get(key: str) -> _Result | None:
    return _cache.get(key)


# ── Helpers ───────────────────────────────────────────────────────────────────

def fmt_duration(secs: int | None) -> str:
    if not secs:
        return "?:??"
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ── Search (sync, runs in executor) ──────────────────────────────────────────

def _search_sync(query: str, n: int = 5) -> list[dict]:
    import yt_dlp  # noqa: PLC0415

    opts = {
        "quiet":          True,
        "no_warnings":    True,
        "extract_flat":   True,
        "default_search": f"ytsearch{n}",
        "noplaylist":     True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(query, download=False)

    out = []
    for e in (info or {}).get("entries", []):
        dur = e.get("duration") or 0
        if dur > MAX_DURATION_SECS:
            continue
        vid_id = e.get("id") or e.get("url", "")
        out.append({
            "url":      f"https://www.youtube.com/watch?v={vid_id}",
            "title":    e.get("title") or "Без названия",
            "uploader": e.get("uploader") or e.get("channel") or "",
            "duration": dur,
        })
    return out[:n]


async def search(query: str) -> list[dict]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(_search_sync, query))


# ── Download (sync, runs in executor) ────────────────────────────────────────

def _download_sync(url: str, out_dir: str) -> tuple[Path, str, str, int]:
    """Download *url* as MP3 into *out_dir*. Returns (path, title, uploader, dur)."""
    import yt_dlp  # noqa: PLC0415

    opts = {
        "quiet":       True,
        "no_warnings": True,
        "format":      "bestaudio/best",
        "outtmpl":     os.path.join(out_dir, "%(title)s.%(ext)s"),
        "postprocessors": [{
            "key":             "FFmpegExtractAudio",
            "preferredcodec":  "mp3",
            "preferredquality": "192",
        }],
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    title    = info.get("title")    or "Unknown"
    uploader = info.get("uploader") or info.get("channel") or ""
    duration = int(info.get("duration") or 0)

    mp3s = list(Path(out_dir).glob("*.mp3"))
    if not mp3s:
        # FFmpeg may not be available — grab whatever was downloaded
        all_files = [p for p in Path(out_dir).iterdir() if p.is_file()]
        if not all_files:
            raise FileNotFoundError("yt-dlp produced no output file")
        mp3s = all_files

    path = mp3s[0]
    size = path.stat().st_size
    if size > MAX_BYTES:
        path.unlink(missing_ok=True)
        raise ValueError(f"Audio too large: {size // (1024 * 1024)} MB > 48 MB limit")

    return path, title, uploader, duration


async def download(url: str, out_dir: str) -> tuple[Path, str, str, int]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(_download_sync, url, out_dir))
