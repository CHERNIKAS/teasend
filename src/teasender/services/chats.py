"""Import chats the account is a member of, and manage them.

Imported chats always land as `permission = unknown` and are therefore NOT
posted to until you explicitly mark them `allowed`/`owner` in the panel. This is
the safety default: importing your dialog list must never start posting anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from teasender.db.models import Chat


@dataclass(slots=True)
class DialogInfo:
    tg_chat_id: int
    title: str
    username: str | None


async def read_dialogs(telegram_service) -> list[DialogInfo]:
    """List groups/supergroups the account belongs to (skip private chats)."""
    out: list[DialogInfo] = []
    async for dialog in telegram_service.client.iter_dialogs():
        if dialog.is_group or dialog.is_channel:
            out.append(
                DialogInfo(
                    tg_chat_id=dialog.id,
                    title=dialog.name or str(dialog.id),
                    username=getattr(dialog.entity, "username", None),
                )
            )
    return out


async def import_dialogs(session: AsyncSession, dialogs: list[DialogInfo]) -> tuple[int, int]:
    """Upsert dialogs into `chats`. Returns (created, updated). Never changes an
    existing chat's permission — that stays under your control."""
    created = updated = 0
    for d in dialogs:
        existing = await session.scalar(
            select(Chat).where(Chat.tg_chat_id == d.tg_chat_id)
        )
        if existing is None:
            session.add(
                Chat(tg_chat_id=d.tg_chat_id, title=d.title, username=d.username)
            )
            created += 1
        else:
            existing.title = d.title
            existing.username = d.username
            existing.is_member = True
            updated += 1
    await session.commit()
    return created, updated
