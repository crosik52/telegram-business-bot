"""Deterministic Pillow renderer for the private conversation insights card."""

from __future__ import annotations

import datetime as dt
import io
from pathlib import Path
from typing import NamedTuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont


class InfoStats(NamedTuple):
    contact_name: str
    total: int
    incoming: int
    outgoing: int
    deleted: int
    edited: int
    media_count: int
    audio_count: int
    first_seen: dt.datetime | None
    last_seen: dt.datetime | None
    note_count: int
    muted_until: dt.datetime | None
    daily: list[tuple[str, int, int]]


CANVAS = (1280, 720)
MARGIN = 44
FONT_DIR = Path(__file__).parent.parent / "assets" / "fonts"

# A quiet ink-and-slate palette. Accent colour only carries meaning.
INK = (13, 19, 29)
SURFACE = (25, 34, 48, 238)
SURFACE_ALT = (30, 40, 55, 245)
EDGE = (88, 105, 126, 125)
TEXT = (238, 242, 247)
MUTED = (144, 157, 174)
FAINT = (82, 96, 115)
INCOMING = (103, 169, 205)
OUTGOING = (206, 157, 112)
ACCENTS = ((129, 175, 205), (207, 160, 113), (159, 145, 202), (180, 169, 121))


def render_info_image(
    stats: InfoStats,
    *,
    avatar_bytes: bytes | None = None,
) -> io.BytesIO:
    """Render a 1280×720 PNG using only deterministic Pillow geometry."""
    image = _background()
    draw = ImageDraw.Draw(image)

    _panel(image, (MARGIN, 36, 1236, 154), radius=22)
    _header(image, draw, stats, avatar_bytes)

    # Four primary metrics establish the scan path.
    metric_y, metric_h, metric_gap = 176, 160, 16
    metric_w = (1192 - metric_gap * 3) // 4
    primary = (
        (stats.total, "ВСЕГО СООБЩЕНИЙ", "messages", ACCENTS[0]),
        (stats.outgoing, "ОТПРАВЛЕНО ВАМИ", "outgoing", OUTGOING),
        (stats.incoming, "ПОЛУЧЕНО", "incoming", INCOMING),
        (_avg_per_day(stats), "В СРЕДНЕМ В ДЕНЬ", "daily", ACCENTS[3]),
    )
    for index, (value, label, icon, color) in enumerate(primary):
        x = MARGIN + index * (metric_w + metric_gap)
        _metric_card(image, draw, (x, metric_y, x + metric_w, metric_y + metric_h),
                     value, label, icon, color, major=True)

    _panel(image, (MARGIN, 358, 778, 676), radius=22)
    _activity_chart(image, draw, stats, (MARGIN, 358, 778, 676))
    _panel(image, (798, 358, 1236, 676), radius=22)
    _conversation_panel(image, draw, stats, (798, 358, 1236, 676))

    output = io.BytesIO()
    image.convert("RGB").save(output, "PNG", optimize=True, compress_level=9)
    output.seek(0)
    return output


def _background() -> Image.Image:
    """Matte vertical ink gradient with an intentionally restrained edge vignette."""
    width, height = CANVAS
    image = Image.new("RGBA", CANVAS)
    pixels = image.load()
    for y in range(height):
        t = y / (height - 1)
        base = tuple(round(INK[i] * (1 - t) + (18, 26, 38)[i] * t) for i in range(3))
        for x in range(width):
            edge = min(x, width - 1 - x) / (width / 2)
            shade = int((1 - edge) * 7)
            pixels[x, y] = tuple(max(0, component - shade) for component in base)
    return image


def _panel(image: Image.Image, box: tuple[int, int, int, int], *, radius: int) -> None:
    """Raised matte panel, with shadow separated from its crisp one-pixel rim."""
    shadow = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    shifted = (box[0], box[1] + 8, box[2], box[3] + 8)
    sd.rounded_rectangle(shifted, radius=radius, fill=(0, 0, 0, 105))
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(9)))
    layer = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.rounded_rectangle(box, radius=radius, fill=SURFACE, outline=EDGE, width=1)
    # Gentle top-facing material highlight, not a glow.
    ld.line((box[0] + radius, box[1], box[2] - radius, box[1]), fill=(173, 189, 206, 58), width=1)
    image.alpha_composite(layer)


def _header(image: Image.Image, draw: ImageDraw.ImageDraw, stats: InfoStats, avatar_bytes: bytes | None) -> None:
    avatar_box = (66, 57, 130, 121)
    _avatar(image, draw, stats.contact_name, avatar_bytes, avatar_box)
    name = _truncate(stats.contact_name or "Собеседник", 38)
    draw.text((150, 60), name, font=_font(27, "semibold"), fill=TEXT)
    draw.text((151, 96), f"{_period_str(stats)}  /  {_days_total(stats)} дн.", font=_font(15), fill=MUTED)
    draw.line((502, 64, 502, 126), fill=(87, 102, 121, 145), width=1)
    draw.text((526, 65), "ЛИЧНЫЙ ДИАЛОГ", font=_font(13, "bold"), fill=FAINT)
    draw.text((526, 91), "Статистика переписки", font=_font(17, "medium"), fill=TEXT)

    chips: list[tuple[str, str]] = []
    if stats.note_count:
        chips.append((f"Заметки  {stats.note_count}", "note"))
    if stats.muted_until and stats.muted_until > _now_for(stats.muted_until):
        chips.append((f"Без звука до {stats.muted_until:%d.%m}", "mute"))
    x = 1210
    for text, kind in reversed(chips):
        width = int(draw.textlength(text, font=_font(14, "medium"))) + 49
        x -= width
        _chip(image, draw, (x, 70, x + width, 111), text, kind)
        x -= 10


def _avatar(image: Image.Image, draw: ImageDraw.ImageDraw, name: str, data: bytes | None,
            box: tuple[int, int, int, int]) -> None:
    mask = Image.new("L", CANVAS, 0)
    ImageDraw.Draw(mask).ellipse(box, fill=255)
    if data:
        try:
            source = Image.open(io.BytesIO(data)).convert("RGB")
            side = min(source.size)
            left, top = (source.width - side) // 2, (source.height - side) // 2
            source = source.crop((left, top, left + side, top + side)).resize((64, 64), Image.Resampling.LANCZOS)
            layer = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
            layer.paste(source, box[:2])
            layer.putalpha(mask)
            image.alpha_composite(layer)
        except Exception:
            _avatar_fallback(draw, name, box)
    else:
        _avatar_fallback(draw, name, box)
    draw.ellipse(box, outline=(174, 191, 205, 130), width=2)


def _avatar_fallback(draw: ImageDraw.ImageDraw, name: str, box: tuple[int, int, int, int]) -> None:
    draw.ellipse(box, fill=(42, 71, 91))
    initials = _initials(name)
    font = _font(21, "bold")
    center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    _centered(draw, center, initials, font, (211, 228, 239))


def _chip(image: Image.Image, draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, kind: str) -> None:
    layer = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.rounded_rectangle(box, radius=13, fill=(49, 62, 79, 210), outline=(100, 118, 137, 120), width=1)
    image.alpha_composite(layer)
    color = (191, 163, 119) if kind == "mute" else (129, 175, 205)
    _icon(draw, kind, box[0] + 19, (box[1] + box[3]) // 2, color, 15)
    draw.text((box[0] + 32, box[1] + 12), text, font=_font(14, "medium"), fill=(211, 219, 226))


def _metric_card(image: Image.Image, draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int],
                 value: int | float, label: str, icon: str, color: tuple[int, int, int], *, major: bool) -> None:
    _panel(image, box, radius=18)
    draw.line((box[0] + 20, box[1] + 20, box[0] + 53, box[1] + 20), fill=color, width=3)
    _icon(draw, icon, box[2] - 34, box[1] + 36, color, 18)
    draw.text((box[0] + 20, box[1] + 47), _fmt_num(value), font=_font(38, "bold"), fill=TEXT)
    draw.text((box[0] + 20, box[1] + 108), label, font=_font(12, "bold"), fill=MUTED)


def _activity_chart(image: Image.Image, draw: ImageDraw.ImageDraw, stats: InfoStats,
                    box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    draw.text((x0 + 24, y0 + 23), "АКТИВНОСТЬ", font=_font(13, "bold"), fill=MUTED)
    draw.text((x0 + 24, y0 + 47), "Последние 30 дней", font=_font(20, "semibold"), fill=TEXT)
    _legend(draw, x1 - 196, y0 + 31, INCOMING, "Входящие")
    _legend(draw, x1 - 96, y0 + 31, OUTGOING, "Исходящие")
    chart = (x0 + 25, y0 + 98, x1 - 25, y1 - 48)
    daily = stats.daily[-14:]
    if not daily:
        draw.line((chart[0], chart[3], chart[2], chart[3]), fill=(84, 98, 116), width=1)
        _centered(draw, ((chart[0] + chart[2]) / 2, (chart[1] + chart[3]) / 2), "Нет сообщений за этот период",
                  _font(16, "medium"), MUTED)
        return
    max_value = max(1, max(incoming + outgoing for _, incoming, outgoing in daily))
    slot = (chart[2] - chart[0]) / len(daily)
    for index, (label, incoming, outgoing) in enumerate(daily):
        center = chart[0] + slot * (index + 0.5)
        total_height = (chart[3] - chart[1]) * (incoming + outgoing) / max_value
        inbound_h = total_height * incoming / max(incoming + outgoing, 1)
        width = max(7, int(slot * 0.48))
        left, right = int(center - width / 2), int(center + width / 2)
        bottom = chart[3]
        draw.rounded_rectangle((left, int(bottom - inbound_h), right, bottom), radius=3, fill=INCOMING)
        if outgoing:
            draw.rounded_rectangle((left, int(bottom - total_height), right, int(bottom - inbound_h) + 2),
                                   radius=3, fill=OUTGOING)
        if index in (0, len(daily) - 1) or index % max(1, len(daily) // 4) == 0:
            _centered(draw, (center, chart[3] + 18), label, _font(11), FAINT)
    draw.line((chart[0], chart[3] + 0.5, chart[2], chart[3] + 0.5), fill=(82, 97, 115), width=1)


def _conversation_panel(image: Image.Image, draw: ImageDraw.ImageDraw, stats: InfoStats,
                        box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    draw.text((x0 + 24, y0 + 23), "БАЛАНС ДИАЛОГА", font=_font(13, "bold"), fill=MUTED)
    total = max(0, stats.incoming) + max(0, stats.outgoing)
    cx, cy, radius = x0 + 122, y0 + 176, 74
    draw.arc((cx - radius, cy - radius, cx + radius, cy + radius), -90, 270, fill=(63, 77, 95), width=18)
    if total:
        incoming_angle = 360 * max(0, stats.incoming) / total
        draw.arc((cx - radius, cy - radius, cx + radius, cy + radius), -90, -90 + incoming_angle,
                 fill=INCOMING, width=18)
        draw.arc((cx - radius, cy - radius, cx + radius, cy + radius), -90 + incoming_angle + 3, 267,
                 fill=OUTGOING, width=18)
        percent = round(stats.incoming * 100 / total)
        _centered(draw, (cx, cy - 8), f"{percent}%", _font(28, "bold"), TEXT)
        _centered(draw, (cx, cy + 19), "входящих", _font(12, "medium"), MUTED)
    else:
        _centered(draw, (cx, cy - 6), "—", _font(35, "bold"), MUTED)
        _centered(draw, (cx, cy + 21), "нет данных", _font(12, "medium"), MUTED)
    _stat_line(draw, x0 + 236, y0 + 105, INCOMING, "Входящие", stats.incoming)
    _stat_line(draw, x0 + 236, y0 + 148, OUTGOING, "Исходящие", stats.outgoing)
    draw.line((x0 + 236, y0 + 184, x1 - 24, y0 + 184), fill=(77, 92, 110), width=1)
    secondary = ((stats.media_count, "Медиа", "media"), (stats.audio_count, "Аудио", "audio"),
                 (stats.edited, "Изменено", "edit"), (stats.deleted, "Удалено", "delete"))
    for idx, (value, label, icon) in enumerate(secondary):
        column_width = (x1 - x0 - 48) / 4
        center_x = x0 + 24 + column_width * (idx + 0.5)
        _icon(draw, icon, round(center_x), y0 + 231, ACCENTS[idx], 14)
        _centered(draw, (center_x, y0 + 257), _fmt_num(value), _font(15, "bold"), TEXT)
        _centered(draw, (center_x, y0 + 282), label, _font(10), MUTED)


def _stat_line(draw: ImageDraw.ImageDraw, x: int, y: int, color: tuple[int, int, int], label: str, value: int) -> None:
    draw.ellipse((x, y + 4, x + 10, y + 14), fill=color)
    draw.text((x + 18, y), label, font=_font(13, "medium"), fill=MUTED)
    draw.text((x + 18, y + 18), _fmt_num(value), font=_font(20, "bold"), fill=TEXT)


def _legend(draw: ImageDraw.ImageDraw, x: int, y: int, color: tuple[int, int, int], text: str) -> None:
    draw.ellipse((x, y + 3, x + 8, y + 11), fill=color)
    draw.text((x + 14, y), text, font=_font(11, "medium"), fill=MUTED)


def _icon(draw: ImageDraw.ImageDraw, kind: str, x: int, y: int, color: tuple[int, int, int], size: int) -> None:
    """Crisp semantic line icons, built from fixed vector primitives."""
    h, w = size // 2, max(1, size // 9)
    if kind == "messages":
        draw.rounded_rectangle((x - h, y - h + 1, x + h, y + h - 3), radius=3, outline=color, width=w)
        draw.line((x - h // 2, y + h - 3, x - h + 1, y + h + 3, x, y + h - 3), fill=color, width=w)
    elif kind in ("incoming", "outgoing"):
        direction = -1 if kind == "incoming" else 1
        draw.line((x - direction * h, y + h, x + direction * h, y - h), fill=color, width=w)
        draw.line((x + direction * h, y - h, x + direction * h, y - h + direction * h), fill=color, width=w)
        draw.line((x + direction * h, y - h, x + direction * h - direction * h, y - h), fill=color, width=w)
    elif kind == "daily":
        for index, height in enumerate((6, 12, 17)):
            bx = x - 7 + index * 7
            draw.line((bx, y + 8, bx, y + 8 - height), fill=color, width=3)
    elif kind == "media":
        draw.rounded_rectangle((x - h, y - h + 1, x + h, y + h - 1), radius=2, outline=color, width=w)
        draw.ellipse((x + 2, y - 5, x + 5, y - 2), fill=color)
        draw.line((x - 6, y + 5, x - 1, y, x + 2, y + 4, x + 7, y - 1), fill=color, width=w)
    elif kind == "audio":
        draw.rounded_rectangle((x - 3, y - 8, x + 3, y + 4), radius=3, outline=color, width=w)
        draw.arc((x - 7, y - 3, x + 7, y + 10), 0, 180, fill=color, width=w)
        draw.line((x, y + 10, x, y + 13), fill=color, width=w)
    elif kind == "edit":
        draw.line((x - 7, y + 7, x + 6, y - 6), fill=color, width=3)
        draw.line((x + 4, y - 8, x + 8, y - 4), fill=color, width=3)
    elif kind == "delete":
        draw.rounded_rectangle((x - 5, y - 5, x + 5, y + 8), radius=1, outline=color, width=w)
        draw.line((x - 7, y - 7, x + 7, y - 7), fill=color, width=w)
        draw.line((x - 2, y - 10, x + 2, y - 10), fill=color, width=w)
    elif kind == "note":
        draw.rectangle((x - 5, y - 7, x + 5, y + 7), outline=color, width=w)
        draw.line((x - 2, y - 2, x + 3, y - 2), fill=color, width=w)
    elif kind == "mute":
        draw.arc((x - 6, y - 5, x + 3, y + 5), -70, 70, fill=color, width=w)
        draw.line((x + 4, y - 6, x + 4, y + 3), fill=color, width=w)
        draw.line((x - 8, y - 8, x + 8, y + 8), fill=color, width=w)


def _font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = {
        "regular": ("Inter-Regular.ttf", "DejaVuSans.ttf"),
        "medium": ("Inter-Medium.ttf", "DejaVuSans.ttf"),
        "semibold": ("Inter-SemiBold.ttf", "DejaVuSans-Bold.ttf"),
        "bold": ("Inter-Bold.ttf", "DejaVuSans-Bold.ttf"),
    }
    for name in names[weight]:
        try:
            path = FONT_DIR / name
            return ImageFont.truetype(str(path) if path.exists() else name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _centered(draw: ImageDraw.ImageDraw, center: tuple[float, float], text: str,
              font: ImageFont.ImageFont, fill: tuple[int, int, int]) -> None:
    box = draw.textbbox((0, 0), text, font=font)
    draw.text((center[0] - (box[2] - box[0]) / 2, center[1] - (box[3] - box[1]) / 2),
              text, font=font, fill=fill)


def _initials(name: str) -> str:
    parts = name.strip().split()
    return "".join(part[0] for part in parts[:2]).upper() or "??"


def _truncate(text: str, length: int) -> str:
    return text if len(text) <= length else f"{text[:length - 1]}…"


def _now_for(value: dt.datetime) -> dt.datetime:
    return dt.datetime.now(value.tzinfo) if value.tzinfo else dt.datetime.now()


def _period_str(stats: InfoStats) -> str:
    if stats.first_seen and stats.last_seen:
        return f"{stats.first_seen:%d.%m.%y} — {stats.last_seen:%d.%m.%y}"
    return "Период не определён"


def _days_total(stats: InfoStats) -> int:
    if stats.first_seen and stats.last_seen:
        return max(1, (stats.last_seen - stats.first_seen).days)
    return 1


def _avg_per_day(stats: InfoStats) -> float:
    return round(stats.total / _days_total(stats), 1)


def _fmt_num(value: float | int) -> str:
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.1f}"
    return f"{int(value):,}".replace(",", "\u2009")