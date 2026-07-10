"""Repository for owner-created contact notes."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact_note import ContactNote


class ContactNoteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self, business_connection_id: str, chat_id: int, text: str
    ) -> ContactNote:
        note = ContactNote(
            business_connection_id=business_connection_id,
            chat_id=chat_id,
            text=text,
        )
        self._session.add(note)
        await self._session.flush()
        return note

    async def get_for_chat(
        self, business_connection_id: str, chat_id: int
    ) -> list[ContactNote]:
        stmt = (
            select(ContactNote)
            .where(
                ContactNote.business_connection_id == business_connection_id,
                ContactNote.chat_id == chat_id,
            )
            .order_by(ContactNote.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
