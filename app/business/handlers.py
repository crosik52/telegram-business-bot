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
"""

from __future__ import annotations

from aiogram import Router
from aiogram.types import BusinessConnection, BusinessMessagesDeleted, Message

from app.database.session import session_scope
from app.logging_config import get_logger
from app.services.message_service import MessageService

logger = get_logger(__name__)
router = Router(name="business")


@router.business_connection()
async def on_business_connection(connection: BusinessConnection) -> None:
    """Persist the lifecycle of a Telegram Business connection."""

    from sqlalchemy import select

    from app.models.business_connection import BusinessConnection as BCModel

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
async def on_edited_business_message(message: Message) -> None:
    """Preserve the original version and append the edited version."""

    if not message.business_connection_id:
        logger.warning("Received edited_business_message without a connection id")
        return

    async with session_scope() as session:
        service = MessageService(session)
        await service.ingest_edited_message(
            message, business_connection_id=message.business_connection_id
        )


@router.deleted_business_messages()
async def on_deleted_business_messages(deleted: BusinessMessagesDeleted) -> None:
    """Mark previously-stored messages as deleted (soft delete only).

    See module docstring: Telegram does not resend deleted content, so this
    only works for messages the bot had already captured.
    """

    if not deleted.business_connection_id:
        logger.warning("Received deleted_business_messages without a connection id")
        return

    async with session_scope() as session:
        service = MessageService(session)
        for message_id in deleted.message_ids:
            await service.mark_deleted(
                business_connection_id=deleted.business_connection_id,
                chat_id=deleted.chat.id,
                message_id=message_id,
            )
