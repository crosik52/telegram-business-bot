"""chart_service.py — 640 × 360 px (16:9) stats card for !инфо / !info.

Layout (three-zone landscape)
─────────────────────────────────────────────────────────────────
  Left zone   x  20–440  w=420   ← avatar + name + 4+4 KPIs
  Separator   x 450      w=1     ← thin vertical rule
  Right zone  x 461–620  w=159   ← donut chart (in/out split)
─────────────────────────────────────────────────────────────────

Left zone is split into three stacked rows:
  Header row      y  18–87   h=69   avatar · name · period · badges
  Primary KPIs    y 101–208  h=107  Total / Ваших / Их / В день
  Secondary KPIs  y 220–342  h=122  Медиа / Аудио / Изменено / Удалено
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
from matplotlib.patches import Circle, FancyBboxPatch

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


def _F(weight: int = 400) -> dict:
    _ensure_fonts()
    fam = "Inter" if "Inter" in fm.fontManager.get_font_names() else "DejaVu Sans"
    return {"fontfamily": fam, "fontweight": weight}


# ── Colour palette ────────────────────────────────────────────────────────────

BG      = "#07101E"     # deep navy page background
CARD_BG = "#0C1929"     # slightly lighter card face
DIV     = "#14263A"     # subtle divider / rule colour
C_TEXT  = "#E3ECFF"     # near-white body text
C_HINT  = "#3E5670"     # muted label / secondary text
C_ACC   = "#7B72F8"     # indigo — avatar ring & accent

# Donut colours
C_IN    = "#3BAEFF"     # incoming — vivid blue
C_OUT   = "#2DD49E"     # outgoing — teal-green

# Primary KPI accent colours  (Total / Outgoing / Incoming / Avg-per-day)
_P = ["#3BAEFF", "#2DD49E", "#69D9FF", "#FFBB40"]
# Secondary KPI accent colours (Media / Audio / Edited / Deleted)
_S = ["#FF7E5C", "#B280FF", "#5AC8FF", "#4D6C8A"]

# ── Canvas geometry (px, origin = top-left) ───────────────────────────────────

DPI  = 100
W    = 640
H    = 360

PAD_X   = 20
PAD_Y   = 18

LEFT_W  = 420
SEP_X   = PAD_X + LEFT_W + 10   # = 450
RIGHT_X = SEP_X + 11             # = 461
RIGHT_W = W - PAD_X - RIGHT_X   # = 159

INNER_H = H - PAD_Y * 2         # = 324

# Row geometry inside left zone
HDR_Y  = PAD_Y          # 18
HDR_H  = 69
KPI_Y  = HDR_Y + HDR_H + 14    # 101
KPI_H  = 107
SEC_Y  = KPI_Y + KPI_H + 12    # 220
SEC_H  = H - PAD_Y - SEC_Y     # 122   (bottom-pad included in SEC_H)


# ── Coordinate helpers ────────────────────────────────────────────────────────

def _ax(fig: plt.Figure, x: float, y_top: float,
        w: float, h: float) -> plt.Axes:
    """Add axes region specified in px (y_top measured from top of canvas)."""
    return fig.add_axes([x / W, (H - y_top - h) / H, w / W, h / H])


def _ha(hex_color: str, alpha: float) -> tuple:
    """#RRGGBB + alpha → (r, g, b, a) tuple for matplotlib."""
    c = hex_color.lstrip("#")
    return (int(c[0:2], 16) / 255,
            int(c[2:4], 16) / 255,
            int(c[4:6], 16) / 255,
            alpha)


# ── Public data contract ──────────────────────────────────────────────────────

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


# ── Entry point ───────────────────────────────────────────────────────────────

def render_info_image(stats: InfoStats) -> io.BytesIO:
    """Return a 640 × 360 PNG stats card as BytesIO."""
    _ensure_fonts()

    fig = plt.figure(figsize=(W / DPI, H / DPI), facecolor=BG, dpi=DPI)
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)

    _draw_bg_card(fig)
    _draw_header(fig, stats)
    _draw_primary_kpis(fig, stats)
    _draw_secondary_kpis(fig, stats)
    _draw_vsep(fig)
    _draw_donut(fig, stats)

    buf = io.BytesIO()
    try:
        fig.savefig(buf, format="png", facecolor=BG, dpi=DPI)
    finally:
        plt.close(fig)
    buf.seek(0)
    return buf


# ── Background card ───────────────────────────────────────────────────────────

def _draw_bg_card(fig: plt.Figure) -> None:
    """Single rounded card that covers the entire canvas."""
    MARGIN = 6
    card = FancyBboxPatch(
        (MARGIN / W, MARGIN / H),
        (W - MARGIN * 2) / W,
        (H - MARGIN * 2) / H,
        boxstyle="round,pad=0,rounding_size=0.018",
        transform=fig.transFigure,
        facecolor=CARD_BG,
        edgecolor=DIV,
        linewidth=0.8,
        zorder=0,
    )
    fig.add_artist(card)

    # Thin indigo accent bar at the very top of the card
    bar = FancyBboxPatch(
        (MARGIN / W, (H - MARGIN - 3) / H),
        (W - MARGIN * 2) / W,
        3 / H,
        boxstyle="round,pad=0,rounding_size=0.005",
        transform=fig.transFigure,
        facecolor=C_ACC,
        edgecolor="none",
        zorder=1,
    )
    fig.add_artist(bar)


# ── Header row ────────────────────────────────────────────────────────────────

def _draw_header(fig: plt.Figure, stats: InfoStats) -> None:
    ax = _ax(fig, PAD_X, HDR_Y, LEFT_W, HDR_H)
    ax.set_xlim(0, LEFT_W)
    ax.set_ylim(0, HDR_H)
    ax.set_facecolor("none")
    ax.axis("off")
    ax.set_zorder(3)

    AV_R  = 26
    AV_CX = AV_R + 4
    AV_CY = HDR_H / 2

    # Avatar: outer glow ring + filled circle
    glow = Circle((AV_CX, AV_CY), AV_R + 3,
                  facecolor=_ha(C_ACC, 0.12),
                  edgecolor="none",
                  transform=ax.transData, zorder=3)
    ax.add_patch(glow)
    circ = Circle((AV_CX, AV_CY), AV_R,
                  facecolor=_ha(C_ACC, 0.22),
                  edgecolor=C_ACC,
                  linewidth=1.5,
                  transform=ax.transData, zorder=4)
    ax.add_patch(circ)

    initials = _initials(stats.contact_name)
    fd7 = _F(700)
    ax.text(AV_CX, AV_CY, initials,
            ha="center", va="center",
            color=C_ACC, fontsize=12, fontweight=700,
            fontfamily=fd7["fontfamily"], zorder=5)

    # Name + period
    tx = AV_CX * 2 + 14
    name = (stats.contact_name[:30] + "…") if len(stats.contact_name) > 30 else stats.contact_name
    fd6 = _F(600)
    ax.text(tx, AV_CY + 12, name,
            ha="left", va="center",
            color=C_TEXT, fontsize=13, fontweight=600,
            fontfamily=fd6["fontfamily"], zorder=4)

    days  = _days_total(stats)
    per   = _period_str(stats)
    sub   = f"{per}  ·  {days} д." if per != "—" else f"{days} д."
    fd4 = _F(400)
    ax.text(tx, AV_CY - 12, sub,
            ha="left", va="center",
            color=C_HINT, fontsize=8.5,
            fontfamily=fd4["fontfamily"], zorder=4)

    # Right-side badges
    badges: list[str] = []
    if stats.note_count:
        badges.append(f"📝 {stats.note_count}")
    now_utc = dt.datetime.now(dt.timezone.utc)
    if stats.muted_until and stats.muted_until > now_utc:
        badges.append(f"🔕 {stats.muted_until.strftime('%d.%m')}")
    if badges:
        ax.text(LEFT_W - 8, AV_CY, "  ·  ".join(badges),
                ha="right", va="center",
                color=_ha(C_HINT, 0.9), fontsize=8,
                fontfamily=fd4["fontfamily"], zorder=4)

    # Thin horizontal rule at bottom of header
    ax.axhline(1, xmin=0, xmax=1, color=DIV, linewidth=0.8, zorder=3)


# ── Primary KPIs (4 metrics in one row) ──────────────────────────────────────

def _draw_primary_kpis(fig: plt.Figure, stats: InfoStats) -> None:
    avg = _avg_per_day(stats)
    kpis = [
        (stats.total,    "Всего",    _P[0]),
        (stats.outgoing, "Ваших",   _P[1]),
        (stats.incoming, "Их",      _P[2]),
        (avg,            "В день",  _P[3]),
    ]
    _kpi_row(fig, KPI_Y, KPI_H, kpis, val_fs=28)


# ── Secondary KPIs (4 metrics in one row) ────────────────────────────────────

def _draw_secondary_kpis(fig: plt.Figure, stats: InfoStats) -> None:
    kpis = [
        (stats.media_count, "Медиа",    _S[0]),
        (stats.audio_count, "Аудио",    _S[1]),
        (stats.edited,      "Изменено", _S[2]),
        (stats.deleted,     "Удалено",  _S[3]),
    ]
    _kpi_row(fig, SEC_Y, SEC_H, kpis, val_fs=22)


def _kpi_row(fig: plt.Figure, y_top: float, h: float,
             kpis: list[tuple], val_fs: int) -> None:
    """Render a horizontal row of N evenly-spaced KPI cells."""
    n      = len(kpis)
    cell_w = LEFT_W / n

    for i, (val, label, color) in enumerate(kpis):
        x  = PAD_X + i * cell_w
        ax = _ax(fig, x, y_top, cell_w, h)
        ax.set_xlim(0, cell_w)
        ax.set_ylim(0, h)
        ax.set_facecolor("none")
        ax.axis("off")
        ax.set_zorder(3)

        cw = cell_w
        fd4 = _F(400)
        fd7 = _F(700)

        # Thin horizontal rule at the top of this row
        ax.axhline(h - 1, xmin=0.04, xmax=0.96,
                   color=DIV, linewidth=0.8, zorder=3)

        # Label (below top rule)
        ax.text(cw / 2, h - 14, label,
                ha="center", va="top",
                color=C_HINT, fontsize=8,
                fontfamily=fd4["fontfamily"], zorder=4)

        # Value (center of cell)
        val_str = _fmt_num(val)
        ax.text(cw / 2, h / 2 + 2, val_str,
                ha="center", va="center",
                color=color, fontsize=val_fs, fontweight=700,
                fontfamily=fd7["fontfamily"], zorder=4)

        # Thin accent underbar
        bar_w   = max(24, cw * 0.40)
        bar_x   = (cw - bar_w) / 2
        accent  = FancyBboxPatch(
            (bar_x, 8), bar_w, 3,
            boxstyle="round,pad=0,rounding_size=1.5",
            facecolor=_ha(color, 0.35),
            edgecolor="none",
            zorder=4,
            transform=ax.transData,
        )
        ax.add_patch(accent)

        # Vertical separator (except after last cell)
        if i < n - 1:
            ax.axvline(cw - 0.5, ymin=0.06, ymax=0.88,
                       color=DIV, linewidth=0.8, zorder=3)


# ── Vertical separator ────────────────────────────────────────────────────────

def _draw_vsep(fig: plt.Figure) -> None:
    ax = _ax(fig, SEP_X, PAD_Y + 4, 1, INNER_H - 8)
    ax.set_facecolor(DIV)
    ax.axis("off")
    ax.set_zorder(2)


# ── Donut chart ───────────────────────────────────────────────────────────────

def _draw_donut(fig: plt.Figure, stats: InfoStats) -> None:
    MARG = 10
    ax = _ax(fig, RIGHT_X, PAD_Y + MARG, RIGHT_W, INNER_H - MARG * 2)
    ax.set_facecolor("none")
    ax.set_zorder(3)
    ax.axis("equal")

    total = stats.incoming + stats.outgoing
    fd7   = _F(700)
    fd4   = _F(400)
    fd5   = _F(500)

    if total == 0:
        ax.pie([1], colors=[DIV], startangle=90,
               wedgeprops=dict(width=0.44, edgecolor=CARD_BG, linewidth=3))
        ax.text(0, 0.10, "—",
                ha="center", va="center",
                color=C_HINT, fontsize=22, fontweight=700,
                fontfamily=fd7["fontfamily"])
        ax.text(0, -0.24, "нет данных",
                ha="center", va="center",
                color=C_HINT, fontsize=7.5,
                fontfamily=fd4["fontfamily"])
        ax.set_xlim(-1.6, 1.6)
        ax.set_ylim(-1.7, 1.5)
        return

    in_pct  = round(stats.incoming / total * 100)

    wedges, _ = ax.pie(
        [max(stats.incoming, 0), max(stats.outgoing, 0)],
        colors=[C_IN, C_OUT],
        startangle=90,
        counterclock=False,
        wedgeprops=dict(width=0.42, edgecolor=CARD_BG, linewidth=3),
    )

    # Section label above donut
    ax.text(0, 1.55, "диалог",
            ha="center", va="center",
            color=C_HINT, fontsize=7.5,
            fontfamily=fd4["fontfamily"])

    # Centre: big % + sub-label
    ax.text(0, 0.15, f"{in_pct}%",
            ha="center", va="center",
            color=C_TEXT, fontsize=19, fontweight=700,
            fontfamily=fd7["fontfamily"])
    ax.text(0, -0.19, "входящих",
            ha="center", va="center",
            color=C_HINT, fontsize=7,
            fontfamily=fd4["fontfamily"])

    # Legend — two compact rows
    ax.legend(
        handles=[
            mpatches.Patch(color=C_IN,  label=f"↓  {_fmt_num(stats.incoming)}"),
            mpatches.Patch(color=C_OUT, label=f"↑  {_fmt_num(stats.outgoing)}"),
        ],
        loc="lower center",
        ncol=2,
        fontsize=7.5,
        framealpha=0,
        labelcolor=C_TEXT,
        bbox_to_anchor=(0.5, -0.26),
        handlelength=1.0,
        handleheight=0.9,
        handletextpad=0.4,
        columnspacing=0.7,
        prop={"family": _F(500)["fontfamily"], "weight": 500, "size": 7.5},
    )
    ax.set_xlim(-1.6, 1.6)
    ax.set_ylim(-1.75, 1.75)


# ── Utilities ─────────────────────────────────────────────────────────────────

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
    # Thin-space thousands separator
    return f"{n:,}".replace(",", "\u2009")
