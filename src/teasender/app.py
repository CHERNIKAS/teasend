"""Entry point: bot panel + Telethon client + planner/sender loops.

    python -m teasender.app
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher

from teasender.bot.handlers import setup_handlers
from teasender.config import get_settings
from teasender.db.session import get_sessionmaker
from teasender.scheduler.runner import BackgroundRunner
from teasender.services.notify import Notifier
from teasender.services.sender import Sender
from teasender.telegram.client import TelegramService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("teasender")


async def main() -> None:
    settings = get_settings()
    sessionmaker = get_sessionmaker()

    telegram = TelegramService(settings)
    await telegram.start()

    bot = Bot(settings.bot_token)
    dp = Dispatcher()

    notifier = Notifier(bot, settings.admin_user_ids)
    sender = Sender(sessionmaker, telegram, settings, notifier)
    runner = BackgroundRunner(settings, sender)

    dp["sessionmaker"] = sessionmaker
    dp["settings"] = settings
    dp["telegram"] = telegram
    dp["sender"] = sender
    dp["runner"] = runner
    setup_handlers(dp, settings)

    await runner.plan_now()
    runner.start()

    mode = "DRY-RUN (без реальной отправки)" if settings.dry_run else "БОЕВОЙ"
    log.info("TeaSender started — режим: %s", mode)
    await notifier.send(f"🚀 TeaSender запущен. Режим: {mode}")

    try:
        await dp.start_polling(bot)
    finally:
        runner.shutdown()
        await telegram.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
