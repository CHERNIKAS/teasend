"""Template list + active toggle. Content is authored in the drafts channel."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from teasender.db.models import Template

router = Router(name="templates")


def _templates_kb(rows: list[Template]) -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton(
                text=f"{'🟢' if t.is_active else '⚪️'} {(t.preview_text or t.label or '')[:40]}",
                callback_data=f"tpl:{t.id}",
            )
        ]
        for t in rows
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


async def render_templates_message(message: Message, sessionmaker) -> None:
    async with sessionmaker() as s:
        rows = list((await s.scalars(select(Template).order_by(Template.id))).all())
    if not rows:
        await message.answer(
            "Шаблонов нет. Напишите объявления в канал-черновик и нажмите «Синхронизация».",
        )
        return
    await message.answer(
        "📝 <b>Шаблоны</b> (🟢 активные участвуют в рассылке):",
        parse_mode="HTML",
        reply_markup=_templates_kb(rows),
    )


@router.callback_query(F.data == "templates")
async def on_templates(cq: CallbackQuery, sessionmaker) -> None:
    async with sessionmaker() as s:
        rows = list((await s.scalars(select(Template).order_by(Template.id))).all())
    if not rows:
        await cq.message.edit_text(
            "Шаблонов нет. Напишите объявления в канал-черновик и нажмите «Синхронизация».",
        )
        await cq.answer()
        return
    try:
        await cq.message.edit_text(
            "📝 <b>Шаблоны</b> (🟢 активные участвуют в рассылке):",
            parse_mode="HTML",
            reply_markup=_templates_kb(rows),
        )
    except Exception:
        pass
    await cq.answer()


@router.callback_query(F.data.startswith("tpl:"))
async def on_toggle_template(cq: CallbackQuery, sessionmaker) -> None:
    tpl_id = int(cq.data.split(":")[1])
    async with sessionmaker() as s:
        tpl = await s.get(Template, tpl_id)
        tpl.is_active = not tpl.is_active
        await s.commit()
        rows = list((await s.scalars(select(Template).order_by(Template.id))).all())
    await cq.answer("Активен" if tpl.is_active else "Отключён")
    try:
        await cq.message.edit_reply_markup(reply_markup=_templates_kb(rows))
    except Exception:
        pass
