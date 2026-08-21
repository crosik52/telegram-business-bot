"""chart_service.py — 640×360 px premium glassmorphism analytics card.

Layout
──────────────────────────────────────────────────────────────────────────
  Row 0  y 14–72   Header glass tile  (avatar · name · period · badges)
  Row 1  y 79–179  4 Primary KPI glass tiles  (Всего / Ваших / Их / День)
  Row 2  y 186–346 Bottom bar
           Left    x  14–414  4 Secondary KPI glass tiles
           Right   x 421–626  Conversation arc / donut tile
──────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import datetime as dt
import io
from pathlib import Path
from typing import NamedTuple

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.patches as mpatches
import numpy as np
from PIL import Image
from matplotlib.patches import (Circle, Ellipse, FancyBboxPatch,
                                FancyArrowPatch)
from matplotlib.colors import LinearSegmentedColormap

# ── Font registration ──────────────────────────────────────────────────────────

_FONTS_DIR = Path(__file__).parent.parent / "assets" / "fonts"
_FONT_REGISTERED = False


def _ensure_fonts() -> None:
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return
    for ttf in _FONTS_DIR.glob("Inter-*.ttf"):
        fm.fontManager.addfont(str(ttf))
    _FONT_REGISTERED = True


def _F(weight: int = 400) -> dict:
    _ensure_fonts()
    fam = "Inter" if "Inter" in fm.fontManager.get_font_names() else "DejaVu Sans"
    return {"fontfamily": fam, "fontweight": weight}


# ── Colour palette ─────────────────────────────────────────────────────────────

BG = "#08101F"           # deep dark navy background

# Glass tile colours (RGBA tuples)
GLASS_FACE = (0.72, 0.82, 1.0, 0.075)   # matte blue-white glass
GLASS_EDGE = (0.86, 0.92, 1.0, 0.16)    # brighter frosted rim
GLASS_GLOW = (0.12, 0.30, 0.60, 0.18)   # ambient blue glow behind tile

# Text
C_TEXT = "#EEF2FF"   # near-white primary
C_SUB  = "#4A5E82"   # muted label
C_DIM  = "#2E3F5E"   # very dim / divider

# Primary KPI accent colours (Blue / Cyan / Violet / Yellow)
ACC = ["#4A9EFF", "#00D4FF", "#9B6DFF", "#FFD166"]

# Secondary KPI accent colours (Coral / Teal / Periwinkle / Slate)
ACC_S = ["#FF7B72", "#3DD68C", "#7C8CFF", "#A0B4C8"]

# Donut
C_IN  = "#4A9EFF"   # incoming — blue
C_OUT = "#3DD68C"   # outgoing — green

# ── Canvas geometry ────────────────────────────────────────────────────────────

DPI = 100
RENDER_SCALE = 3
W   = 640
H   = 360
PAD = 14
GAP = 7

# Row heights
HDR_Y = PAD               # 14
HDR_H = 58                # → bottom = 72
PRI_Y = HDR_Y + HDR_H + GAP   # 79
PRI_H = 100               # → bottom = 179
BOT_Y = PRI_Y + PRI_H + GAP   # 186
BOT_H = H - PAD - BOT_Y  # 360-14-186 = 160

# Bottom split
SEC_W  = 400              # secondary KPI section width
RING_X = PAD + SEC_W + GAP    # 421
RING_W = W - PAD - RING_X     # 205

# ── Helpers ────────────────────────────────────────────────────────────────────

def _ax(fig: plt.Figure, x: float, y_top: float,
        w: float, h: float, zorder: int = 3) -> plt.Axes:
    ax = fig.add_axes([x / W, (H - y_top - h) / H, w / W, h / H])
    ax.set_zorder(zorder)
    return ax


def _rgba(hex_color: str, alpha: float = 1.0) -> tuple:
    c = hex_color.lstrip("#")
    r, g, b = int(c[0:2], 16) / 255, int(c[2:4], 16) / 255, int(c[4:6], 16) / 255
    return (r, g, b, alpha)


def _hex_blend(hex_color: str, alpha: float) -> tuple:
    return _rgba(hex_color, alpha)


# ── Public data contract ───────────────────────────────────────────────────────

class InfoStats(NamedTuple):
    contact_name: str
    total:        int
    incoming:     int
    outgoing:     int
    deleted:      int
    edited:       int
    media_count:  int
    audio_count:  int
    first_seen:   dt.datetime | None
    last_seen:    dt.datetime | None
    note_count:   int
    muted_until:  dt.datetime | None
    daily:        list[tuple[str, int, int]]   # (dd.mm, inbound, outbound)


# ── Entry point ────────────────────────────────────────────────────────────────

def render_info_image(
    stats: InfoStats,
    *,
    avatar_bytes: bytes | None = None,
) -> io.BytesIO:
    """Return a 640×360 PNG premium glassmorphism analytics card."""
    _ensure_fonts()

    render_dpi = DPI * RENDER_SCALE
    fig = plt.figure(
        figsize=(W / DPI, H / DPI),
        facecolor=BG,
        dpi=render_dpi,
    )
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)

    _draw_bg(fig)
    _draw_header(fig, stats, avatar_bytes)
    _draw_primary_kpis(fig, stats)
    _draw_secondary_kpis(fig, stats)
    _draw_ring(fig, stats)

    high_res = io.BytesIO()
    try:
        fig.savefig(
            high_res,
            format="png",
            facecolor=BG,
            dpi=render_dpi,
            metadata={"Software": "Telegram Analytics"},
        )
    finally:
        plt.close(fig)

    high_res.seek(0)
    with Image.open(high_res) as rendered:
        rendered = rendered.convert("RGB")
        rendered = rendered.resize((W, H), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        rendered.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


# ── Background ─────────────────────────────────────────────────────────────────

def _draw_bg(fig: plt.Figure) -> None:
    """Subtle dark gradient + ambient glow blobs to give depth."""
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.set_facecolor(BG)
    ax.axis("off")
    ax.set_zorder(0)

    # Left ambient glow (blue)
    for r, a in [(200, 0.06), (130, 0.09), (70, 0.07)]:
        ax.add_patch(Ellipse((0, H), r * 2.2, r * 1.4,
                             facecolor=_rgba("#1A4080", a), edgecolor="none"))
    # Right ambient glow (violet)
    for r, a in [(180, 0.05), (100, 0.07), (55, 0.05)]:
        ax.add_patch(Ellipse((W, 0), r * 2.0, r * 1.3,
                             facecolor=_rgba("#3A1F6A", a), edgecolor="none"))
    # Top-right subtle cyan
    for r, a in [(120, 0.04), (60, 0.05)]:
        ax.add_patch(Ellipse((W * 0.75, H), r * 2, r,
                             facecolor=_rgba("#003850", a), edgecolor="none"))


# ── Glass tile primitive ───────────────────────────────────────────────────────

def _glass_tile(fig: plt.Figure, x: float, y_top: float,
                w: float, h: float,
                accent: str | None = None,
                zorder: int = 2) -> None:
    """Draw a frosted-glass rounded rectangle on the figure canvas."""
    radius = 0.014   # relative rounding

    # Wide ambient shadow plus a tighter coloured halo create real depth.
    wide_shadow = FancyBboxPatch(
        ((x - 4) / W, (H - y_top - h - 5) / H),
        (w + 8) / W, (h + 10) / H,
        boxstyle=f"round,pad=0,rounding_size={radius + 0.003}",
        transform=fig.transFigure,
        facecolor=(0.0, 0.0, 0.0, 0.19),
        edgecolor="none",
        zorder=zorder - 2,
    )
    fig.add_artist(wide_shadow)

    shadow = FancyBboxPatch(
        ((x - 2) / W, (H - y_top - h - 2) / H),
        (w + 4) / W, (h + 4) / H,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        transform=fig.transFigure,
        facecolor=GLASS_GLOW,
        edgecolor="none",
        zorder=zorder - 1,
    )
    fig.add_artist(shadow)

    # Glass body
    tile = FancyBboxPatch(
        (x / W, (H - y_top - h) / H),
        w / W, h / H,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        transform=fig.transFigure,
        facecolor=GLASS_FACE,
        edgecolor=GLASS_EDGE,
        linewidth=0.75,
        zorder=zorder,
    )
    fig.add_artist(tile)

    # Liquid reflection: a muted, broad highlight over the upper glass area.
    reflection = FancyBboxPatch(
        ((x + 1.5) / W, (H - y_top - h * 0.40) / H),
        (w - 3) / W, (h * 0.40 - 1.5) / H,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        transform=fig.transFigure,
        facecolor=(0.80, 0.90, 1.0, 0.025),
        edgecolor="none",
        zorder=zorder + 1,
    )
    fig.add_artist(reflection)

    # Inner highlight — crisp rim on top and left.
    highlight = FancyBboxPatch(
        (x / W, (H - y_top - 1.5) / H),
        w / W, 1.5 / H,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        transform=fig.transFigure,
        facecolor=(1, 1, 1, 0.06),
        edgecolor="none",
        zorder=zorder + 1,
    )
    fig.add_artist(highlight)

    left_rim = FancyBboxPatch(
        (x / W, (H - y_top - h + 3) / H),
        1.2 / W, (h - 6) / H,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        transform=fig.transFigure,
        facecolor=(1, 1, 1, 0.075),
        edgecolor="none",
        zorder=zorder + 1,
    )
    fig.add_artist(left_rim)

    # Coloured accent stripe at top
    if accent:
        stripe = FancyBboxPatch(
            (x / W, (H - y_top - 2.5) / H),
            w / W, 2.5 / H,
            boxstyle=f"round,pad=0,rounding_size={radius}",
            transform=fig.transFigure,
            facecolor=_rgba(accent, 0.90),
            edgecolor="none",
            zorder=zorder + 2,
        )
        fig.add_artist(stripe)


# ── Header ─────────────────────────────────────────────────────────────────────

def _draw_header(
    fig: plt.Figure,
    stats: InfoStats,
    avatar_bytes: bytes | None,
) -> None:
    _glass_tile(fig, PAD, HDR_Y, W - PAD * 2, HDR_H, zorder=2)

    ax = _ax(fig, PAD, HDR_Y, W - PAD * 2, HDR_H, zorder=5)
    tw = W - PAD * 2
    ax.set_xlim(0, tw)
    ax.set_ylim(0, HDR_H)
    ax.set_facecolor("none")
    ax.axis("off")

    AV_R  = 22
    AV_CX = AV_R + 14
    AV_CY = HDR_H / 2

    # Avatar glow ring
    ax.add_patch(Circle((AV_CX, AV_CY), AV_R + 5,
                        facecolor=_rgba("#4A9EFF", 0.10), edgecolor="none",
                        transform=ax.transData, zorder=2))
    # Avatar glass circle / current Telegram profile photo.
    ax.add_patch(Circle((AV_CX, AV_CY), AV_R,
                        facecolor=_rgba("#4A9EFF", 0.18),
                        edgecolor=_rgba("#4A9EFF", 0.50),
                        linewidth=1.2,
                        transform=ax.transData, zorder=3))
    avatar_drawn = False
    if avatar_bytes:
        try:
            with Image.open(io.BytesIO(avatar_bytes)) as source:
                avatar = source.convert("RGB")
                side = min(avatar.size)
                left = (avatar.width - side) // 2
                top = (avatar.height - side) // 2
                avatar = avatar.crop((left, top, left + side, top + side))
                avatar = avatar.resize((256, 256), Image.Resampling.LANCZOS)

            image = ax.imshow(
                np.asarray(avatar),
                extent=(
                    AV_CX - AV_R + 1.5,
                    AV_CX + AV_R - 1.5,
                    AV_CY - AV_R + 1.5,
                    AV_CY + AV_R - 1.5,
                ),
                interpolation="lanczos",
                zorder=4,
            )
            image.set_clip_path(
                Circle(
                    (AV_CX, AV_CY),
                    AV_R - 1.5,
                    transform=ax.transData,
                )
            )
            avatar_drawn = True
        except Exception:
            avatar_drawn = False

    if not avatar_drawn:
        ax.text(AV_CX, AV_CY, _initials(stats.contact_name),
                ha="center", va="center",
                color="#4A9EFF", fontsize=11, fontweight=700,
                fontfamily=_F(700)["fontfamily"], zorder=4)

    # Fine glossy crescent along the avatar rim.
    ax.add_patch(mpatches.Arc(
        (AV_CX, AV_CY), AV_R * 1.82, AV_R * 1.82,
        theta1=28, theta2=142,
        color=_rgba("#FFFFFF", 0.48),
        linewidth=0.9,
        transform=ax.transData,
        zorder=5,
    ))

    tx = AV_CX * 2 + 10
    name = (stats.contact_name[:34] + "…") if len(stats.contact_name) > 34 else stats.contact_name
    # Name
    ax.text(tx, AV_CY + 10, name,
            ha="left", va="center",
            color=C_TEXT, fontsize=12.5, fontweight=600,
            fontfamily=_F(600)["fontfamily"], zorder=4)

    # Period
    per  = _period_str(stats)
    days = _days_total(stats)
    sub  = f"{per}  ·  {days} дн." if per != "—" else f"{days} дн."
    ax.text(tx, AV_CY - 11, sub,
            ha="left", va="center",
            color=C_SUB, fontsize=8,
            fontfamily=_F(400)["fontfamily"], zorder=4)

    # Badges (right side)
    badges: list[str] = []
    if stats.note_count:
        badges.append(f"Заметки: {stats.note_count}")
    now_utc = dt.datetime.now(dt.timezone.utc)
    if stats.muted_until and stats.muted_until > now_utc:
        badges.append(f"Без звука до {stats.muted_until.strftime('%d.%m')}")
    if badges:
        ax.text(tw - 14, AV_CY, "  ·  ".join(badges),
                ha="right", va="center",
                color=C_SUB, fontsize=8,
                fontfamily=_F(400)["fontfamily"], zorder=4)

    # Thin separator dot between name column and badge column
    if badges:
        ax.axvline(tw - 110, ymin=0.2, ymax=0.8,
                   color=_rgba(C_DIM, 0.8), linewidth=0.6, zorder=3)


# ── Primary KPIs ───────────────────────────────────────────────────────────────

def _draw_primary_kpis(fig: plt.Figure, stats: InfoStats) -> None:
    avg  = _avg_per_day(stats)
    data = [
        (stats.total,    "Всего",   ACC[0]),
        (stats.outgoing, "Ваших",   ACC[1]),
        (stats.incoming, "Их",      ACC[2]),
        (avg,            "В день",  ACC[3]),
    ]
    full_w = W - PAD * 2
    n      = len(data)
    tile_w = (full_w - GAP * (n - 1)) / n

    for i, (val, label, color) in enumerate(data):
        x = PAD + i * (tile_w + GAP)
        _glass_tile(fig, x, PRI_Y, tile_w, PRI_H, accent=color, zorder=3)

        ax = _ax(fig, x, PRI_Y, tile_w, PRI_H, zorder=6)
        ax.set_xlim(0, tile_w)
        ax.set_ylim(0, PRI_H)
        ax.set_facecolor("none")
        ax.axis("off")

        # Subtle glow ellipse behind number
        ax.add_patch(Ellipse((tile_w / 2, PRI_H / 2 + 6),
                             tile_w * 0.65, 38,
                             facecolor=_rgba(color, 0.10),
                             edgecolor="none",
                             transform=ax.transData, zorder=1))

        # Big number
        val_str = _fmt_num(val)
        ax.text(tile_w / 2, PRI_H / 2 + 10, val_str,
                ha="center", va="center",
                color=color, fontsize=26, fontweight=700,
                fontfamily=_F(700)["fontfamily"], zorder=4)

        # Label
        ax.text(tile_w / 2, 16, label,
                ha="center", va="center",
                color=C_SUB, fontsize=8.5, fontweight=500,
                fontfamily=_F(500)["fontfamily"], zorder=4)


# ── Secondary KPIs ─────────────────────────────────────────────────────────────

def _draw_secondary_kpis(fig: plt.Figure, stats: InfoStats) -> None:
    data = [
        (stats.media_count, "Медиа",    ACC_S[0]),
        (stats.audio_count, "Аудио",    ACC_S[1]),
        (stats.edited,      "Изменено", ACC_S[2]),
        (stats.deleted,     "Удалено",  ACC_S[3]),
    ]
    n      = len(data)
    tile_w = (SEC_W - GAP * (n - 1)) / n

    for i, (val, label, color) in enumerate(data):
        x = PAD + i * (tile_w + GAP)
        _glass_tile(fig, x, BOT_Y, tile_w, BOT_H, zorder=3)

        ax = _ax(fig, x, BOT_Y, tile_w, BOT_H, zorder=6)
        ax.set_xlim(0, tile_w)
        ax.set_ylim(0, BOT_H)
        ax.set_facecolor("none")
        ax.axis("off")

        # Coloured accent dot
        ax.add_patch(Circle((tile_w / 2, BOT_H - 20), 4,
                            facecolor=color, edgecolor="none",
                            transform=ax.transData, zorder=3))

        # Number
        val_str = _fmt_num(val)
        ax.text(tile_w / 2, BOT_H / 2 + 4, val_str,
                ha="center", va="center",
                color=C_TEXT, fontsize=20, fontweight=700,
                fontfamily=_F(700)["fontfamily"], zorder=4)

        # Label
        ax.text(tile_w / 2, 14, label,
                ha="center", va="center",
                color=C_SUB, fontsize=8,
                fontfamily=_F(400)["fontfamily"], zorder=4)

        # Thin horizontal accent rule under label
        rule_w = tile_w * 0.35
        ax.axhline(22, xmin=(tile_w / 2 - rule_w / 2) / tile_w,
                   xmax=(tile_w / 2 + rule_w / 2) / tile_w,
                   color=_rgba(color, 0.50), linewidth=1.0, zorder=3)


# ── Conversation ring ──────────────────────────────────────────────────────────

def _draw_ring(fig: plt.Figure, stats: InfoStats) -> None:
    _glass_tile(fig, RING_X, BOT_Y, RING_W, BOT_H, zorder=3)

    ax = _ax(fig, RING_X, BOT_Y, RING_W, BOT_H, zorder=6)
    ax.set_facecolor("none")
    ax.set_zorder(6)
    ax.axis("equal")

    total = stats.incoming + stats.outgoing
    fd7   = _F(700)
    fd4   = _F(400)
    fd5   = _F(500)

    if total == 0:
        ax.pie([1], colors=[_rgba(C_DIM, 0.4)], startangle=90,
               wedgeprops=dict(width=0.38, edgecolor=_rgba(BG, 0.5), linewidth=2))
        ax.text(0, 0.10, "—",
                ha="center", va="center",
                color=C_SUB, fontsize=20, fontweight=700,
                fontfamily=fd7["fontfamily"])
        ax.text(0, -0.28, "нет данных",
                ha="center", va="center",
                color=C_SUB, fontsize=7,
                fontfamily=fd4["fontfamily"])
        ax.set_xlim(-1.55, 1.55)
        ax.set_ylim(-1.65, 1.55)
        return

    in_pct  = round(stats.incoming / total * 100)
    out_pct = 100 - in_pct

    # Donut
    wedges, _ = ax.pie(
        [max(stats.incoming, 0), max(stats.outgoing, 0)],
        colors=[C_IN, C_OUT],
        startangle=90,
        counterclock=False,
        wedgeprops=dict(width=0.38,
                        edgecolor=_rgba("#08101F", 0.80),
                        linewidth=2.5),
    )

    # Glow ring (fake) — very subtle outer halo
    ax.add_patch(Circle((0, 0), 1.06,
                        facecolor="none",
                        edgecolor=_rgba(C_IN, 0.12),
                        linewidth=4,
                        transform=ax.transData, zorder=0))

    # Centre label
    ax.text(0, 0.17, f"{in_pct}%",
            ha="center", va="center",
            color=C_TEXT, fontsize=17, fontweight=700,
            fontfamily=fd7["fontfamily"])
    ax.text(0, -0.18, "вход.",
            ha="center", va="center",
            color=C_SUB, fontsize=7,
            fontfamily=fd4["fontfamily"])

    # Legend
    ax.legend(
        handles=[
            mpatches.Patch(color=C_IN,  label=f"↓ {_fmt_num(stats.incoming)}"),
            mpatches.Patch(color=C_OUT, label=f"↑ {_fmt_num(stats.outgoing)}"),
        ],
        loc="lower center",
        ncol=2,
        fontsize=7.5,
        framealpha=0,
        labelcolor=C_TEXT,
        bbox_to_anchor=(0.5, -0.28),
        handlelength=0.85,
        handleheight=0.85,
        handletextpad=0.35,
        columnspacing=0.6,
        prop={"family": fd5["fontfamily"], "weight": 500, "size": 7.5},
    )

    ax.set_xlim(-1.55, 1.55)
    ax.set_ylim(-1.70, 1.65)


# ── Utilities ──────────────────────────────────────────────────────────────────

def _initials(name: str) -> str:
    parts = name.strip().split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return name[:2].upper() if name else "??"


def _period_str(stats: InfoStats) -> str:
    if stats.first_seen and stats.last_seen:
        return (f"{stats.first_seen.strftime('%d.%m.%y')}"
                f" — {stats.last_seen.strftime('%d.%m.%y')}")
    return "—"


def _days_total(stats: InfoStats) -> int:
    if stats.first_seen and stats.last_seen:
        return max(1, (stats.last_seen - stats.first_seen).days)
    return 1


def _avg_per_day(stats: InfoStats) -> float:
    return round(stats.total / _days_total(stats), 1)


def _fmt_num(v: float | int) -> str:
    if isinstance(v, float) and v != int(v):
        return f"{v:.1f}"
    n = int(v)
    return f"{n:,}".replace(",", "\u2009")   # thin-space thousands separator
