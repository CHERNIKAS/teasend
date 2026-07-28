"""Admin notifications via the control bot."""
from __future__ import annotations

import logging

from aiogram import Bot

log = logging.getLogger("teasender.notify")


class Notifier:
    def __init__(self, bot: Bot, admin_ids: list[int]) -> None:
        self._bot = bot
        self._admin_ids = admin_ids

    async def send(self, text: str) -> None:
        for uid in self._admin_ids:
            try:
                await self._bot.send_message(uid, text)
            except Exception as exc:  # notification must never break the pipeline
                log.warning("failed to notify %s: %s", uid, exc)
