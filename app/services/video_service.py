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
import time
from functools import partial
from pathlib import Path
from typing import Callable

from aiogram import Bot
from aiogram.types import FSInputFile, Message

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

def _build_progress_bar(downloaded: int, total: int | None, width: int = 10) -> str:
    """Return a block-character progress bar, e.g. ██████░░░░ 60%"""
    if not total:
        return "░" * width + " …%"
    ratio = min(downloaded / total, 1.0)
    filled = round(ratio * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"{bar} {round(ratio * 100)}%"


def _download_sync(url: str, out_dir: str, progress_hook: Callable | None = None) -> Path:
    """Download *url* into *out_dir* using yt-dlp.

    Returns the Path of the downloaded file.
    Raises yt_dlp.DownloadError / ValueError on failure.

    Design notes
    ------------
    - No ffmpeg postprocessors: Railway and similar hosts often lack ffmpeg.
      We pick a single pre-muxed stream (best mp4), so no merging is needed.
    - File discovery: scan the output directory after download instead of
      relying on prepare_filename(), which returns ".NA" when the format is
      resolved at runtime rather than statically.
    - Duration guard: done via yt-dlp's internal `download_ranges` only for
      platforms that support it; otherwise we rely on max_filesize.
    """
    import yt_dlp  # local import — only loaded in the executor thread

    ydl_opts: dict = {
        # Single pre-muxed stream — no ffmpeg merge required.
        # Prefer mp4; fall back to whatever is available.
        "format": "best[ext=mp4]/best",
        "outtmpl": os.path.join(out_dir, "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "max_filesize": MAX_BYTES,
        # Hard cap: reject anything over 3 minutes to avoid full-length videos.
        "match_filter": lambda info, *, incomplete=False: (
            "Video too long (> 3 min)" if (info.get("duration") or 0) > 180 else None
        ),
    }
    if progress_hook:
        ydl_opts["progress_hooks"] = [progress_hook]

    # TikTok requires auth cookies for age-sensitive / region-locked videos.
    # Store the Netscape-format cookie file content in TIKTOK_COOKIES secret.
    if "tiktok.com" in url.lower():
        import logging as _logging
        _tlog = _logging.getLogger(__name__)
        cookies_content = os.environ.get("TIKTOK_COOKIES", "").strip()
        if not cookies_content:
            _tlog.warning("TikTok: TIKTOK_COOKIES env var is empty or not set")
        else:
            # Railway (and many CI/CD platforms) stores multiline env vars with
            # literal \n escape sequences instead of real newlines.  Normalise.
            if "\n" not in cookies_content and "\\n" in cookies_content:
                cookies_content = cookies_content.replace("\\n", "\n")
                _tlog.info("TikTok: normalised literal \\\\n → newlines in cookie content")

            # Lenient validation: at least one non-comment data line with
            # 7 tab-separated fields (Netscape format).  One bad line in an
            # otherwise valid file shouldn't discard all cookies.
            data_lines = [
                line for line in cookies_content.splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
            good_lines = [l for l in data_lines if len(l.split("\t")) == 7]
            bad_lines  = [l for l in data_lines if len(l.split("\t")) != 7]
            is_netscape = bool(good_lines)
            _tlog.info(
                "TikTok cookies: %d data lines (%d valid / %d malformed) — using=%s",
                len(data_lines), len(good_lines), len(bad_lines), is_netscape,
            )
            if is_netscape:
                cookie_path = os.path.join(out_dir, "_cookies.txt")
                with open(cookie_path, "w", encoding="utf-8") as fh:
                    fh.write(cookies_content)
                ydl_opts["cookiefile"] = cookie_path
                _tlog.info("TikTok: cookiefile set (%d bytes)", len(cookies_content))
            else:
                _tlog.warning(
                    "TikTok: TIKTOK_COOKIES has %d lines but none have 7 tab-separated "
                    "fields — not a Netscape cookie file, skipping auth",
                    len(data_lines),
                )

        # TikTok blocks yt-dlp without a browser-like User-Agent
        ydl_opts.setdefault("http_headers", {})
        ydl_opts["http_headers"]["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        )

    def _run_download(opts: dict) -> None:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

    try:
        _run_download(ydl_opts)
    except yt_dlp.utils.DownloadError as exc:
        # If we used cookies and the download still failed, retry once without
        # them — some errors are caused by stale/invalid cookie data, and a
        # cookieless attempt may succeed for publicly-available videos.
        if "cookiefile" in ydl_opts:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "TikTok: download failed with cookies (%s), retrying without auth", exc
            )
            opts_no_cookie = {k: v for k, v in ydl_opts.items() if k != "cookiefile"}
            _run_download(opts_no_cookie)
        else:
            raise

    # Scan the directory — more reliable than prepare_filename() which can
    # return ".NA" when the format/extension is resolved at download time.
    video_exts = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v"}
    candidates = [
        p for p in Path(out_dir).iterdir()
        if p.suffix.lower() in video_exts and p.stat().st_size > 0
    ]
    if not candidates:
        raise FileNotFoundError(
            f"yt-dlp finished but no video file found in {out_dir} "
            f"(files: {[p.name for p in Path(out_dir).iterdir()]})"
        )

    # Pick the largest file (in case of thumbnails or part files)
    path = max(candidates, key=lambda p: p.stat().st_size)

    size = path.stat().st_size
    if size > MAX_BYTES:
        path.unlink(missing_ok=True)
        raise ValueError(f"Video too large: {size // (1024 * 1024)} MB > 45 MB limit")

    return path


# ── Main async entry point ────────────────────────────────────────────────────

_SPINNERS = ["⏳", "⌛"]


async def handle_video_link(
    bot: Bot,
    chat_id: int,
    business_connection_id: str,
    url: str,
    platform: str,
) -> None:
    """Download the video at *url* and send it to the business chat.

    Sends a status message before downloading, edits it with live progress,
    and deletes it once the video is delivered (or on failure).

    This function is fire-and-forget — all errors are swallowed after logging.
    """
    import shutil

    label = _PLATFORM_LABELS.get(platform, platform)
    tmp_dir = tempfile.mkdtemp(prefix="vidbot_")
    status_msg: Message | None = None

    # ── Send initial status message ───────────────────────────────────────
    try:
        status_msg = await bot.send_message(
            chat_id=chat_id,
            business_connection_id=business_connection_id,
            text=f"⏳ Скачиваю {label}...",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not send status message to chat_id=%s: %s", chat_id, exc)

    loop = asyncio.get_running_loop()
    _last_edit: list[float] = [0.0]
    _spinner_idx: list[int] = [0]
    _EDIT_INTERVAL = 3.0  # seconds between edits (Telegram rate limit is ~1/s)

    async def _edit_status(text: str) -> None:
        """Edit the status message — silently ignore errors (e.g. not modified)."""
        if status_msg is None:
            return
        try:
            await bot.edit_message_text(
                business_connection_id=business_connection_id,
                chat_id=chat_id,
                message_id=status_msg.message_id,
                text=text,
            )
        except Exception:
            pass

    async def _delete_status() -> None:
        if status_msg is None:
            return
        import aiohttp
        url = f"https://api.telegram.org/bot{bot.token}/deleteMessage"
        payload = {
            "chat_id": status_msg.chat.id,
            "message_id": status_msg.message_id,
            "business_connection_id": business_connection_id,
        }
        try:
            async with aiohttp.ClientSession() as _s:
                async with _s.post(url, json=payload) as resp:
                    data = await resp.json()
            if data.get("ok"):
                return  # deleted successfully
            logger.warning(
                "deleteMessage failed for chat_id=%s msg_id=%s: %s",
                status_msg.chat.id, status_msg.message_id, data,
            )
        except Exception as _exc:
            logger.warning("deleteMessage HTTP error: %s", _exc)

        # Fall back: edit to a checkmark so the message looks resolved.
        try:
            await bot.edit_message_text(
                business_connection_id=business_connection_id,
                chat_id=status_msg.chat.id,
                message_id=status_msg.message_id,
                text="✅",
            )
        except Exception as _edit_exc:
            logger.warning(
                "Status message cleanup failed (delete + edit) chat_id=%s msg_id=%s: %s",
                status_msg.chat.id, status_msg.message_id, _edit_exc,
            )

    def _progress_hook(d: dict) -> None:
        """Called by yt-dlp in the executor thread on every progress tick."""
        now = time.monotonic()
        if now - _last_edit[0] < _EDIT_INTERVAL:
            return
        _last_edit[0] = now

        if d["status"] == "downloading":
            downloaded = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            speed = d.get("speed")  # bytes/s
            bar = _build_progress_bar(downloaded, total)

            speed_str = ""
            if speed:
                if speed >= 1024 * 1024:
                    speed_str = f" · {speed / 1024 / 1024:.1f} МБ/с"
                elif speed >= 1024:
                    speed_str = f" · {speed / 1024:.0f} КБ/с"

            _spinner_idx[0] = (_spinner_idx[0] + 1) % len(_SPINNERS)
            icon = _SPINNERS[_spinner_idx[0]]
            text = f"{icon} Скачиваю {label}...\n{bar}{speed_str}"

            asyncio.run_coroutine_threadsafe(_edit_status(text), loop)

    try:
        logger.info("Downloading %s video: %s", label, url)

        path: Path = await loop.run_in_executor(
            None, partial(_download_sync, url, tmp_dir, _progress_hook)
        )

        size_mb = path.stat().st_size / 1024 / 1024
        logger.info("Downloaded %s (%.1f MB), uploading to chat %s", path.name, size_mb, chat_id)

        # Switch status to "uploading"
        await _edit_status(f"📤 Загружаю в Telegram...")

        await bot.send_video(
            chat_id=chat_id,
            business_connection_id=business_connection_id,
            video=FSInputFile(path),
            caption=f"📥 {label}",
            supports_streaming=True,
        )
        logger.info("Video sent to chat_id=%s", chat_id)

        # Clean up status message — video is already in the chat
        await _delete_status()

    except Exception as exc:  # noqa: BLE001
        logger.warning("Video download/send failed for %s (%s): %s", url, label, exc)
        await _delete_status()

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
