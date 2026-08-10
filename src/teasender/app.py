"""Entry point: bot panel + Telethon client + planner/sender loops.

    python -m teasender.app
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from telethon import events

from teasender.bot.handlers import setup_handlers
from teasender.config import get_settings
from teasender.db.session import get_sessionmaker
from teasender.scheduler.runner import BackgroundRunner
from teasender.services import monitor
from teasender.services.deletions import handle_deletions
from teasender.services.monitor import handle_incoming
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
    runner = BackgroundRunner(settings, sender, telegram, notifier)

    await monitor.load_keywords(sessionmaker)
    try:
        me = await telegram.client.get_me()
        monitor.set_me(me.id, getattr(me, "username", None))
    except Exception:  # noqa: BLE001
        log.warning("could not resolve account for mention detection")

    @telegram.client.on(events.MessageDeleted)
    async def _on_msg_deleted(event) -> None:  # noqa: ANN001
        try:
            await handle_deletions(
                sessionmaker, notifier, event.deleted_ids,
                getattr(event, "channel_id", None),
            )
        except Exception:  # noqa: BLE001
            log.exception("deletion handler failed")

    @telegram.client.on(events.NewMessage(incoming=True))
    async def _on_new_message(event) -> None:  # noqa: ANN001
        try:
            await handle_incoming(sessionmaker, notifier, event)
        except Exception:  # noqa: BLE001
            log.exception("monitor handler failed")

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
