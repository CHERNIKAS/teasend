"""Campaign categories: create, schedule, assign chats & templates.

Each category is an independent campaign — its own posts/day, time window,
weekdays and template set — broadcast to the chats assigned to it.
"""
from __future__ import annotations

from datetime import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from teasender.bot import ui
from teasender.core.enums import Permission
from teasender.db.models import Category, Chat, ChatCategory, Template, TemplateCategory

router = Router(name="categories")

PAGE = 10
_DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
_ELIGIBLE = (Permission.allowed, Permission.owner)


async def _get_cat(s: AsyncSession, cat_id: int) -> Category | None:
    return await s.scalar(
        select(Category).where(Category.id == cat_id)
        .options(selectinload(Category.chats), selectinload(Category.templates))
    )


# --- list ---------------------------------------------------------------------

def _list_kb(cats: list[Category]) -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(
            text=f"{'🟢' if c.is_active else '⏸️'} {c.name[:30]}",
            callback_data=f"cat:{c.id}",
        )]
        for c in cats
    ]
    kb.append([InlineKeyboardButton(text="➕ Создать категорию", callback_data="catnew")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


async def render_categories_message(message: Message, sessionmaker) -> None:
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    async with sessionmaker() as s:
        cats = list((await s.scalars(select(Category).order_by(Category.name))).all())
    text = "🗂 <b>Категории (кампании)</b>\nУ каждой своё расписание, чаты и шаблоны."
    await ui.open_panel(message.bot, message.chat.id, text, _list_kb(cats))


async def _render_list(message: Message, sessionmaker) -> None:
    async with sessionmaker() as s:
        cats = list((await s.scalars(select(Category).order_by(Category.name))).all())
    text = "🗂 <b>Категории (кампании)</b>\nУ каждой своё расписание, чаты и шаблоны."
    await ui.edit_panel(message, text, _list_kb(cats))


@router.callback_query(F.data == "cats")
async def on_cats(cq: CallbackQuery, sessionmaker) -> None:
    await _render_list(cq.message, sessionmaker)
    await cq.answer()


@router.callback_query(F.data == "catnew")
async def on_catnew(cq: CallbackQuery) -> None:
    await ui.edit_panel(
        cq.message,
        "➕ <b>Новая категория</b>\nОтправь: <code>/newcat Название</code>",
        InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="cats")]]),
    )
    await cq.answer()


@router.message(Command("newcat"))
async def on_new_category(message: Message, sessionmaker) -> None:
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    name = message.text[len("/newcat"):].strip()[:80]
    if not name:
        await ui.open_panel(message.bot, message.chat.id, "Формат: <code>/newcat Название</code>")
        return
    async with sessionmaker() as s:
        exists = await s.scalar(select(Category).where(Category.name == name))
        if exists is None:
            s.add(Category(name=name))
            await s.commit()
    await render_categories_message(message, sessionmaker)


# --- category card ------------------------------------------------------------

def _days_str(mask: int) -> str:
    return ",".join(n for i, n in enumerate(_DAYS) if mask & (1 << i)) or "—"


def _card_text(cat: Category) -> str:
    return (
        f"🗂 <b>{cat.name}</b>\n"
        f"Статус: {'🟢 активна' if cat.is_active else '⏸️ выключена'}\n\n"
        f"Постов в день: {cat.posts_per_day}\n"
        f"Окно: {cat.window_start:%H:%M}–{cat.window_end:%H:%M}\n"
        f"Дни: {_days_str(cat.days_mask)}\n"
        f"Чатов: {len(cat.chats)} · шаблонов: {len([t for t in cat.templates if t.is_active])}"
    )


def _card_kb(cat: Category) -> InlineKeyboardMarkup:
    cid = cat.id
    day_row = [
        InlineKeyboardButton(
            text=("✅" if cat.days_mask & (1 << i) else "▫️") + n,
            callback_data=f"catday:{cid}:{i}",
        )
        for i, n in enumerate(_DAYS)
    ]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="⏸️ Выключить" if cat.is_active else "🟢 Включить",
            callback_data=f"catact:{cid}",
        )],
        [
            InlineKeyboardButton(text="➖", callback_data=f"catppd:{cid}:-1"),
            InlineKeyboardButton(text=f"Постов/день: {cat.posts_per_day}", callback_data="noop"),
            InlineKeyboardButton(text="➕", callback_data=f"catppd:{cid}:1"),
        ],
        [
            InlineKeyboardButton(text="➖", callback_data=f"catwin:{cid}:s:-1"),
            InlineKeyboardButton(text=f"Старт: {cat.window_start:%H}:00", callback_data="noop"),
            InlineKeyboardButton(text="➕", callback_data=f"catwin:{cid}:s:1"),
        ],
        [
            InlineKeyboardButton(text="➖", callback_data=f"catwin:{cid}:e:-1"),
            InlineKeyboardButton(text=f"Стоп: {cat.window_end:%H}:00", callback_data="noop"),
            InlineKeyboardButton(text="➕", callback_data=f"catwin:{cid}:e:1"),
        ],
        day_row,
        [InlineKeyboardButton(text=f"💬 Чаты категории: {len(cat.chats)}", callback_data=f"catchats:{cid}:0")],
        [InlineKeyboardButton(text="🧩 Шаблоны категории", callback_data=f"cattpls:{cid}")],
        [InlineKeyboardButton(text="🗑 Удалить категорию", callback_data=f"catdel:{cid}")],
        [InlineKeyboardButton(text="⬅️ К категориям", callback_data="cats")],
    ])


async def _render_card(message: Message, cat: Category) -> None:
    await ui.edit_panel(message, _card_text(cat), _card_kb(cat))


async def _mutate_card(cq: CallbackQuery, sessionmaker, cat_id: int, fn) -> None:
    async with sessionmaker() as s:
        cat = await _get_cat(s, cat_id)
        if cat is None:
            await cq.answer("Категория не найдена", show_alert=True)
            return
        fn(cat)
        await s.commit()
        await s.refresh(cat)
        cat = await _get_cat(s, cat_id)
        await _render_card(cq.message, cat)


@router.callback_query(F.data.startswith("cat:"))
async def on_card(cq: CallbackQuery, sessionmaker) -> None:
    cat_id = int(cq.data.split(":")[1])
    async with sessionmaker() as s:
        cat = await _get_cat(s, cat_id)
    if cat is None:
        await cq.answer("Категория не найдена", show_alert=True)
        return
    await _render_card(cq.message, cat)
    await cq.answer()


@router.callback_query(F.data.startswith("catact:"))
async def on_toggle_active(cq: CallbackQuery, sessionmaker) -> None:
    cat_id = int(cq.data.split(":")[1])
    await _mutate_card(cq, sessionmaker, cat_id, lambda c: setattr(c, "is_active", not c.is_active))
    await cq.answer()


@router.callback_query(F.data.startswith("catppd:"))
async def on_ppd(cq: CallbackQuery, sessionmaker) -> None:
    _, cat_id, delta = cq.data.split(":")
    await _mutate_card(
        cq, sessionmaker, int(cat_id),
        lambda c: setattr(c, "posts_per_day", max(0, min(50, c.posts_per_day + int(delta)))),
    )
    await cq.answer()


@router.callback_query(F.data.startswith("catwin:"))
async def on_win(cq: CallbackQuery, sessionmaker) -> None:
    _, cat_id, which, delta = cq.data.split(":")

    def _apply(c: Category) -> None:
        if which == "s":
            c.window_start = time((c.window_start.hour + int(delta)) % 24, 0)
        else:
            c.window_end = time((c.window_end.hour + int(delta)) % 24, 0)

    await _mutate_card(cq, sessionmaker, int(cat_id), _apply)
    await cq.answer()


@router.callback_query(F.data.startswith("catday:"))
async def on_day(cq: CallbackQuery, sessionmaker) -> None:
    _, cat_id, idx = cq.data.split(":")
    bit = 1 << int(idx)
    await _mutate_card(cq, sessionmaker, int(cat_id), lambda c: setattr(c, "days_mask", c.days_mask ^ bit))
    await cq.answer()


# --- delete -------------------------------------------------------------------

@router.callback_query(F.data.startswith("catdel:"))
async def on_del_ask(cq: CallbackQuery) -> None:
    cid = int(cq.data.split(":")[1])
    await ui.edit_panel(
        cq.message, "🗑 <b>Удалить категорию?</b>\nЧаты и шаблоны не удалятся, только их привязка к кампании.",
        InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Да", callback_data=f"catdelyes:{cid}"),
            InlineKeyboardButton(text="❌ Нет", callback_data=f"cat:{cid}"),
        ]]),
    )
    await cq.answer()


@router.callback_query(F.data.startswith("catdelyes:"))
async def on_del_yes(cq: CallbackQuery, sessionmaker) -> None:
    cid = int(cq.data.split(":")[1])
    async with sessionmaker() as s:
        cat = await s.get(Category, cid)
        if cat is not None:
            await s.delete(cat)
            await s.commit()
    await _render_list(cq.message, sessionmaker)
    await cq.answer("Удалено")


# --- chats picker -------------------------------------------------------------

@router.callback_query(F.data.startswith("catchats:"))
async def on_chats_picker(cq: CallbackQuery, sessionmaker) -> None:
    _, cat_id_s, page_s = cq.data.split(":")
    cat_id, page = int(cat_id_s), int(page_s)
    async with sessionmaker() as s:
        rows = list((await s.scalars(
            select(Chat).where(Chat.permission.in_(_ELIGIBLE))
            .order_by(Chat.title).offset(page * PAGE).limit(PAGE + 1)
        )).all())
        member_ids = set((await s.scalars(
            select(ChatCategory.chat_id).where(ChatCategory.category_id == cat_id)
        )).all())
    has_next = len(rows) > PAGE
    rows = rows[:PAGE]

    kb = [
        [InlineKeyboardButton(
            text=f"{'✅' if c.id in member_ids else '⬜️'} {c.title[:32]}",
            callback_data=f"catchattgl:{cat_id}:{c.id}:{page}",
        )]
        for c in rows
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"catchats:{cat_id}:{page-1}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"catchats:{cat_id}:{page+1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton(text="⬅️ К категории", callback_data=f"cat:{cat_id}")])
    await ui.edit_panel(
        cq.message,
        "💬 <b>Чаты категории</b>\nОтметь, какие разрешённые чаты входят в кампанию.",
        InlineKeyboardMarkup(inline_keyboard=kb),
    )
    await cq.answer()


@router.callback_query(F.data.startswith("catchattgl:"))
async def on_chat_toggle(cq: CallbackQuery, sessionmaker) -> None:
    _, cat_id_s, chat_id_s, page_s = cq.data.split(":")
    cat_id, chat_id = int(cat_id_s), int(chat_id_s)
    async with sessionmaker() as s:
        link = await s.get(ChatCategory, {"chat_id": chat_id, "category_id": cat_id})
        if link is None:
            s.add(ChatCategory(chat_id=chat_id, category_id=cat_id))
        else:
            await s.delete(link)
        await s.commit()
    cq.data = f"catchats:{cat_id}:{page_s}"
    await on_chats_picker(cq, sessionmaker)


# --- templates picker ---------------------------------------------------------

@router.callback_query(F.data.startswith("cattpls:"))
async def on_tpls_picker(cq: CallbackQuery, sessionmaker) -> None:
    cat_id = int(cq.data.split(":")[1])
    async with sessionmaker() as s:
        templates = list((await s.scalars(
            select(Template).where(Template.is_active.is_(True)).order_by(Template.id)
        )).all())
        member_ids = set((await s.scalars(
            select(TemplateCategory.template_id).where(TemplateCategory.category_id == cat_id)
        )).all())
    if not templates:
        await cq.answer("Нет активных шаблонов", show_alert=True)
        return
    kb = [
        [InlineKeyboardButton(
            text=f"{'✅' if t.id in member_ids else '⬜️'} {(t.preview_text or t.label or '')[:34]}",
            callback_data=f"cattpltgl:{cat_id}:{t.id}",
        )]
        for t in templates
    ]
    kb.append([InlineKeyboardButton(text="⬅️ К категории", callback_data=f"cat:{cat_id}")])
    await ui.edit_panel(
        cq.message,
        "🧩 <b>Шаблоны категории</b>\nОтмеченные крутятся в этой кампании (2+ — миксятся). "
        "Если ничего не отмечено — первый активный.",
        InlineKeyboardMarkup(inline_keyboard=kb),
    )
    await cq.answer()


@router.callback_query(F.data.startswith("cattpltgl:"))
async def on_tpl_toggle(cq: CallbackQuery, sessionmaker) -> None:
    _, cat_id_s, tpl_id_s = cq.data.split(":")
    cat_id, tpl_id = int(cat_id_s), int(tpl_id_s)
    async with sessionmaker() as s:
        link = await s.get(TemplateCategory, {"template_id": tpl_id, "category_id": cat_id})
        if link is None:
            s.add(TemplateCategory(template_id=tpl_id, category_id=cat_id))
        else:
            await s.delete(link)
        await s.commit()
    cq.data = f"cattpls:{cat_id}"
    await on_tpls_picker(cq, sessionmaker)
