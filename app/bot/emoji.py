"""Custom animated emoji constants for bot messages.

Sourced from two Telegram emoji packs:
  • NewsEmoji  — https://t.me/addemoji/NewsEmoji
  • TONEmoji   — https://t.me/addemoji/TONEmoji

Usage (parse_mode="HTML" required):
    from app.bot.emoji import FIRE, WARNING
    await bot.send_message(chat_id=..., text=f"{FIRE} Что-то горит!", parse_mode="HTML")
"""

from __future__ import annotations


def _e(emoji_id: str, fallback: str) -> str:
    """Return a Telegram custom-emoji HTML tag with a plain-text fallback."""
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


# ── NewsEmoji pack ────────────────────────────────────────────────────────────
WARNING       = _e("5447644880824181073", "⚠️")
CROSS         = _e("5210952531676504517", "❌")
CHECK         = _e("5206607081334906820", "✔️")
BELL          = _e("5458603043203327669", "🔔")
CHART_BAR     = _e("5231200819986047254", "📊")
BUBBLE        = _e("5443038326535759644", "💬")
FIRE          = _e("5424972470023104089", "🔥")
BOOM          = _e("5276032951342088188", "💥")
PIN           = _e("5397782960512444700", "📌")
DIAMOND       = _e("5427168083074628963", "💎")
STAR          = _e("5438496463044752972", "⭐️")
SPARKLES      = _e("5325547803936572038", "✨")
CROWN         = _e("5217822164362739968", "👑")
LOCK          = _e("5296369303661067030", "🔒")
PENCIL        = _e("5395444784611480792", "✏️")
GEAR          = _e("5341715473882955310", "⚙️")
GAMEPAD       = _e("5361741454685256344", "🎮")
MEGAPHONE     = _e("5424818078833715060", "📣")
PARTY         = _e("5461151367559141950", "🎉")
TRASH         = _e("5445267414562389170", "🗑")
MAGNIFIER     = _e("5231012545799666522", "🔍")
QUESTION      = _e("5436113877181941026", "❓")
INFO          = _e("5334544901428229844", "ℹ️")
BULB          = _e("5422439311196834318", "💡")
REFRESH       = _e("5375338737028841420", "🔄")
LIGHTNING     = _e("5456140674028019486", "⚡️")
CHART_UP      = _e("5244837092042750681", "📈")
CHART_DOWN    = _e("5246762912428603768", "📉")

# ── TONEmoji pack ─────────────────────────────────────────────────────────────
COIN          = _e("5382164415019768638", "🪙")
MONEY_BAG     = _e("5417924076503062111", "💰")
TROPHY        = _e("5188344996356448758", "🏆")
ROCKET        = _e("5188481279963715781", "🚀")
TARGET        = _e("5461009483314517035", "🎯")
INBOX         = _e("5472239203590888751", "📩")
SLOTS         = _e("5235989279024373566", "🎰")
KEY           = _e("5307843983102204243", "🔑")
