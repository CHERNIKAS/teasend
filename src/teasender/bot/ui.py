"""Single-panel UI: exactly one live bot message in the chat.

- `open_panel`  -> switching sections from the bottom menu: delete the old panel
  and send one fresh message (it lands at the bottom).
- `edit_panel`  -> navigating inside a section (inline taps): edit in place, no
  duplicate messages ever (edit failures are swallowed, never resent).
- `set_panel`   -> update the tracked panel by id (e.g. progress -> result).
"""
from __future__ import annotations

from contextlib import suppress

# chat_id -> message_id of the current live panel.
_PANELS: dict[int, int] = {}


def remember(chat_id: int, message_id: int) -> None:
    _PANELS[chat_id] = message_id


async def delete_safe(bot, chat_id: int, message_id: int) -> None:
    with suppress(Exception):
        await bot.delete_message(chat_id, message_id)


async def open_panel(bot, chat_id: int, text: str, reply_markup=None, *, parse_mode="HTML") -> None:
    old = _PANELS.get(chat_id)
    if old is not None:
        await delete_safe(bot, chat_id, old)
    sent = await bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)
    _PANELS[chat_id] = sent.message_id


async def edit_panel(message, text: str, reply_markup=None, *, parse_mode="HTML") -> None:
    """Edit the message the user is interacting with, in place. Never resends."""
    _PANELS[message.chat.id] = message.message_id
    with suppress(Exception):
        await message.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)


async def set_panel(bot, chat_id: int, text: str, reply_markup=None, *, parse_mode="HTML") -> None:
    mid = _PANELS.get(chat_id)
    if mid is not None:
        try:
            await bot.edit_message_text(
                text, chat_id=chat_id, message_id=mid,
                parse_mode=parse_mode, reply_markup=reply_markup,
            )
            return
        except Exception:
            pass
    await open_panel(bot, chat_id, text, reply_markup, parse_mode=parse_mode)
