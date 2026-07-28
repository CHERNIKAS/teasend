"""Template list + active toggle. Content is authored in the drafts channel."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from teasender.bot import ui
from teasender.db.models import Template

router = Router(name="templates")


def _templates_kb(rows: list[Template]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{'🟢' if t.is_active else '⚪️'} {(t.preview_text or t.label or '')[:40]}",
                    callback_data=f"tpl:{t.id}",
                )
            ]
            for t in rows
        ]
    )


async def _load(sessionmaker) -> list[Template]:
    async with sessionmaker() as s:
        return list((await s.scalars(select(Template).order_by(Template.id))).all())


def _payload(rows: list[Template]):
    if not rows:
        return "Шаблонов нет. Напишите объявления в канал-черновик и нажмите «Синхронизация».", None
    return "📝 <b>Шаблоны</b> (🟢 активные участвуют в рассылке):", _templates_kb(rows)


async def render_templates_message(message: Message, sessionmaker) -> None:
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    text, kb = _payload(await _load(sessionmaker))
    await ui.open_panel(message.bot, message.chat.id, text, kb)


@router.callback_query(F.data == "templates")
async def on_templates(cq: CallbackQuery, sessionmaker) -> None:
    text, kb = _payload(await _load(sessionmaker))
    await ui.edit_panel(cq.message, text, kb)
    await cq.answer()


@router.callback_query(F.data.startswith("tpl:"))
async def on_toggle_template(cq: CallbackQuery, sessionmaker) -> None:
    tpl_id = int(cq.data.split(":")[1])
    async with sessionmaker() as s:
        tpl = await s.get(Template, tpl_id)
        tpl.is_active = not tpl.is_active
        await s.commit()
        active = tpl.is_active
    text, kb = _payload(await _load(sessionmaker))
    await ui.edit_panel(cq.message, text, kb)
    await cq.answer("Активен" if active else "Отключён")
