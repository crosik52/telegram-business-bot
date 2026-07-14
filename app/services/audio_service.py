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

PAGE_SIZE  = 5    # results per page shown in inline keyboard
SEARCH_N   = 15   # total results fetched from YouTube


# ── Persistent file_id cache (youtube url → telegram file_id) ────────────────
# After a track is uploaded to Telegram once, the file_id is valid forever.
# Using it in InlineQueryResultCachedAudio lets Telegram send audio instantly
# when user selects the result — no placeholder, no button needed.

_file_id_cache: dict[str, str] = {}   # url → telegram file_id


def get_cached_file_id(url: str) -> str | None:
    return _file_id_cache.get(url)


def cache_file_id(url: str, file_id: str) -> None:
    _file_id_cache[url] = file_id


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
    """Persist a single result and return its 8-char key."""
    _evict()
    key = uuid4().hex[:8]
    _cache[key] = _Result(url, title, uploader, duration, bc_id, chat_id)
    return key


def get(key: str) -> _Result | None:
    return _cache.get(key)


# ── Search-session cache (for pagination) ─────────────────────────────────────
# A session holds the ordered list of result-keys for an entire search query.
# Navigation callbacks reference the session key + page number.

class _Session:
    """Full results list for one !mp3 search — used for page navigation.

    Each entry: {"key": str, "title": str, "uploader": str, "duration": int}
    ``key`` maps into ``_cache`` for the actual download URL.
    """
    __slots__ = ("entries", "query", "ts")

    def __init__(self, entries: list[dict], query: str) -> None:
        self.entries = entries   # [{key, title, uploader, duration}, …]
        self.query   = query
        self.ts      = time.monotonic()


_sessions: dict[str, _Session] = {}


def _evict_sessions() -> None:
    cutoff = time.monotonic() - _CACHE_TTL
    stale = [k for k, v in _sessions.items() if v.ts < cutoff]
    for k in stale:
        del _sessions[k]


def store_session(entries: list[dict], query: str) -> str:
    """Persist a search session and return its 8-char session key."""
    _evict_sessions()
    sk = uuid4().hex[:8]
    _sessions[sk] = _Session(entries, query)
    return sk


def get_session(sk: str) -> _Session | None:
    return _sessions.get(sk)


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
        "quiet":        True,
        "no_warnings":  True,
        "extract_flat": "in_playlist",
        "noplaylist":   False,
        "extractor_args": {
            "youtube": {"player_client": ["android"]},
        },
    }
    search_url = f"ytsearch{n or SEARCH_N}:{query}"
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(search_url, download=False)

    logger.debug("mp3 search raw: type=%s entries=%s",
                 type(info).__name__,
                 len((info or {}).get("entries", [])))

    out = []
    for e in (info or {}).get("entries", []):
        if not e:
            continue
        dur    = e.get("duration") or 0
        vid_id = e.get("id") or ""
        if not vid_id:
            continue
        # Skip only if we have a confirmed duration that's too long
        if dur and dur > MAX_DURATION_SECS:
            continue
        # Best thumbnail: prefer the 480px hqdefault, fall back to any URL
        thumb = e.get("thumbnail") or ""
        if not thumb:
            thumbs = e.get("thumbnails") or []
            if thumbs:
                thumb = thumbs[-1].get("url", "")
        out.append({
            "url":       f"https://www.youtube.com/watch?v={vid_id}",
            "title":     e.get("title") or "Без названия",
            "uploader":  e.get("uploader") or e.get("channel") or "",
            "duration":  dur,
            "thumbnail": thumb,
        })
    return out[:n]


async def search(query: str, n: int = SEARCH_N) -> list[dict]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(_search_sync, query, n))


# ── Stream directly to memory (no disk) ──────────────────────────────────────

async def stream_to_bytes(url: str) -> tuple[bytes, str]:
    """Pipe yt-dlp → ffmpeg → RAM.  Returns (audio_bytes, filename).

    No temporary files are created.  The pipeline is:
      yt-dlp -o -  →  ffmpeg -i pipe:0 -f mp3 pipe:1   (if ffmpeg present)
      yt-dlp -o -                                        (raw m4a fallback)
    """
    import shutil as _shutil  # noqa: PLC0415

    has_ffmpeg = bool(_shutil.which("ffmpeg"))

    ytdlp_args = [
        "yt-dlp",
        "--no-playlist",
        "--extractor-args", "youtube:player_client=android",
        "--max-filesize", "48m",
        "--no-part",
        "-q",
        "-f", "bestaudio" if has_ffmpeg else "bestaudio[ext=m4a]/bestaudio",
        "-o", "-",
        url,
    ]

    # Step 1: yt-dlp → raw audio bytes in RAM
    ytdlp_proc = await asyncio.create_subprocess_exec(
        *ytdlp_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    raw_bytes, _ = await ytdlp_proc.communicate()

    if not raw_bytes:
        raise RuntimeError("yt-dlp produced no output")

    if has_ffmpeg:
        # Step 2: raw bytes → ffmpeg stdin → mp3 bytes
        ffmpeg_proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-i", "pipe:0",
            "-vn", "-acodec", "libmp3lame", "-q:a", "2",
            "-f", "mp3", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        audio_bytes, _ = await ffmpeg_proc.communicate(input=raw_bytes)
        del raw_bytes   # free memory immediately
        filename = "track.mp3"
    else:
        audio_bytes = raw_bytes
        filename = "track.m4a"

    if not audio_bytes:
        raise RuntimeError("yt-dlp pipeline produced no output")
    if len(audio_bytes) > MAX_BYTES:
        raise ValueError(f"Audio too large: {len(audio_bytes) // (1024*1024)} MB")

    return audio_bytes, filename


# ── Download (sync, runs in executor) ────────────────────────────────────────

_AUDIO_EXTS = {".mp3", ".m4a", ".ogg", ".opus", ".webm", ".aac", ".flac", ".wav"}


def _download_sync(url: str, out_dir: str) -> tuple[Path, str, str, int]:
    """Download audio into *out_dir*. Returns (path, title, uploader, dur).

    Tries MP3 conversion via FFmpeg first; falls back to the raw bestaudio
    format (m4a/opus/webm) so it works even without FFmpeg on the server.
    Telegram natively plays all common audio containers.
    """
    import shutil as _shutil  # noqa: PLC0415
    import yt_dlp             # noqa: PLC0415

    ffmpeg_ok = _shutil.which("ffmpeg") is not None

    opts: dict = {
        "quiet":       True,
        "no_warnings": True,
        "format":      "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl":     os.path.join(out_dir, "%(title)s.%(ext)s"),
        "noplaylist":  True,
        "extractor_args": {
            "youtube": {"player_client": ["android"]},
        },
    }
    if ffmpeg_ok:
        opts["postprocessors"] = [{
            "key":              "FFmpegExtractAudio",
            "preferredcodec":   "mp3",
            "preferredquality": "192",
        }]

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    title    = info.get("title")    or "Unknown"
    uploader = info.get("uploader") or info.get("channel") or ""
    duration = int(info.get("duration") or 0)

    # Find the downloaded file (any audio extension)
    files = [p for p in Path(out_dir).iterdir()
             if p.is_file() and p.suffix.lower() in _AUDIO_EXTS]
    if not files:
        # Last resort — grab whatever is there
        files = [p for p in Path(out_dir).iterdir() if p.is_file()]
    if not files:
        raise FileNotFoundError("yt-dlp produced no output file")

    path = files[0]
    size = path.stat().st_size
    if size > MAX_BYTES:
        path.unlink(missing_ok=True)
        raise ValueError(f"Audio too large: {size // (1024 * 1024)} MB > 48 MB limit")

    return path, title, uploader, duration


async def download(url: str, out_dir: str) -> tuple[Path, str, str, int]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(_download_sync, url, out_dir))
