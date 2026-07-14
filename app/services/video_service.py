"""Video/photo download service — detects Reels / TikTok / YouTube Shorts links
and sends the downloaded media back into the business chat.

Supported platforms
-------------------
- Instagram Reels  (instagram.com/reel/…)
- Instagram Posts  (instagram.com/p/…)        ← photos / carousels
- TikTok           (tiktok.com/… | vm.tiktok.com/… | vt.tiktok.com/…)
  including photo slideshows
- YouTube Shorts   (youtube.com/shorts/…)

Limits
------
- Max file size: 45 MB (Telegram bot API hard-caps at 50 MB; we leave headroom).
- Max photos in album: 10 (Telegram limit).
- yt-dlp is run in a thread-pool executor so it doesn't block the event loop.
- Temp files are always cleaned up, even on failure.

Error handling
--------------
All errors are caught and logged as warnings; the chat never receives an
error message — silence is preferable to noise on unavailable content.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from functools import partial
from pathlib import Path
from typing import Callable

import time

from aiogram import Bot
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo, Message

from app.logging_config import get_logger

logger = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_BYTES = 45 * 1024 * 1024  # 45 MB
MAX_PHOTOS = 10                # Telegram album cap

_PLATFORM_LABELS = {
    "instagram": "Instagram",
    "tiktok":    "TikTok",
    "youtube":   "YouTube Shorts",
}

# ── URL detection ─────────────────────────────────────────────────────────────

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("instagram", re.compile(
        r"https?://(?:www\.)?instagram\.com/(?:reel|p)/[A-Za-z0-9_-]+/?[^\s]*",
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

_VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def extract_video_url(text: str) -> tuple[str, str] | None:
    """Return (url, platform_key) for the first matching link, or None."""
    for platform, pattern in _PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0).rstrip(".,;!?)"), platform
    return None


# ── Download helpers (sync, run in executor) ──────────────────────────────────

def _build_base_opts(out_dir: str, max_bytes: int) -> dict:
    return {
        "outtmpl":     os.path.join(out_dir, "%(id)s_%(autonumber)s.%(ext)s"),
        "quiet":       True,
        "no_warnings": True,
        "noplaylist":  True,
        "max_filesize": max_bytes,
    }


def _apply_tiktok_opts(ydl_opts: dict, url: str, out_dir: str) -> None:
    """Mutate *ydl_opts* in-place with TikTok-specific settings."""
    import logging as _logging
    _tlog = _logging.getLogger(__name__)

    cookies_content = os.environ.get("TIKTOK_COOKIES", "").strip()
    if not cookies_content:
        _tlog.warning("TikTok: TIKTOK_COOKIES env var is empty or not set")
    else:
        if "\n" not in cookies_content and "\\n" in cookies_content:
            cookies_content = cookies_content.replace("\\n", "\n")

        data_lines = [l for l in cookies_content.splitlines()
                      if l.strip() and not l.strip().startswith("#")]
        good_lines = [l for l in data_lines if len(l.split("\t")) == 7]
        if good_lines:
            cookie_path = os.path.join(out_dir, "_cookies.txt")
            with open(cookie_path, "w", encoding="utf-8") as fh:
                fh.write(cookies_content)
            ydl_opts["cookiefile"] = cookie_path
            _tlog.info("TikTok: cookiefile set (%d bytes)", len(cookies_content))
        else:
            _tlog.warning("TikTok: cookie content has no valid Netscape lines, skipping auth")

    ydl_opts.setdefault("http_headers", {})
    ydl_opts["http_headers"]["User-Agent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )


def _scan_dir(out_dir: str) -> tuple[list[Path], list[Path]]:
    """Return (video_files, image_files) found in out_dir, non-empty files only."""
    videos, images = [], []
    for p in Path(out_dir).iterdir():
        if p.name.startswith("_") or p.stat().st_size == 0:
            continue
        ext = p.suffix.lower()
        if ext in _VIDEO_EXTS:
            videos.append(p)
        elif ext in _IMAGE_EXTS:
            images.append(p)
    return videos, images


def _download_sync(
    url: str,
    out_dir: str,
    progress_hook: Callable | None = None,
) -> tuple[list[Path], str]:
    """Download *url* into *out_dir* using yt-dlp.

    Returns (files, media_type) where:
    - media_type == "video": files is a list with one video Path
    - media_type == "photo": files is a list of image Paths (sorted, max 10)

    Raises on unrecoverable failure.
    """
    import yt_dlp

    is_tiktok = "tiktok.com" in url.lower()

    # ── Attempt 1: video ──────────────────────────────────────────────────────
    opts_video = {
        **_build_base_opts(out_dir, MAX_BYTES),
        "format": "best[ext=mp4]/best",
        "match_filter": lambda info, *, incomplete=False: (
            "Video too long (> 3 min)" if (info.get("duration") or 0) > 180 else None
        ),
    }
    if progress_hook:
        opts_video["progress_hooks"] = [progress_hook]
    if is_tiktok:
        _apply_tiktok_opts(opts_video, url, out_dir)

    def _run(opts: dict) -> None:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

    video_download_ok = False
    try:
        _run(opts_video)
        video_download_ok = True
    except yt_dlp.utils.DownloadError as exc:
        # Retry without cookies if TikTok cookie-auth failed
        if "cookiefile" in opts_video:
            logger.warning("TikTok: cookie download failed (%s), retrying without auth", exc)
            opts_no_cookie = {k: v for k, v in opts_video.items() if k != "cookiefile"}
            try:
                _run(opts_no_cookie)
                video_download_ok = True
            except yt_dlp.utils.DownloadError:
                pass  # fall through to photo attempt

    videos, images = _scan_dir(out_dir)

    if video_download_ok and videos:
        best = max(videos, key=lambda p: p.stat().st_size)
        if best.stat().st_size > MAX_BYTES:
            best.unlink(missing_ok=True)
            raise ValueError(f"Video too large: {best.stat().st_size // (1024*1024)} MB > 45 MB")
        return [best], "video"

    # ── Attempt 2: photos (carousel / slideshow) ──────────────────────────────
    # Clear whatever partial files may exist from the video attempt
    for p in Path(out_dir).iterdir():
        if not p.name.startswith("_"):
            p.unlink(missing_ok=True)

    opts_photo = {
        **_build_base_opts(out_dir, MAX_BYTES),
        # No format restriction — let yt-dlp pick the native format (images for slideshows)
    }
    if progress_hook:
        opts_photo["progress_hooks"] = [progress_hook]
    if is_tiktok:
        _apply_tiktok_opts(opts_photo, url, out_dir)

    ydl_photo_ok = False
    try:
        _run(opts_photo)
        ydl_photo_ok = True
    except yt_dlp.utils.DownloadError as exc:
        if "cookiefile" in opts_photo:
            opts_no_cookie = {k: v for k, v in opts_photo.items() if k != "cookiefile"}
            try:
                _run(opts_no_cookie)
                ydl_photo_ok = True
            except yt_dlp.utils.DownloadError:
                pass  # fall through to gallery-dl
        else:
            # e.g. "Unsupported URL" for TikTok /photo/ — fall through
            logger.debug("yt-dlp photo attempt failed (%s), will try gallery-dl", exc)

    if ydl_photo_ok:
        videos2, images2 = _scan_dir(out_dir)
        if videos2:
            return [max(videos2, key=lambda p: p.stat().st_size)], "video"
        if images2:
            return sorted(images2, key=lambda p: p.name)[:MAX_PHOTOS], "photo"

    # ── Attempt 3: gallery-dl (TikTok carousels, Instagram photos) ───────────
    # Clear partial files before gallery-dl writes its own
    for p in Path(out_dir).iterdir():
        if not p.name.startswith("_"):
            p.unlink(missing_ok=True)

    try:
        images3 = _gallery_dl_download(url, out_dir)
        if images3:
            return images3[:MAX_PHOTOS], "photo"
    except Exception as exc:
        logger.debug("gallery-dl attempt failed: %s", exc)

    raise FileNotFoundError(
        f"All download attempts failed for {url}. "
        f"Remaining files: {[p.name for p in Path(out_dir).iterdir()]}"
    )


def _gallery_dl_download(url: str, out_dir: str) -> list[Path]:
    """Download images from *url* using gallery-dl.

    Returns a sorted list of image Paths found in out_dir after download.
    Raises on failure.
    """
    import subprocess, sys

    result = subprocess.run(
        [
            sys.executable, "-m", "gallery_dl",
            "--dest", out_dir,
            "--filename", "{num:>03}_{filename}.{extension}",
            "--no-mtime",
            url,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gallery-dl exited {result.returncode}: {result.stderr.strip()[:300]}"
        )

    _, images = _scan_dir(out_dir)
    return sorted(images, key=lambda p: p.name)


# ── Progress helpers ──────────────────────────────────────────────────────────

_SPINNERS = ["⏳", "⌛"]


def _build_progress_bar(downloaded: int, total: int | None, width: int = 10) -> str:
    if not total:
        return "░" * width + " …%"
    ratio = min(downloaded / total, 1.0)
    filled = round(ratio * width)
    return "█" * filled + "░" * (width - filled) + f" {round(ratio * 100)}%"


# ── Main async entry point ────────────────────────────────────────────────────

async def handle_video_link(
    bot: Bot,
    chat_id: int,
    business_connection_id: str,
    url: str,
    platform: str,
) -> None:
    """Download *url*, showing live progress, then deliver the media."""
    import shutil

    label   = _PLATFORM_LABELS.get(platform, platform)
    tmp_dir = tempfile.mkdtemp(prefix="vidbot_")
    loop    = asyncio.get_running_loop()

    status_msg: Message | None = None

    try:
        status_msg = await bot.send_message(
            chat_id=chat_id,
            business_connection_id=business_connection_id,
            text=f"⏳ Скачиваю {label}...",
        )
    except Exception as exc:
        logger.warning("Could not send status message to chat_id=%s: %s", chat_id, exc)

    _last_edit:   list[float] = [0.0]
    _spinner_idx: list[int]   = [0]
    _EDIT_INTERVAL = 3.0

    async def _edit_status(text: str) -> None:
        if status_msg is None:
            return
        try:
            await bot.edit_message_text(
                business_connection_id=business_connection_id,
                chat_id=status_msg.chat.id,
                message_id=status_msg.message_id,
                text=text,
            )
        except Exception:
            pass

    async def _delete_status() -> None:
        if status_msg is None:
            return
        try:
            await bot.delete_message(
                chat_id=status_msg.chat.id,
                message_id=status_msg.message_id,
                business_connection_id=business_connection_id,
            )
        except Exception:
            pass

    def _progress_hook(d: dict) -> None:
        now = time.monotonic()
        if now - _last_edit[0] < _EDIT_INTERVAL:
            return
        _last_edit[0] = now
        if d["status"] != "downloading":
            return
        downloaded = d.get("downloaded_bytes") or 0
        total      = d.get("total_bytes") or d.get("total_bytes_estimate")
        speed      = d.get("speed")
        bar        = _build_progress_bar(downloaded, total)
        speed_str  = ""
        if speed:
            speed_str = (
                f" · {speed / 1024 / 1024:.1f} МБ/с" if speed >= 1_048_576
                else f" · {speed / 1024:.0f} КБ/с"
            )
        _spinner_idx[0] = (_spinner_idx[0] + 1) % len(_SPINNERS)
        icon = _SPINNERS[_spinner_idx[0]]
        asyncio.run_coroutine_threadsafe(
            _edit_status(f"{icon} Скачиваю {label}...\n{bar}{speed_str}"), loop
        )

    try:
        logger.info("Downloading %s media: %s", label, url)

        paths, media_type = await loop.run_in_executor(
            None, partial(_download_sync, url, tmp_dir, _progress_hook)
        )

        logger.info(
            "Downloaded %s file(s) as %s from %s, uploading to chat %s",
            len(paths), media_type, label, chat_id,
        )
        await _edit_status("📤 Загружаю в Telegram...")

        if media_type == "photo":
            await _delete_status()
            if len(paths) == 1:
                await bot.send_photo(
                    chat_id=chat_id,
                    business_connection_id=business_connection_id,
                    photo=FSInputFile(paths[0]),
                )
            else:
                media_group = [InputMediaPhoto(media=FSInputFile(p)) for p in paths]
                await bot.send_media_group(
                    chat_id=chat_id,
                    business_connection_id=business_connection_id,
                    media=media_group,
                )

        else:  # video
            path = paths[0]
            if status_msg is not None:
                await bot.edit_message_media(
                    business_connection_id=business_connection_id,
                    chat_id=status_msg.chat.id,
                    message_id=status_msg.message_id,
                    media=InputMediaVideo(
                        media=FSInputFile(path),
                        supports_streaming=True,
                    ),
                )
            else:
                await bot.send_video(
                    chat_id=chat_id,
                    business_connection_id=business_connection_id,
                    video=FSInputFile(path),
                    supports_streaming=True,
                )

        logger.info("Media delivered to chat_id=%s (%s)", chat_id, media_type)

    except Exception as exc:
        logger.warning("Media download/send failed for %s (%s): %s", url, label, exc)
        await _delete_status()

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
