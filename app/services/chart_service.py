"""chart_service.py — premium stats card for the !info command.

Renders a 1080 × 1920 PNG (portrait) with:
  • Header   — avatar circle with initials, contact name, date range
  • Stat grid — 4 KPI cards (total / yours / theirs / avg per day)
  • Donut     — inbound vs outbound split
  • Bar chart — 30-day daily activity (inbound + outbound)
  • Footer    — disclaimer

Color palette / typography follow the design spec supplied by the user.
The font is Inter (bundled in app/assets/fonts/); falls back to DejaVu Sans.
"""

from __future__ import annotations

import datetime as dt
import io
import os
from pathlib import Path
from typing import NamedTuple

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.patches as mpatches
import matplotlib.ticker as mticker
import numpy as np
from matplotlib.patches import FancyBboxPatch, Circle

# ── Font registration ─────────────────────────────────────────────────────────

_FONTS_DIR = Path(__file__).parent.parent / "assets" / "fonts"
_FONT_REGISTERED = False

def _ensure_fonts() -> None:
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return
    for ttf in _FONTS_DIR.glob("Inter-*.ttf"):
        fm.fontManager.addfont(str(ttf))
    _FONT_REGISTERED = True

def _font(weight: int = 400) -> dict:
    """Return fontdict for fig.text / ax.text."""
    _ensure_fonts()
    names = fm.fontManager.get_font_names()
    family = "Inter" if "Inter" in names else "DejaVu Sans"
    return {"fontfamily": family, "fontweight": weight}

# ── Palette ───────────────────────────────────────────────────────────────────

BG_PAGE   = "#F8F9FC"
BG_CARD   = "#FFFFFF"
C_PRIMARY = "#7C5CFF"
C_BLUE    = "#5AA7FF"
C_GREEN   = "#4ADE80"
C_AMBER   = "#F59E0B"
C_RED     = "#EF4444"
C_TEXT    = "#111827"
C_HINT    = "#6B7280"
C_BORDER  = "#E5E7EB"
C_SHADOW  = "#C8CDD8"

# ── Canvas ────────────────────────────────────────────────────────────────────

DPI   = 100
W_PX  = 1080
H_PX  = 1920
PAD_X = 36          # horizontal page padding
PAD_TOP = 52        # top page padding

# ── Public data contract (unchanged) ─────────────────────────────────────────

class InfoStats(NamedTuple):
    contact_name: str
    total:    int
    incoming: int
    outgoing: int
    deleted:  int
    edited:   int
    first_seen:  dt.datetime | None
    last_seen:   dt.datetime | None
    note_count:  int
    muted_until: dt.datetime | None
    daily: list[tuple[str, int, int]]   # (label "dd.mm", inbound, outbound)


# ── Main entry point ──────────────────────────────────────────────────────────

def render_info_image(stats: InfoStats) -> io.BytesIO:
    """Return a 1080 × 1920 PNG stats card as BytesIO."""
    _ensure_fonts()

    fig = plt.figure(figsize=(W_PX / DPI, H_PX / DPI), facecolor=BG_PAGE, dpi=DPI)
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)

    # ── Layout constants (px from top-left) ──────────────────────────────────
    CW = W_PX - PAD_X * 2   # card width = 1008
    GAP_CARD  = 24
    GAP_STATS = 16
    STAT_W = (CW - GAP_STATS) // 2   # 496
    STAT_H = 185
    R = 0.010   # card corner rounding in figure-fraction units

    y = PAD_TOP

    # 1. Header card
    HDR_H = 290
    _card(fig, PAD_X, y, CW, HDR_H, R)
    _draw_header(fig, stats, PAD_X, y, CW, HDR_H)
    y += HDR_H + GAP_CARD

    # 2. Stat grid (2 × 2)
    avg = _avg_per_day(stats)
    kpis = [
        (stats.total,    "Всего сообщений",    C_PRIMARY),
        (stats.outgoing, "Ваших сообщений",    C_BLUE),
        (stats.incoming, "Сообщений контакта", C_GREEN),
        (avg,            "В среднем в день",   C_AMBER),
    ]
    for row in range(2):
        for col in range(2):
            sx = PAD_X + col * (STAT_W + GAP_STATS)
            sy = y + row * (STAT_H + GAP_STATS)
            val, lbl, clr = kpis[row * 2 + col]
            _card(fig, sx, sy, STAT_W, STAT_H, R)
            _draw_stat_card(fig, sx, sy, STAT_W, STAT_H, val, lbl, clr)
    y += STAT_H * 2 + GAP_STATS + GAP_CARD

    # 3. Donut card
    DONUT_H = 370
    _card(fig, PAD_X, y, CW, DONUT_H, R)
    _section_title(fig, PAD_X + 28, y + 28, "Распределение сообщений")
    ax_d = _add_axes(fig, PAD_X + 32, y + 56, CW - 64, DONUT_H - 70)
    _draw_donut(ax_d, stats)
    y += DONUT_H + GAP_CARD

    # 4. Bar chart card
    BAR_H = H_PX - y - PAD_TOP - 44   # fill remaining space
    _card(fig, PAD_X, y, CW, BAR_H, R)
    _section_title(fig, PAD_X + 28, y + 28, "Активность за 30 дней")
    ax_b = _add_axes(fig, PAD_X + 32, y + 64, CW - 64, BAR_H - 90)
    _draw_bars(ax_b, stats)
    y += BAR_H + GAP_CARD

    # 5. Footer
    _fig_text(fig, W_PX / 2, (y + H_PX) / 2,
              "Данные учитываются с момента подключения бота",
              ha="center", va="center", fontsize=11, color=C_HINT,
              fontstyle="italic")

    buf = io.BytesIO()
    try:
        fig.savefig(buf, format="png", facecolor=BG_PAGE, dpi=DPI)
    finally:
        plt.close(fig)
    buf.seek(0)
    return buf


# ── Layout helpers ────────────────────────────────────────────────────────────

def _fx(px: float) -> float:
    return px / W_PX

def _fy(px_from_top: float, h_px: float = 0) -> float:
    """Figure-fraction y for the *bottom* of a region."""
    return (H_PX - px_from_top - h_px) / H_PX

def _fw(px: float) -> float:
    return px / W_PX

def _fh(px: float) -> float:
    return px / H_PX

def _add_axes(fig: plt.Figure, x: float, y_top: float,
              w: float, h: float) -> plt.Axes:
    """Add axes at pixel coordinates (y_top = px from top of canvas)."""
    return fig.add_axes([_fx(x), _fy(y_top, h), _fw(w), _fh(h)])

def _fig_text(fig: plt.Figure, x_px: float, y_top_px: float,
              text: str, **kwargs) -> None:
    fd = _font(kwargs.pop("fontweight", 400))
    fig.text(_fx(x_px), _fy(y_top_px), text,
             transform=fig.transFigure,
             fontfamily=fd["fontfamily"],
             **kwargs)

def _section_title(fig: plt.Figure, x_px: float, y_top_px: float,
                   text: str) -> None:
    fd = _font(600)
    fig.text(_fx(x_px), _fy(y_top_px), text,
             transform=fig.transFigure,
             color=C_TEXT, fontsize=15,
             fontfamily=fd["fontfamily"], fontweight=600,
             va="top")


# ── Card background ───────────────────────────────────────────────────────────

def _card(fig: plt.Figure, x: float, y_top: float,
          w: float, h: float, r: float) -> None:
    """Draw a white rounded card with a soft drop-shadow."""
    tr = fig.transFigure

    # Shadow (offset 0, -6px; slightly larger; low alpha)
    shadow_expand = 4
    shadow_offset = 6
    shadow = FancyBboxPatch(
        (_fx(x - shadow_expand),
         _fy(y_top + shadow_offset, h + shadow_expand * 2)),
        _fw(w + shadow_expand * 2),
        _fh(h + shadow_expand * 2),
        boxstyle=f"round,pad=0,rounding_size={r * 1.1}",
        transform=tr,
        facecolor=C_SHADOW, edgecolor="none",
        linewidth=0, alpha=0.18, zorder=1,
    )
    fig.add_artist(shadow)

    # Card face
    face = FancyBboxPatch(
        (_fx(x), _fy(y_top, h)),
        _fw(w), _fh(h),
        boxstyle=f"round,pad=0,rounding_size={r}",
        transform=tr,
        facecolor=BG_CARD,
        edgecolor=C_BORDER, linewidth=0.6,
        zorder=2,
    )
    fig.add_artist(face)


# ── Header section ────────────────────────────────────────────────────────────

def _draw_header(fig: plt.Figure, stats: InfoStats,
                 x: float, y_top: float, w: float, h: float) -> None:
    ax = _add_axes(fig, x, y_top, w, h)
    ax.set_xlim(0, w)
    ax.set_ylim(0, h)
    ax.set_facecolor("none")
    ax.axis("off")
    ax.set_zorder(3)

    INNER = 32

    # Avatar circle
    AV_R = 52
    cx, cy = INNER + AV_R, h - INNER - AV_R
    circle = Circle((cx, cy), AV_R,
                    facecolor=C_PRIMARY, edgecolor="none",
                    transform=ax.transData, zorder=4)
    ax.add_patch(circle)

    # Initials
    initials = _initials(stats.contact_name)
    fd = _font(700)
    ax.text(cx, cy, initials,
            ha="center", va="center",
            color="white", fontsize=22, fontweight=700,
            fontfamily=fd["fontfamily"], zorder=5)

    # Name
    tx = cx * 2 + 8
    fd600 = _font(600)
    ax.text(tx, cy + 16, stats.contact_name,
            ha="left", va="center",
            color=C_TEXT, fontsize=20, fontweight=600,
            fontfamily=fd600["fontfamily"], zorder=4)

    # Subtitle line: period
    period_str = _period_str(stats)
    fd400 = _font(400)
    ax.text(tx, cy - 14, period_str,
            ha="left", va="center",
            color=C_HINT, fontsize=12,
            fontfamily=fd400["fontfamily"], zorder=4)

    # Days badge
    days = _days_total(stats)
    badge_label = f"{days} дн."
    badge_x = w - INNER - 10
    badge_y = h - INNER - 14
    badge_w, badge_h = 90, 32
    badge = FancyBboxPatch(
        (badge_x - badge_w, badge_y - badge_h / 2),
        badge_w, badge_h,
        boxstyle="round,pad=0,rounding_size=10",
        transform=ax.transData,
        facecolor=_hex_alpha(C_PRIMARY, 0.10),
        edgecolor=_hex_alpha(C_PRIMARY, 0.25),
        linewidth=1, zorder=4,
    )
    ax.add_patch(badge)
    ax.text(badge_x - badge_w / 2, badge_y,
            badge_label, ha="center", va="center",
            color=C_PRIMARY, fontsize=12, fontweight=600,
            fontfamily=fd600["fontfamily"], zorder=5)

    # Divider
    div_y = h - INNER * 2 - AV_R * 2 - 12
    ax.axhline(div_y, xmin=INNER / w, xmax=(w - INNER) / w,
               color=C_BORDER, linewidth=0.8, zorder=3)

    # Bottom row: message count + notes + mute indicators
    parts = [f"{stats.total} сообщений"]
    if stats.note_count:
        parts.append(f"{stats.note_count} заметок")
    if stats.muted_until and stats.muted_until > dt.datetime.now(dt.timezone.utc):
        parts.append(f"откл. до {stats.muted_until.strftime('%d.%m %H:%M')}")

    bottom_y = div_y - 28
    ax.text(INNER, bottom_y, "  •  ".join(parts),
            ha="left", va="center",
            color=C_HINT, fontsize=12,
            fontfamily=fd400["fontfamily"], zorder=4)


# ── Stat KPI card ─────────────────────────────────────────────────────────────

def _draw_stat_card(fig: plt.Figure,
                    x: float, y_top: float, w: float, h: float,
                    value: float | int, label: str, color: str) -> None:
    ax = _add_axes(fig, x, y_top, w, h)
    ax.set_xlim(0, w)
    ax.set_ylim(0, h)
    ax.set_facecolor("none")
    ax.axis("off")
    ax.set_zorder(3)

    fd700 = _font(700)
    fd400 = _font(400)
    fd600 = _font(600)

    # Accent dot (icon substitute)
    dot = Circle((28, h - 28), 10,
                 facecolor=_hex_alpha(color, 0.15),
                 edgecolor=color, linewidth=1.5,
                 transform=ax.transData, zorder=4)
    ax.add_patch(dot)
    # Inner dot
    dot2 = Circle((28, h - 28), 4,
                  facecolor=color, edgecolor="none",
                  transform=ax.transData, zorder=5)
    ax.add_patch(dot2)

    # Label (top)
    ax.text(48, h - 28, label,
            ha="left", va="center",
            color=C_HINT, fontsize=11,
            fontfamily=fd400["fontfamily"], zorder=4)

    # Value (center, large)
    val_str = _fmt_value(value)
    ax.text(w / 2, h / 2 + 8, val_str,
            ha="center", va="center",
            color=color, fontsize=32, fontweight=700,
            fontfamily=fd700["fontfamily"], zorder=4)

    # Color bar at bottom
    bar = FancyBboxPatch(
        (24, 18), w - 48, 6,
        boxstyle="round,pad=0,rounding_size=3",
        transform=ax.transData,
        facecolor=_hex_alpha(color, 0.18),
        edgecolor="none", zorder=4,
    )
    ax.add_patch(bar)
    filled_w = max(8, (w - 48) * min(1.0, value / max(1, value + 1)))
    bar2 = FancyBboxPatch(
        (24, 18), filled_w, 6,
        boxstyle="round,pad=0,rounding_size=3",
        transform=ax.transData,
        facecolor=color, edgecolor="none", alpha=0.7, zorder=5,
    )
    ax.add_patch(bar2)


# ── Donut chart ───────────────────────────────────────────────────────────────

def _draw_donut(ax: plt.Axes, stats: InfoStats) -> None:
    ax.set_facecolor("none")
    ax.set_zorder(3)
    ax.axis("equal")

    total = stats.incoming + stats.outgoing or 1
    sizes  = [max(0, stats.incoming), max(0, stats.outgoing)]
    colors = [C_BLUE, C_PRIMARY]
    labels = ["Входящие", "Исходящие"]

    wedges, _ = ax.pie(
        sizes,
        colors=colors,
        startangle=90,
        counterclock=False,
        wedgeprops=dict(width=0.52, edgecolor="white", linewidth=3),
    )

    fd = _font(700)
    # Center percentage
    in_pct = round(stats.incoming / total * 100)
    ax.text(0, 0.08, f"{in_pct}%",
            ha="center", va="center",
            color=C_TEXT, fontsize=26, fontweight=700,
            fontfamily=fd["fontfamily"])
    fd2 = _font(400)
    ax.text(0, -0.22, "входящих",
            ha="center", va="center",
            color=C_HINT, fontsize=11,
            fontfamily=fd2["fontfamily"])

    # Legend (bottom)
    legend_patches = [
        mpatches.Patch(color=c, label=f"{l}  {v}")
        for c, l, v in zip(colors, labels, sizes)
    ]
    legend = ax.legend(
        handles=legend_patches,
        loc="lower center",
        ncol=2,
        fontsize=12,
        framealpha=0,
        labelcolor=C_TEXT,
        bbox_to_anchor=(0.5, -0.18),
        handlelength=1.2,
        handleheight=1.0,
    )
    ax.set_xlim(-1.4, 1.4)


# ── Bar chart ─────────────────────────────────────────────────────────────────

def _draw_bars(ax: plt.Axes, stats: InfoStats) -> None:
    ax.set_facecolor("none")
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_zorder(3)

    if not stats.daily:
        fd = _font(400)
        ax.text(0.5, 0.5, "Нет данных за последние 30 дней",
                ha="center", va="center", transform=ax.transAxes,
                color=C_HINT, fontsize=13, fontfamily=fd["fontfamily"])
        ax.set_xticks([])
        ax.set_yticks([])
        return

    labels   = [d[0] for d in stats.daily]
    inbound  = np.array([d[1] for d in stats.daily], dtype=float)
    outbound = np.array([d[2] for d in stats.daily], dtype=float)
    x = np.arange(len(labels))
    n = len(labels)

    BAR_W = 0.30
    GAP   = 0.08

    # Draw rounded bars (extend below 0 to clip bottom corners)
    ax.set_ylim(0, max(max(inbound), max(outbound), 1) * 1.22)
    ax.set_xlim(-0.7, n - 0.3)

    for xi, (hi, ho) in enumerate(zip(inbound, outbound)):
        for val, x_off, color in [(hi, -BAR_W / 2 - GAP / 2, C_BLUE),
                                   (ho,  GAP / 2,             C_PRIMARY)]:
            if val > 0:
                r = BAR_W * 0.48  # nearly semicircular top
                p = FancyBboxPatch(
                    (xi + x_off, -r),        # extend below 0
                    BAR_W, val + r,           # extra height compensates
                    boxstyle=f"round,pad=0,rounding_size={r}",
                    facecolor=color,
                    edgecolor="none",
                    linewidth=0,
                    alpha=0.88,
                    clip_on=True,
                    zorder=3,
                )
                ax.add_patch(p)

    # Grid
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True, nbins=4))
    ax.yaxis.grid(True, color=C_BORDER, linewidth=0.6, alpha=0.7, zorder=0)
    ax.set_axisbelow(True)

    # Tick styling
    tick_labels = [labels[i] if (n <= 15 or i % 2 == 0) else ""
                   for i in range(n)]
    ax.set_xticks(x)
    ax.set_xticklabels(tick_labels, rotation=45, ha="right",
                       fontsize=9, color=C_HINT)
    ax.tick_params(axis="x", length=0, pad=6)
    ax.tick_params(axis="y", colors=C_HINT, labelsize=9, length=0, pad=8)

    # Legend
    fd = _font(400)
    ax.legend(
        handles=[
            mpatches.Patch(color=C_BLUE,    label="Входящие"),
            mpatches.Patch(color=C_PRIMARY, label="Исходящие"),
        ],
        loc="upper right",
        fontsize=10,
        framealpha=0,
        labelcolor=C_HINT,
        handlelength=1,
    )

    # Apply font family to tick labels
    try:
        _name = _font(400)["fontfamily"]
        for lbl in ax.get_xticklabels() + ax.get_yticklabels():
            lbl.set_fontfamily(_name)
    except Exception:
        pass


# ── Utility ───────────────────────────────────────────────────────────────────

def _initials(name: str) -> str:
    parts = name.strip().split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return name[:2].upper() if name else "??"


def _period_str(stats: InfoStats) -> str:
    if stats.first_seen and stats.last_seen:
        f = stats.first_seen.strftime("%d.%m.%Y")
        l = stats.last_seen.strftime("%d.%m.%Y")
        return f"{f} — {l}"
    return "Период неизвестен"


def _days_total(stats: InfoStats) -> int:
    if stats.first_seen and stats.last_seen:
        return max(1, (stats.last_seen - stats.first_seen).days)
    return 1


def _avg_per_day(stats: InfoStats) -> float:
    days = _days_total(stats)
    return round(stats.total / days, 1)


def _fmt_value(v: float | int) -> str:
    if isinstance(v, float) and v != int(v):
        return f"{v:.1f}"
    return str(int(v))


def _hex_alpha(hex_color: str, alpha: float) -> tuple:
    """Convert #RRGGBB + alpha to (r, g, b, a) for matplotlib."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255
    return (r, g, b, alpha)
