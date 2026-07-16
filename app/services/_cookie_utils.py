"""Shared cookie-format utilities used by audio_service and video_service."""
from __future__ import annotations

import json as _json
import time as _time


def json_cookies_to_netscape(raw: str) -> str:
    """Convert a JSON cookie array (exported by browser extensions such as
    'Cookie Editor' or 'EditThisCookie') to Netscape/Mozilla cookie file
    format that yt-dlp accepts via ``cookiefile``.

    Returns an empty string if *raw* is not valid JSON or not a list.
    """
    try:
        cookies = _json.loads(raw)
        if not isinstance(cookies, list):
            return ""
    except (_json.JSONDecodeError, ValueError):
        return ""

    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        if not isinstance(c, dict):
            continue
        domain = str(c.get("domain") or "")
        if not domain:
            continue
        if not domain.startswith("."):
            domain = "." + domain
        path   = str(c.get("path") or "/")
        secure = "TRUE" if c.get("secure") else "FALSE"
        expiry = str(int(c.get("expirationDate") or int(_time.time()) + 86_400 * 365))
        name   = str(c.get("name") or "")
        value  = str(c.get("value") or "")
        lines.append(f"{domain}\tTRUE\t{path}\t{secure}\t{expiry}\t{name}\t{value}")
    return "\n".join(lines)
