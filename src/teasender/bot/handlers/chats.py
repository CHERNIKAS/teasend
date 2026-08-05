"""Chat management: filtered list, permission, enable/disable, rule editing,
per-chat template selection, and bulk apply.

Rules are edited with buttons in the chat card. The old text command still works
as a power-user shortcut:

    /rule <chat_id> ppd=2 window=9-22 days=12345
"""
from __future__ import annotations

import random
from datetime import time

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from teasender.bot import ui
from teasender.bot.keyboards import (
    chat_detail_kb,
    chat_templates_kb,
    chats_list_kb,
    perm_label,
)
from teasender.core.enums import Permission
from teasender.db.models import Chat, ChatTemplate, LogEntry, Template

router = Router(name="chats")

PAGE = 15
SEARCH_LIMIT = 25
_ELIGIBLE = (Permission.allowed, Permission.owner)

# panel chat_id -> last search query, so toggles can re-render the same results.
_SEARCH: dict[int, str] = {}
# panel chat_id -> (filter, page) the current chat card was opened from, so
# "back to list" returns to that list rather than the chat's new status.
_ORIGIN: dict[int, tuple[str, int]] = {}


def _days_str(mask: int) -> str:
    names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    return ",".join(n for i, n in enumerate(names) if mask & (1 << i)) or "—"


_FILTER_LABELS = {
    "allowed": "✅ Разрешённые",
    "unknown": "❔ Не проверенные",
    "denied": "⛔ Запрещённые",
    "all": "Все",
    "deleted": "🗑 Удаляли посты",
}


def _origin_filter(panel_chat_id: int) -> str:
    return _ORIGIN.get(panel_chat_id, ("allowed", 0))[0]


def _filter_clause(filt: str):
    if filt == "allowed":
        return Chat.permission.in_(_ELIGIBLE)
    if filt == "unknown":
        return Chat.permission == Permission.unknown
    if filt == "denied":
        return Chat.permission == Permission.denied
    if filt == "deleted":
        return Chat.id.in_(
            select(LogEntry.chat_id).where(
                LogEntry.event == "post_deleted", LogEntry.chat_id.is_not(None)
            )
        )
    return None  # "all"


async def _get_chat(s: AsyncSession, chat_id: int) -> Chat | None:
    return await s.scalar(
        select(Chat).where(Chat.id == chat_id).options(selectinload(Chat.templates))
    )


async def _load_page(sessionmaker, filt: str, page: int) -> tuple[list[Chat], bool]:
    clause = _filter_clause(filt)
    async with sessionmaker() as s:
        stmt = select(Chat).order_by(Chat.title).offset(page * PAGE).limit(PAGE + 1)
        if clause is not None:
            stmt = stmt.where(clause)
        rows = list((await s.scalars(stmt)).all())
    return rows[:PAGE], len(rows) > PAGE


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
        "deleted": "🗑 Удаляли посты",
    }.get(filt, "Чаты")
    return f"💬 <b>Чаты</b> · {label} ({total})"


async def _list_payload(sessionmaker, filt: str, page: int):
    chats, has_next = await _load_page(sessionmaker, filt, page)
    text = await _list_text(sessionmaker, filt)
    return text, chats_list_kb(chats, filt, page, PAGE, has_next)


async def render_chats_message(message: Message, sessionmaker, filt: str, page: int) -> None:
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    text, kb = await _list_payload(sessionmaker, filt, page)
    await ui.open_panel(message.bot, message.chat.id, text, kb)


@router.callback_query(F.data.startswith("chats:"))
async def on_chats(cq: CallbackQuery, sessionmaker) -> None:
    _, filt, page_s = cq.data.split(":")
    text, kb = await _list_payload(sessionmaker, filt, int(page_s))
    await ui.edit_panel(cq.message, text, kb)
    await cq.answer()


def _detail_text(chat: Chat) -> str:
    tpls = [t for t in chat.templates if t.is_active]
    if tpls:
        tpl_line = "Шаблоны: " + ", ".join((t.preview_text or t.label or "?")[:20] for t in tpls[:3])
        if len(tpls) > 3:
            tpl_line += f" +{len(tpls) - 3}"
    else:
        tpl_line = "Шаблоны: по умолчанию (первый активный)"
    return (
        f"<b>{chat.title}</b>\n"
        f"ID: <code>{chat.tg_chat_id}</code>\n"
        f"Статус: {perm_label(chat.permission)}\n"
        f"{tpl_line}\n"
        f"Отправлено/ошибок: {chat.success_count}/{chat.fail_count}"
    )


async def _render_detail(message: Message, chat: Chat) -> None:
    tpl_count = len([t for t in chat.templates if t.is_active])
    filt, page = _ORIGIN.get(message.chat.id, ("allowed", 0))
    await ui.edit_panel(
        message, _detail_text(chat),
        chat_detail_kb(chat, tpl_count, back_filt=filt, back_page=page),
    )


@router.callback_query(F.data.startswith("chat:"))
async def on_chat_detail(cq: CallbackQuery, sessionmaker) -> None:
    parts = cq.data.split(":")
    chat_id = int(parts[1])
    # Remember which list we came from (only when the caller passed it).
    if len(parts) >= 4:
        _ORIGIN[cq.message.chat.id] = (parts[2], int(parts[3]))
    async with sessionmaker() as s:
        chat = await _get_chat(s, chat_id)
        if chat is None:
            await cq.answer("Чат не найден", show_alert=True)
            return
        await _render_detail(cq.message, chat)
    await cq.answer()


async def _mutate_and_render(cq: CallbackQuery, sessionmaker, chat_id: int, fn) -> None:
    async with sessionmaker() as s:
        chat = await _get_chat(s, chat_id)
        if chat is None:
            await cq.answer("Чат не найден", show_alert=True)
            return
        fn(chat)
        await s.commit()
        await s.refresh(chat)
        await _render_detail(cq.message, chat)


def _set_permission(chat: Chat, value: str) -> None:
    """Permission is the single on/off switch: allowing a chat also activates it,
    denying it also deactivates it."""
    perm = Permission(value)
    chat.permission = perm
    chat.is_enabled = perm in _ELIGIBLE


@router.callback_query(F.data.startswith("perm:"))
async def on_set_perm(cq: CallbackQuery, sessionmaker) -> None:
    _, chat_id, value = cq.data.split(":")
    await _mutate_and_render(cq, sessionmaker, int(chat_id), lambda c: _set_permission(c, value))
    await cq.answer("✅ Разрешён и активен" if value == "allowed" else "⛔ Запрещён")


@router.callback_query(F.data.startswith("ppd:"))
async def on_ppd(cq: CallbackQuery, sessionmaker) -> None:
    _, chat_id, delta = cq.data.split(":")
    await _mutate_and_render(
        cq, sessionmaker, int(chat_id),
        lambda c: setattr(c, "posts_per_day", max(0, min(50, c.posts_per_day + int(delta)))),
    )
    await cq.answer()


@router.callback_query(F.data.startswith("win:"))
async def on_window(cq: CallbackQuery, sessionmaker) -> None:
    _, chat_id, which, delta = cq.data.split(":")

    def _apply(c: Chat) -> None:
        if which == "s":
            c.window_start = time((c.window_start.hour + int(delta)) % 24, 0)
        else:
            c.window_end = time((c.window_end.hour + int(delta)) % 24, 0)

    await _mutate_and_render(cq, sessionmaker, int(chat_id), _apply)
    await cq.answer()


@router.callback_query(F.data.startswith("day:"))
async def on_day(cq: CallbackQuery, sessionmaker) -> None:
    _, chat_id, idx = cq.data.split(":")
    bit = 1 << int(idx)
    await _mutate_and_render(
        cq, sessionmaker, int(chat_id), lambda c: setattr(c, "days_mask", c.days_mask ^ bit)
    )
    await cq.answer()


# --- Per-chat template selection ----------------------------------------------

def _tpl_text(chat: Chat) -> str:
    return (
        f"🧩 <b>Шаблоны для «{chat.title}»</b>\n"
        f"Отмеченные участвуют. Ничего не отмечено — идёт первый активный. "
        f"Отметишь 2+ — бот их миксит."
    )


async def _render_templates(message: Message, sessionmaker, chat_id: int) -> bool:
    async with sessionmaker() as s:
        chat = await _get_chat(s, chat_id)
        if chat is None:
            return False
        templates = list((await s.scalars(
            select(Template).where(Template.is_active.is_(True)).order_by(Template.id)
        )).all())
        assigned = {t.id for t in chat.templates}
    if not templates:
        return False
    await ui.edit_panel(message, _tpl_text(chat), chat_templates_kb(chat_id, templates, assigned))
    return True


@router.callback_query(F.data.startswith("ctpl:"))
async def on_chat_templates(cq: CallbackQuery, sessionmaker) -> None:
    chat_id = int(cq.data.split(":")[1])
    if not await _render_templates(cq.message, sessionmaker, chat_id):
        await cq.answer("Нет активных шаблонов", show_alert=True)
        return
    await cq.answer()


@router.callback_query(F.data.startswith("ctpltgl:"))
async def on_chat_template_toggle(cq: CallbackQuery, sessionmaker) -> None:
    _, chat_id_s, tpl_id_s = cq.data.split(":")
    chat_id, tpl_id = int(chat_id_s), int(tpl_id_s)
    async with sessionmaker() as s:
        link = await s.get(ChatTemplate, {"chat_id": chat_id, "template_id": tpl_id})
        if link is None:
            s.add(ChatTemplate(chat_id=chat_id, template_id=tpl_id))
        else:
            await s.delete(link)
        await s.commit()
    await _render_templates(cq.message, sessionmaker, chat_id)
    await cq.answer()


# --- Bulk apply ---------------------------------------------------------------

@router.callback_query(F.data.startswith("applyall:"))
async def on_apply_all(cq: CallbackQuery, sessionmaker) -> None:
    src_id = int(cq.data.split(":")[1])
    async with sessionmaker() as s:
        src = await _get_chat(s, src_id)
        if src is None:
            await cq.answer("Чат не найден", show_alert=True)
            return
        src_tpl_ids = [t.id for t in src.templates]
        filt = _origin_filter(cq.message.chat.id)
        clause = _filter_clause(filt)
        stmt = select(Chat).where(Chat.id != src_id)
        if clause is not None:
            stmt = stmt.where(clause)
        targets = list((await s.scalars(stmt)).all())
        for c in targets:
            c.posts_per_day = src.posts_per_day
            c.min_interval_minutes = src.min_interval_minutes
            c.window_start = src.window_start
            c.window_end = src.window_end
            c.days_mask = src.days_mask
            await s.execute(ChatTemplate.__table__.delete().where(ChatTemplate.chat_id == c.id))
            for tid in src_tpl_ids:
                s.add(ChatTemplate(chat_id=c.id, template_id=tid))
        await s.commit()
        n = len(targets)
        src = await _get_chat(s, src_id)
        if src is not None:
            await _render_detail(cq.message, src)
    await cq.answer(f"Применено к {n} чатам", show_alert=True)


def _confirm_kb(yes_cb: str, no_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да", callback_data=yes_cb),
        InlineKeyboardButton(text="❌ Нет", callback_data=no_cb),
    ]])


@router.callback_query(F.data.startswith("asktest:"))
async def on_ask_test(cq: CallbackQuery, sessionmaker) -> None:
    cid = int(cq.data.split(":")[1])
    await ui.edit_panel(
        cq.message,
        "🚀 <b>Отправить тестовый пост в этот чат прямо сейчас?</b>\n"
        "Уйдёт реальное сообщение (независимо от паузы).",
        _confirm_kb(f"testnow:{cid}", f"chat:{cid}"),
    )
    await cq.answer()


@router.callback_query(F.data.startswith("askapply:"))
async def on_ask_apply(cq: CallbackQuery, sessionmaker) -> None:
    cid = int(cq.data.split(":")[1])
    filt = _origin_filter(cq.message.chat.id)
    label = _FILTER_LABELS.get(filt, "Все")
    await ui.edit_panel(
        cq.message,
        f"📋 <b>Применить настройки этого чата ко ВСЕМ из списка «{label}»?</b>\n"
        "Их расписание (постов/день, окно, дни) и набор шаблонов будут перезаписаны.",
        _confirm_kb(f"applyall:{cid}", f"chat:{cid}"),
    )
    await cq.answer()


@router.callback_query(F.data.startswith("testnow:"))
async def on_test_now(cq: CallbackQuery, sessionmaker, telegram) -> None:
    """Send one post to THIS chat right now, directly — independent of the queue,
    the account pause state and the daily schedule."""
    chat_id = int(cq.data.split(":")[1])
    async with sessionmaker() as s:
        chat = await _get_chat(s, chat_id)
        if chat is None:
            await cq.answer("Чат не найден", show_alert=True)
            return
        # Pick this chat's template (mix if several); fall back to first active.
        assigned = [t for t in chat.templates if t.is_active]
        if not assigned:
            assigned = list((await s.scalars(
                select(Template).where(Template.is_active.is_(True)).order_by(Template.id).limit(1)
            )).all())
        if not assigned:
            await cq.answer("Нет активных шаблонов", show_alert=True)
            return
        tpl = random.choice(assigned)
        target_tg_id = chat.tg_chat_id
        src = (tpl.source_channel_id, tpl.source_message_id, tpl.grouped_id)

    ok = True
    err = ""
    try:
        await telegram.copy_to(target_tg_id, *src)
    except Exception as exc:  # noqa: BLE001
        ok, err = False, type(exc).__name__
    # Return to the chat card either way.
    async with sessionmaker() as s:
        chat = await _get_chat(s, chat_id)
        if chat is not None:
            await _render_detail(cq.message, chat)
    await cq.answer("✅ Отправлено в этот чат" if ok else f"⚠️ Не ушло: {err}", show_alert=True)


@router.callback_query(F.data.startswith("permpage:"))
async def on_perm_page(cq: CallbackQuery, sessionmaker) -> None:
    _, filt, page_s, action = cq.data.split(":")
    page = int(page_s)
    value = "allowed" if action == "allow" else "denied"
    chats, _ = await _load_page(sessionmaker, filt, page)
    async with sessionmaker() as s:
        n = 0
        for c in chats:
            db_chat = await s.get(Chat, c.id)
            _set_permission(db_chat, value)
            n += 1
        await s.commit()
    await cq.answer(f"{'Разрешено' if action == 'allow' else 'Запрещено'}: {n}", show_alert=True)
    text, kb = await _list_payload(sessionmaker, filt, page)
    await ui.edit_panel(cq.message, text, kb)


# --- Search + quick exclude ---------------------------------------------------

def _mark(chat: Chat) -> str:
    if chat.permission == Permission.denied:
        return "⛔"
    if chat.permission in _ELIGIBLE:
        return "✅"
    return "❔"


async def _search_payload(sessionmaker, query: str):
    async with sessionmaker() as s:
        rows = list((await s.scalars(
            select(Chat)
            .where(Chat.title.ilike(f"%{query}%"))
            .order_by(Chat.title)
            .limit(SEARCH_LIMIT + 1)
        )).all())
    truncated = len(rows) > SEARCH_LIMIT
    rows = rows[:SEARCH_LIMIT]

    kb: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=f"{_mark(c)} {c.title[:32]}", callback_data=f"xtgl:{c.id}")]
        for c in rows
    ]
    if rows:
        kb.append([
            InlineKeyboardButton(text="⛔ Исключить всё", callback_data="xall:deny"),
            InlineKeyboardButton(text="✅ Разрешить всё", callback_data="xall:allow"),
        ])
    kb.append([InlineKeyboardButton(text="⬅️ К чатам", callback_data="chats:allowed:0")])

    if not rows:
        text = f"🔎 По «{query}» ничего не найдено."
    else:
        text = (
            f"🔎 <b>Поиск: «{query}»</b> — найдено {len(rows)}{'+' if truncated else ''}\n"
            f"Тап по чату — вычеркнуть (⛔) или вернуть (✅)."
        )
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


@router.message(F.text.startswith("/find"))
async def on_find(message: Message, sessionmaker) -> None:
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    query = message.text[len("/find"):].strip()
    if not query:
        await ui.open_panel(message.bot, message.chat.id, "Формат: <code>/find часть названия</code>")
        return
    _SEARCH[message.chat.id] = query
    text, kb = await _search_payload(sessionmaker, query)
    await ui.open_panel(message.bot, message.chat.id, text, kb)


@router.callback_query(F.data.startswith("xtgl:"))
async def on_search_toggle(cq: CallbackQuery, sessionmaker) -> None:
    chat_id = int(cq.data.split(":")[1])
    async with sessionmaker() as s:
        chat = await s.get(Chat, chat_id)
        if chat is not None:
            value = "denied" if chat.permission != Permission.denied else "allowed"
            _set_permission(chat, value)
            await s.commit()
    query = _SEARCH.get(cq.message.chat.id, "")
    text, kb = await _search_payload(sessionmaker, query)
    await ui.edit_panel(cq.message, text, kb)
    await cq.answer()


@router.callback_query(F.data.startswith("xall:"))
async def on_search_bulk(cq: CallbackQuery, sessionmaker) -> None:
    action = cq.data.split(":")[1]
    query = _SEARCH.get(cq.message.chat.id, "")
    value = "deny" if action == "deny" else "allow"
    async with sessionmaker() as s:
        rows = list((await s.scalars(
            select(Chat).where(Chat.title.ilike(f"%{query}%"))
        )).all())
        for c in rows:
            _set_permission(c, "denied" if value == "deny" else "allowed")
        await s.commit()
        n = len(rows)
    text, kb = await _search_payload(sessionmaker, query)
    await ui.edit_panel(cq.message, text, kb)
    await cq.answer(f"Обновлено: {n}", show_alert=True)


@router.message(F.text.startswith("/rule"))
async def on_rule(message: Message, sessionmaker) -> None:
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Формат: /rule <chat_id> ppd=2 window=9-22 days=12345")
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
