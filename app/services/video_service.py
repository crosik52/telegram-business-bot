"""Video download service — detects Reels / TikTok / YouTube Shorts links
and sends the downloaded video back into the business chat.

Supported platforms
-------------------
- Instagram Reels  (instagram.com/reel/…)
- TikTok           (tiktok.com/… | vm.tiktok.com/… | vt.tiktok.com/…)
- YouTube Shorts   (youtube.com/shorts/…)

Limits
------
- Max file size: 45 MB (Telegram bot API hard-caps at 50 MB; we leave headroom).
- yt-dlp is run in a thread-pool executor so it doesn't block the event loop.
- Temp files are always cleaned up, even on failure.

Error handling
--------------
All errors are caught and logged as warnings; the chat never receives an
error message — silence is preferable to noise on unavailable videos.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from functools import partial
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile

from app.logging_config import get_logger

logger = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_BYTES = 45 * 1024 * 1024  # 45 MB

# Platform labels shown in the caption sent with the video.
_PLATFORM_LABELS = {
    "instagram": "Instagram Reels",
    "tiktok":    "TikTok",
    "youtube":   "YouTube Shorts",
}

# ── URL detection ─────────────────────────────────────────────────────────────

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("instagram", re.compile(
        r"https?://(?:www\.)?instagram\.com/reel/[A-Za-z0-9_-]+/?[^\s]*",
        re.IGNORECASE,
    )),
    ("tiktok", re.compile(
        r"https?://(?:www\.|vm\.|vt\.)?tiktok\.com/[^\s]+",
        re.IGNORECASE,
    )),
    ("youtube", re.compile(
        r"https?://(?:www\.)?youtube\.com/shorts/[A-Za-z0-9_-]+/?[^\s]*",
        re.IGNORECASE,
    )),
]


def extract_video_url(text: str) -> tuple[str, str] | None:
    """Return (url, platform_key) for the first matching video link, or None."""
    for platform, pattern in _PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0).rstrip(".,;!?)"), platform
    return None


# ── Download (sync, runs in executor) ────────────────────────────────────────

def _download_sync(url: str, out_dir: str) -> Path:
    """Download *url* into *out_dir* using yt-dlp.

    Returns the Path of the downloaded file.
    Raises yt_dlp.DownloadError / ValueError on failure.
    """
    import yt_dlp  # local import — only needed when this function runs

    ydl_opts: dict = {
        "format": (
            # Best mp4 under 45 MB, fall back to best available mp4/webm
            f"bestvideo[ext=mp4][filesize<{MAX_BYTES}]+bestaudio[ext=m4a]/"
            f"best[ext=mp4][filesize<{MAX_BYTES}]/"
            "best[ext=mp4]/best"
        ),
        "outtmpl": os.path.join(out_dir, "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "max_filesize": MAX_BYTES,
        # Trim to 3 minutes — avoids accidentally downloading full videos
        # that share the same URL pattern (e.g. long TikToks).
        "match_filter": yt_dlp.utils.match_filter_func("duration < 180"),
        "merge_output_format": "mp4",
        "postprocessors": [{
            "key": "FFmpegVideoConvertor",
            "preferedformat": "mp4",
        }],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    # yt-dlp may return a playlist wrapper even with noplaylist=True
    if "entries" in info:
        info = info["entries"][0]

    filename = ydl.prepare_filename(info)
    # yt-dlp may have changed the extension after post-processing
    path = Path(filename)
    if not path.exists():
        # Try the merged .mp4 variant
        path = path.with_suffix(".mp4")
    if not path.exists():
        raise FileNotFoundError(f"yt-dlp finished but output not found: {filename}")

    size = path.stat().st_size
    if size > MAX_BYTES:
        path.unlink(missing_ok=True)
        raise ValueError(f"Video too large: {size // (1024*1024)} MB > 45 MB limit")

    return path


# ── Main async entry point ────────────────────────────────────────────────────

async def handle_video_link(
    bot: Bot,
    chat_id: int,
    business_connection_id: str,
    url: str,
    platform: str,
) -> None:
    """Download the video at *url* and send it to the business chat.

    This function is fire-and-forget — all errors are swallowed after logging.
    """
    label = _PLATFORM_LABELS.get(platform, platform)
    tmp_dir = tempfile.mkdtemp(prefix="vidbot_")

    try:
        logger.info("Downloading %s video: %s", label, url)

        loop = asyncio.get_running_loop()
        path: Path = await loop.run_in_executor(
            None, partial(_download_sync, url, tmp_dir)
        )

        logger.info("Downloaded %s (%.1f MB), sending to chat %s", path.name, path.stat().st_size / 1024 / 1024, chat_id)

        await bot.send_video(
            chat_id=chat_id,
            business_connection_id=business_connection_id,
            video=FSInputFile(path),
            caption=f"📥 {label}",
            supports_streaming=True,
        )
        logger.info("Video sent to chat_id=%s", chat_id)

    except Exception as exc:  # noqa: BLE001
        logger.warning("Video download/send failed for %s (%s): %s", url, label, exc)

    finally:
        # Always clean up temp directory
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
