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
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from sqlalchemy import select

from app.config import get_settings
from app.database.session import session_scope
from app.logging_config import get_logger
from app.models.wallet import UserWallet
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
            InlineKeyboardButton(text="🔗 Пригласить друга", callback_data="share_ref"),
            InlineKeyboardButton(text="❓ Помощь", callback_data="help_main"),
        ],
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
        "Чтобы бот начал работать:\n\n"
        "1️⃣ Нажми на tg://settings/edit → пролистай до «автоматизации чатов»\n\n"
        "2️⃣ Вставь туда @{bot_username}\n\n"
        "3️⃣ Готово! Теперь бот работает и показывает статистику с момента подключения.",
    ),
}


# ── /start ─────────────────────────────────────────────────────────────────────

_GREETING = (
    "👋 Привет! Я твой умный помощник для Telegram.\n\n"
    "Вот что я умею:\n\n"
    '<tg-emoji emoji-id="5220070652756635426">👀</tg-emoji>'
    " Если собеседник удалит или отредактирует сообщение — ты сразу увидишь оригинал\n"
    '<tg-emoji emoji-id="5222472119295684375">🎶</tg-emoji>'
    " Скачиваю треки по запросу прямо в чате с собеседником (<code>!mp3 название</code>)\n"
    '<tg-emoji emoji-id="5219943216781995020">⚡</tg-emoji>'
    " Скачиваю видео из TikTok, Instagram и YouTube по ссылке (beta)\n"
    '<tg-emoji emoji-id="5244820603663296299">🐾</tg-emoji>'
    " Питомец, монеты, квесты, уровни — всё в мини-приложении\n"
    '<tg-emoji emoji-id="5100657930429006538">❤️</tg-emoji>'
    " Дружба и отношения с собеседниками — стрики, подарки, открытки\n"
    '<tg-emoji emoji-id="5310224206732996002">⭐</tg-emoji>'
    " Premium-аналитика: топ слов, эмодзи, ии анализ диалогов\n\n"
    "💡 <b>Все функции работают без Telegram Premium!</b>\n\n"
    "Нажми <b>📊 Статистика</b>, чтобы открыть мини-приложение 👇"
)


async def _send_welcome(message: Message, *, is_new_user: bool, user=None) -> None:
    """Send the standard /start greeting (after subscription is confirmed).

    *user* overrides message.from_user when called from a callback context
    (where message.from_user would be the bot, not the human).
    """
    from_user = user or message.from_user
    settings = get_settings()
    keyboard = None
    if settings.webhook_base_url:
        base_url = settings.webhook_base_url.rstrip("/")
        extra: list[list[InlineKeyboardButton]] = []
        extra.append([
            InlineKeyboardButton(text="📜 Соглашение",         web_app=WebAppInfo(url=base_url + "/terms")),
            InlineKeyboardButton(text="🔒 Конфиденциальность", web_app=WebAppInfo(url=base_url + "/privacy")),
        ])
        username    = (from_user.username or "").lstrip("@").lower() if from_user else ""
        admin_uname = settings.miniapp_admin_username.lstrip("@").lower()
        if admin_uname and username == admin_uname:
            extra.append([
                InlineKeyboardButton(text="🛠 Админ-панель", web_app=WebAppInfo(url=base_url + "/app/admin"))
            ])
        keyboard = _app_kb(base_url, extra_rows=extra)

    await message.answer(_GREETING, parse_mode="HTML", reply_markup=keyboard)

    if is_new_user:
        _, connect_text = _HELP_SECTIONS["help_connect"]
        connect_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⚙️ Открыть настройки Telegram", url="tg://settings/edit")
        ]])
        await message.answer(connect_text, parse_mode="HTML", reply_markup=connect_kb)

    logger.info("Sent /start greeting to chat_id=%s (new=%s)", message.chat.id, is_new_user)


async def _check_channel_gate(
    bot,
    user_id: int,
) -> list:
    """Return list of unsubscribed required channels (empty = all good)."""
    try:
        from app.repositories.channel_repository import ChannelRepository          # noqa: PLC0415
        from app.services.channel_subscription_service import get_unsubscribed_channels  # noqa: PLC0415
        async with session_scope() as db:
            active = await ChannelRepository(db).get_active()
        if not active:
            return []
        return await get_unsubscribed_channels(bot, user_id, active)
    except Exception as exc:
        logger.warning("channel_gate check failed for user %s — allowing through: %s", user_id, exc)
        return []


def _gate_keyboard(unsub_channels: list) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"📢 {ch.display_title}", url=ch.join_url)]
        for ch in unsub_channels
    ]
    rows.append([InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(CommandStart())
async def on_start(message: Message) -> None:
    """Greet the user with a feature overview and action buttons."""

    if not message.from_user:
        return

    user_id = message.from_user.id

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

    # ── Detect first-time user ────────────────────────────────────────────────
    is_new_user = False
    try:
        async with session_scope() as db:
            result = await db.execute(
                select(UserWallet).where(UserWallet.owner_telegram_id == user_id)
            )
            is_new_user = result.scalar_one_or_none() is None
    except Exception as exc:
        logger.debug("First-time user check failed: %s", exc)

    # ── Channel subscription gate ─────────────────────────────────────────────
    unsub = await _check_channel_gate(message.bot, user_id)
    if unsub:
        titles = "\n".join(f"• {ch.display_title}" for ch in unsub)
        await message.answer(
            f"👋 Привет! Чтобы получить доступ к боту — подпишись на {'канал' if len(unsub) == 1 else 'каналы'}:\n\n"
            f"{titles}\n\n"
            f"После подписки нажми кнопку ниже 👇",
            parse_mode="HTML",
            reply_markup=_gate_keyboard(unsub),
        )
        return

    await _send_welcome(message, is_new_user=is_new_user)


# ── "Я подписался" — повторная проверка подписки ─────────────────────────────

@router.callback_query(F.data == "check_sub")
async def on_check_sub(callback: CallbackQuery) -> None:
    await callback.answer()
    if not callback.from_user or not callback.message:
        return

    user_id = callback.from_user.id
    unsub = await _check_channel_gate(callback.bot, user_id)

    if unsub:
        # Still not subscribed — update the message with fresh button list
        try:
            titles = "\n".join(f"• {ch.display_title}" for ch in unsub)
            await callback.message.edit_text(
                f"❌ Ты ещё не подписан{'а' if False else ''}. Подпишись и нажми кнопку снова:\n\n{titles}",
                parse_mode="HTML",
                reply_markup=_gate_keyboard(unsub),
            )
        except Exception:
            await callback.answer("Подпишись на каналы и попробуй снова.", show_alert=True)
        return

    # Gate passed — delete gate message, detect new-user, send welcome
    try:
        await callback.message.delete()
    except Exception:
        pass

    is_new_user = False
    try:
        async with session_scope() as db:
            result = await db.execute(
                select(UserWallet).where(UserWallet.owner_telegram_id == user_id)
            )
            is_new_user = result.scalar_one_or_none() is None
    except Exception as exc:
        logger.debug("First-time user check in check_sub failed: %s", exc)

    await _send_welcome(callback.message, is_new_user=is_new_user, user=callback.from_user)


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
    uid = callback.from_user.id

    # ── Bot username + referral stats ─────────────────────────────────────────
    try:
        bot_info = await callback.bot.get_me()  # type: ignore[union-attr]
        bot_username = bot_info.username or ""
    except Exception:
        bot_username = ""

    link = f"https://t.me/{bot_username}?start=ref_{uid}" if bot_username else None

    # Fetch light referral stats (active friends + level name)
    active_count  = 0
    level_name    = "Bronze"
    ref_reward    = 7      # default fallback
    try:
        from app.repositories.referral_repository import ReferralRepository  # noqa: PLC0415
        async with session_scope() as db:
            ref_repo = ReferralRepository(db)
            stats = await ref_repo.get_user_stats(uid, bot_username)
            active_count = stats.get("active_count", 0)
            level_name   = stats.get("level", {}).get("name", "Bronze")
            ref_reward   = stats.get("referrer_reward_days", ref_reward)
    except Exception:
        pass

    # ── Compose card ──────────────────────────────────────────────────────────
    _LEVEL_EMOJI = {
        "Bronze":   "🥉",
        "Silver":   "🥈",
        "Gold":     "🥇",
        "Diamond":  "💎",
        "Platinum": "👑",
    }
    lv_emoji = _LEVEL_EMOJI.get(level_name, "🔰")

    friends_line = (
        f"👥 Активных друзей: <b>{active_count}</b>\n"
        f"{lv_emoji} Уровень: <b>{level_name}</b>\n\n"
        if active_count > 0 else ""
    )

    share_text = (
        "🤖 Попробуй этого бота для Telegram Business!\n"
        "📊 Статистика, питомец, монеты, уровни и многое другое.\n"
        "Регистрируйся по ссылке:"
    )
    import urllib.parse as _up                                               # noqa: PLC0415
    share_url = (
        f"https://t.me/share/url?url={_up.quote(link, safe='')}"
        f"&text={_up.quote(share_text, safe='')}"
    ) if link else None

    try:
        if link:
            await callback.bot.send_message(  # type: ignore[union-attr]
                uid,
                f"🎁 <b>Пригласи друга — получи бонус!</b>\n\n"
                f"{friends_line}"
                f"💡 За каждого активного друга ты получаешь "
                f"<b>{ref_reward} дней Premium</b> бесплатно.\n\n"
                f"🔗 Твоя ссылка:\n"
                f"<code>{link}</code>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="📤 Поделиться с другом",
                        url=share_url,
                    )],
                ]),
            )
        else:
            await callback.bot.send_message(  # type: ignore[union-attr]
                uid,
                "🔗 Реферальная программа пока недоступна.",
            )
    except Exception as exc:
        logger.warning("share_ref: failed to send ref link to %s: %s", uid, exc)


# ── /me ───────────────────────────────────────────────────────────────────────

@router.message(Command("me"))
async def on_me(message: Message) -> None:
    """Profile card: coins, messages, subscription, pet, bond, member since."""
    if not message.from_user:
        return

    uid      = message.from_user.id
    settings = get_settings()

    balance      = 0
    msg_count    = 0
    sub_label    = "Нет"
    pet_line     = ""
    bond_line    = ""
    member_since = ""

    try:
        import json as _json                                                    # noqa: PLC0415
        import datetime as _dt                                                  # noqa: PLC0415
        from sqlalchemy import func, or_                                        # noqa: PLC0415
        from app.models.business_connection import BusinessConnection           # noqa: PLC0415
        from app.models.message import Message as MsgModel                     # noqa: PLC0415
        from app.models.relationship import Relationship                        # noqa: PLC0415
        from app.repositories.subscription_repository import SubscriptionRepository  # noqa: PLC0415

        async with session_scope() as db:

            # ── Wallet: fetch columns directly to avoid ORM expiry issues ─────
            wallet_row = (await db.execute(
                select(UserWallet.balance, UserWallet.created_at)
                .where(UserWallet.owner_telegram_id == uid)
            )).one_or_none()
            if wallet_row is not None:
                balance = wallet_row[0] or 0
                if wallet_row[1]:
                    member_since = wallet_row[1].strftime("%d.%m.%Y")

            # ── Total messages via business connection join ───────────────────
            msg_count = (await db.execute(
                select(func.count(MsgModel.id))
                .join(
                    BusinessConnection,
                    MsgModel.business_connection_id == BusinessConnection.business_connection_id,
                )
                .where(BusinessConnection.user_telegram_id == uid)
            )).scalar() or 0

            # ── Subscription ──────────────────────────────────────────────────
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

            # ── Pet ───────────────────────────────────────────────────────────
            from app.models.pet import ChatPet                              # noqa: PLC0415
            pet = (await db.execute(
                select(ChatPet).where(
                    (ChatPet.user_a_id == uid) | (ChatPet.user_b_id == uid),
                    ChatPet.is_alive.is_(True),
                ).limit(1)
            )).scalar_one_or_none()
            if pet:
                # Compute live hunger from timestamps (same formula as pet_repository)
                _now   = _dt.datetime.now(_dt.timezone.utc)
                _ref   = pet.last_fed_at or pet.born_at
                if _ref.tzinfo is None:
                    _ref = _ref.replace(tzinfo=_dt.timezone.utc)
                _hours = max(0.0, (_now - _ref).total_seconds() / 3600)
                hunger = max(0, round(100 - _hours / 72 * 100))
                mood   = round(pet.mood or 0)
                pet_line = (
                    f"🐾 Питомец: <b>{pet.pet_name}</b> · Ур.{pet.level} · "
                    f"🍖{hunger}% 😊{mood}%\n"
                )

            # ── Active relationship bond + streak ─────────────────────────────
            rel = (await db.execute(
                select(Relationship).where(
                    or_(Relationship.user_a_id == uid, Relationship.user_b_id == uid),
                    Relationship.status == "active",
                ).limit(1)
            )).scalar_one_or_none()
            if rel:
                tier_map = {
                    "friends": "👫 Друзья",
                    "dating":  "💕 Влюблённые",
                    "married": "💍 Пара",
                }
                tier_label  = tier_map.get(rel.rel_type, rel.rel_type)
                streak_days = 0
                if rel.meta:
                    try:
                        meta        = _json.loads(rel.meta)
                        streak_days = meta.get("streak", {}).get("days", 0)
                    except Exception:
                        pass
                streak_line = f"🔥 Стрик пары: <b>{streak_days} дн.</b>\n" if streak_days else ""
                bond_line   = f"💞 Связь: <b>{tier_label} · Ур.{rel.level}</b>\n" + streak_line

    except Exception:
        logger.exception("/me: DB query failed for user %s", uid)

    since_line = f"📅 В боте с: {member_since}\n" if member_since else ""

    def _fmt(n: int) -> str:
        """Format integer with thin-space thousands separator."""
        return f"{n:,}".replace(",", "\u2009")

    text = (
        f"👤 <b>Мой профиль</b>\n\n"
        f"🪙 Монеты: <b>{_fmt(balance)}</b>\n"
        f"💬 Сообщений: <b>{_fmt(msg_count)}</b>\n"
        f"⭐ Подписка: <b>{sub_label}</b>\n"
        + pet_line
        + bond_line
        + since_line
    )

    kb = None
    if settings.webhook_base_url:
        base_url = settings.webhook_base_url.rstrip("/")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📊 Открыть мини-приложение", web_app=WebAppInfo(url=base_url + "/app"))
        ]])

    await message.answer(text.strip(), parse_mode="HTML", reply_markup=kb)


# ══════════════════════════════════════════════════════════════════════════════
# /broadcast — admin-only: send any message to all users via copy_message
# ══════════════════════════════════════════════════════════════════════════════

# State: admin_user_id → {"from_chat_id": int, "message_id": int} | None (waiting)
_pending_broadcast: dict[int, dict | None] = {}


def _is_admin(username: str | None) -> bool:
    if not username:
        return False
    settings = get_settings()
    return username.lstrip("@").lower() == settings.miniapp_admin_username.lstrip("@").lower()


def _broadcast_confirm_kb(count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Разослать {count} пользователям", callback_data="broadcast_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast_cancel")],
    ])


@router.message(Command("broadcast"))
async def on_broadcast_cmd(message: Message) -> None:
    """Start broadcast session — admin only."""
    if not message.from_user or not _is_admin(message.from_user.username):
        await message.answer("⛔ Нет доступа.")
        return

    _pending_broadcast[message.from_user.id] = None  # mark as waiting

    await message.answer(
        "📢 <b>Режим рассылки</b>\n\n"
        "Отправь мне <b>одно сообщение</b> — текст, фото, видео, с любым форматированием "
        "и премиум-эмодзи. Я покажу тебе превью и попрошу подтверждение перед отправкой.\n\n"
        "Чтобы отменить — нажми кнопку ниже.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast_cancel")]
        ]),
    )


@router.message(F.func(lambda m: m.from_user and m.from_user.id in _pending_broadcast and _pending_broadcast.get(m.from_user.id) is None))
async def on_broadcast_message(message: Message) -> None:
    """Capture the message the admin wants to broadcast."""
    if not message.from_user:
        return

    admin_id = message.from_user.id

    # Count recipients
    recipient_count = 0
    try:
        from app.models.business_connection import BusinessConnection  # noqa: PLC0415
        async with session_scope() as db:
            from sqlalchemy import func as _func, distinct  # noqa: PLC0415
            result = await db.execute(
                select(_func.count(distinct(BusinessConnection.user_telegram_id)))
                .where(BusinessConnection.is_blocked.is_(False))
            )
            recipient_count = result.scalar_one() or 0
    except Exception as exc:
        logger.warning("broadcast: failed to count recipients: %s", exc)

    # Store the source message reference
    _pending_broadcast[admin_id] = {
        "from_chat_id": message.chat.id,
        "message_id":   message.message_id,
    }

    # Forward to admin as preview, then ask for confirmation
    await message.answer(
        f"👆 <b>Превью сообщения</b>\n\n"
        f"Сообщение выше будет скопировано <b>{recipient_count}</b> пользователям "
        f"(все с активным подключением бота).\n\n"
        f"Подтверждаешь рассылку?",
        parse_mode="HTML",
        reply_markup=_broadcast_confirm_kb(recipient_count),
    )


@router.callback_query(F.data == "broadcast_confirm")
async def on_broadcast_confirm(callback: CallbackQuery) -> None:
    await callback.answer()
    if not callback.from_user or not _is_admin(callback.from_user.username):
        await callback.answer("⛔ Нет доступа.", show_alert=True)
        return

    admin_id = callback.from_user.id
    pending = _pending_broadcast.pop(admin_id, None)
    if not pending:
        await callback.message.edit_text("⚠️ Нет активной рассылки. Начни заново: /broadcast")  # type: ignore[union-attr]
        return

    from_chat_id = pending["from_chat_id"]
    source_msg_id = pending["message_id"]

    await callback.message.edit_text("⏳ Рассылаем…")  # type: ignore[union-attr]

    # Fetch recipients
    owner_ids: list[int] = []
    try:
        from app.models.business_connection import BusinessConnection  # noqa: PLC0415
        from sqlalchemy import distinct  # noqa: PLC0415
        async with session_scope() as db:
            result = await db.execute(
                select(distinct(BusinessConnection.user_telegram_id))
                .where(BusinessConnection.is_blocked.is_(False))
            )
            owner_ids = [row[0] for row in result.all()]
    except Exception as exc:
        logger.error("broadcast_confirm: failed to fetch recipients: %s", exc)
        await callback.message.edit_text("❌ Не удалось получить список пользователей.")  # type: ignore[union-attr]
        return

    sent = 0
    failed = 0
    for uid in owner_ids:
        try:
            await callback.bot.copy_message(  # type: ignore[union-attr]
                chat_id=uid,
                from_chat_id=from_chat_id,
                message_id=source_msg_id,
            )
            sent += 1
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            failed += 1
            logger.debug("broadcast: skip uid=%s: %s", uid, exc)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.warning("broadcast: failed uid=%s: %s", uid, exc)

    logger.info(
        "Admin @%s broadcast via bot: sent=%s failed=%s",
        callback.from_user.username, sent, failed,
    )
    await callback.message.edit_text(  # type: ignore[union-attr]
        f"✅ <b>Рассылка завершена</b>\n\n"
        f"Отправлено: <b>{sent}</b>\n"
        f"Не доставлено: <b>{failed}</b>",
        parse_mode="HTML",
    )


# ══════════════════════════════════════════════════════════════════════════════
# /refund — admin-only: refund Stars for a specific payment charge ID
# ══════════════════════════════════════════════════════════════════════════════

@router.message(Command("refund"))
async def on_refund_cmd(message: Message) -> None:
    """Refund Stars for a payment. Usage: /refund <user_id> <charge_id>"""
    if not message.from_user or not _is_admin(message.from_user.username):
        await message.answer("⛔ Нет доступа.")
        return

    # Parse args
    args = (message.text or "").split(maxsplit=2)[1:]  # skip "/refund"
    if len(args) < 2:
        await message.answer(
            "⚠️ Использование:\n"
            "<code>/refund &lt;user_id&gt; &lt;charge_id&gt;</code>\n\n"
            "Где:\n"
            "• <code>user_id</code> — Telegram ID пользователя\n"
            "• <code>charge_id</code> — ID операции (например: "
            "<code>5678901234_1234_56789</code>)",
            parse_mode="HTML",
        )
        return

    raw_uid, charge_id = args[0], args[1].strip()
    try:
        user_id = int(raw_uid)
    except ValueError:
        await message.answer(f"❌ Неверный user_id: <code>{raw_uid}</code>", parse_mode="HTML")
        return

    # ── Step 1: Telegram refund ───────────────────────────────────────────────
    processing = await message.answer("⏳ Выполняем возврат…")
    try:
        await message.bot.refund_star_payment(  # type: ignore[union-attr]
            user_id=user_id,
            telegram_payment_charge_id=charge_id,
        )
        tg_ok = True
        tg_error: str | None = None
    except Exception as exc:
        tg_ok = False
        tg_error = str(exc)
        logger.warning("refund: Telegram API error uid=%s charge=%s: %s", user_id, charge_id, exc)

    # ── Step 2: mark subscription as refunded in DB (best-effort) ────────────
    db_info: str = "не найдена в БД"
    if tg_ok:
        try:
            from app.models.subscription import UserSubscription  # noqa: PLC0415
            from sqlalchemy import update as sa_update             # noqa: PLC0415
            async with session_scope() as db:
                result = await db.execute(
                    select(UserSubscription).where(
                        UserSubscription.payment_charge_id == charge_id
                    )
                )
                sub = result.scalar_one_or_none()
                if sub is not None:
                    sub_type = sub.sub_type or "premium"
                    stars = sub.stars_paid
                    await db.execute(
                        sa_update(UserSubscription)
                        .where(UserSubscription.payment_charge_id == charge_id)
                        .values(is_active=False, status="refunded")
                    )
                    db_info = (
                        f"подписка <b>{sub_type}</b> деактивирована "
                        f"(<b>{stars}⭐</b> → refunded)"
                    )
                else:
                    db_info = "запись в БД не найдена (разовая покупка?)"
        except Exception as exc:
            logger.warning("refund: DB update failed charge=%s: %s", charge_id, exc)
            db_info = f"ошибка обновления БД: {exc}"

    # ── Reply ─────────────────────────────────────────────────────────────────
    if tg_ok:
        text = (
            "✅ <b>Возврат выполнен</b>\n\n"
            f"👤 User ID: <code>{user_id}</code>\n"
            f"🔑 Charge ID: <code>{charge_id}</code>\n"
            f"🗄 БД: {db_info}"
        )
    else:
        text = (
            "❌ <b>Ошибка возврата</b>\n\n"
            f"👤 User ID: <code>{user_id}</code>\n"
            f"🔑 Charge ID: <code>{charge_id}</code>\n\n"
            f"Ответ Telegram:\n<code>{tg_error}</code>"
        )

    logger.info(
        "Admin @%s refund uid=%s charge=%s ok=%s",
        message.from_user.username, user_id, charge_id, tg_ok,
    )
    try:
        await processing.edit_text(text, parse_mode="HTML")
    except Exception:
        await message.answer(text, parse_mode="HTML")


@router.callback_query(F.data == "broadcast_cancel")
async def on_broadcast_cancel(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.from_user:
        _pending_broadcast.pop(callback.from_user.id, None)
    try:
        await callback.message.edit_text("❌ Рассылка отменена.")  # type: ignore[union-attr]
    except Exception:
        pass
