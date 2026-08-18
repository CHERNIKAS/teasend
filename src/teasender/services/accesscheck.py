"""Proactively check our standing in chats (member / banned / can't post).

Throttled and batched. Chats where we're not a member / banned / can't send get
sending turned off and a note, so you can see them without trying to post.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import func, select

from teasender.core.enums import Permission
from teasender.db.models import Chat

log = logging.getLogger("teasender.accesscheck")

_PROBLEM = {"не участник", "забанен", "нет отправки"}


async def scan_access(sessionmaker, telegram, limit: int = 50) -> dict:
    """Check up to `limit` not-yet-checked chats. Returns a summary."""
    async with sessionmaker() as s:
        chats = list((await s.scalars(
            select(Chat).where(Chat.permission_note.is_(None)).order_by(Chat.id).limit(limit)
        )).all())
        targets = [(c.id, c.tg_chat_id) for c in chats]

    checked = problems = 0
    by_status: dict[str, int] = {}
    for chat_id, tg_id in targets:
        status = await telegram.check_access(tg_id)
        by_status[status] = by_status.get(status, 0) + 1
        async with sessionmaker() as s:
            c = await s.get(Chat, chat_id)
            if c is not None:
                c.permission_note = status
                if status in _PROBLEM:
                    c.is_enabled = False
                    c.permission = Permission.denied
                    problems += 1
                await s.commit()
        checked += 1
        await asyncio.sleep(1.2)

    async with sessionmaker() as s:
        remaining = await s.scalar(
            select(func.count()).select_from(Chat).where(Chat.permission_note.is_(None))
        )
    log.info("access scan: %d checked, %d problems", checked, problems)
    return {"checked": checked, "problems": problems, "by_status": by_status, "remaining": remaining or 0}
