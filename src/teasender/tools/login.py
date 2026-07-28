"""CLI: interactive one-time login to the personal account.

    python -m teasender.tools.login

Prompts for phone, the code Telegram sends, and (if set) your 2FA password —
entered by YOU, in your terminal. The resulting session string is encrypted with
your Fernet key and written to SESSION_FILE. The password is never stored.
"""
from __future__ import annotations

import asyncio

from telethon import TelegramClient
from telethon.sessions import StringSession

from teasender.config import get_settings
from teasender.core.security import load_cipher, save_session


async def _run() -> None:
    settings = get_settings()
    cipher = load_cipher(settings.secret_key_file)

    client = TelegramClient(StringSession(), settings.api_id, settings.api_hash)
    # Telethon's .start() handles the interactive phone/code/2FA prompts.
    await client.start()

    session_string = client.session.save()
    save_session(cipher, session_string, settings.session_file)
    me = await client.get_me()
    print(f"Logged in as {me.first_name} (id={me.id}).")
    print(f"Encrypted session written to {settings.session_file}.")
    await client.disconnect()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
