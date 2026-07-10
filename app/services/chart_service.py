"""chart_service.py — generate a styled stats image for the !info command.

Produces a PNG as a BytesIO buffer.  Uses matplotlib with a custom dark theme
that matches the Telegram dark colour palette.

Designed to be called from commands.py::_cmd_info.
"""

from __future__ import annotations

import datetime as dt
import io
from typing import NamedTuple

import matplotlib
matplotlib.use("Agg")  # headless backend — must be set before pyplot import

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
import matplotlib.ticker as mticker


# ── Telegram-dark palette ─────────────────────────────────────────────────────

BG_CARD   = "#17212b"   # main card background
BG_PANEL  = "#232e3c"   # stats-box background
BG_CHART  = "#1c2733"   # chart area background
COL_BAR_IN  = "#3d9be9"   # inbound messages bar
COL_BAR_OUT = "#5ac85a"   # outbound messages bar
COL_TEXT  = "#e8e8e8"   # primary text
COL_HINT  = "#6c8494"   # secondary / hint text
COL_ACCENT = "#3d9be9"  # accent (same as inbound for coherence)
COL_RED   = "#e05555"   # deleted / danger
COL_GOLD  = "#f0c040"   # edited / notes


# ── Public API ────────────────────────────────────────────────────────────────

class InfoStats(NamedTuple):
    contact_name: str          # display name of the interlocutor
    total: int
    incoming: int              # messages FROM the contact
    outgoing: int              # messages FROM the owner
    deleted: int
    edited: int
    first_seen: dt.datetime | None
    last_seen:  dt.datetime | None
    note_count: int
    muted_until: dt.datetime | None
    # daily breakdown: list of (date_str "dd.mm", inbound_count, outbound_count)
    # most-recent last, length ≤ 30
    daily: list[tuple[str, int, int]]


def render_info_image(stats: InfoStats) -> io.BytesIO:
    """Return a PNG BytesIO with the stats card."""

    fig = plt.figure(figsize=(10, 6.2), facecolor=BG_CARD, dpi=130)
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)

    gs = GridSpec(
        3, 3,
        figure=fig,
        left=0.03, right=0.97,
        top=0.91, bottom=0.06,
        hspace=0.55, wspace=0.35,
    )

    # ── Header ────────────────────────────────────────────────────────────────
    fig.text(
        0.5, 0.965,
        "Статистика чата",
        ha="center", va="top",
        color=COL_TEXT, fontsize=16, fontweight="bold",
    )
    fig.text(
        0.5, 0.925,
        stats.contact_name,
        ha="center", va="top",
        color=COL_ACCENT, fontsize=12,
    )

    # ── Bar chart (spans top two rows across all 3 columns) ───────────────────
    ax_bar = fig.add_subplot(gs[0:2, :])
    _draw_bar_chart(ax_bar, stats)

    # ── Stat boxes (bottom row, one column each) ──────────────────────────────
    ax_msgs   = fig.add_subplot(gs[2, 0])
    ax_del    = fig.add_subplot(gs[2, 1])
    ax_dates  = fig.add_subplot(gs[2, 2])

    _draw_stat_box(ax_msgs,  "Сообщений",
                   str(stats.total),
                   f"вх. {stats.incoming}  исх. {stats.outgoing}",
                   COL_ACCENT)
    del_pct = round(stats.deleted / stats.total * 100) if stats.total else 0
    _draw_stat_box(ax_del,   "Удалено / Изменено",
                   f"{stats.deleted} ({del_pct}%)",
                   f"изменено: {stats.edited}",
                   COL_RED)

    first_str = stats.first_seen.strftime("%d.%m.%Y") if stats.first_seen else "—"
    last_str  = stats.last_seen.strftime("%d.%m %H:%M")  if stats.last_seen  else "—"
    note_line = f"заметок: {stats.note_count}"
    _draw_stat_box(ax_dates, "Период",
                   first_str,
                   f"по {last_str}\n{note_line}",
                   COL_GOLD)

    # ── Footer ────────────────────────────────────────────────────────────────
    footer = "Учитываются сообщения с момента подключения бота"
    if stats.muted_until and stats.muted_until > dt.datetime.now(dt.timezone.utc):
        footer += f"  •  уведомления откл. до {stats.muted_until.strftime('%d.%m %H:%M')}"
    fig.text(
        0.5, 0.01,
        footer,
        ha="center", va="bottom",
        color=COL_HINT, fontsize=7.5,
    )

    buf = io.BytesIO()
    try:
        fig.savefig(buf, format="png", facecolor=BG_CARD, dpi=130)
    finally:
        plt.close(fig)
    buf.seek(0)
    return buf


# ── Private helpers ───────────────────────────────────────────────────────────

def _draw_bar_chart(ax: plt.Axes, stats: InfoStats) -> None:
    ax.set_facecolor(BG_CHART)
    for spine in ax.spines.values():
        spine.set_visible(False)

    daily = stats.daily
    if not daily:
        ax.text(0.5, 0.5, "Нет данных", transform=ax.transAxes,
                ha="center", va="center", color=COL_HINT, fontsize=11)
        ax.set_xticks([])
        ax.set_yticks([])
        return

    labels  = [d[0] for d in daily]
    inbound  = [d[1] for d in daily]
    outbound = [d[2] for d in daily]
    x = range(len(daily))
    w = 0.4

    bars_in  = ax.bar([i - w/2 for i in x], inbound,  width=w,
                      color=COL_BAR_IN,  alpha=0.85, zorder=3)
    bars_out = ax.bar([i + w/2 for i in x], outbound, width=w,
                      color=COL_BAR_OUT, alpha=0.85, zorder=3)

    ax.set_xlim(-0.8, len(daily) - 0.2)
    ax.tick_params(colors=COL_HINT, labelsize=7)
    ax.set_xticks(list(x))

    # Show every-other label when > 14 days to avoid crowding
    tick_labels = [labels[i] if (len(daily) <= 14 or i % 2 == 0) else "" for i in x]
    ax.set_xticklabels(tick_labels, rotation=45, ha="right", fontsize=7, color=COL_HINT)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True, nbins=4))
    ax.tick_params(axis="y", colors=COL_HINT, labelsize=7)
    ax.grid(axis="y", color=BG_PANEL, linewidth=0.8, zorder=0)

    ax.legend(
        handles=[
            mpatches.Patch(color=COL_BAR_IN,  label="Входящие"),
            mpatches.Patch(color=COL_BAR_OUT, label="Исходящие"),
        ],
        loc="upper left",
        fontsize=8,
        framealpha=0,
        labelcolor=COL_HINT,
    )


def _draw_stat_box(
    ax: plt.Axes,
    title: str,
    value: str,
    subtitle: str,
    accent: str,
) -> None:
    ax.set_facecolor(BG_PANEL)
    for spine in ax.spines.values():
        spine.set_edgecolor(accent)
        spine.set_linewidth(1.2)

    ax.set_xticks([])
    ax.set_yticks([])

    ax.text(0.5, 0.85, title,
            transform=ax.transAxes, ha="center", va="top",
            color=COL_HINT, fontsize=8.5)
    ax.text(0.5, 0.52, value,
            transform=ax.transAxes, ha="center", va="center",
            color=accent, fontsize=14, fontweight="bold")
    ax.text(0.5, 0.13, subtitle,
            transform=ax.transAxes, ha="center", va="bottom",
            color=COL_HINT, fontsize=7.5, linespacing=1.4)
