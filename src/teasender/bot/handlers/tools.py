"""Leads & auto-join screen: keywords to monitor, join queue and daily cap."""
from __future__ import annotations

import html
from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import delete, func, select

from teasender.bot import ui
from teasender.db.models import JoinQueue, utcnow
from teasender.services import monitor
from teasender.services.settings_store import (
    JOIN_CAP,
    KEYWORDS,
    get_setting,
    set_setting,
)

router = Router(name="tools")

_KW_PROMPT = "🔎 Пришли ключевые слова через запятую ответом на это сообщение."
_JOIN_PROMPT = "📥 Пришли чаты (@username или ссылки, по одному в строке) ответом на это сообщение."


async def _state(sessionmaker):
    async with sessionmaker() as s:
        cap = int((await get_setting(s, JOIN_CAP, "5")) or "5")
        kw = await get_setting(s, KEYWORDS, "") or ""
        pending = await s.scalar(
            select(func.count()).select_from(JoinQueue).where(JoinQueue.status == "pending")
        )
        since = utcnow() - timedelta(hours=24)
        joined24 = await s.scalar(
            select(func.count()).select_from(JoinQueue)
            .where(JoinQueue.status == "joined", JoinQueue.joined_at >= since)
        )
    return cap, kw, pending or 0, joined24 or 0


def _payload(cap: int, kw: str, pending: int, joined24: int):
    kw_show = html.escape(kw) if kw else "— (выкл)"
    text = (
        "🔗 <b>Лиды и вступление</b>\n\n"
        f"🔎 Мониторю слова: {kw_show}\n"
        "<i>Пингую, когда в чатах отвечают на твой пост, тегают тебя или пишут эти слова.</i>\n\n"
        f"📥 Автовступление: в очереди <b>{pending}</b>, за сутки {joined24}/{cap}\n"
        "<i>Бот вступает равномерно, не превышая лимит в день.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Задать слова", callback_data="setkw")],
        [InlineKeyboardButton(text="➕ Добавить чаты в очередь", callback_data="addjoin")],
        [
            InlineKeyboardButton(text="➖", callback_data="jcap:-1"),
            InlineKeyboardButton(text=f"Лимит/сутки: {cap}", callback_data="noop"),
            InlineKeyboardButton(text="➕", callback_data="jcap:1"),
        ],
        [InlineKeyboardButton(text="🧹 Очистить очередь", callback_data="joinclr")],
    ])
    return text, kb


async def render_tools_message(message: Message, sessionmaker) -> None:
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    text, kb = _payload(*await _state(sessionmaker))
    await ui.open_panel(message.bot, message.chat.id, text, kb)


async def _rerender(cq: CallbackQuery, sessionmaker) -> None:
    text, kb = _payload(*await _state(sessionmaker))
    await ui.edit_panel(cq.message, text, kb)


@router.callback_query(F.data.startswith("jcap:"))
async def on_cap(cq: CallbackQuery, sessionmaker) -> None:
    delta = int(cq.data.split(":")[1])
    async with sessionmaker() as s:
        cap = int((await get_setting(s, JOIN_CAP, "5")) or "5")
        cap = max(0, min(50, cap + delta))
        await set_setting(s, JOIN_CAP, str(cap))
        await s.commit()
    await _rerender(cq, sessionmaker)
    await cq.answer()


@router.callback_query(F.data == "joinclr")
async def on_clear(cq: CallbackQuery, sessionmaker) -> None:
    async with sessionmaker() as s:
        await s.execute(delete(JoinQueue).where(JoinQueue.status == "pending"))
        await s.commit()
    await _rerender(cq, sessionmaker)
    await cq.answer("Очередь очищена")


@router.callback_query(F.data == "setkw")
async def on_setkw(cq: CallbackQuery) -> None:
    await cq.message.answer(_KW_PROMPT, reply_markup=ForceReply(input_field_placeholder="куплю чай, ищу пуэр, где купить"))
    await cq.answer()


@router.callback_query(F.data == "addjoin")
async def on_addjoin(cq: CallbackQuery) -> None:
    await cq.message.answer(_JOIN_PROMPT, reply_markup=ForceReply(input_field_placeholder="@chat1  https://t.me/chat2"))
    await cq.answer()


async def _save_keywords(message: Message, sessionmaker, raw: str) -> None:
    words = monitor.parse_keywords(raw)
    async with sessionmaker() as s:
        await set_setting(s, KEYWORDS, ", ".join(words))
        await s.commit()
    monitor.set_keywords(words)
    shown = ", ".join(words) if words else "— (выкл)"
    await ui.open_panel(message.bot, message.chat.id, f"✅ Мониторю слова: {html.escape(shown)}")


async def _add_join(message: Message, sessionmaker, raw: str) -> None:
    refs = [r.strip() for r in raw.replace(",", "\n").splitlines() if r.strip()]
    added = 0
    async with sessionmaker() as s:
        for ref in refs:
            exists = await s.scalar(
                select(JoinQueue).where(JoinQueue.ref == ref, JoinQueue.status == "pending")
            )
            if exists is None:
                s.add(JoinQueue(ref=ref))
                added += 1
        await s.commit()
    await ui.open_panel(
        message.bot, message.chat.id,
        f"✅ Добавлено в очередь: {added}. Бот вступит по лимиту (см. «🔗 Лиды и вступление»).",
    )


@router.message(F.reply_to_message.func(lambda m: m and (m.text or "").startswith(_KW_PROMPT[:15])))
async def on_kw_reply(message: Message, sessionmaker) -> None:
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    if message.reply_to_message:
        await ui.delete_safe(message.bot, message.chat.id, message.reply_to_message.message_id)
    await _save_keywords(message, sessionmaker, message.text or "")


@router.message(F.reply_to_message.func(lambda m: m and (m.text or "").startswith(_JOIN_PROMPT[:15])))
async def on_join_reply(message: Message, sessionmaker) -> None:
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    if message.reply_to_message:
        await ui.delete_safe(message.bot, message.chat.id, message.reply_to_message.message_id)
    await _add_join(message, sessionmaker, message.text or "")


@router.message(Command("keywords"))
async def cmd_keywords(message: Message, sessionmaker) -> None:
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    await _save_keywords(message, sessionmaker, (message.text or "")[len("/keywords"):].strip())


@router.message(Command("join"))
async def cmd_join(message: Message, sessionmaker) -> None:
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    await _add_join(message, sessionmaker, (message.text or "")[len("/join"):].strip())
