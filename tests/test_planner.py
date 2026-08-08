"""Planner: permission gate, per-chat quota, idempotency."""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

# Plan as if at the very start of the window, so the full daily quota applies
# (the planner scales the quota down for late starts).
_START_OF_DAY = datetime(2026, 6, 1, 0, 1, tzinfo=ZoneInfo("Europe/Istanbul"))

from teasender.core.enums import Permission, PublicationStatus
from teasender.db.models import Chat, Publication, Template
from teasender.services.planner import plan_day
from sqlalchemy import func, select


async def _seed_template(s):
    t = Template(source_channel_id=-100, source_message_id=1, label="promo",
                 preview_text="Чай", is_active=True)
    s.add(t)
    await s.flush()
    return t


def _chat(**kw) -> Chat:
    base = dict(
        tg_chat_id=kw.pop("tg"), title=kw.pop("title", "c"),
        permission=Permission.allowed, is_enabled=kw.pop("is_enabled", True),
        posts_per_day=2, min_interval_minutes=60,
        window_start=time(0, 0), window_end=time(23, 59), days_mask=0b1111111,
    )
    base.update(kw)
    return Chat(**base)


@pytest.mark.asyncio
async def test_send_gate_and_quota(sessionmaker):
    # The send gate is is_enabled ("отправка"), independent of the permission label.
    async with sessionmaker() as s:
        await _seed_template(s)
        s.add_all([
            _chat(tg=-1, title="on", is_enabled=True, posts_per_day=2),
            _chat(tg=-2, title="off", is_enabled=False, posts_per_day=2),
            # Denied label but sending explicitly enabled -> still broadcasts.
            _chat(tg=-3, title="denied_on", permission=Permission.denied, is_enabled=True, posts_per_day=2),
        ])
        await s.commit()

        n = await plan_day(s, "Europe/Istanbul", now=_START_OF_DAY)

    async with sessionmaker() as s:
        total = await s.scalar(select(func.count()).select_from(Publication))
        by_chat = (await s.execute(
            select(Chat.title, func.count(Publication.id))
            .join(Publication, Publication.chat_id == Chat.id)
            .group_by(Chat.title)
        )).all()

    assert n == 4  # two enabled chats, 2 posts each
    assert total == 4
    assert dict(by_chat) == {"on": 2, "denied_on": 2}


@pytest.mark.asyncio
async def test_idempotent_topup(sessionmaker):
    async with sessionmaker() as s:
        await _seed_template(s)
        s.add(_chat(tg=-1, title="allowed", posts_per_day=3, min_interval_minutes=30))
        await s.commit()
        await plan_day(s, "Europe/Istanbul", now=_START_OF_DAY)
        await plan_day(s, "Europe/Istanbul", now=_START_OF_DAY)  # second run must not exceed quota

    async with sessionmaker() as s:
        total = await s.scalar(
            select(func.count()).select_from(Publication)
            .where(Publication.status == PublicationStatus.planned)
        )
    assert total == 3


@pytest.mark.asyncio
async def test_days_mask_excludes_today(sessionmaker):
    async with sessionmaker() as s:
        await _seed_template(s)
        # days_mask=0 -> no weekday enabled -> nothing planned.
        s.add(_chat(tg=-1, title="allowed", days_mask=0))
        await s.commit()
        n = await plan_day(s, "Europe/Istanbul", now=_START_OF_DAY)
    assert n == 0
