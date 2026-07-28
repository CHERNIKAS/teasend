"""Router assembly with the admin filter applied at router level."""
from __future__ import annotations

from aiogram import Dispatcher

from teasender.bot.handlers import chats, menu, templates
from teasender.bot.security import IsAdmin
from teasender.config import Settings


def setup_handlers(dp: Dispatcher, settings: Settings) -> None:
    admin = IsAdmin(settings)
    for module in (menu, chats, templates):
        module.router.message.filter(admin)
        module.router.callback_query.filter(admin)
        dp.include_router(module.router)
