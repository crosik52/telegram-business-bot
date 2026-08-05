"""Regular Telegram bot commands (not part of the Business API).

These handlers respond to plain private-chat messages sent directly to the
bot itself (e.g. /start, /help, /me). They are separate from
`app/business/handlers.py`, which only reacts to Business API updates.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from app.config import get_settings
from app.database.session import session_scope
from app.logging_config import get_logger
from app.repositories.referral_repository import ReferralRepository
from app.bot import emoji as E

logger = get_logger(__name__)
router = Router(name="commands")


# ── Shared keyboard helpers ────────────────────────────────────────────────────

def _app_kb(base_url: str, *, extra_rows: list[list[InlineKeyboardButton]] | None = None) -> InlineKeyboardMarkup:
    """Main action keyboard for /start."""
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="📊 Статистика и профиль", web_app=WebAppInfo(url=base_url + "/app"))],
        [
            InlineKeyboardButton(text="🐾 Питомец", web_app=WebAppInfo(url=base_url + "/app#pet")),
            InlineKeyboardButton(text="💞 Связи", web_app=WebAppInfo(url=base_url + "/app#interact")),
        ],
        [
            InlineKeyboardButton(text="⭐ Premium", web_app=WebAppInfo(url=base_url + "/app#giveaway")),
            InlineKeyboardButton(text="🔗 Пригласить друга", callback_data="share_ref"),
        ],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help_main")],
    ]
    if extra_rows:
        rows += extra_rows
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _help_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🎵 Музыка",      callback_data="help_music"),
            InlineKeyboardButton(text="📹 Видео",        callback_data="help_video"),
        ],
        [
            InlineKeyboardButton(text="🗒 Заметки",      callback_data="help_notes"),
            InlineKeyboardButton(text="🐾 Питомец",     callback_data="help_pet"),
        ],
        [
            InlineKeyboardButton(text="💞 Связи",       callback_data="help_rel"),
            InlineKeyboardButton(text="🪙 Монеты",      callback_data="help_coins"),
        ],
        [InlineKeyboardButton(text="🔌 Как подключить бота", callback_data="help_connect")],
        [InlineKeyboardButton(text="« Назад",            callback_data="help_main")],
    ])


_HELP_SECTIONS: dict[str, tuple[str, str]] = {
    "help_main": (
        "❓ <b>Помощь</b>",
        "Выбери раздел, о котором хочешь узнать подробнее 👇",
    ),
    "help_music": (
        "🎵 <b>Музыка</b>",
        "• Инлайн-поиск: напиши <code>@botname песня</code> в любом чате\n"
        "• В бизнес-чате: <code>!mp3 название</code> / <code>!мп3</code> — скачать и отправить трек\n"
        "• Поддерживается пагинация результатов и история прослушиваний",
    ),
    "help_video": (
        "📹 <b>Видео по ссылке</b>",
        "Брось ссылку на видео из TikTok, Instagram или YouTube прямо в чат с собеседником — бот скачает и отправит видео.\n\n"
        "Для YouTube можно выбрать качество: 720p, 480p, 360p или только аудио.",
    ),
    "help_notes": (
        "🗒 <b>Заметки и напоминания</b>",
        "• <code>!note текст</code> / <code>!заметка</code> — сохранить заметку по чату\n"
        "• <code>!notes</code> / <code>!заметки</code> — посмотреть все заметки\n"
        "• <code>!mute 30m/2h/1d</code> / <code>!мут</code> — заглушить чат на время\n"
        "• <code>!unmute</code> / <code>!размут</code> — снять мут\n\n"
        "Заметкам можно ставить напоминания кнопкой прямо в боте 🔔",
    ),
    "help_pet": (
        "🐾 <b>Питомец</b>",
        "В мини-приложении можно завести питомца и ухаживать за ним:\n"
        "• Корми, играй, обнимай — питомец растёт и набирает уровень\n"
        "• Прокачивай навыки: бонус XP, устойчивость к голоду и настроению\n"
        "• Рейтинг питомцев среди всех пользователей\n\n"
        "Питомец зависит от твоей активности в чатах 📈",
    ),
    "help_rel": (
        "💞 <b>Связи</b>",
        "Дружи, встречайся, женись — прямо в Telegram!\n"
        "• 3 уровня отношений: Друзья → Отношения → Брак\n"
        "• 10 уровней внутри каждого + титулы\n"
        "• 🔥 Стрик пары: дарите подарки в один день — растёт серия и бонусы\n"
        "• 🎯 Недельные квесты, 📅 годовщины, 💞 совместимость\n"
        "• 🎁 5 видов подарков — от розы до бриллианта\n"
        "• <code>!открытка текст</code> — отправить красивую открытку партнёру",
    ),
    "help_coins": (
        "🪙 <b>Монеты и Premium</b>",
        "• Монеты начисляются за активность в бизнес-чатах\n"
        "• Трать в магазине: бусты XP, темы, рамки и питомцы\n"
        "• Ежедневный бонус — заходи каждый день\n"
        "• Квесты и достижения дают дополнительные награды\n\n"
        "⭐ <b>Premium</b> — расширенная аналитика слов и эмодзи, история сообщений, бонусные монеты и множители XP.",
    ),
    "help_connect": (
        "🔌 <b>Как подключить бота</b>",
        "Чтобы бот начал работать, нужен Telegram Business:\n\n"
        "1️⃣ Перейди в <b>Настройки → Telegram для бизнеса → Чат-боты</b>\n"
        "   (или нажми кнопку ниже — откроется сразу нужный экран)\n\n"
        "2️⃣ Добавь этого бота в список\n\n"
        "3️⃣ Готово! Бот получит доступ к твоим бизнес-чатам\n\n"
        "📌 Telegram Business есть в <b>Telegram Premium</b>. Если Premium нет — его можно получить через реферальную программу бота.",
    ),
}


# ── /start ─────────────────────────────────────────────────────────────────────

_GREETING = (
    "👋 Привет! Я твой умный помощник для <b>Telegram для бизнеса</b>.\n\n"
    "Вот что я умею:\n\n"
    "✏️ Если собеседник удалит или отредактирует сообщение — ты сразу увидишь оригинал\n"
    "🎵 Скачиваю треки по запросу прямо в чат (<code>!mp3 название</code>)\n"
    "📹 Скачиваю видео из TikTok, Instagram и YouTube по ссылке\n"
    "🐾 Питомец, монеты, квесты, уровни — всё в мини-приложении\n"
    "💞 Дружба и отношения с собеседниками — стрики, подарки, открытки\n"
    "⭐ Premium-аналитика: топ слов, эмодзи, история переписки\n\n"
    "Нажми <b>📊 Статистика</b>, чтобы открыть мини-приложение 👇"
)


@router.message(CommandStart())
async def on_start(message: Message) -> None:
    """Greet the user with a feature overview and action buttons."""

    settings = get_settings()

    # ── Referral deep-link handling ───────────────────────────────────────────
    parts = (message.text or "").split()
    start_arg = parts[1] if len(parts) > 1 else None
    if start_arg and start_arg.startswith("ref_") and message.from_user:
        try:
            referrer_id = int(start_arg[4:])
            referred_id = message.from_user.id
            async with session_scope() as db:
                repo = ReferralRepository(db)
                ref, reason = await repo.create_referral(
                    referrer_id,
                    referred_id,
                    referred_first_name=message.from_user.first_name or None,
                    referred_username=message.from_user.username or None,
                )
            if ref:
                logger.info(
                    "Referral registered: referrer=%s → referred=%s",
                    referrer_id, referred_id,
                )
                try:
                    u = message.from_user
                    who = u.first_name or ""
                    if u.username:
                        who += f" (@{u.username})"
                    who = who.strip() or f"#{referred_id}"
                    bot_info = await message.bot.get_me()  # type: ignore[union-attr]
                    conn_btn = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="🔌 Подключить бота", url="tg://settings/edit")
                    ]])
                    await message.bot.send_message(  # type: ignore[union-attr]
                        referrer_id,
                        f"{E.PARTY} По твоей ссылке перешёл <b>{who}</b>!\n\n"
                        f"Как только он подключит бота к Business-аккаунту — "
                        f"ты получишь вознаграждение {E.STAR}",
                        parse_mode="HTML",
                    )
                except Exception as notify_exc:
                    logger.debug("Failed to notify referrer %s: %s", referrer_id, notify_exc)
        except (ValueError, Exception) as exc:
            logger.debug("Referral deep-link error: %s", exc)

    keyboard = None
    if settings.webhook_base_url:
        base_url = settings.webhook_base_url.rstrip("/")
        extra: list[list[InlineKeyboardButton]] = []

        # Terms / privacy
        extra.append([
            InlineKeyboardButton(text="📜 Соглашение",       web_app=WebAppInfo(url=base_url + "/terms")),
            InlineKeyboardButton(text="🔒 Конфиденциальность", web_app=WebAppInfo(url=base_url + "/privacy")),
        ])

        # Admin button for the admin account
        username     = (message.from_user.username or "").lstrip("@").lower() if message.from_user else ""
        admin_uname  = settings.miniapp_admin_username.lstrip("@").lower()
        if admin_uname and username == admin_uname:
            extra.append([
                InlineKeyboardButton(text="🛠 Админ-панель", web_app=WebAppInfo(url=base_url + "/app/admin"))
            ])

        keyboard = _app_kb(base_url, extra_rows=extra)

    await message.answer(_GREETING, parse_mode="HTML", reply_markup=keyboard)
    logger.info("Sent /start greeting to chat_id=%s", message.chat.id)


# ── /help ──────────────────────────────────────────────────────────────────────

@router.message(Command("help"))
async def on_help(message: Message) -> None:
    title, body = _HELP_SECTIONS["help_main"]
    await message.answer(
        f"{title}\n\n{body}",
        parse_mode="HTML",
        reply_markup=_help_kb(),
    )


@router.callback_query(F.data.startswith("help_"))
async def on_help_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    key = callback.data or "help_main"
    title, body = _HELP_SECTIONS.get(key, _HELP_SECTIONS["help_main"])

    settings = get_settings()
    extra_btn: list[InlineKeyboardButton] = []

    # For the connect section add a deep-link button
    if key == "help_connect":
        extra_btn = [InlineKeyboardButton(text="⚙️ Открыть настройки Telegram", url="tg://settings/edit")]

    # Build keyboard: section buttons + optional extra
    kb = _help_kb()
    if extra_btn:
        kb.inline_keyboard.insert(0, [extra_btn[0]])

    # Add mini-app button on main help screen
    if key == "help_main" and settings.webhook_base_url:
        base_url = settings.webhook_base_url.rstrip("/")
        kb.inline_keyboard.insert(0, [
            InlineKeyboardButton(text="📊 Открыть мини-приложение", web_app=WebAppInfo(url=base_url + "/app"))
        ])

    text = f"{title}\n\n{body}"
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)  # type: ignore[union-attr]
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)  # type: ignore[union-attr]


# ── share_ref callback ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "share_ref")
async def on_share_ref(callback: CallbackQuery) -> None:
    await callback.answer()
    if not callback.from_user:
        return
    async with session_scope() as db:
        repo = ReferralRepository(db)
        info = await repo.get_referral_info(callback.from_user.id)
    if info and info.get("ref_link"):
        link = info["ref_link"]
        await callback.message.answer(  # type: ignore[union-attr]
            f"🔗 <b>Твоя реферальная ссылка:</b>\n\n"
            f"<code>{link}</code>\n\n"
            f"Каждый активный друг приносит тебе бонусы ⭐",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="📤 Поделиться",
                    url=f"https://t.me/share/url?url={link}&text=Крутой+бот+для+Telegram+Business!",
                )
            ]]),
        )
    else:
        await callback.message.answer(  # type: ignore[union-attr]
            "🔗 Реферальная программа пока недоступна.",
        )


# ── /me ───────────────────────────────────────────────────────────────────────

@router.message(Command("me"))
async def on_me(message: Message) -> None:
    """Quick personal stats: balance, subscription, pet, streak."""
    if not message.from_user:
        return

    uid = message.from_user.id
    settings = get_settings()

    balance    = 0
    sub_label  = "Нет"
    pet_line   = "Нет питомца"
    streak     = 0

    try:
        from app.models.wallet import UserWallet          # noqa: PLC0415
        from app.models.pet import Pet                    # noqa: PLC0415
        from app.repositories.subscription_repository import SubscriptionRepository  # noqa: PLC0415
        from sqlalchemy import select as _sel             # noqa: PLC0415
        import datetime as _dt                            # noqa: PLC0415

        async with session_scope() as db:
            # Wallet
            w = (await db.execute(
                _sel(UserWallet).where(UserWallet.owner_telegram_id == uid)
            )).scalar_one_or_none()
            if w:
                balance = w.balance

            # Subscription
            sub_repo = SubscriptionRepository(db)
            vip = await sub_repo.get_active_vip_subscription(uid)
            if vip:
                left = (vip.expires_at - _dt.datetime.now(_dt.timezone.utc)).days + 1
                sub_label = f"💎 VIP · ещё {left} дн."
            else:
                sub = await sub_repo.get_active_subscription(uid)
                if sub:
                    left = (sub.expires_at - _dt.datetime.now(_dt.timezone.utc)).days + 1
                    sub_label = f"⭐ Premium · ещё {left} дн."

            # Alive pet (first)
            pet = (await db.execute(
                _sel(Pet).where(
                    Pet.owner_telegram_id == uid,
                    Pet.is_alive.is_(True),
                ).limit(1)
            )).scalar_one_or_none()
            if pet:
                hunger = round(pet.hunger or 0)
                mood   = round(pet.mood   or 0)
                pet_line = (
                    f"{pet.pet_name} · Ур.{pet.level} · "
                    f"🍖{hunger}% 😊{mood}%"
                )

            # Daily streak (messages table count as proxy — use wallet daily_claimed if exists)
            if hasattr(w, "streak_days") and w:
                streak = getattr(w, "streak_days", 0) or 0
    except Exception:
        logger.exception("/me: DB query failed for user %s", uid)

    streak_line = f"🔥 Стрик: {streak} дн.\n" if streak else ""

    text = (
        f"📊 <b>Твой профиль</b>\n\n"
        f"🪙 Баланс: <b>{balance:,}".replace(",", " ") + f" монет</b>\n"
        f"⭐ Подписка: <b>{sub_label}</b>\n"
        f"🐾 Питомец: {pet_line}\n"
        f"{streak_line}"
    )

    kb = None
    if settings.webhook_base_url:
        base_url = settings.webhook_base_url.rstrip("/")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📊 Открыть мини-приложение", web_app=WebAppInfo(url=base_url + "/app"))
        ]])

    await message.answer(text, parse_mode="HTML", reply_markup=kb)
