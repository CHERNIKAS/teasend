"""Chat management: filtered list, permission, enable/disable, rule editing.

Posting rules are edited with buttons in the chat card (posts/day, interval,
time window, weekdays). The old text command still works as a power-user
shortcut:

    /rule <chat_id> ppd=2 interval=360 window=9-22 days=12345
"""
from __future__ import annotations

from datetime import time

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from teasender.bot.keyboards import (
    chat_detail_kb,
    chats_list_kb,
    perm_label,
)
from teasender.core.enums import Permission
from teasender.db.models import Chat

router = Router(name="chats")

PAGE = 8
_ELIGIBLE = (Permission.allowed, Permission.owner)


def _days_str(mask: int) -> str:
    names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    return ",".join(n for i, n in enumerate(names) if mask & (1 << i)) or "—"


def _filter_clause(filt: str):
    if filt == "allowed":
        return Chat.permission.in_(_ELIGIBLE)
    if filt == "unknown":
        return Chat.permission == Permission.unknown
    if filt == "denied":
        return Chat.permission == Permission.denied
    return None  # "all"


async def _load_page(sessionmaker, filt: str, page: int) -> tuple[list[Chat], bool]:
    clause = _filter_clause(filt)
    async with sessionmaker() as s:
        stmt = select(Chat).order_by(Chat.title).offset(page * PAGE).limit(PAGE + 1)
        if clause is not None:
            stmt = stmt.where(clause)
        rows = list((await s.scalars(stmt)).all())
    has_next = len(rows) > PAGE
    return rows[:PAGE], has_next


async def _list_text(sessionmaker, filt: str) -> str:
    clause = _filter_clause(filt)
    async with sessionmaker() as s:
        stmt = select(func.count()).select_from(Chat)
        if clause is not None:
            stmt = stmt.where(clause)
        total = await s.scalar(stmt)
    label = {
        "allowed": "✅ Разрешённые",
        "unknown": "❔ Не проверенные",
        "denied": "⛔ Запрещённые",
        "all": "Все",
    }.get(filt, "Чаты")
    return f"💬 <b>Чаты</b> · {label} ({total})"


async def render_chats_message(message: Message, sessionmaker, filt: str, page: int) -> None:
    chats, has_next = await _load_page(sessionmaker, filt, page)
    text = await _list_text(sessionmaker, filt)
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=chats_list_kb(chats, filt, page, PAGE, has_next),
    )


@router.callback_query(F.data.startswith("chats:"))
async def on_chats(cq: CallbackQuery, sessionmaker) -> None:
    _, filt, page_s = cq.data.split(":")
    page = int(page_s)
    chats, has_next = await _load_page(sessionmaker, filt, page)
    text = await _list_text(sessionmaker, filt)
    try:
        await cq.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=chats_list_kb(chats, filt, page, PAGE, has_next),
        )
    except Exception:
        pass
    await cq.answer()


def _back_filt(chat: Chat) -> str:
    if chat.permission in _ELIGIBLE:
        return "allowed"
    if chat.permission == Permission.denied:
        return "denied"
    if chat.permission == Permission.unknown:
        return "unknown"
    return "all"


def _detail_text(chat: Chat) -> str:
    return (
        f"<b>{chat.title}</b>\n"
        f"ID: <code>{chat.tg_chat_id}</code>\n"
        f"Разрешение: {perm_label(chat.permission)}\n"
        f"Активен: {'да' if chat.is_enabled else 'нет'}\n\n"
        f"Постов в день: {chat.posts_per_day}\n"
        f"Мин. интервал: {chat.min_interval_minutes} мин\n"
        f"Окно: {chat.window_start:%H:%M}–{chat.window_end:%H:%M}\n"
        f"Дни: {_days_str(chat.days_mask)}\n"
        f"Успешно/ошибок: {chat.success_count}/{chat.fail_count}"
    )


async def _render_detail(cq: CallbackQuery, chat: Chat) -> None:
    back = _back_filt(chat)
    try:
        await cq.message.edit_text(
            _detail_text(chat),
            parse_mode="HTML",
            reply_markup=chat_detail_kb(chat, back_filt=back),
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("chat:"))
async def on_chat_detail(cq: CallbackQuery, sessionmaker) -> None:
    chat_id = int(cq.data.split(":")[1])
    async with sessionmaker() as s:
        chat = await s.get(Chat, chat_id)
    if chat is None:
        await cq.answer("Чат не найден", show_alert=True)
        return
    await _render_detail(cq, chat)
    await cq.answer()


@router.callback_query(F.data.startswith("perm:"))
async def on_set_perm(cq: CallbackQuery, sessionmaker) -> None:
    _, chat_id, value = cq.data.split(":")
    async with sessionmaker() as s:
        chat = await s.get(Chat, int(chat_id))
        chat.permission = Permission(value)
        await s.commit()
        await s.refresh(chat)
        await _render_detail(cq, chat)
    await cq.answer(f"Статус: {value}")


@router.callback_query(F.data.startswith("enable:"))
async def on_toggle_enable(cq: CallbackQuery, sessionmaker) -> None:
    chat_id = int(cq.data.split(":")[1])
    async with sessionmaker() as s:
        chat = await s.get(Chat, chat_id)
        chat.is_enabled = not chat.is_enabled
        await s.commit()
        await s.refresh(chat)
        await _render_detail(cq, chat)
    await cq.answer("Готово")


@router.callback_query(F.data.startswith("ppd:"))
async def on_ppd(cq: CallbackQuery, sessionmaker) -> None:
    _, chat_id, delta = cq.data.split(":")
    async with sessionmaker() as s:
        chat = await s.get(Chat, int(chat_id))
        chat.posts_per_day = max(0, min(50, chat.posts_per_day + int(delta)))
        await s.commit()
        await s.refresh(chat)
        await _render_detail(cq, chat)
    await cq.answer()


@router.callback_query(F.data.startswith("intv:"))
async def on_interval(cq: CallbackQuery, sessionmaker) -> None:
    _, chat_id, delta = cq.data.split(":")
    async with sessionmaker() as s:
        chat = await s.get(Chat, int(chat_id))
        chat.min_interval_minutes = max(0, min(1440, chat.min_interval_minutes + int(delta)))
        await s.commit()
        await s.refresh(chat)
        await _render_detail(cq, chat)
    await cq.answer()


@router.callback_query(F.data.startswith("win:"))
async def on_window(cq: CallbackQuery, sessionmaker) -> None:
    _, chat_id, which, delta = cq.data.split(":")
    async with sessionmaker() as s:
        chat = await s.get(Chat, int(chat_id))
        if which == "s":
            h = (chat.window_start.hour + int(delta)) % 24
            chat.window_start = time(h, 0)
        else:
            h = (chat.window_end.hour + int(delta)) % 24
            chat.window_end = time(h, 0)
        await s.commit()
        await s.refresh(chat)
        await _render_detail(cq, chat)
    await cq.answer()


@router.callback_query(F.data.startswith("day:"))
async def on_day(cq: CallbackQuery, sessionmaker) -> None:
    _, chat_id, idx = cq.data.split(":")
    bit = 1 << int(idx)
    async with sessionmaker() as s:
        chat = await s.get(Chat, int(chat_id))
        chat.days_mask ^= bit
        await s.commit()
        await s.refresh(chat)
        await _render_detail(cq, chat)
    await cq.answer()


@router.callback_query(F.data.startswith("permpage:"))
async def on_perm_page(cq: CallbackQuery, sessionmaker) -> None:
    _, filt, page_s = cq.data.split(":")
    page = int(page_s)
    chats, _ = await _load_page(sessionmaker, filt, page)
    async with sessionmaker() as s:
        n = 0
        for c in chats:
            if c.permission == Permission.unknown:
                db_chat = await s.get(Chat, c.id)
                db_chat.permission = Permission.allowed
                n += 1
        await s.commit()
    await cq.answer(f"Разрешено: {n}", show_alert=True)
    # Re-render the same filtered page (now emptier).
    chats, has_next = await _load_page(sessionmaker, filt, page)
    text = await _list_text(sessionmaker, filt)
    try:
        await cq.message.edit_text(
            text, parse_mode="HTML",
            reply_markup=chats_list_kb(chats, filt, page, PAGE, has_next),
        )
    except Exception:
        pass


@router.message(F.text.startswith("/rule"))
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
