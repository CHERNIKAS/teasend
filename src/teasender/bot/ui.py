"""Single-panel UI helpers: keep one live message per chat instead of spamming.

Every section (status, chats, templates) is rendered into the SAME message,
which we edit in place. Button taps that arrive as user messages are deleted so
the chat with the bot stays clean.
"""
from __future__ import annotations

from contextlib import suppress

# chat_id -> message_id of the current live panel.
_PANELS: dict[int, int] = {}


def remember_panel(chat_id: int, message_id: int) -> None:
    _PANELS[chat_id] = message_id


async def delete_safe(bot, chat_id: int, message_id: int) -> None:
    with suppress(Exception):
        await bot.delete_message(chat_id, message_id)


async def show_panel(
    bot, chat_id: int, text: str, reply_markup=None, *, parse_mode: str = "HTML"
) -> None:
    """Edit the existing panel if we have one; otherwise send a new panel."""
    mid = _PANELS.get(chat_id)
    if mid is not None:
        try:
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=mid,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            return
        except Exception:
            pass  # panel was deleted / not modified -> fall through to resend
    sent = await bot.send_message(
        chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup
    )
    _PANELS[chat_id] = sent.message_id
