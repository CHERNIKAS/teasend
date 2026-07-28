"""Chat management: list, permission, enable/disable, and rule editing.

Rule editing uses a compact command instead of a multi-step form:

    /rule <chat_id> ppd=2 interval=360 window=9-22 days=12345

  ppd      posts per day
  interval min minutes between posts
  window   allowed hours, "HH-HH" (local time)
  days     weekday digits, 1=Mon .. 7=Sun (e.g. 12345 = Mon-Fri)

Only the keys you pass are changed.
"""
from __future__ import annotations

from datetime import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from teasender.bot.keyboards import back_button, chat_detail_kb, perm_label
from teasender.core.enums import Permission
from teasender.db.models import Chat

router = Router(name="chats")

PAGE = 8


def _days_str(mask: int) -> str:
    names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    return ",".join(n for i, n in enumerate(names) if mask & (1 << i)) or "—"


@router.callback_query(F.data.startswith("chats:"))
async def on_chats(cq: CallbackQuery, sessionmaker) -> None:
    page = int(cq.data.split(":")[1])
    async with sessionmaker() as s:
        rows = (
            await s.scalars(
                select(Chat).order_by(Chat.title).offset(page * PAGE).limit(PAGE)
            )
        ).all()
    if not rows:
        await cq.message.edit_text("Чатов пока нет. Нажмите «Синхронизация».",
                                   reply_markup=back_button())
        await cq.answer()
        return

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    kb = [
        [InlineKeyboardButton(
            text=f"{perm_label(c.permission)} · {c.title[:30]}",
            callback_data=f"chat:{c.id}",
        )]
        for c in rows
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"chats:{page-1}"))
    if len(rows) == PAGE:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"chats:{page+1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton(text="⬅️ Меню", callback_data="menu")])
    await cq.message.edit_text("💬 Чаты:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await cq.answer()


@router.callback_query(F.data.startswith("chat:"))
async def on_chat_detail(cq: CallbackQuery, sessionmaker) -> None:
    chat_id = int(cq.data.split(":")[1])
    async with sessionmaker() as s:
        chat = await s.get(Chat, chat_id)
    if chat is None:
        await cq.answer("Чат не найден", show_alert=True)
        return
    text = (
        f"<b>{chat.title}</b>\n"
        f"ID: <code>{chat.tg_chat_id}</code>\n"
        f"Разрешение: {perm_label(chat.permission)}\n"
        f"Активен: {'да' if chat.is_enabled else 'нет'}\n\n"
        f"Постов в день: {chat.posts_per_day}\n"
        f"Мин. интервал: {chat.min_interval_minutes} мин\n"
        f"Окно: {chat.window_start:%H:%M}–{chat.window_end:%H:%M}\n"
        f"Дни: {_days_str(chat.days_mask)}\n"
        f"Успешно/ошибок: {chat.success_count}/{chat.fail_count}\n\n"
        f"Правила: <code>/rule {chat.id} ppd=1 interval=360 window=9-22 days=1234567</code>"
    )
    await cq.message.edit_text(text, parse_mode="HTML", reply_markup=chat_detail_kb(chat))
    await cq.answer()


@router.callback_query(F.data.startswith("perm:"))
async def on_set_perm(cq: CallbackQuery, sessionmaker) -> None:
    _, chat_id, value = cq.data.split(":")
    async with sessionmaker() as s:
        chat = await s.get(Chat, int(chat_id))
        chat.permission = Permission(value)
        await s.commit()
    await cq.answer(f"Статус: {value}")
    cq.data = f"chat:{chat_id}"
    await on_chat_detail(cq, sessionmaker)


@router.callback_query(F.data.startswith("enable:"))
async def on_toggle_enable(cq: CallbackQuery, sessionmaker) -> None:
    chat_id = int(cq.data.split(":")[1])
    async with sessionmaker() as s:
        chat = await s.get(Chat, chat_id)
        chat.is_enabled = not chat.is_enabled
        await s.commit()
    await cq.answer("Готово")
    cq.data = f"chat:{chat_id}"
    await on_chat_detail(cq, sessionmaker)


@router.message(Command("rule"))
async def on_rule(message: Message, sessionmaker) -> None:
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Формат: /rule <chat_id> ppd=2 interval=360 window=9-22 days=12345")
        return
    try:
        chat_id = int(parts[1])
        kv = dict(p.split("=", 1) for p in parts[2:] if "=" in p)
    except ValueError:
        await message.answer("Не разобрал параметры.")
        return

    async with sessionmaker() as s:
        chat = await s.get(Chat, chat_id)
        if chat is None:
            await message.answer("Чат не найден.")
            return
        try:
            if "ppd" in kv:
                chat.posts_per_day = max(0, int(kv["ppd"]))
            if "interval" in kv:
                chat.min_interval_minutes = max(0, int(kv["interval"]))
            if "window" in kv:
                a, b = kv["window"].split("-")
                chat.window_start = time(int(a), 0)
                chat.window_end = time(int(b), 0)
            if "days" in kv:
                mask = 0
                for ch in kv["days"]:
                    d = int(ch)
                    if 1 <= d <= 7:
                        mask |= 1 << (d - 1)
                chat.days_mask = mask
        except (ValueError, IndexError):
            await message.answer("Ошибка в значениях параметров.")
            return
        await s.commit()
    await message.answer("✅ Правила обновлены.")
