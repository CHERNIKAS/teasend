"""Admin access boundary for the panel (numeric user id whitelist)."""
from __future__ import annotations

from aiogram.filters import Filter
from aiogram.types import TelegramObject

from teasender.config import Settings


class IsAdmin(Filter):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def __call__(self, event: TelegramObject) -> bool:
        user = getattr(event, "from_user", None)
        return bool(user and self._settings.is_admin(user.id))
