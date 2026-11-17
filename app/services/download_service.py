"""One bounded public-video job, with forced checked HTTPS proxy and local ffmpeg.

Anonymous by default; optional isolated owner cookie files for authenticated
!video commands only. No plugins, DRM bypass or external JS runtimes.
Provider availability is deliberately not promised: extractors change often.
Use the async download context until Telegram upload is finished; its temporary
files are removed on success, failure or cancellation.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, nullcontext, suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from app.logging_config import get_logger
from app.services.download_proxy import DownloadProxy

logger = get_logger(__name__)

MAX_BYTES = 45 * 1024 * 1024
MAX_WORKSPACE_BYTES = 96 * 1024 * 1024
MAX_DECODE_PIXELS = 1920 * 1080
SUPPORTED_HOSTS = {
    "youtube.com": "youtube", "www.youtube.com": "youtube",
    "m.youtube.com": "youtube", "youtu.be": "youtube",
    "tiktok.com": "tiktok", "www.tiktok.com": "tiktok",
    "m.tiktok.com": "tiktok", "vm.tiktok.com": "tiktok", "vt.tiktok.com": "tiktok",
    "instagram.com": "instagram", "www.instagram.com": "instagram",
}


class DownloadError(Exception):
    """Safe error code; never includes extractor stderr, URL or credentials."""


class RunStatus(int):
    """Integer-compatible exit status carrying a bounded safe diagnostic only."""

    code: str

    def __new__(cls, value: int, code: str):
        instance = super().__new__(cls, value)
        instance.code = code
        return instance


def classify_stderr(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace").lower()
    groups = (
        ("tls_verification_failed", ("certificate verify failed", "certificate_verify_failed", "unable to get local issuer")),
        ("cli_invalid", ("unrecognized arguments", "no such option", "usage: yt-dlp", "invalid format specification")),
        ("downloader_missing", ("no module named yt_dlp", "no module named gallery_dl", "ffmpeg not found")),
        ("rate_limited", ("http error 429", "too many requests", "rate limit")),
        ("authentication_required", ("sign in", "login required", "authentication required", "http error 401", "cookies are needed")),
        ("format_unavailable", ("requested format is not available", "no video formats found", "no suitable formats")),
        ("source_forbidden", ("http error 403", "403 forbidden", "access denied")),
        ("transport_failed", ("connection refused", "connection reset", "timed out", "proxyerror", "unable to download", "network is unreachable")),
    )
    return next((code for code, patterns in groups if any(pattern in text for pattern in patterns)), "unknown")


def validate_video_url(url: str, *, audio: bool = False) -> str:
    if len(url) > 2048 or any(ord(c) < 33 or c == "\\" for c in url):
        raise DownloadError("invalid_url")
    hosts = dict(SUPPORTED_HOSTS)
    if audio:
        hosts.update({"music.youtube.com": "youtube", "soundcloud.com": "soundcloud", "www.soundcloud.com": "soundcloud"})
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https" or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.fragment
            or parsed.hostname not in hosts
        ):
            raise DownloadError("unsupported_url")
        platform = hosts[parsed.hostname]
        if not parsed.path.strip("/"):
            raise DownloadError("video_url_required")
        if platform == "instagram" and not parsed.path.startswith(("/reel/", "/reels/", "/p/")):
            raise DownloadError("public_video_url_required")
        return platform
    except (ValueError, UnicodeError) as error:
        raise DownloadError("invalid_url") from error


@dataclass(frozen=True)
class DownloadedVideo:
    path: Path
    platform: str
    size_bytes: int


@dataclass(frozen=True)
class DownloadedMedia:
    paths: tuple[Path, ...]
    kind: str
    title: str = ""
    uploader: str = ""
    duration: int = 0


class DownloadService:
    def __init__(self, *, queue_limit: int = 4, queue_timeout: float = 15,
                 job_timeout: float = 120, ffmpeg: str | None = None) -> None:
        self._slot = asyncio.Semaphore(1)
        self._waiting = 0
        self.queue_limit = queue_limit
        self.queue_timeout = queue_timeout
        self.job_timeout = job_timeout
        self.ffmpeg = ffmpeg or shutil.which("ffmpeg")

    @staticmethod
    def _check_workspace(folder: Path) -> None:
        total = 0
        for count, path in enumerate(folder.rglob("*"), 1):
            if count > 256 or path.is_symlink():
                raise DownloadError("unsafe_output")
            if path.is_file():
                size = path.stat().st_size
                total += size
                if size > MAX_BYTES or total > MAX_WORKSPACE_BYTES:
                    raise DownloadError("video_too_large")

    @staticmethod
    def _child_env(folder: Path, proxy: str) -> dict[str, str]:
        # Do not inherit Bot Token, database URL, provider keys or cookie env.
        env = {key: os.environ[key] for key in (
            "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "LANG"
        ) if key in os.environ}
        env.update({
            "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": str(folder), "USERPROFILE": str(folder),
            "APPDATA": str(folder), "XDG_CONFIG_HOME": str(folder),
            "TEMP": str(folder), "TMP": str(folder),
            "HTTP_PROXY": proxy, "HTTPS_PROXY": proxy, "ALL_PROXY": proxy,
            "http_proxy": proxy, "https_proxy": proxy, "all_proxy": proxy,
            "NO_PROXY": "", "no_proxy": "",
        })
        return env

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            if sys.platform != "win32":
                # A descendant can retain stderr after its parent exits.
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            # Windows taskkill cannot identify orphaned descendants after the
            # parent PID disappears. No untrusted plugins/JS are enabled; this
            # is bounded cleanup, not a Windows process/network sandbox.
            return
        if sys.platform == "win32":
            # Kill downloader and any ffmpeg descendants, not only the parent.
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(process.pid), "/T", "/F",
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            with suppress(TimeoutError):
                await asyncio.wait_for(killer.wait(), 5)
        else:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        with suppress(ProcessLookupError):
            process.kill()
        await asyncio.wait_for(process.wait(), 5)

    async def _run(self, command: list[str], folder: Path,
                   env: dict[str, str], deadline: float, *, capture: Path | None = None) -> int:
        if time.monotonic() >= deadline:
            raise DownloadError("download_timeout")
        kwargs = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                  if os.name == "nt" else {"start_new_session": True})
        with capture.open("wb") if capture else nullcontext(subprocess.DEVNULL) as output:
            try:
                process = await asyncio.create_subprocess_exec(
                    *command, cwd=folder, env=env, stdin=subprocess.DEVNULL,
                    stdout=output, stderr=subprocess.PIPE, **kwargs,
                )
            except FileNotFoundError:
                raise DownloadError("downloader_missing") from None
            async def drain_stderr() -> bytes:
                retained = bytearray()
                assert process.stderr is not None
                while chunk := await process.stderr.read(8192):
                    if len(retained) < 64 * 1024:
                        retained.extend(chunk[:64 * 1024 - len(retained)])
                return bytes(retained)
            stderr_task = asyncio.create_task(drain_stderr())
            waiter = asyncio.create_task(process.wait())
            try:
                while not waiter.done():
                    self._check_workspace(folder)
                    if capture and capture.exists() and capture.stat().st_size > 2 * 1024 * 1024:
                        raise DownloadError("metadata_too_large")
                    if time.monotonic() >= deadline:
                        raise DownloadError("download_timeout")
                    await asyncio.wait({waiter}, timeout=0.1)
                self._check_workspace(folder)
                status = waiter.result()
                try:
                    raw = await asyncio.wait_for(stderr_task, max(0.001, deadline - time.monotonic()))
                except TimeoutError:
                    raise DownloadError("download_timeout") from None
                code = classify_stderr(raw) if status else "ok"
                del raw
                if status:
                    tool = "yt_dlp" if "yt_dlp" in command else "gallery_dl" if "gallery_dl" in command else "local_media"
                    stage = "extract" if tool in {"yt_dlp", "gallery_dl"} else "convert"
                    logger.warning("Download subprocess failed tool=%s stage=%s code=%s", tool, stage, code)
                return RunStatus(status, code)
            finally:
                try:
                    if process.returncode is None or not waiter.done() or not stderr_task.done():
                        with suppress(Exception):
                            await asyncio.wait_for(self._terminate(process), 6)
                finally:
                    # A parent's wait() can remain pending until inherited
                    # pipes close. Never let that bypass the job deadline.
                    for task in (waiter, stderr_task):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(waiter, stderr_task, return_exceptions=True)

    async def _fetch(self, url: str, platform: str, folder: Path,
                     env: dict[str, str], proxy: str, deadline: float, *, cookie_file: Path | None = None) -> Path | tuple[Path, Path]:
        # Prefer a combined stream; fall back to independently fetched native
        # video/audio tracks and local-only merge, always through checked proxy.
        command = [
            sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-plugin-dirs",
            "--no-js-runtimes", "--no-playlist", "--no-cache-dir", "--proxy", proxy,
            "--socket-timeout", "8", "--retries", "1", "--fragment-retries", "1",
            "--concurrent-fragments", "1", "--downloader", "dash,m3u8:native",
            # Native HLS may delegate unsupported manifests to FFmpegFD.
            # Make FFmpeg unavailable INSIDE yt-dlp; only _convert may invoke
            # our separately supplied binary with local-only protocol policy.
            "--ffmpeg-location", str(folder / "disabled-network-ffmpeg"),
            "--fixup", "never",
            "--max-filesize", str(MAX_BYTES), "--no-progress", "--no-warnings",
            "--format", ("best[height<=720][protocol=https]/best[height<=720][protocol=m3u8_native]/"
                         "best[width<=720][protocol=https]/best[width<=720][protocol=m3u8_native]/"
                         "best[width<=?1920][height<=?1920][protocol=https]/"
                         "best[width<=?1920][height<=?1920][protocol=m3u8_native]"),
            "--output", str(folder / "source.%(ext)s"), *self._platform_args(platform), "--", url,
        ]
        if cookie_file is not None:
            command[-2:-2] = ["--cookies", str(cookie_file)]
        status = await self._run(command, folder, env, deadline)
        if status != 0:
            self._clear_source_attempt(folder)
            tracks, split_status = await self._fetch_split(url, platform, folder, env, proxy, deadline, cookie_file)
            if tracks is not None:
                return tracks
            if getattr(split_status, "code", "unknown") != "unknown":
                status = split_status
        if status != 0 and platform == "instagram":
            self._clear_source_attempt(folder)
            # Same checked transport; optional owner jar stays scoped to this job.
            gallery_command = [
                sys.executable, "-m", "gallery_dl", "--config-ignore", "--proxy", proxy,
                "-o", "downloader.ytdl.enabled=false",
                "--http-timeout", "8", "--retries", "1", "--range", "1",
                "--filter", "extension in ('mp4', 'webm', 'mov')",
                "--directory", str(folder), "--filename", "source.{extension}",
                "--", url,
            ]
            if cookie_file is not None:
                gallery_command[-2:-2] = ["--cookies", str(cookie_file)]
            status = await self._run(gallery_command, folder, env, deadline)
        if status != 0:
            raise DownloadError(getattr(status, "code", "video_unavailable"))
        self._check_workspace(folder)
        videos = [path for path in folder.rglob("*") if path.is_file()
                  and path.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv", ".ts"}
                  and path.stat().st_size > 0]
        if len(videos) != 1:
            raise DownloadError("video_unavailable")
        return videos[0]

    @staticmethod
    def _clear_source_attempt(folder: Path) -> None:
        # Fixed extractor output prefix only; never the owner jar or other job.
        for path in folder.glob("source.*"):
            if path.is_symlink() or not path.is_file():
                raise DownloadError("unsafe_output")
            path.unlink()

    async def _fetch_split(self, url: str, platform: str, folder: Path, env: dict[str, str],
                           proxy: str, deadline: float, cookie_file: Path | None):
        """Fetch tracks independently via native checked transport; merge locally.

        The extractor never receives a '+' format or a usable ffmpeg binary.
        40MiB video + 10MiB audio + 45MiB output fit the 96MiB workspace cap.
        Clients are bounded compatibility choices, not an access/DRM bypass.
        """
        clients = [None, "android", "web"] if platform == "youtube" else [None]
        last_status = 1
        for number, client in enumerate(clients):
            attempt = folder / f"split-{number}"
            attempt.mkdir()
            tracks = []
            for kind, format_name, limit in (("video", "bestvideo[height<=720]", 40 * 1024 * 1024),
                                             ("audio", "bestaudio", 10 * 1024 * 1024)):
                selectors = [format_name]
                if kind == "video":
                    selectors.append("bestvideo[width<=720]")
                    selectors.append("bestvideo[width<=?1920][height<=?1920]")
                selector = "/".join(name + f"[protocol={protocol}]" for name in selectors
                                    for protocol in ("https", "http_dash_segments", "m3u8_native"))
                command = self._extractor_args(folder, proxy) + self._platform_args(platform) + [
                    "--no-playlist", "--max-filesize", str(limit), "--format", selector,
                    "--output", str(attempt / f"{kind}.%(ext)s"),
                ]
                if client:
                    command += ["--extractor-args", "youtube:player_client=" + client]
                if cookie_file is not None:
                    command += ["--cookies", str(cookie_file)]
                command += ["--", url]
                last_status = await self._run(command, folder, env, deadline)
                self._check_workspace(folder)
                files = [path for path in attempt.glob(kind + ".*") if path.is_file()
                         and path.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov", ".m4v", ".m4a", ".ogg", ".opus", ".ts"}]
                if last_status != 0:
                    break
                if len(files) != 1 or files[0].stat().st_size == 0:
                    last_status = RunStatus(1, "format_unavailable")
                    break
                if files[0].stat().st_size > limit:
                    raise DownloadError("video_too_large")
                tracks.append(files[0])
            if len(tracks) == 2:
                return (tracks[0], tracks[1]), last_status
            # Remove only files in this controlled, freshly created attempt.
            for path in attempt.iterdir():
                if path.is_symlink() or not path.is_file():
                    raise DownloadError("unsafe_output")
                path.unlink()
            attempt.rmdir()
        return None, last_status

    async def _convert(self, source: Path | tuple[Path, Path], folder: Path,
                       env: dict[str, str], deadline: float) -> Path:
        if not self.ffmpeg:
            raise DownloadError("ffmpeg_unavailable")
        output = folder / "telegram.mp4"
        inputs = list(source) if isinstance(source, tuple) else [source]
        input_args = []
        for path in inputs:
            if not path.is_file() or path.is_symlink() or path.parent.resolve() not in {folder.resolve(), folder.resolve() / "split-0", folder.resolve() / "split-1", folder.resolve() / "split-2"}:
                raise DownloadError("unsafe_output")
            input_args += ["-protocol_whitelist", "file,pipe",
                           "-format_whitelist", "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,mpegts,ogg",
                           # Metadata may omit or misreport dimensions. Enforce
                           # the actual decoded frame budget, including portrait.
                           "-max_pixels", str(MAX_DECODE_PIXELS), "-threads", "1", "-i", str(path)]
        status = await self._run([
            self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-max_alloc", "33554432", "-protocol_whitelist", "file,pipe",
            "-format_whitelist", "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,mpegts",
            "-filter_threads", "1", *input_args,
            "-map", "0:v:0", "-map", "1:a:0" if len(inputs) == 2 else "0:a?", "-c:v", "libx264", "-threads", "1",
            "-preset", "veryfast", "-crf", "25", "-pix_fmt", "yuv420p",
            "-vf", "scale=w='min(1280,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
            "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(output),
        ], folder, env, deadline)
        if status != 0 or not output.is_file() or not 0 < output.stat().st_size <= MAX_BYTES:
            raise DownloadError("video_conversion_failed")
        return output

    @asynccontextmanager
    async def _job(self):
        if self._waiting >= self.queue_limit:
            raise DownloadError("download_queue_full")
        self._waiting += 1
        try:
            try:
                await asyncio.wait_for(self._slot.acquire(), self.queue_timeout)
            except TimeoutError as error:
                raise DownloadError("download_queue_timeout") from error
        finally:
            self._waiting -= 1
        try:
            deadline = time.monotonic() + self.job_timeout
            with tempfile.TemporaryDirectory(prefix="savelog-media-") as temporary:
                folder = Path(temporary)
                async with DownloadProxy() as proxy:
                    env = self._child_env(folder, proxy.url)
                    yield folder, env, proxy.url, deadline
        except (DownloadError, asyncio.CancelledError):
            raise
        except Exception as error:
            raise DownloadError("download_failed") from error
        finally:
            self._slot.release()

    @asynccontextmanager
    async def download(self, url: str, *, cookie_owner_id: int | None = None) -> AsyncIterator[DownloadedVideo]:
        platform = validate_video_url(url)
        async with self._job() as (folder, env, proxy, deadline):
            cookie_file = None
            if cookie_owner_id is not None:
                from app.config import DownloadCookieSettings
                from app.services.download_cookies import CookiePolicyError, prepare_cookie_file
                try:
                    cookie_file = prepare_cookie_file(DownloadCookieSettings(), cookie_owner_id, platform, folder)
                except CookiePolicyError as error:
                    raise DownloadError(str(error)) from None
            if cookie_file is None:
                source = await self._fetch(url, platform, folder, env, proxy, deadline)
            else:
                source = await self._fetch(url, platform, folder, env, proxy, deadline, cookie_file=cookie_file)
            result = await self._convert(source, folder, env, deadline)
            yield DownloadedVideo(result, platform, result.stat().st_size)


    @staticmethod
    def _platform_args(platform: str) -> list[str]:
        # Preserve the original TikTok browser profile. Other providers keep
        # their extractor defaults; checked transport and limits are unchanged.
        if platform == "tiktok":
            return ["--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"]
        return []

    @staticmethod
    def _extractor_args(folder: Path, proxy: str) -> list[str]:
        return [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-plugin-dirs",
                "--no-js-runtimes", "--no-cache-dir", "--proxy", proxy,
                "--socket-timeout", "8", "--retries", "1", "--fragment-retries", "1",
                "--concurrent-fragments", "1", "--downloader", "dash,m3u8:native",
                "--ffmpeg-location", str(folder / "disabled-network-ffmpeg"),
                "--fixup", "never", "--no-progress", "--no-warnings"]

    async def _audio(self, url, folder, env, proxy, deadline) -> DownloadedMedia:
        command = self._extractor_args(folder, proxy) + [
            "--no-playlist", "--max-filesize", str(20 * 1024 * 1024),
            "--format", "bestaudio[protocol=https]/bestaudio[protocol=m3u8_native]",
            "--write-info-json", "--output", str(folder / "audio-source.%(ext)s"), "--", url,
        ]
        if await self._run(command, folder, env, deadline) != 0:
            raise DownloadError("audio_unavailable")
        files = [p for p in folder.glob("audio-source.*") if p.suffix.lower() in
                 {".m4a", ".mp4", ".webm", ".mp3", ".ogg", ".opus", ".flac", ".wav", ".aac"}]
        if len(files) != 1 or not self.ffmpeg:
            raise DownloadError("audio_unavailable")
        title, uploader, duration = "Audio", "", 0
        metadata = folder / "audio-source.info.json"
        if not metadata.is_file():
            raise DownloadError("audio_metadata_unavailable")
        if metadata.exists():
            if metadata.stat().st_size > 2 * 1024 * 1024:
                raise DownloadError("metadata_too_large")
            data = json.loads(metadata.read_text(encoding="utf-8"))
            title = str(data.get("title") or "Audio")[:200]
            uploader = str(data.get("uploader") or "")[:200]
            duration = int(data.get("duration") or 0)
            if not 0 < duration <= 900:
                raise DownloadError("audio_too_long")
        output = folder / "audio.mp3"
        command = [self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                   "-max_alloc", "33554432", "-protocol_whitelist", "file,pipe",
                   "-format_whitelist", "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,mp3,ogg,flac,wav,aac,mpegts",
                   "-threads", "1", "-i", str(files[0]), "-map", "0:a:0", "-vn",
                   "-c:a", "libmp3lame", "-b:a", "128k", "-threads", "1",
                   "-t", "900", "-fs", str(20 * 1024 * 1024), str(output)]
        if await self._run(command, folder, env, deadline) != 0 or not output.is_file():
            raise DownloadError("audio_conversion_failed")
        if not 0 < output.stat().st_size <= 20 * 1024 * 1024:
            raise DownloadError("audio_too_large")
        return DownloadedMedia((output,), "audio", title, uploader, duration)

    async def _photos(self, url, folder, env, proxy, deadline) -> DownloadedMedia:
        from PIL import Image

        images = [p for p in folder.rglob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
        if not images:
            command = [sys.executable, "-m", "gallery_dl", "--config-ignore", "--proxy", proxy,
                "-o", "downloader.ytdl.enabled=false",
                       "--http-timeout", "8", "--retries", "1", "--range", "1-10",
                       "--filter", "extension in ('jpg', 'jpeg', 'png', 'webp')",
                       "-D", str(folder / "photos"), "--", url]
            if await self._run(command, folder, env, deadline) != 0:
                raise DownloadError("photos_unavailable")
            images = [p for p in folder.rglob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
        if not 1 <= len(images) <= 10:
            raise DownloadError("photo_count_limit")
        results = []
        for number, image in enumerate(sorted(images)):
            if time.monotonic() >= deadline:
                raise DownloadError("download_timeout")
            self._check_workspace(folder)
            if image.stat().st_size > 10 * 1024 * 1024:
                raise DownloadError("photo_too_large")
            try:
                with Image.open(image) as decoded:
                    if (decoded.format not in {"JPEG", "PNG", "WEBP"} or decoded.width * decoded.height > 20_000_000
                        or max(decoded.size) > 6000 or min(decoded.size) <= 0 or getattr(decoded, "n_frames", 1) != 1):
                        raise DownloadError("unsafe_image")
                    decoded.verify()  # bounded header/container validation, no pixel conversion
                if not self.ffmpeg:
                    raise DownloadError("ffmpeg_unavailable")
                output = folder / f"telegram-photo-{number}.jpg"
                status = await self._run([
                    self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-max_alloc", "33554432", "-protocol_whitelist", "file,pipe",
                    "-format_whitelist", "jpeg_pipe,png_pipe,webp_pipe,image2",
                    "-threads", "1", "-filter_threads", "1", "-i", str(image),
                    "-frames:v", "1", "-c:v", "mjpeg", "-threads", "1", "-q:v", "3", str(output),
                ], folder, env, deadline)
                if status != 0 or not output.is_file():
                    raise DownloadError("photo_conversion_failed")
                if not 0 < output.stat().st_size <= 10 * 1024 * 1024:
                    raise DownloadError("photo_too_large")
                with Image.open(output) as verified:
                    if verified.format != "JPEG":
                        raise DownloadError("unsafe_image")
                    verified.verify()
                results.append(output)
            except DownloadError:
                raise
            except Exception:
                raise DownloadError("unsafe_image") from None
        self._check_workspace(folder)
        return DownloadedMedia(tuple(results), "photo")

    @asynccontextmanager
    async def media(self, url: str, *, mode: str = "auto") -> AsyncIterator[DownloadedMedia]:
        if mode not in {"audio", "auto", "photos"}:
            raise DownloadError("unsupported_media_mode")
        platform = validate_video_url(url, audio=mode == "audio")
        async with self._job() as (folder, env, proxy, deadline):
            if mode == "audio":
                result = await self._audio(url, folder, env, proxy, deadline)
            elif mode == "photos":
                result = await self._photos(url, folder, env, proxy, deadline)
            else:
                try:
                    source = await self._fetch(url, platform, folder, env, proxy, deadline)
                    video = await self._convert(source, folder, env, deadline)
                    result = DownloadedMedia((video,), "video")
                except DownloadError as error:
                    # Public slideshow/carousel fallback, still under SAME checked
                    # transport/limits. Never retry timeout/size/conversion failures.
                    if platform not in {"instagram", "tiktok"} or str(error) not in {
                        "video_unavailable", "format_unavailable", "authentication_required", "source_forbidden", "unknown",
                    }:
                        raise
                    result = await self._photos(url, folder, env, proxy, deadline)
            yield result

    async def search(self, query: str, n: int = 15) -> list[dict]:
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200 or any(ord(c) < 32 for c in query):
            raise DownloadError("invalid_search_query")
        count = max(1, min(int(n), 15))
        direct = "://" in query
        if direct:
            validate_video_url(query, audio=True)
        async with self._job() as (folder, env, proxy, deadline):
            results: list[dict] = []
            seen = set()
            targets = [query] if direct else [f"ytsearchmusic{count}:{query}", f"ytsearch{count}:{query}", f"scsearch{count}:{query}"]
            for target in targets:
                output = folder / "search.json"
                command = self._extractor_args(folder, proxy) + ["--flat-playlist", "--skip-download",
                           "--dump-single-json", "--playlist-end", str(count), "--", target]
                if await self._run(command, folder, env, deadline, capture=output) != 0:
                    continue
                if not output.is_file() or output.stat().st_size > 2 * 1024 * 1024:
                    raise DownloadError("metadata_too_large")
                data = json.loads(output.read_text(encoding="utf-8"))
                entries = data.get("entries") or [data]
                for entry in entries[:count]:
                    if not isinstance(entry, dict):
                        continue
                    url = entry.get("webpage_url") or entry.get("url") or ""
                    if not str(url).startswith("https://") and entry.get("id"):
                        url = "https://www.youtube.com/watch?v=" + str(entry["id"])
                    try:
                        validate_video_url(url, audio=True)
                        duration = int(entry.get("duration") or 0)
                    except (DownloadError, ValueError, TypeError):
                        continue
                    if url in seen or duration > 900 or duration < 0:
                        continue
                    seen.add(url)
                    results.append({"url": url, "title": str(entry.get("title") or "Audio")[:200],
                                    "uploader": str(entry.get("uploader") or entry.get("channel") or "")[:200],
                                    "duration": duration})
                if len(results) >= count or direct:
                    break
            return results[:count]


_shared_service: DownloadService | None = None


def get_download_service() -> DownloadService:
    """Process-wide queue for ALL public video/photo/audio/search entry points."""
    global _shared_service
    if _shared_service is None:
        _shared_service = DownloadService()
    return _shared_service
