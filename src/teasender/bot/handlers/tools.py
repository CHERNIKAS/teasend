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
    SMART_CAP,
    SMART_DEAD_DAYS,
    SMART_DEFAULTS,
    SMART_MIN_INT_H,
    SMART_MODE,
    SMART_PROBE_DAYS,
    SMART_SHARE,
    SMART_WINDOW,
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
        [InlineKeyboardButton(text="🧠 Умная рассылка", callback_data="smart")],
        [InlineKeyboardButton(text="✍️ Задать слова", callback_data="setkw")],
        [InlineKeyboardButton(text="➕ Добавить чаты в очередь", callback_data="addjoin")],
        [
            InlineKeyboardButton(text="➖", callback_data="jcap:-1"),
            InlineKeyboardButton(text=f"Лимит вступлений/сутки: {cap}", callback_data="noop"),
            InlineKeyboardButton(text="➕", callback_data="jcap:1"),
        ],
        [InlineKeyboardButton(text="🧹 Очистить очередь", callback_data="joinclr")],
    ])
    return text, kb


# --- Smart broadcasting settings ---------------------------------------------

_SMART_LIMITS = {
    SMART_SHARE: (1, 100),
    SMART_CAP: (0, 20),
    SMART_DEAD_DAYS: (1, 30),
    SMART_PROBE_DAYS: (1, 30),
    SMART_MIN_INT_H: (1, 48),
}


async def _smart_state(sessionmaker):
    async with sessionmaker() as s:
        st = {"on": (await get_setting(s, SMART_MODE, "off")) == "on"}
        for key in (SMART_SHARE, SMART_CAP, SMART_DEAD_DAYS, SMART_PROBE_DAYS, SMART_MIN_INT_H):
            st[key] = int(float(await get_setting(s, key, SMART_DEFAULTS[key])))
        st[SMART_WINDOW] = await get_setting(s, SMART_WINDOW, SMART_DEFAULTS[SMART_WINDOW])
    return st


def _smart_payload(st: dict):
    on = st["on"]
    text = (
        f"🧠 <b>Умная рассылка: {'🟢 ВКЛ' if on else '⚪️ выкл'}</b>\n"
        "Бот сам подбирает частоту по активности каждого чата — ручные графики не нужны.\n\n"
        f"• Доля рекламы от потока: <b>{st[SMART_SHARE]}%</b>\n"
        f"• Потолок постов/день на чат: <b>{st[SMART_CAP]}</b>\n"
        f"• Тихий чат: тишина ≥ <b>{st[SMART_DEAD_DAYS]}</b> дн → пробник\n"
        f"• Пробник раз в: <b>{st[SMART_PROBE_DAYS]}</b> дн\n"
        f"• Мин. интервал: <b>{st[SMART_MIN_INT_H]}</b> ч\n"
        f"• Окно: <b>{st[SMART_WINDOW]}</b>\n\n"
        "<i>Тихим/мёртвым — один пробник, потом ждём активность.</i>"
    )

    def row(label, key):
        return [
            InlineKeyboardButton(text="➖", callback_data=f"sm:{key}:-1"),
            InlineKeyboardButton(text=f"{label}: {st[key]}", callback_data="noop"),
            InlineKeyboardButton(text="➕", callback_data=f"sm:{key}:1"),
        ]

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚪️ Выключить" if on else "🟢 Включить", callback_data="sm:mode:0")],
        row("Доля %", SMART_SHARE),
        row("Потолок/день", SMART_CAP),
        row("Тихий, дн", SMART_DEAD_DAYS),
        row("Пробник, дн", SMART_PROBE_DAYS),
        row("Интервал, ч", SMART_MIN_INT_H),
        [
            InlineKeyboardButton(text="Старт ➖", callback_data="sm:wins:-1"),
            InlineKeyboardButton(text=f"Окно {st[SMART_WINDOW]}", callback_data="noop"),
            InlineKeyboardButton(text="Стоп ➕", callback_data="sm:wine:1"),
        ],
        [
            InlineKeyboardButton(text="Старт ➕", callback_data="sm:wins:1"),
            InlineKeyboardButton(text="Стоп ➖", callback_data="sm:wine:-1"),
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="tools")],
    ])
    return text, kb


@router.callback_query(F.data == "tools")
async def on_tools_back(cq: CallbackQuery, sessionmaker) -> None:
    await _rerender(cq, sessionmaker)
    await cq.answer()


@router.callback_query(F.data == "smart")
async def on_smart(cq: CallbackQuery, sessionmaker) -> None:
    text, kb = _smart_payload(await _smart_state(sessionmaker))
    await ui.edit_panel(cq.message, text, kb)
    await cq.answer()


@router.callback_query(F.data.startswith("sm:"))
async def on_smart_edit(cq: CallbackQuery, sessionmaker) -> None:
    _, field, delta_s = cq.data.split(":")
    delta = int(delta_s)
    async with sessionmaker() as s:
        if field == "mode":
            cur = await get_setting(s, SMART_MODE, "off")
            await set_setting(s, SMART_MODE, "off" if cur == "on" else "on")
        elif field in _SMART_LIMITS:
            lo, hi = _SMART_LIMITS[field]
            cur = int(float(await get_setting(s, field, SMART_DEFAULTS[field])))
            await set_setting(s, field, str(max(lo, min(hi, cur + delta))))
        elif field in ("wins", "wine"):
            raw = await get_setting(s, SMART_WINDOW, SMART_DEFAULTS[SMART_WINDOW])
            try:
                a, b = (int(x) for x in raw.split("-"))
            except Exception:  # noqa: BLE001
                a, b = 9, 22
            if field == "wins":
                a = (a + delta) % 24
            else:
                b = (b + delta) % 24
            await set_setting(s, SMART_WINDOW, f"{a}-{b}")
        await s.commit()
    text, kb = _smart_payload(await _smart_state(sessionmaker))
    await ui.edit_panel(cq.message, text, kb)
    await cq.answer()


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
