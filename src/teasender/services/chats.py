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
    """List groups and supergroups the account belongs to.

    Skips private chats and broadcast channels: Telethon reports both supergroups
    and broadcast channels as `is_channel`, so we tell them apart by the entity's
    `broadcast` flag and keep only groups/supergroups (where you can actually post
    as a member)."""
    out: list[DialogInfo] = []
    async for dialog in telegram_service.client.iter_dialogs():
        entity = dialog.entity
        if getattr(entity, "broadcast", False):
            continue  # broadcast channel — not a group, skip
        if dialog.is_group or dialog.is_channel:
            out.append(
                DialogInfo(
                    tg_chat_id=dialog.id,
                    title=dialog.name or str(dialog.id),
                    username=getattr(entity, "username", None),
                )
            )
    return out


async def broadcast_channel_ids(telegram_service) -> set[int]:
    """Ids of broadcast channels the account follows (not groups)."""
    ids: set[int] = set()
    async for dialog in telegram_service.client.iter_dialogs():
        if getattr(dialog.entity, "broadcast", False):
            ids.add(dialog.id)
    return ids


async def purge_channels(session: AsyncSession, telegram_service) -> int:
    """Delete previously-imported broadcast channels from the chats table."""
    ids = await broadcast_channel_ids(telegram_service)
    if not ids:
        return 0
    rows = list((await session.scalars(
        select(Chat).where(Chat.tg_chat_id.in_(ids))
    )).all())
    for chat in rows:
        await session.delete(chat)
    await session.commit()
    return len(rows)


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
