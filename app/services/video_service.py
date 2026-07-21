"""Video/photo download service — detects Reels / TikTok / YouTube links
and sends the downloaded media back into the business chat.

Supported platforms
-------------------
- Instagram Reels  (instagram.com/reel/…)
- Instagram Posts  (instagram.com/p/…)        ← photos / carousels
- TikTok           (tiktok.com/… | vm.tiktok.com/… | vt.tiktok.com/…)
  including photo slideshows
- YouTube          (youtube.com/shorts/… | youtube.com/watch?v=… | youtu.be/…)

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


def _video_dimensions(path: str) -> tuple[int, int]:
    """Return (width, height) of a video file using ffprobe.

    Falls back to (0, 0) on any error so callers can skip the params
    entirely rather than sending wrong values to Telegram.
    """
    import json
    import subprocess

    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                "-select_streams", "v:0",
                path,
            ],
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        streams = json.loads(out).get("streams", [])
        if not streams:
            return 0, 0
        s = streams[0]
        w = int(s.get("width", 0))
        h = int(s.get("height", 0))
        # Some Instagram / TikTok files have a rotation tag that swaps axes.
        rotation = int(s.get("tags", {}).get("rotate", 0))
        if rotation in (90, 270):
            w, h = h, w
        return w, h
    except Exception as exc:
        logger.debug("ffprobe failed for %s: %s", path, exc)
        return 0, 0

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_BYTES = 45 * 1024 * 1024  # 45 MB
MAX_PHOTOS = 10                # Telegram album cap

_PLATFORM_LABELS = {
    "instagram": "Instagram",
    "tiktok":    "TikTok",
    "youtube":   "YouTube",
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
        r"https?://(?:www\.)?youtube\.com/(?:shorts/[A-Za-z0-9_-]+|watch\?[^\s]*?v=[A-Za-z0-9_-]+)[^\s]*"
        r"|https?://youtu\.be/[A-Za-z0-9_-]+[^\s]*",
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

def _find_ffmpeg() -> str:
    """Return the full path to an ffmpeg binary.

    Tries (in order):
    1. System ffmpeg via shutil.which
    2. Bundled static binary from imageio-ffmpeg (works on Railway / any Linux)
    Returns empty string if neither is available.
    """
    import shutil as _shutil
    sys_ff = _shutil.which("ffmpeg")
    if sys_ff:
        return sys_ff
    try:
        import imageio_ffmpeg as _iio
        return _iio.get_ffmpeg_exe()
    except Exception:
        return ""


def _build_base_opts(out_dir: str, max_bytes: int) -> dict:
    ffmpeg = _find_ffmpeg()
    ffmpeg_dir = os.path.dirname(ffmpeg) if ffmpeg else ""
    logger.info("Video opts: ffmpeg=%s", ffmpeg or "NOT FOUND")
    opts: dict = {
        "outtmpl":             os.path.join(out_dir, "%(id)s_%(autonumber)s.%(ext)s"),
        "quiet":               True,
        "no_warnings":         True,
        "noplaylist":          True,
        "max_filesize":        max_bytes,
        "socket_timeout":      30,
        "retries":             3,
        "fragment_retries":    3,
    }
    if ffmpeg_dir:
        opts["ffmpeg_location"] = ffmpeg_dir
    return opts


from app.services._cookie_utils import json_cookies_to_netscape as _json_cookies_to_netscape


def _apply_tiktok_opts(ydl_opts: dict, url: str, out_dir: str) -> None:
    """Mutate *ydl_opts* in-place with TikTok-specific settings.

    Supported TIKTOK_COOKIES formats (tried in order):
      1. Netscape cookie file (7 tab-separated columns per row)
      2. Netscape file stored with ``\\n`` escape sequences instead of real newlines
      3. JSON array exported by browser extensions (Cookie Editor, EditThisCookie, …)
      4. Raw HTTP Cookie header string  (name=val; name2=val2)
         → injected via http_headers["Cookie"]
    """
    import logging as _logging
    _tlog = _logging.getLogger(__name__)

    raw = os.environ.get("TIKTOK_COOKIES", "").strip()
    if not raw:
        _tlog.warning("TikTok: TIKTOK_COOKIES is empty — downloads may fail for private/age-gated content")
        _apply_tiktok_ua(ydl_opts)
        return

    # ── Normalise escaped newlines ─────────────────────────────────────────────
    if "\n" not in raw and "\\n" in raw:
        raw = raw.replace("\\n", "\n")

    # ── 1. Netscape cookie file ────────────────────────────────────────────────
    data_lines = [l for l in raw.splitlines() if l.strip() and not l.strip().startswith("#")]
    netscape_lines = [l for l in data_lines if len(l.split("\t")) == 7]
    if netscape_lines:
        cookie_path = os.path.join(out_dir, "_cookies.txt")
        with open(cookie_path, "w", encoding="utf-8") as fh:
            fh.write(raw)
        ydl_opts["cookiefile"] = cookie_path
        _tlog.info("TikTok: Netscape cookiefile set (%d valid rows)", len(netscape_lines))
        _apply_tiktok_ua(ydl_opts)
        return

    # ── 2. JSON array (browser extension export) ───────────────────────────────
    if raw.lstrip().startswith("["):
        netscape_str = _json_cookies_to_netscape(raw)
        if netscape_str:
            cookie_path = os.path.join(out_dir, "_cookies.txt")
            with open(cookie_path, "w", encoding="utf-8") as fh:
                fh.write(netscape_str)
            ydl_opts["cookiefile"] = cookie_path
            _tlog.info("TikTok: JSON→Netscape cookiefile set (%d bytes)", len(netscape_str))
            _apply_tiktok_ua(ydl_opts)
            return
        _tlog.warning("TikTok: TIKTOK_COOKIES looks like JSON but failed to parse")

    # ── 3. Raw HTTP Cookie header  (name=val; name2=val2) ──────────────────────
    # Heuristic: no newlines, contains "=", no tabs, does NOT start with "#"
    # (to avoid mis-detecting a collapsed Netscape file).
    if "=" in raw and "\n" not in raw and "\t" not in raw and not raw.startswith("#"):
        ydl_opts.setdefault("http_headers", {})
        ydl_opts["http_headers"]["Cookie"] = raw
        _tlog.info("TikTok: raw Cookie header injected (%d chars)", len(raw))
        _apply_tiktok_ua(ydl_opts)
        return

    _tlog.warning(
        "TikTok: TIKTOK_COOKIES present (%d bytes) but no recognised format "
        "(Netscape / JSON / Cookie header). Downloads will proceed without auth.",
        len(raw),
    )
    _apply_tiktok_ua(ydl_opts)


def _apply_instagram_opts(ydl_opts: dict, url: str, out_dir: str) -> None:
    """Inject Instagram cookies from INSTAGRAM_COOKIES env var (same formats as TikTok)."""
    raw = os.environ.get("INSTAGRAM_COOKIES", "").strip()
    if not raw:
        logger.debug("Instagram: INSTAGRAM_COOKIES not set — carousels may fail without auth")
        return

    if "\n" not in raw and "\\n" in raw:
        raw = raw.replace("\\n", "\n")

    data_lines = [l for l in raw.splitlines() if l.strip() and not l.strip().startswith("#")]
    netscape_lines = [l for l in data_lines if len(l.split("\t")) == 7]
    if netscape_lines:
        cookie_path = os.path.join(out_dir, "_ig_cookies.txt")
        with open(cookie_path, "w", encoding="utf-8") as fh:
            fh.write(raw)
        ydl_opts["cookiefile"] = cookie_path
        logger.info("Instagram: Netscape cookiefile set (%d rows)", len(netscape_lines))
        return

    if raw.lstrip().startswith("["):
        netscape_str = _json_cookies_to_netscape(raw)
        if netscape_str:
            cookie_path = os.path.join(out_dir, "_ig_cookies.txt")
            with open(cookie_path, "w", encoding="utf-8") as fh:
                fh.write(netscape_str)
            ydl_opts["cookiefile"] = cookie_path
            logger.info("Instagram: JSON→Netscape cookiefile set (%d bytes)", len(netscape_str))
            return
        logger.warning("Instagram: INSTAGRAM_COOKIES looks like JSON but failed to parse")

    if "=" in raw and "\n" not in raw and "\t" not in raw:
        ydl_opts.setdefault("http_headers", {})
        ydl_opts["http_headers"]["Cookie"] = raw
        logger.info("Instagram: raw Cookie header injected (%d chars)", len(raw))


def _apply_youtube_opts(ydl_opts: dict, out_dir: str) -> None:
    """Apply YouTube-specific yt-dlp options to bypass bot-detection.

    Strategy:
    1. Always set Node.js for n-challenge + tv/mweb/web player clients.
    2. If YOUTUBE_COOKIES is set, also inject cookies (cookiefile preferred).
       Cookies + player clients together give the best success rate.
    """
    # Always use Node.js to solve YouTube's n-challenge (requires Node 22+).
    ydl_opts["js_runtimes"] = {"node": {}}

    raw = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if not raw:
        # Without cookies use unauthenticated TV/mweb clients to bypass bot-check.
        logger.info("YouTube: no YOUTUBE_COOKIES set, using tv/mweb player_client bypass")
        ydl_opts["extractor_args"] = {
            "youtube": {"player_client": ["tv", "mweb", "web"]}
        }
        return

    # Cookies present: use the standard web client so DASH streams are available.
    # tv/mweb clients don't expose DASH format lists → "format not available" errors.
    ydl_opts["extractor_args"] = {
        "youtube": {"player_client": ["web"]}
    }

    # ── Normalise newlines ────────────────────────────────────────────────────
    if "\n" not in raw and "\\n" in raw:
        raw = raw.replace("\\n", "\n")

    # Replit secrets sometimes collapse real newlines to spaces; reconstruct.
    if "\n" not in raw and "\t" in raw:
        import re as _re
        raw = _re.sub(r" +(?=\.?[A-Za-z0-9_-]+\.[A-Za-z]+\t)", "\n", raw)
        logger.info("YouTube: reconstructed %d lines from space-collapsed cookies", len(raw.splitlines()))

    # ── Detect format and write cookiefile ───────────────────────────────────
    data_lines = [l for l in raw.splitlines() if l.strip() and not l.strip().startswith("#")]
    netscape_lines = [l for l in data_lines if len(l.split("\t")) == 7]

    cookie_path = os.path.join(out_dir, "_yt_cookies.txt")

    if netscape_lines:
        with open(cookie_path, "w", encoding="utf-8") as fh:
            fh.write(raw)
        ydl_opts["cookiefile"] = cookie_path
        logger.info("YouTube: Netscape cookiefile set (%d rows)", len(netscape_lines))
        return

    if raw.lstrip().startswith("["):
        netscape_str = _json_cookies_to_netscape(raw)
        if netscape_str:
            with open(cookie_path, "w", encoding="utf-8") as fh:
                fh.write(netscape_str)
            ydl_opts["cookiefile"] = cookie_path
            logger.info("YouTube: JSON→Netscape cookiefile set (%d bytes)", len(netscape_str))
            return
        logger.warning("YouTube: YOUTUBE_COOKIES looks like JSON but parse failed")

    # Last resort: raw Cookie header (weaker than cookiefile but better than nothing)
    if "=" in raw:
        # Use only the first line in case reconstruction produced multiple
        first_line = raw.splitlines()[0].strip()
        ydl_opts.setdefault("http_headers", {})
        ydl_opts["http_headers"]["Cookie"] = first_line
        logger.warning("YouTube: falling back to raw Cookie header (%d chars) — "
                       "may still fail bot check; provide Netscape cookie file", len(first_line))
    else:
        logger.warning("YouTube: YOUTUBE_COOKIES set but format unrecognised (len=%d, "
                       "preview=%.80r) — proceeding without cookies", len(raw), raw)


def _apply_tiktok_ua(ydl_opts: dict) -> None:
    """Inject a realistic browser User-Agent (always applied for TikTok)."""
    ydl_opts.setdefault("http_headers", {})
    ydl_opts["http_headers"]["User-Agent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )


def _scan_dir(out_dir: str) -> tuple[list[Path], list[Path]]:
    """Return (video_files, image_files) found in out_dir recursively, non-empty files only."""
    videos, images = [], []
    for p in Path(out_dir).rglob("*"):
        if not p.is_file():
            continue
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

    is_tiktok    = "tiktok.com" in url.lower()
    is_instagram = "instagram.com" in url.lower()
    is_youtube   = "youtube.com" in url.lower() or "youtu.be" in url.lower()

    # ── Attempt 1: video ──────────────────────────────────────────────────────
    MAX_DURATION = 1200  # 20 minutes — anything longer won't fit in 45 MB anyway
    opts_video = {
        **_build_base_opts(out_dir, MAX_BYTES),
        # Prefer merged mp4 streams; merge_output_format forces ffmpeg remux to
        # mp4 so Telegram always gets a proper video, not a webm document.
        # With auth: YouTube serves DASH (bestvideo+bestaudio); ffmpeg merges to mp4.
        # No ext= filters — they block DASH streams on some player clients.
        "format": (
            "bestvideo[height<=720]+bestaudio"
            "/best[height<=720]"
            "/bestvideo+bestaudio"
            "/best"
        ),
        "merge_output_format": "mp4",
        "match_filter": lambda info, *, incomplete=False: (
            f"Video too long (> {MAX_DURATION // 60} min)"
            if (info.get("duration") or 0) > MAX_DURATION else None
        ),
    }
    if progress_hook:
        opts_video["progress_hooks"] = [progress_hook]
    if is_tiktok:
        _apply_tiktok_opts(opts_video, url, out_dir)
    if is_instagram:
        _apply_instagram_opts(opts_video, url, out_dir)
    if is_youtube:
        _apply_youtube_opts(opts_video, out_dir)

    def _run(opts: dict) -> None:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

    _AUTH_ERRORS = ("sign in", "login", "not available", "private video", "members only")

    video_download_ok = False
    try:
        _run(opts_video)
        video_download_ok = True
    except yt_dlp.utils.DownloadError as exc:
        exc_lower = str(exc).lower()
        # Only retry without cookies for genuine auth failures, NOT for
        # "Requested format is not available" (retrying without auth makes it worse).
        is_auth_error = any(k in exc_lower for k in ("sign in", "login",
                                                       "private video", "members only"))
        if "cookiefile" in opts_video and is_auth_error:
            logger.warning("Video: auth error with cookies (%s), retrying without auth", exc)
            opts_no_cookie = {k: v for k, v in opts_video.items() if k != "cookiefile"}
            try:
                _run(opts_no_cookie)
                video_download_ok = True
            except yt_dlp.utils.DownloadError:
                pass  # fall through to photo attempt
        elif not video_download_ok:
            logger.warning("Video: download failed (%s), falling through", exc)

    videos, images = _scan_dir(out_dir)

    if video_download_ok and videos:
        best = max(videos, key=lambda p: p.stat().st_size)
        # Convert any non-mp4 to mp4 so Telegram sends it as a video, not a document
        if best.suffix.lower() != ".mp4":
            _ff = _find_ffmpeg()
            if _ff:
                mp4_path = best.with_suffix(".mp4")
                import subprocess as _sp
                result = _sp.run(
                    [_ff, "-y", "-i", str(best),
                     "-c:v", "copy", "-c:a", "aac", str(mp4_path)],
                    capture_output=True,
                )
                if result.returncode == 0 and mp4_path.exists():
                    best.unlink(missing_ok=True)
                    best = mp4_path
                    logger.info("Video: converted %s → mp4", best.name)
                else:
                    logger.warning("Video: ffmpeg conversion failed (%s), sending original",
                                   result.stderr[-200:] if result.stderr else "no stderr")
            else:
                logger.warning("Video: ffmpeg not available, sending original %s", best.suffix)
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
        # Carousels are multi-entry "playlists" in yt-dlp — must allow all items
        "noplaylist": False,
    }
    if progress_hook:
        opts_photo["progress_hooks"] = [progress_hook]
    if is_tiktok:
        _apply_tiktok_opts(opts_photo, url, out_dir)
    if is_instagram:
        _apply_instagram_opts(opts_photo, url, out_dir)
    if is_youtube:
        # Photo fallback uses bypass clients (tv/mweb/web) instead of web-only.
        # web-only client (used in attempt 1) requires DASH and fails for many videos;
        # bypass clients expose non-DASH formats and succeed more often.
        # merge_output_format="mp4" (in base opts) ensures mp4 output.
        opts_photo["js_runtimes"] = {"node": {}}
        opts_photo["extractor_args"] = {
            "youtube": {"player_client": ["tv", "mweb", "web"]}
        }
        # Re-use cookie file if the video attempt already wrote it.
        _yt_cookies = os.path.join(out_dir, "_yt_cookies.txt")
        if os.path.exists(_yt_cookies):
            opts_photo["cookiefile"] = _yt_cookies

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
            logger.debug("yt-dlp photo attempt failed (%s), will try gallery-dl", exc)

    if ydl_photo_ok:
        videos2, images2 = _scan_dir(out_dir)
        if videos2:
            best2 = max(videos2, key=lambda p: p.stat().st_size)
            if best2.stat().st_size > MAX_BYTES:
                best2.unlink(missing_ok=True)
                raise ValueError(
                    f"Video too large: {best2.stat().st_size // (1024 * 1024)} MB > 45 MB"
                )
            return [best2], "video"
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

    cmd = [
        sys.executable, "-m", "gallery_dl",
        "--dest", out_dir,
        # Force flat output — no platform subdirectories
        "--directory", "",
        "--filename", "{num:>03}_{filename}.{extension}",
        "--no-mtime",
    ]

    # Inject Instagram cookies so gallery-dl can fetch login-gated carousels
    if "instagram.com" in url.lower():
        raw = os.environ.get("INSTAGRAM_COOKIES", "").strip()
        if raw:
            if "\n" not in raw and "\\n" in raw:
                raw = raw.replace("\\n", "\n")
            # gallery-dl expects a Netscape cookie file via --cookies
            data_lines = [l for l in raw.splitlines() if l.strip() and not l.strip().startswith("#")]
            netscape_lines = [l for l in data_lines if len(l.split("\t")) == 7]
            if netscape_lines:
                cookie_path = os.path.join(out_dir, "_ig_cookies.txt")
                with open(cookie_path, "w", encoding="utf-8") as fh:
                    fh.write(raw)
                cmd += ["--cookies", cookie_path]
            elif raw.lstrip().startswith("["):
                netscape_str = _json_cookies_to_netscape(raw)
                if netscape_str:
                    cookie_path = os.path.join(out_dir, "_ig_cookies.txt")
                    with open(cookie_path, "w", encoding="utf-8") as fh:
                        fh.write(netscape_str)
                    cmd += ["--cookies", cookie_path]

    cmd.append(url)

    result = subprocess.run(
        cmd,
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
    link_message_id: int | None = None,
) -> None:
    """Download *url*, showing live progress, then deliver the media.

    If *link_message_id* is provided it is deleted from the chat after
    the media is successfully sent, so the original link disappears.
    """
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

        paths, media_type = await asyncio.wait_for(
            loop.run_in_executor(
                None, partial(_download_sync, url, tmp_dir, _progress_hook)
            ),
            timeout=300,  # 5-minute hard cap; yt-dlp can hang on merge
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
            _w, _h = _video_dimensions(path)
            _dim: dict = {}
            if _w and _h:
                _dim = {"width": _w, "height": _h}
            if status_msg is not None:
                await bot.edit_message_media(
                    business_connection_id=business_connection_id,
                    chat_id=status_msg.chat.id,
                    message_id=status_msg.message_id,
                    media=InputMediaVideo(
                        media=FSInputFile(path),
                        supports_streaming=True,
                        **_dim,
                    ),
                )
            else:
                await bot.send_video(
                    chat_id=chat_id,
                    business_connection_id=business_connection_id,
                    video=FSInputFile(path),
                    supports_streaming=True,
                    **_dim,
                )

        logger.info("Media delivered to chat_id=%s (%s)", chat_id, media_type)

        # Delete the original link message now that the video is in the chat.
        # bot.delete_message() does not forward business_connection_id, so we
        # call bot(DeleteMessage(...)) directly — the model allows extra fields.
        if link_message_id is not None:
            try:
                from aiogram.methods import DeleteMessage as _DeleteMessage
                await bot(_DeleteMessage(
                    chat_id=chat_id,
                    message_id=link_message_id,
                    business_connection_id=business_connection_id,
                ))
            except Exception as _del_exc:
                logger.warning(
                    "Could not delete link message %s in chat %s: %s",
                    link_message_id, chat_id, _del_exc,
                )

    except Exception as exc:
        logger.warning("Media download/send failed for %s (%s): %s", url, label, exc)
        await _delete_status()

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
