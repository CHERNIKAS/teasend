"""Incoming-message monitor: replies to our posts, mentions, keyword hits.

Notifies the admin with a link to the message so a lead is never missed. The bot
never replies on your behalf.
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from teasender.core.enums import PublicationStatus
from teasender.db.models import Chat, Publication
from teasender.services.settings_store import KEYWORDS, get_setting

log = logging.getLogger("teasender.monitor")

# In-memory keyword list (shared with the bot handler that edits it).
_KEYWORDS: list[str] = []


def parse_keywords(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [w.strip().lower() for w in raw.replace("\n", ",").split(",") if w.strip()]


def set_keywords(words: list[str]) -> None:
    _KEYWORDS[:] = words


def get_keywords() -> list[str]:
    return list(_KEYWORDS)


async def load_keywords(sessionmaker) -> None:
    async with sessionmaker() as s:
        raw = await get_setting(s, KEYWORDS, "")
    set_keywords(parse_keywords(raw))


def _message_link(chat_tg_id: int, username: str | None, msg_id: int) -> str:
    if username:
        return f"https://t.me/{username}/{msg_id}"
    internal = str(chat_tg_id).replace("-100", "").lstrip("-")
    return f"https://t.me/c/{internal}/{msg_id}"


async def handle_incoming(sessionmaker, notifier, event) -> None:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    msg = event.message
    if msg is None or getattr(msg, "out", False):
        return  # our own message
    text = msg.raw_text or ""
    low = text.lower()
    chat_tg = event.chat_id

    reasons: list[str] = []

    reply_id = msg.reply_to_msg_id
    if reply_id:
        async with sessionmaker() as s:
            hit = await s.scalar(
                select(Publication.id)
                .join(Chat, Chat.id == Publication.chat_id)
                .where(
                    Publication.tg_message_id == reply_id,
                    Chat.tg_chat_id == chat_tg,
                    Publication.status == PublicationStatus.sent,
                )
            )
        if hit:
            reasons.append("💬 Ответ на твой пост")

    if getattr(msg, "mentioned", False):
        reasons.append("🔔 Упоминание")

    kw_hits = [k for k in _KEYWORDS if k in low]
    if kw_hits:
        reasons.append("🔎 Ключевое: " + ", ".join(kw_hits))

    if not reasons:
        return

    try:
        chat = await event.get_chat()
    except Exception:  # noqa: BLE001
        chat = None
    title = getattr(chat, "title", None) or "чат"
    username = getattr(chat, "username", None)
    try:
        sender = await msg.get_sender()
        who = getattr(sender, "first_name", None) or getattr(sender, "username", None) or "кто-то"
    except Exception:  # noqa: BLE001
        who = "кто-то"

    link = _message_link(chat_tg, username, msg.id)
    snippet = text[:250] if text else "[без текста]"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔗 Открыть", url=link),
    ]])
    await notifier.send(
        f"{' · '.join(reasons)}\n"
        f"Чат: {title}\n"
        f"От: {who}\n"
        f"«{snippet}»",
        reply_markup=kb,
    )
