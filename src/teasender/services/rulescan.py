"""Scan chats' description/pinned for posting rules and apply them.

Throttled and batched so it never floods. A chat with ads forbidden gets its
sending turned off; a frequency rule sets its min interval.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import or_, select

from teasender.core.enums import Permission
from teasender.db.models import Chat
from teasender.services.rules import parse_rules

log = logging.getLogger("teasender.rulescan")

_ELIGIBLE = (Permission.allowed, Permission.owner)


async def scan_batch(sessionmaker, telegram, limit: int = 60) -> dict:
    """Scan up to `limit` not-yet-scanned chats. Returns a summary dict."""
    async with sessionmaker() as s:
        chats = list((await s.scalars(
            select(Chat).where(Chat.rule_note.is_(None)).order_by(Chat.id).limit(limit)
        )).all())
        targets = [(c.id, c.tg_chat_id) for c in chats]

    scanned = rules_found = ads_off = 0
    for chat_id, tg_id in targets:
        try:
            text = await telegram.read_chat_rules(tg_id)
        except Exception:  # noqa: BLE001
            text = ""
        rule = parse_rules(text)
        async with sessionmaker() as s:
            c = await s.get(Chat, chat_id)
            if c is not None:
                c.rule_min_interval_h = rule.min_interval_h
                c.rule_ads_forbidden = rule.ads_forbidden
                c.rule_note = rule.note or "нет правил"
                if rule.ads_forbidden and c.is_enabled:
                    c.is_enabled = False  # don't post where ads are forbidden
                    ads_off += 1
                if rule.min_interval_h or rule.ads_forbidden:
                    rules_found += 1
                await s.commit()
        scanned += 1
        await asyncio.sleep(1.5)  # be gentle with the API

    async with sessionmaker() as s:
        remaining = await s.scalar(
            select(Chat.id).where(Chat.rule_note.is_(None)).limit(1)
        )
        remaining_n = 0
        if remaining is not None:
            from sqlalchemy import func
            remaining_n = await s.scalar(
                select(func.count()).select_from(Chat).where(Chat.rule_note.is_(None))
            )
    log.info("rule scan: %d scanned, %d rules, %d ads-off", scanned, rules_found, ads_off)
    return {"scanned": scanned, "rules": rules_found, "ads_off": ads_off, "remaining": remaining_n}
