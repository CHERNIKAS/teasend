"""CLI: import the account's group/channel dialogs into the DB.

    python -m teasender.tools.import_chats

All imported chats start as permission=unknown (not posted to). Mark the ones
where announcements are allowed in the bot panel.
"""
from __future__ import annotations

import asyncio

from teasender.config import get_settings
from teasender.db.session import get_sessionmaker
from teasender.services.chats import import_dialogs, read_dialogs
from teasender.telegram.client import TelegramService


async def _run() -> None:
    settings = get_settings()
    tg = TelegramService(settings)
    await tg.start()
    try:
        dialogs = await read_dialogs(tg)
        async with get_sessionmaker()() as s:
            created, updated = await import_dialogs(s, dialogs)
        print(f"Imported: +{created} new, {updated} updated.")
        print("All new chats are 'unknown' — enable the allowed ones in the panel.")
    finally:
        await tg.close()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
