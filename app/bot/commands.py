"""Regular Telegram bot commands (not part of the Business API).

These handlers respond to plain private-chat messages sent directly to the
bot itself (e.g. `/start`). They are separate from `app/business/handlers.py`,
which only reacts to Business API updates (business_message, etc.) coming
from the owner's connected personal account.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from app.config import get_settings
from app.logging_config import get_logger

logger = get_logger(__name__)
router = Router(name="commands")

_GREETING = """\
👋 Привет! Я твой умный помощник для <b>Telegram для бизнеса</b>.

Вот что я умею:

📨 <b>Мониторинг сообщений</b>
Если собеседник отредактирует или удалит сообщение — ты сразу узнаешь об этом и увидишь оригинальный текст.

🎵 <b>Музыка прямо в чате</b>
• Инлайн-поиск: напиши <code>@{username} название песни</code> в любом чате — выбери трек, и он появится прямо там
• Команда <code>!mp3 название</code> (или <code>!мп3</code>) — найти и загрузить трек в бизнес-чате

📹 <b>Видео по ссылке</b>
Брось ссылку на видео из TikTok, Instagram или YouTube прямо в переписку с собеседником — бот скачает и отправит видео в чат

📊 <b>Статистика общения</b>
Открой мини-приложение кнопкой ниже — там топ собеседников, активность по дням, серии общения (streak), достижения и многое другое.

🗒 <b>Команды в бизнес-чате</b> (пишешь сам, собеседник не видит):
<code>!info</code> · <code>!инфо</code> — статистика по собеседнику
<code>!note текст</code> · <code>!заметка текст</code> — сохранить заметку
<code>!notes</code> · <code>!заметки</code> — все заметки по чату
<code>!mute 30m/2h/1d</code> · <code>!мут</code> — отключить уведомления из чата
<code>!unmute</code> · <code>!размут</code> — включить обратно
<code>!help</code> · <code>!помощь</code> — справка

🐾 <b>Питомцы и монеты</b>
Зарабатывай монеты за активность, заводи питомцев, выполняй квесты — всё доступно в мини-приложении.

💎 <b>Премиум</b>
Расширенная аналитика слов и эмодзи, история сообщений, дополнительные привилегии — оформи подписку прямо в мини-приложении.

─────────────────────────
Чтобы бот начал получать сообщения, подключи его:
<b>Настройки → Telegram для бизнеса → Чат-боты</b>
"""


@router.message(CommandStart())
async def on_start(message: Message) -> None:
    """Greet the user with a full feature overview and action buttons."""

    settings = get_settings()
    bot_username = ""
    try:
        bot_info = await message.bot.get_me()  # type: ignore[union-attr]
        bot_username = bot_info.username or ""
    except Exception:
        pass

    text = _GREETING.replace("{username}", bot_username)

    keyboard: list[list[InlineKeyboardButton]] = []

    if settings.webhook_base_url:
        base_url = settings.webhook_base_url.rstrip("/")

        keyboard.append([
            InlineKeyboardButton(
                text="📊 Моя статистика",
                web_app=WebAppInfo(url=base_url + "/app"),
            )
        ])
        keyboard.append([
            InlineKeyboardButton(
                text="📜 Соглашение",
                web_app=WebAppInfo(url=base_url + "/terms"),
            ),
            InlineKeyboardButton(
                text="🔒 Конфиденциальность",
                web_app=WebAppInfo(url=base_url + "/privacy"),
            ),
        ])

        username = (message.from_user.username or "").lstrip("@").lower() if message.from_user else ""
        admin_username = settings.miniapp_admin_username.lstrip("@").lower()
        if admin_username and username == admin_username:
            keyboard.append([
                InlineKeyboardButton(
                    text="🛠 Админ-панель",
                    web_app=WebAppInfo(url=base_url + "/app/admin"),
                )
            ])

    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None
    await message.answer(text, parse_mode="HTML", reply_markup=reply_markup)
    logger.info("Sent /start greeting to chat_id=%s", message.chat.id)
