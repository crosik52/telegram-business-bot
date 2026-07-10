"""admin_chart_service.py — admin overview infographic (1080 × 1920 PNG).

Layout:
  1. Header card  — "Статистика бота", date, total-users badge
  2. KPI grid 3×2 — users / active / blocked / avg msgs / total msgs / coins
  3. Growth chart  — 30-day messages (bars) + new connections (line/dots)
  4. Top-5 users   — ranked list with relative message bar
  5. Footer

Palette and typography match chart_service.py (same design system).
"""

from __future__ import annotations

import datetime as dt
import io
from typing import NamedTuple

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.patches as mpatches
import matplotlib.ticker as mticker
import numpy as np
from matplotlib.patches import FancyBboxPatch, Circle

# ── Font registration (shared with chart_service) ────────────────────────────

from pathlib import Path

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
C_INDIGO  = "#6366F1"
C_PINK    = "#EC4899"
C_TEXT    = "#111827"
C_HINT    = "#6B7280"
C_BORDER  = "#E5E7EB"
C_SHADOW  = "#C8CDD8"

# ── Canvas ────────────────────────────────────────────────────────────────────

DPI   = 100
W_PX  = 1080
H_PX  = 1920
PAD_X = 36
PAD_TOP = 52


# ── Data contract ─────────────────────────────────────────────────────────────

class AdminStats(NamedTuple):
    generated_at:  dt.datetime
    total_users:   int
    active_users:  int           # is_enabled and not is_blocked
    blocked_users: int
    total_messages: int
    avg_messages:  float         # per user
    total_coins:   int           # sum of wallet_balance
    media_pct:     int           # % of media messages from total
    # 30-day daily series: (label "dd.mm", messages, new_connections)
    growth: list[tuple[str, int, int]]
    # top users: (display_name, total_messages, total_chats, wallet_balance)
    top_users: list[tuple[str, int, int, int]]


# ── Main entry point ──────────────────────────────────────────────────────────

def render_admin_image(stats: AdminStats) -> io.BytesIO:
    """Return a 1080 × 1920 PNG admin overview card as BytesIO."""
    _ensure_fonts()

    fig = plt.figure(figsize=(W_PX / DPI, H_PX / DPI), facecolor=BG_PAGE, dpi=DPI)
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)

    CW       = W_PX - PAD_X * 2   # 1008
    GAP_CARD = 20
    GAP_STAT = 14
    STAT_W   = (CW - GAP_STAT) // 2
    STAT_H   = 162
    R        = 0.010

    y = PAD_TOP

    # ── 1. Header ────────────────────────────────────────────────────────────
    HDR_H = 240
    _card(fig, PAD_X, y, CW, HDR_H, R)
    _draw_header(fig, stats, PAD_X, y, CW, HDR_H)
    y += HDR_H + GAP_CARD

    # ── 2. KPI grid 3×2 ──────────────────────────────────────────────────────
    kpis = [
        (stats.total_users,   "Всего пользователей", C_PRIMARY),
        (stats.active_users,  "Активных",            C_GREEN),
        (stats.blocked_users, "Заблокировано",       C_RED),
        (_fmt_big(stats.avg_messages), "Среднее сообщ./юзер", C_BLUE),
        (_fmt_big(stats.total_messages), "Всего сообщений",   C_INDIGO),
        (_fmt_big(stats.total_coins),    "Монет в обороте",   C_AMBER),
    ]
    for row in range(3):
        for col in range(2):
            sx = PAD_X + col * (STAT_W + GAP_STAT)
            sy = y + row * (STAT_H + GAP_STAT)
            val, lbl, clr = kpis[row * 2 + col]
            _card(fig, sx, sy, STAT_W, STAT_H, R)
            _draw_stat_card(fig, sx, sy, STAT_W, STAT_H, val, lbl, clr)
    y += STAT_H * 3 + GAP_STAT * 2 + GAP_CARD

    # ── 3. Growth chart ───────────────────────────────────────────────────────
    GROWTH_H = 390
    _card(fig, PAD_X, y, CW, GROWTH_H, R)
    _section_title(fig, PAD_X + 28, y + 26, "Активность за 30 дней")
    ax_g = _add_axes(fig, PAD_X + 32, y + 60, CW - 64, GROWTH_H - 84)
    _draw_growth(ax_g, stats)
    y += GROWTH_H + GAP_CARD

    # ── 4. Top users ──────────────────────────────────────────────────────────
    TOP_H = H_PX - y - PAD_TOP - 40
    _card(fig, PAD_X, y, CW, TOP_H, R)
    _section_title(fig, PAD_X + 28, y + 26, "Топ пользователей")
    ax_t = _add_axes(fig, PAD_X + 20, y + 56, CW - 40, TOP_H - 72)
    _draw_top_users(ax_t, stats)
    y += TOP_H + GAP_CARD

    # ── Footer ────────────────────────────────────────────────────────────────
    fd = _font(400)
    fig.text(0.5, _fy((y + H_PX) / 2),
             f"Сформировано {stats.generated_at.strftime('%d.%m.%Y %H:%M')} UTC",
             ha="center", va="center",
             color=C_HINT, fontsize=11, fontstyle="italic",
             fontfamily=fd["fontfamily"],
             transform=fig.transFigure)

    buf = io.BytesIO()
    try:
        fig.savefig(buf, format="png", facecolor=BG_PAGE, dpi=DPI)
    finally:
        plt.close(fig)
    buf.seek(0)
    return buf


# ── Layout helpers ────────────────────────────────────────────────────────────

def _fx(px): return px / W_PX
def _fy(px_from_top, h=0): return (H_PX - px_from_top - h) / H_PX
def _fw(px): return px / W_PX
def _fh(px): return px / H_PX

def _add_axes(fig, x, y_top, w, h):
    return fig.add_axes([_fx(x), _fy(y_top, h), _fw(w), _fh(h)])

def _section_title(fig, x_px, y_top_px, text):
    fd = _font(600)
    fig.text(_fx(x_px), _fy(y_top_px), text,
             transform=fig.transFigure,
             color=C_TEXT, fontsize=15,
             fontfamily=fd["fontfamily"], fontweight=600, va="top")


# ── Card background ───────────────────────────────────────────────────────────

def _card(fig, x, y_top, w, h, r):
    tr = fig.transFigure
    shadow_expand, shadow_offset = 4, 6
    fig.add_artist(FancyBboxPatch(
        (_fx(x - shadow_expand), _fy(y_top + shadow_offset, h + shadow_expand * 2)),
        _fw(w + shadow_expand * 2), _fh(h + shadow_expand * 2),
        boxstyle=f"round,pad=0,rounding_size={r*1.1}",
        transform=tr, facecolor=C_SHADOW, edgecolor="none",
        linewidth=0, alpha=0.18, zorder=1,
    ))
    fig.add_artist(FancyBboxPatch(
        (_fx(x), _fy(y_top, h)), _fw(w), _fh(h),
        boxstyle=f"round,pad=0,rounding_size={r}",
        transform=tr, facecolor=BG_CARD,
        edgecolor=C_BORDER, linewidth=0.6, zorder=2,
    ))


# ── Header ────────────────────────────────────────────────────────────────────

def _draw_header(fig, stats: AdminStats, x, y_top, w, h):
    ax = _add_axes(fig, x, y_top, w, h)
    ax.set_xlim(0, w); ax.set_ylim(0, h)
    ax.set_facecolor("none"); ax.axis("off"); ax.set_zorder(3)

    INNER = 32

    # Bot icon circle
    cx, cy = INNER + 44, h - INNER - 44
    ax.add_patch(Circle((cx, cy), 44, facecolor=C_PRIMARY, edgecolor="none",
                         transform=ax.transData, zorder=4))
    fd7 = _font(700)
    ax.text(cx, cy, "БОТ", ha="center", va="center",
            color="white", fontsize=14, fontweight=700,
            fontfamily=fd7["fontfamily"], zorder=5)

    # Title + date
    fd6 = _font(600); fd4 = _font(400)
    ax.text(cx * 2 + 8, cy + 16, "Статистика бота",
            ha="left", va="center", color=C_TEXT, fontsize=22, fontweight=600,
            fontfamily=fd6["fontfamily"], zorder=4)
    _MONTHS_RU = ["янв","фев","мар","апр","мая","июн",
                  "июл","авг","сен","окт","ноя","дек"]
    date_str = f"{stats.generated_at.day} {_MONTHS_RU[stats.generated_at.month-1]} {stats.generated_at.year}"
    ax.text(cx * 2 + 8, cy - 16, date_str,
            ha="left", va="center", color=C_HINT, fontsize=13,
            fontfamily=fd4["fontfamily"], zorder=4)

    # Users badge (top-right)
    badge_label = f"{stats.total_users} польз."
    bw, bh = 110, 32
    bx = w - INNER - 8
    by = h - INNER - 14
    ax.add_patch(FancyBboxPatch(
        (bx - bw, by - bh / 2), bw, bh,
        boxstyle="round,pad=0,rounding_size=10",
        transform=ax.transData,
        facecolor=_rgba(C_PRIMARY, 0.10), edgecolor=_rgba(C_PRIMARY, 0.25),
        linewidth=1, zorder=4,
    ))
    ax.text(bx - bw / 2, by, badge_label, ha="center", va="center",
            color=C_PRIMARY, fontsize=12, fontweight=600,
            fontfamily=fd6["fontfamily"], zorder=5)

    # Divider + summary line
    div_y = h - INNER * 2 - 88 - 12
    ax.axhline(div_y, xmin=INNER/w, xmax=(w-INNER)/w,
               color=C_BORDER, linewidth=0.8, zorder=3)

    active_pct = round(stats.active_users / max(1, stats.total_users) * 100)
    parts = [
        f"{stats.total_messages:,} сообщений".replace(",", " "),
        f"{active_pct}% активных",
        f"{stats.media_pct}% медиа",
    ]
    ax.text(INNER, div_y - 26, "  •  ".join(parts),
            ha="left", va="center", color=C_HINT, fontsize=12,
            fontfamily=fd4["fontfamily"], zorder=4)


# ── KPI stat card ─────────────────────────────────────────────────────────────

def _draw_stat_card(fig, x, y_top, w, h, value, label, color):
    ax = _add_axes(fig, x, y_top, w, h)
    ax.set_xlim(0, w); ax.set_ylim(0, h)
    ax.set_facecolor("none"); ax.axis("off"); ax.set_zorder(3)

    fd7 = _font(700); fd4 = _font(400)

    # Accent dot
    ax.add_patch(Circle((28, h-28), 10, facecolor=_rgba(color, 0.15),
                         edgecolor=color, linewidth=1.5, transform=ax.transData, zorder=4))
    ax.add_patch(Circle((28, h-28), 4,  facecolor=color, edgecolor="none",
                         transform=ax.transData, zorder=5))

    ax.text(48, h-28, label, ha="left", va="center",
            color=C_HINT, fontsize=11, fontfamily=fd4["fontfamily"], zorder=4)

    val_str = value if isinstance(value, str) else str(value)
    val_fs  = 22 if len(val_str) > 7 else 30
    ax.text(w/2, h/2+8, val_str, ha="center", va="center",
            color=color, fontsize=val_fs, fontweight=700,
            fontfamily=fd7["fontfamily"], zorder=4)

    # Bottom progress bar (cosmetic — always partial fill)
    try:
        num_val = float(str(value).replace(" ", "").replace(",", "")) if not isinstance(value, (int, float)) else float(value)
    except ValueError:
        num_val = 0
    fill = min(1.0, num_val / max(1, num_val + 1)) if num_val >= 0 else 0.5
    ax.add_patch(FancyBboxPatch((24, 18), w-48, 6,
                                 boxstyle="round,pad=0,rounding_size=3",
                                 transform=ax.transData,
                                 facecolor=_rgba(color, 0.15), edgecolor="none", zorder=4))
    ax.add_patch(FancyBboxPatch((24, 18), max(8, (w-48)*fill), 6,
                                 boxstyle="round,pad=0,rounding_size=3",
                                 transform=ax.transData,
                                 facecolor=color, edgecolor="none", alpha=0.7, zorder=5))


# ── Growth chart ──────────────────────────────────────────────────────────────

def _draw_growth(ax, stats: AdminStats):
    ax.set_facecolor("none")
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_zorder(3)

    if not stats.growth:
        ax.text(0.5, 0.5, "Нет данных", ha="center", va="center",
                transform=ax.transAxes, color=C_HINT, fontsize=13)
        ax.set_xticks([]); ax.set_yticks([])
        return

    labels = [g[0] for g in stats.growth]
    msgs   = np.array([g[1] for g in stats.growth], dtype=float)
    conns  = np.array([g[2] for g in stats.growth], dtype=float)
    x = np.arange(len(labels))
    n = len(labels)
    BAR_W = 0.45

    # Messages bars
    max_msg = max(max(msgs), 1)
    ax.set_ylim(0, max_msg * 1.25)
    ax.set_xlim(-0.7, n - 0.3)

    for xi, mv in enumerate(msgs):
        if mv > 0:
            r = BAR_W * 0.48
            ax.add_patch(FancyBboxPatch(
                (xi - BAR_W/2, -r), BAR_W, mv + r,
                boxstyle=f"round,pad=0,rounding_size={r}",
                facecolor=C_INDIGO, edgecolor="none", alpha=0.80,
                clip_on=True, zorder=3,
            ))

    # New connections line (secondary, scaled to same axis)
    max_conn = max(max(conns), 1)
    conns_scaled = conns / max_conn * max_msg * 0.75
    ax2 = ax.twinx()
    ax2.set_ylim(0, max_conn * 1.25)
    ax2.spines[:].set_visible(False)
    ax2.tick_params(axis="y", colors=C_GREEN, labelsize=9, length=0, pad=6)
    ax2.yaxis.set_major_locator(mticker.MaxNLocator(integer=True, nbins=4))
    ax2.plot(x, conns, color=C_GREEN, linewidth=2, marker="o",
             markersize=4, markerfacecolor="white", markeredgewidth=1.5,
             markeredgecolor=C_GREEN, zorder=5, alpha=0.9)
    try:
        fd4 = _font(400)
        for lbl in ax2.get_yticklabels():
            lbl.set_fontfamily(fd4["fontfamily"])
    except Exception:
        pass

    # Grid + tick labels
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True, nbins=4))
    ax.yaxis.grid(True, color=C_BORDER, linewidth=0.5, alpha=0.7, zorder=0)
    ax.set_axisbelow(True)
    tick_labels = [labels[i] if (n <= 15 or i % 2 == 0) else "" for i in range(n)]
    ax.set_xticks(x); ax.set_xticklabels(tick_labels, rotation=45, ha="right",
                                           fontsize=9, color=C_HINT)
    ax.tick_params(axis="x", length=0, pad=6)
    ax.tick_params(axis="y", colors=C_HINT, labelsize=9, length=0, pad=8)

    ax.legend(handles=[
        mpatches.Patch(color=C_INDIGO, label="Сообщения"),
        mpatches.Patch(color=C_GREEN,  label="Подключения"),
    ], loc="upper right", fontsize=10, framealpha=0, labelcolor=C_HINT)

    try:
        fd4 = _font(400)
        for lbl in ax.get_xticklabels() + ax.get_yticklabels():
            lbl.set_fontfamily(fd4["fontfamily"])
    except Exception:
        pass


# ── Top-users list ────────────────────────────────────────────────────────────

def _draw_top_users(ax, stats: AdminStats):
    ax.set_facecolor("none")
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_zorder(3)
    ax.set_xticks([]); ax.set_yticks([])

    users = stats.top_users[:5]
    if not users:
        ax.text(0.5, 0.5, "Нет пользователей", ha="center", va="center",
                transform=ax.transAxes, color=C_HINT, fontsize=13)
        return

    n = len(users)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, n)

    max_msgs = max(u[1] for u in users) or 1
    row_colors = [C_PRIMARY, C_BLUE, C_INDIGO, C_GREEN, C_AMBER]
    fd4 = _font(400); fd6 = _font(600); fd7 = _font(700)

    for i, (name, msgs, chats, coins) in enumerate(users):
        row_y = n - i - 1          # top row first
        cy    = row_y + 0.5        # vertical center of row
        bar_w = msgs / max_msgs * 0.55
        color = row_colors[i % len(row_colors)]

        # Row background (alternating)
        if i % 2 == 0:
            bg = FancyBboxPatch((0.01, row_y + 0.06), 0.98, 0.88,
                                boxstyle="round,pad=0,rounding_size=0.03",
                                transform=ax.transData,
                                facecolor=_rgba(color, 0.05),
                                edgecolor="none", zorder=2)
            ax.add_patch(bg)

        # Rank circle
        rank_c = Circle((0.04, cy), 0.30,
                         facecolor=_rgba(color, 0.15),
                         edgecolor=color, linewidth=1.2,
                         transform=ax.transData, zorder=4)
        ax.add_patch(rank_c)
        ax.text(0.04, cy, str(i + 1), ha="center", va="center",
                color=color, fontsize=13, fontweight=700,
                fontfamily=fd7["fontfamily"], zorder=5)

        # Name
        display = name if len(name) <= 18 else name[:16] + "…"
        ax.text(0.10, cy + 0.14, display,
                ha="left", va="center",
                color=C_TEXT, fontsize=12, fontweight=600,
                fontfamily=fd6["fontfamily"], zorder=4)

        # Secondary stats
        ax.text(0.10, cy - 0.15,
                f"{msgs:,} сообщ.  ·  {chats} чатов  ·  {coins} монет".replace(",", " "),
                ha="left", va="center",
                color=C_HINT, fontsize=10,
                fontfamily=fd4["fontfamily"], zorder=4)

        # Relative bar
        bar_x = 0.42
        ax.add_patch(FancyBboxPatch(
            (bar_x, cy - 0.10), 0.56, 0.20,
            boxstyle="round,pad=0,rounding_size=0.05",
            transform=ax.transData,
            facecolor=_rgba(color, 0.12), edgecolor="none", zorder=3,
        ))
        if bar_w > 0:
            ax.add_patch(FancyBboxPatch(
                (bar_x, cy - 0.10), bar_w * 0.56, 0.20,
                boxstyle="round,pad=0,rounding_size=0.05",
                transform=ax.transData,
                facecolor=color, edgecolor="none", alpha=0.75, zorder=4,
            ))
        # Message count label on bar
        ax.text(bar_x + 0.56 + 0.01, cy, str(msgs),
                ha="left", va="center",
                color=color, fontsize=11, fontweight=600,
                fontfamily=fd6["fontfamily"], zorder=5)


# ── Utilities ─────────────────────────────────────────────────────────────────

def _rgba(hex_color: str, alpha: float) -> tuple:
    h = hex_color.lstrip("#")
    r, g, b = int(h[:2], 16)/255, int(h[2:4], 16)/255, int(h[4:], 16)/255
    return (r, g, b, alpha)


def _fmt_big(v: float | int) -> str:
    """Format large numbers with k/M suffix."""
    if isinstance(v, float):
        if v >= 1_000_000:
            return f"{v/1_000_000:.1f}M"
        if v >= 1_000:
            return f"{v/1_000:.1f}k"
        return f"{v:.1f}"
    n = int(v)
    if n >= 1_000_000:
        return f"{n//1_000_000}M"
    if n >= 10_000:
        return f"{n//1_000}k"
    return str(n)
