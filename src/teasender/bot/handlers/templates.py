"""Template list + active toggle. Content is authored in the drafts channel."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import select

from teasender.bot.keyboards import back_button
from teasender.db.models import Template

router = Router(name="templates")


@router.callback_query(F.data == "templates")
async def on_templates(cq: CallbackQuery, sessionmaker) -> None:
    async with sessionmaker() as s:
        rows = (await s.scalars(select(Template).order_by(Template.id))).all()
    if not rows:
        await cq.message.edit_text(
            "Шаблонов нет. Напишите объявления в канал-черновик и нажмите «Синхронизация».",
            reply_markup=back_button(),
        )
        await cq.answer()
        return

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    kb = [
        [InlineKeyboardButton(
            text=f"{'🟢' if t.is_active else '⚪️'} {(t.preview_text or '')[:40]}",
            callback_data=f"tpl:{t.id}",
        )]
        for t in rows
    ]
    kb.append([InlineKeyboardButton(text="⬅️ Меню", callback_data="menu")])
    await cq.message.edit_text(
        "📝 Шаблоны (🟢 активные участвуют в рассылке):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
    )
    await cq.answer()


@router.callback_query(F.data.startswith("tpl:"))
async def on_toggle_template(cq: CallbackQuery, sessionmaker) -> None:
    tpl_id = int(cq.data.split(":")[1])
    async with sessionmaker() as s:
        tpl = await s.get(Template, tpl_id)
        tpl.is_active = not tpl.is_active
        await s.commit()
    await cq.answer("Активен" if tpl.is_active else "Отключён")
    await on_templates(cq, sessionmaker)
