"""Postcard image generation — beautiful relationship greeting cards.

Renders a 1000×700 PNG with a tier-specific gradient, decorative hearts,
a frosted center card with the message, partner names, and couple stats.
Fonts: Inter (bundled, full Cyrillic support).
"""
from __future__ import annotations

import io
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

_FONTS = Path(__file__).parent.parent / "assets" / "fonts"

W, H = 1000, 700

# tier → (gradient top, gradient bottom, accent, heart emoji-ish glyph color)
_THEMES: dict[str, dict] = {
    "friends": {
        "top": (255, 200, 87), "bottom": (255, 128, 8),
        "accent": (255, 255, 255), "deco": (255, 235, 180),
        "header": "ДРУЖЕСКАЯ ОТКРЫТКА",
    },
    "dating": {
        "top": (255, 94, 135), "bottom": (155, 33, 98),
        "accent": (255, 255, 255), "deco": (255, 180, 205),
        "header": "ОТКРЫТКА С ЛЮБОВЬЮ",
    },
    "married": {
        "top": (162, 89, 255), "bottom": (66, 20, 143),
        "accent": (255, 255, 255), "deco": (215, 185, 255),
        "header": "СЕМЕЙНАЯ ОТКРЫТКА",
    },
}


def _font(size: int, weight: str = "Regular") -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(_FONTS / f"Inter-{weight}.ttf"), size)
    except Exception:
        return ImageFont.load_default()


def _gradient(top: tuple, bottom: tuple) -> Image.Image:
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        t = y / (H - 1)
        r = round(top[0] + (bottom[0] - top[0]) * t)
        g = round(top[1] + (bottom[1] - top[1]) * t)
        b = round(top[2] + (bottom[2] - top[2]) * t)
        for x in range(W):
            px[x, y] = (r, g, b)
    return img


def _heart(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, fill: tuple) -> None:
    """Vector heart at (cx, cy)."""
    r = size / 4
    # two circles + rotated square approximation
    draw.ellipse([cx - 2 * r, cy - 2 * r, cx, cy], fill=fill)
    draw.ellipse([cx, cy - 2 * r, cx + 2 * r, cy], fill=fill)
    draw.polygon(
        [(cx - 2 * r, cy - r * 0.6), (cx + 2 * r, cy - r * 0.6), (cx, cy + 2 * r)],
        fill=fill,
    )


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        probe = f"{cur} {w}".strip()
        if font.getlength(probe) <= max_w:
            cur = probe
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines[:6]  # hard cap


def render_postcard(
    *,
    rel_type: str,
    sender_name: str,
    partner_name: str,
    message: str,
    days_together: int | None = None,
    streak_days: int = 0,
    title: str | None = None,
) -> bytes:
    theme = _THEMES.get(rel_type, _THEMES["friends"])
    img = _gradient(theme["top"], theme["bottom"])

    # ── decorative hearts layer (blurred, translucent) ────────────────────────
    deco = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ddraw = ImageDraw.Draw(deco)
    rng = random.Random(hash((sender_name, partner_name)) & 0xFFFF)
    for _ in range(26):
        x, y = rng.randint(0, W), rng.randint(0, H)
        s = rng.randint(14, 64)
        alpha = rng.randint(18, 60)
        _heart(ddraw, x, y, s, (*theme["deco"], alpha))
    deco = deco.filter(ImageFilter.GaussianBlur(2))
    img = Image.alpha_composite(img.convert("RGBA"), deco)

    draw = ImageDraw.Draw(img)

    # ── frosted center card ───────────────────────────────────────────────────
    card = [70, 110, W - 70, H - 110]
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.rounded_rectangle(card, radius=36, fill=(255, 255, 255, 46))
    odraw.rounded_rectangle(card, radius=36, outline=(255, 255, 255, 130), width=2)
    # top specular line
    odraw.line([card[0] + 40, card[1] + 2, card[2] - 40, card[1] + 2],
               fill=(255, 255, 255, 190), width=2)
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    white = theme["accent"]
    shadow = (0, 0, 0, 70)

    def _text_c(y: int, s: str, font: ImageFont.FreeTypeFont, fill=white) -> int:
        w = font.getlength(s)
        x = (W - w) / 2
        draw.text((x + 1, y + 2), s, font=font, fill=shadow)
        draw.text((x, y), s, font=font, fill=fill)
        return y + font.size + 8

    # header
    y = _text_c(150, theme["header"], _font(26, "Bold"))

    # big heart divider
    _heart(draw, W // 2, y + 36, 40, (*white, 255))
    y += 78

    # names
    names = f"{sender_name}  ♥  {partner_name}"
    name_font = _font(44, "Bold")
    while name_font.getlength(names) > W - 220 and name_font.size > 24:
        name_font = _font(name_font.size - 4, "Bold")
    y = _text_c(y, names, name_font)

    if title:
        y = _text_c(y + 2, f"«{title}»", _font(20, "Medium"), fill=(255, 255, 255))

    # message
    y += 22
    msg_font = _font(30, "Medium")
    for line in _wrap(message, msg_font, W - 260):
        y = _text_c(y, line, msg_font)

    # footer stats
    footer_bits = []
    if days_together is not None and days_together >= 0:
        footer_bits.append(f"Вместе {days_together} дн.")
    if streak_days > 0:
        footer_bits.append(f"Стрик {streak_days} дн.")
    if footer_bits:
        _text_c(H - 168, "  ·  ".join(footer_bits), _font(22, "SemiBold"))

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()
