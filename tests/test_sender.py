"""Sender: real delivery path, counters, and flood auto-pause."""
from __future__ import annotations

from datetime import time, timedelta

import pytest
from sqlalchemy import select

from teasender.config import get_settings
from teasender.core.enums import AccountState, Permission, PublicationStatus
from teasender.db.models import Account, Chat, Publication, Template, utcnow
from teasender.services.sender import Sender


class FakeTelegram:
    def __init__(self, raise_exc=None):
        self.calls = []
        self._raise = raise_exc

    async def copy_to(self, target, ch, mid, gid):
        if self._raise is not None:
            raise self._raise
        self.calls.append(target)
        return 555


class FakeNotifier:
    def __init__(self):
        self.msgs = []

    async def send(self, text):
        self.msgs.append(text)


class FloodWaitError(Exception):
    def __init__(self, seconds):
        super().__init__("flood")
        self.seconds = seconds


async def _seed(s, **chat_kw):
    t = Template(source_channel_id=-100, source_message_id=1, label="p", is_active=True)
    s.add(t)
    await s.flush()
    chat = Chat(
        tg_chat_id=-1, title="allowed", permission=Permission.allowed, is_enabled=True,
        posts_per_day=1, min_interval_minutes=1,
        window_start=time(0, 0), window_end=time(23, 59), days_mask=0b1111111,
        **chat_kw,
    )
    s.add(chat)
    await s.flush()
    pub = Publication(
        chat_id=chat.id, template_id=t.id,
        scheduled_at=utcnow() - timedelta(minutes=1),
        status=PublicationStatus.planned,
    )
    s.add(pub)
    await s.commit()
    return chat.id, pub.id


@pytest.mark.asyncio
async def test_successful_delivery(sessionmaker):
    async with sessionmaker() as s:
        chat_id, pub_id = await _seed(s)

    tg, notifier = FakeTelegram(), FakeNotifier()
    sender = Sender(sessionmaker, tg, get_settings(), notifier)
    await sender.run_due_once()

    async with sessionmaker() as s:
        pub = await s.get(Publication, pub_id)
        chat = await s.get(Chat, chat_id)

    assert pub.status == PublicationStatus.sent
    assert pub.tg_message_id == 555
    assert chat.success_count == 1 and chat.last_sent_at is not None
    assert tg.calls == [-1]
    assert any("Доставлено" in m for m in notifier.msgs)


@pytest.mark.asyncio
async def test_flood_wait_auto_pauses(sessionmaker):
    async with sessionmaker() as s:
        _chat_id, pub_id = await _seed(s)

    tg = FakeTelegram(raise_exc=FloodWaitError(seconds=120))
    notifier = FakeNotifier()
    sender = Sender(sessionmaker, tg, get_settings(), notifier)
    await sender.run_due_once()

    async with sessionmaker() as s:
        pub = await s.get(Publication, pub_id)
        acc = await s.scalar(select(Account).limit(1))

    # Publication returns to planned (retry later); account is flood-paused.
    assert pub.status == PublicationStatus.planned
    assert acc.state == AccountState.flood_paused
    assert acc.flood_until is not None
    assert any("паузе" in m.lower() for m in notifier.msgs)


@pytest.mark.asyncio
async def test_paused_account_sends_nothing(sessionmaker):
    async with sessionmaker() as s:
        _chat_id, pub_id = await _seed(s)
        acc = await s.scalar(select(Account).limit(1))
        acc.state = AccountState.paused
        await s.commit()

    tg, notifier = FakeTelegram(), FakeNotifier()
    sender = Sender(sessionmaker, tg, get_settings(), notifier)
    await sender.run_due_once()

    async with sessionmaker() as s:
        pub = await s.get(Publication, pub_id)
    assert pub.status == PublicationStatus.planned
    assert tg.calls == []
