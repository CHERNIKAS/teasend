"""Background loops: (re)plan the day and send what is due.

Two jobs:
  * plan  — tops up each chat's daily quota within its rules (every 30 min).
  * send  — delivers due publications one at a time (every 60 s).

Both are guarded by max_instances=1 so a long run never overlaps itself.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from teasender.config import Settings
from teasender.db.session import get_sessionmaker
from teasender.services.planner import plan_day
from teasender.services.sender import Sender

log = logging.getLogger("teasender.scheduler")


class BackgroundRunner:
    def __init__(self, settings: Settings, sender: Sender) -> None:
        self._settings = settings
        self._sender = sender
        self._scheduler = AsyncIOScheduler(timezone=settings.timezone)

    async def _plan(self) -> None:
        async with get_sessionmaker()() as s:
            n = await plan_day(s, self._settings.timezone)
        if n:
            log.info("planned %d publications", n)

    async def _send(self) -> None:
        await self._sender.run_due_once()

    def start(self) -> None:
        self._scheduler.add_job(
            self._plan, "interval", minutes=30, id="plan",
            max_instances=1, coalesce=True, next_run_time=None,
        )
        self._scheduler.add_job(
            self._send, "interval", seconds=60, id="send",
            max_instances=1, coalesce=True,
        )
        self._scheduler.start()

    async def plan_now(self) -> None:
        await self._plan()

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
