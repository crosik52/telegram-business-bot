"""aiogram handlers for Telegram Business updates.

Covers every officially supported Business API update type:

- `business_connection`   -> connection lifecycle (created/updated/revoked)
- `business_message`      -> new incoming/outgoing message in a connected chat
- `edited_business_message` -> a business message was edited
- `deleted_business_messages` -> one or more business messages were deleted

Telegram Business API limitation (documented, not worked around):
Telegram does NOT send the deleted message's content in the
`deleted_business_messages` update — only chat_id + message_ids. This bot
therefore relies entirely on having captured the message beforehand via
`business_message` in order to preserve its content after deletion. If a
message was sent before this bot was connected, its content cannot be
recovered when later deleted. This is a Telegram platform limitation, not
a bug in this implementation.

Owner notifications: this bot is designed to be connected by *any* user
(multi-tenant "commercial" mode). Whenever the *counterparty* in a
connected chat edits or deletes a message, the bot DMs the connection
owner (the account that connected it) a formatted notification showing
the previous/new content, similar to Telegram's own edit-history UI.
"""

from __future__ import annotations

from html import escape as html_escape

from aiogram import Bot, Router
from aiogram.types import BusinessConnection, BusinessMessagesDeleted, Message
from sqlalchemy import select

from app.database.session import session_scope
from app.logging_config import get_logger
from app.models.business_connection import BusinessConnection as BCModel
from app.services.message_service import MessageService

logger = get_logger(__name__)
router = Router(name="business")

_PREVIEW_LIMIT = 500


def _preview(text: str | None) -> str:
    if not text:
        return "<i>(нет текста / медиа)</i>"
    trimmed = text if len(text) <= _PREVIEW_LIMIT else text[:_PREVIEW_LIMIT] + "…"
    return html_escape(trimmed)


def _counterpart_label(chat, owner_telegram_id: int) -> str:
    """A human-readable label for the other side of the chat."""

    if getattr(chat, "id", None) == owner_telegram_id:
        return "себя"
    parts = [p for p in (chat.first_name, chat.last_name) if p]
    name = " ".join(parts) or (chat.title or "собеседником")
    label = html_escape(name)
    if chat.username:
        label += f" (@{html_escape(chat.username)})"
    return label


async def _get_business_connection(business_connection_id: str) -> BCModel | None:
    async with session_scope() as session:
        result = await session.execute(
            select(BCModel).where(
                BCModel.business_connection_id == business_connection_id
            )
        )
        return result.scalar_one_or_none()


@router.business_connection()
async def on_business_connection(connection: BusinessConnection) -> None:
    """Persist the lifecycle of a Telegram Business connection."""

    async with session_scope() as session:
        result = await session.execute(
            select(BCModel).where(
                BCModel.business_connection_id == connection.id
            )
        )
        record = result.scalar_one_or_none()

        if record is None:
            record = BCModel(
                business_connection_id=connection.id,
                user_telegram_id=connection.user.id,
                user_first_name=connection.user.first_name,
                user_last_name=connection.user.last_name,
                user_username=connection.user.username,
                can_reply=connection.can_reply,
                is_enabled=connection.is_enabled,
            )
            session.add(record)
        else:
            record.can_reply = connection.can_reply
            record.is_enabled = connection.is_enabled
            record.user_first_name = connection.user.first_name
            record.user_last_name = connection.user.last_name
            record.user_username = connection.user.username

    logger.info(
        "Business connection %s enabled=%s can_reply=%s",
        connection.id,
        connection.is_enabled,
        connection.can_reply,
    )


@router.business_message()
async def on_business_message(message: Message) -> None:
    """Store every incoming/outgoing business message immediately."""

    if not message.business_connection_id:
        logger.warning("Received business_message without a connection id")
        return

    async with session_scope() as session:
        service = MessageService(session)
        await service.ingest_new_message(
            message, business_connection_id=message.business_connection_id
        )


@router.edited_business_message()
async def on_edited_business_message(message: Message, bot: Bot) -> None:
    """Preserve the original version, append the edited version, and notify the owner."""

    if not message.business_connection_id:
        logger.warning("Received edited_business_message without a connection id")
        return

    async with session_scope() as session:
        service = MessageService(session)
        outcome = await service.ingest_edited_message(
            message, business_connection_id=message.business_connection_id
        )

    if outcome.is_first_capture:
        # We never saw the pre-edit content, so there's nothing meaningful
        # to compare/notify about yet.
        return

    connection = await _get_business_connection(message.business_connection_id)
    if connection is None:
        logger.warning(
            "No stored BusinessConnection for id=%s; skipping owner notification",
            message.business_connection_id,
        )
        return

    sender = message.from_user
    if sender is not None and sender.id == connection.user_telegram_id:
        # The owner edited their own outgoing message — not what the
        # "notify me when the other side edits" feature is for.
        return

    if connection.is_blocked or not connection.notifications_enabled:
        logger.info(
            "Owner notifications disabled for connection_id=%s; skipping edit notification",
            connection.business_connection_id,
        )
        return

    counterpart = _counterpart_label(message.chat, connection.user_telegram_id)
    text = (
        f"✏️ {counterpart} отредактировал(а) сообщение:\n\n"
        f"🔍 <b>Прошлое значение:</b>\n«{_preview(outcome.previous_text or outcome.previous_caption)}»\n\n"
        f"📝 <b>Новое значение:</b>\n«{_preview(message.text or message.caption)}»"
    )

    try:
        await bot.send_message(chat_id=connection.user_telegram_id, text=text)
    except Exception:
        logger.exception(
            "Failed to notify owner user_telegram_id=%s about edit",
            connection.user_telegram_id,
        )


@router.deleted_business_messages()
async def on_deleted_business_messages(deleted: BusinessMessagesDeleted, bot: Bot) -> None:
    """Mark previously-stored messages as deleted and notify the owner.

    See module docstring: Telegram does not resend deleted content, so this
    only works for messages the bot had already captured.
    """

    if not deleted.business_connection_id:
        logger.warning("Received deleted_business_messages without a connection id")
        return

    connection = await _get_business_connection(deleted.business_connection_id)

    async with session_scope() as session:
        service = MessageService(session)
        for message_id in deleted.message_ids:
            removed = await service.mark_deleted(
                business_connection_id=deleted.business_connection_id,
                chat_id=deleted.chat.id,
                message_id=message_id,
            )

            if (
                connection is None
                or removed is None
                or removed.sender_telegram_id == connection.user_telegram_id
                or connection.is_blocked
                or not connection.notifications_enabled
            ):
                # Unknown connection, untracked message, the owner deleted
                # their own message, or notifications are disabled for this
                # connection — nothing to notify about.
                continue

            counterpart = _counterpart_label(deleted.chat, connection.user_telegram_id)
            text = (
                f"🗑 {counterpart} удалил(а) сообщение:\n\n"
                f"«{_preview(removed.text or removed.caption)}»"
            )
            try:
                await bot.send_message(chat_id=connection.user_telegram_id, text=text)
            except Exception:
                logger.exception(
                    "Failed to notify owner user_telegram_id=%s about deletion",
                    connection.user_telegram_id,
                )
