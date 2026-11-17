"""Opt-in owner cookies: bounded readonly source and one private per-job copy.

Cookie values never enter argv, environment, logs or exceptions. Only the
temporary filename is supplied to an extractor. Anonymous entry points never
load a source file. No shared mutable cookie jar survives a job.
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path


class CookiePolicyError(ValueError):
    """Safe code only, without filenames or cookie values."""


def prepare_cookie_file(settings, owner_id: int | None, platform: str, folder: Path) -> Path | None:
    if owner_id is None:
        return None
    configured_owner = settings.download_cookie_owner_id
    if configured_owner is None:
        return None
    if type(owner_id) is not int or owner_id <= 0:
        raise CookiePolicyError("cookie_owner_invalid")
    if owner_id != configured_owner:
        return None  # other owners retain public downloads, never this jar
    source = getattr(settings, f"{platform}_cookie_file", None)
    if source is None:
        return None
    domains = {"youtube": "youtube.com", "instagram": "instagram.com", "tiktok": "tiktok.com"}
    domain = domains.get(platform)
    if not domain:
        raise CookiePolicyError("cookie_platform_invalid")
    source = Path(source)
    if not source.is_absolute() or source.is_symlink():
        raise CookiePolicyError("cookie_source_unsafe")
    descriptor = None
    try:
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 256 * 1024:
            raise CookiePolicyError("cookie_source_unsafe")
        if os.name != "nt" and info.st_mode & 0o077:
            raise CookiePolicyError("cookie_source_permissions")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            raw = stream.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise CookiePolicyError("cookie_source_unsafe")
        lines = []
        for line in raw.decode("utf-8").splitlines():
            if not line.strip() or (line.startswith("#") and not line.startswith("#HttpOnly_")):
                continue
            fields = line.removeprefix("#HttpOnly_").split("\t")
            if len(fields) != 7:
                raise CookiePolicyError("cookie_format_invalid")
            host, include_subdomains, path, secure, expiry, name, value = fields
            normalized = host.lower().lstrip(".")
            if normalized != domain and not normalized.endswith("." + domain):
                continue  # never copy another provider/account's domain
            if (not re.fullmatch(r"[A-Za-z0-9.-]+", host) or include_subdomains not in {"TRUE", "FALSE"}
                or secure not in {"TRUE", "FALSE"} or not expiry.isascii() or not expiry.isdigit()
                or not path.startswith("/") or not name or any(ord(c) < 32 or ord(c) == 127 for c in path + name + value)):
                raise CookiePolicyError("cookie_format_invalid")
            lines.append(line)
        if not lines:
            raise CookiePolicyError("cookie_domains_empty")
        target = folder / "owner-cookies.txt"
        with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8", newline="\n") as stream:
            stream.write("# Netscape HTTP Cookie File\n" + "\n".join(lines) + "\n")
        return target
    except CookiePolicyError:
        raise
    except Exception:
        raise CookiePolicyError("cookie_source_unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
