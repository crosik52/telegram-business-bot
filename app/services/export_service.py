"""Export message data to JSON / CSV."""

from __future__ import annotations

import csv
import io
import json
from typing import Literal

from app.models.message import Message


def _message_to_dict(message: Message) -> dict:
    return {
        "id": message.id,
        "business_connection_id": message.business_connection_id,
        "chat_id": message.chat_id,
        "message_id": message.message_id,
        "reply_to_message_id": message.reply_to_message_id,
        "sender_telegram_id": message.sender_telegram_id,
        "sender_username": message.sender_username,
        "sender_first_name": message.sender_first_name,
        "sender_last_name": message.sender_last_name,
        "text": message.text,
        "caption": message.caption,
        "original_text": message.original_text,
        "original_caption": message.original_caption,
        "media_type": message.media_type.value,
        "file_id": message.file_id,
        "sent_at": message.sent_at.isoformat() if message.sent_at else None,
        "is_edited": message.is_edited,
        "edit_count": message.edit_count,
        "last_edited_at": (
            message.last_edited_at.isoformat() if message.last_edited_at else None
        ),
        "is_deleted": message.is_deleted,
        "deleted_at": message.deleted_at.isoformat() if message.deleted_at else None,
    }


class ExportService:
    """Serializes a list of messages into JSON or CSV bytes."""

    def to_json(self, messages: list[Message]) -> str:
        payload = [_message_to_dict(m) for m in messages]
        return json.dumps(payload, indent=2, ensure_ascii=False)

    def to_csv(self, messages: list[Message]) -> str:
        rows = [_message_to_dict(m) for m in messages]
        buffer = io.StringIO()
        fieldnames = list(rows[0].keys()) if rows else [
            "id",
            "business_connection_id",
            "chat_id",
            "message_id",
            "reply_to_message_id",
            "sender_telegram_id",
            "sender_username",
            "sender_first_name",
            "sender_last_name",
            "text",
            "caption",
            "original_text",
            "original_caption",
            "media_type",
            "file_id",
            "sent_at",
            "is_edited",
            "edit_count",
            "last_edited_at",
            "is_deleted",
            "deleted_at",
        ]
        writer = csv.DictWriter(buffer, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return buffer.getvalue()

    def export(self, messages: list[Message], fmt: Literal["json", "csv"]) -> tuple[str, str, str]:
        """Returns (content, media_type, filename)."""

        if fmt == "json":
            return self.to_json(messages), "application/json", "messages_export.json"
        return self.to_csv(messages), "text/csv", "messages_export.csv"
