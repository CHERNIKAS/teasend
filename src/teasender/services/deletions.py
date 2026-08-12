"""React when one of our sent posts is deleted from a chat.

A deleted announcement almost always means ads aren't welcome there, so we
auto-deny the chat (stops further posting, protects the account) and notify.
The chat can be re-allowed manually in the panel.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from teasender.core.enums import LogLevel, Permission, PublicationStatus
from teasender.db.models import Chat, LogEntry, Publication
from teasender.services.notify import Notifier

log = logging.getLogger("teasender.deletions")

# Message ids the bot itself deleted (e.g. /cleanup_bad) — so our own deletions
# don't trigger the "someone deleted our post" auto-deny.
_SELF_DELETED: set[int] = set()


def mark_self_deleted(ids: list[int]) -> None:
    _SELF_DELETED.update(ids)


def _peer_from_channel_id(channel_id: int | None) -> int | None:
    """Telethon reports a bare channel id (e.g. 1586677744); our chats store the
    Bot-API style id (-1001586677744). Convert so we can match."""
    if channel_id is None:
        return None
    return int(f"-100{channel_id}")


async def handle_deletions(
    sessionmaker: async_sessionmaker[AsyncSession],
    notifier: Notifier,
    deleted_ids: list[int],
    channel_id: int | None,
) -> None:
    # Ignore deletions the bot performed itself (consume them from the set).
    remaining = []
    for i in deleted_ids:
        if i in _SELF_DELETED:
            _SELF_DELETED.discard(i)
        else:
            remaining.append(i)
    deleted_ids = remaining
    if not deleted_ids:
        return

    peer = _peer_from_channel_id(channel_id)
    async with sessionmaker() as s:
        stmt = (
            select(Publication)
            .where(
                Publication.tg_message_id.in_(deleted_ids),
                Publication.status == PublicationStatus.sent,
            )
        )
        pubs = list((await s.scalars(stmt)).all())
        if not pubs:
            return

        touched: dict[int, Chat] = {}
        for pub in pubs:
            chat = await s.get(Chat, pub.chat_id)
            if chat is None:
                continue
            # If we know the channel, only act on deletions from that channel.
            if peer is not None and chat.tg_chat_id != peer:
                continue
            s.add(LogEntry(
                level=LogLevel.warning,
                event="post_deleted",
                chat_id=chat.id,
                publication_id=pub.id,
                message="Наш пост удалён из чата",
            ))
            chat.permission = Permission.denied
            chat.is_enabled = False
            touched[chat.id] = chat
        await s.commit()

    for chat in touched.values():
        log.info("post deleted in chat %s -> auto-denied", chat.title)
        await notifier.send(
            f"🗑 Наш пост удалили в «{chat.title}».\n"
            f"Чат авто-запрещён (⛔), рассылка туда остановлена. "
            f"Вернуть можно в «Чаты» → карточка → ✅ Разрешить."
        )
