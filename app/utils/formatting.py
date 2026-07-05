"""Display formatting helpers for Jinja2 templates."""

from __future__ import annotations

import datetime as dt


def format_datetime(value: dt.datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M:%S UTC")


def truncate(text: str | None, length: int = 80) -> str:
    if not text:
        return ""
    return text if len(text) <= length else text[: length - 1] + "\u2026"
