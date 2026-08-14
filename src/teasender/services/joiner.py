"""Rate-limited auto-join from the queue.

Joins at most `join_cap` chats per 24h, spread evenly (min gap = 24h / cap), so
Telegram doesn't flag the account for mass-joining.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func, select

from teasender.db.models import JoinQueue, as_utc, utcnow
from teasender.services.settings_store import JOIN_CAP, JOIN_ON, get_setting

log = logging.getLogger("teasender.joiner")


async def process_join(sessionmaker, telegram, notifier) -> None:
    async with sessionmaker() as s:
        if (await get_setting(s, JOIN_ON, "on")) != "on":
            return
        cap = int((await get_setting(s, JOIN_CAP, "5")) or "5")
        if cap <= 0:
            return
        since = utcnow() - timedelta(hours=24)
        joined_24h = await s.scalar(
            select(func.count()).select_from(JoinQueue)
            .where(JoinQueue.status == "joined", JoinQueue.joined_at >= since)
        )
        if (joined_24h or 0) >= cap:
            return
        # Spread joins evenly across the day.
        last = await s.scalar(
            select(func.max(JoinQueue.joined_at)).where(JoinQueue.status == "joined")
        )
        min_gap = timedelta(hours=24) / cap
        if last is not None and utcnow() - as_utc(last) < min_gap:
            return
        item = await s.scalar(
            select(JoinQueue).where(JoinQueue.status == "pending")
            .order_by(JoinQueue.added_at).limit(1)
        )
        if item is None:
            return
        item_id, ref = item.id, item.ref

    try:
        result = await telegram.join_ref(ref)
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        if name == "FloodWaitError":
            log.warning("join flood-wait, will retry later: %s", ref)
            return  # keep pending, try next cycle
        status = "skipped" if "AlreadyParticipant" in name else "failed"
        async with sessionmaker() as s:
            it = await s.get(JoinQueue, item_id)
            if it:
                it.status = status
                it.note = name
                await s.commit()
        if status == "failed":
            await notifier.send(f"⚠️ Не удалось вступить: {ref}\n{name}")
        return

    async with sessionmaker() as s:
        it = await s.get(JoinQueue, item_id)
        if it:
            it.status = "requested" if result == "requested" else "joined"
            it.joined_at = utcnow()
            await s.commit()
    log.info("joined %s (%s)", ref, result)
    await notifier.send(f"✅ Вступил: {ref}")
