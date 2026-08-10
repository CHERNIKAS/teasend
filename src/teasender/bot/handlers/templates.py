"""Content screen: post mode (templates/pool), templates list, pool caption."""
from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select, update

from teasender.bot import ui
from teasender.core.enums import PublicationStatus
from teasender.db.models import Publication, Template
from teasender.services.settings_store import (
    CAPTION,
    POST_MODE,
    get_setting,
    set_setting,
)
from teasender.services.spintax import spin

router = Router(name="templates")

_SOURCE_BTN = InlineKeyboardButton(text="📡 Источник (канал)", callback_data="source")
_MODE_TPL = "templates"
_MODE_POOL = "pool"


async def _state(sessionmaker) -> tuple[str, str, list[Template]]:
    async with sessionmaker() as s:
        mode = await get_setting(s, POST_MODE, _MODE_TPL)
        caption = await get_setting(s, CAPTION, "") or ""
        rows = list((await s.scalars(select(Template).order_by(Template.id))).all())
    return mode, caption, rows


def _payload(mode: str, caption: str, rows: list[Template]):
    mode_btn = InlineKeyboardButton(
        text=("🎛 Режим: 🧩 ПУЛ фото" if mode == _MODE_POOL else "🎛 Режим: 📄 Шаблоны"),
        callback_data="mode",
    )
    if mode == _MODE_POOL:
        cap = html.escape(caption) if caption else "— (без текста)"
        text = (
            "🧩 <b>Режим: ПУЛ фото</b>\n"
            "Бот берёт 2–4 случайных фото из канала-источника и шлёт альбом.\n\n"
            f"Подпись (спинтакс): {cap}\n\n"
            "<i>Спинтакс:</i> в фигурных скобках варианты через <code>|</code> — "
            "при каждой отправке подставляется случайный.\n"
            "<i>Задать:</i> <code>/caption {Привет|Хай}, чай в продаже 🍵 {пишите в лс|заказ в личку}</code>"
        )
        kb = [
            [mode_btn],
            [InlineKeyboardButton(text="✍️ Изменить подпись", callback_data="setcap")],
            [_SOURCE_BTN],
        ]
        return text, InlineKeyboardMarkup(inline_keyboard=kb)

    # templates mode
    kb = [[mode_btn]]
    if rows:
        for t in rows:
            kb.append([InlineKeyboardButton(
                text=f"{'🟢' if t.is_active else '⚪️'} {(t.preview_text or t.label or '')[:40]}",
                callback_data=f"tpl:{t.id}",
            )])
        text = "📄 <b>Режим: Шаблоны</b> (🟢 активные участвуют в рассылке):"
    else:
        text = "📄 <b>Режим: Шаблоны</b>\nШаблонов нет — напишите пост в канал-источник и нажмите «Синхронизация»."
    kb.append([_SOURCE_BTN])
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


async def render_templates_message(message: Message, sessionmaker) -> None:
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    text, kb = _payload(*await _state(sessionmaker))
    await ui.open_panel(message.bot, message.chat.id, text, kb)


async def _rerender(cq: CallbackQuery, sessionmaker) -> None:
    text, kb = _payload(*await _state(sessionmaker))
    await ui.edit_panel(cq.message, text, kb)


@router.callback_query(F.data == "templates")
async def on_templates(cq: CallbackQuery, sessionmaker) -> None:
    await _rerender(cq, sessionmaker)
    await cq.answer()


@router.callback_query(F.data == "mode")
async def on_toggle_mode(cq: CallbackQuery, sessionmaker) -> None:
    async with sessionmaker() as s:
        cur = await get_setting(s, POST_MODE, _MODE_TPL)
        new = _MODE_POOL if cur != _MODE_POOL else _MODE_TPL
        await set_setting(s, POST_MODE, new)
        # Drop still-pending posts of the old type so the queue doesn't send a
        # tail in the previous mode after switching.
        old_type = (
            Publication.template_id.is_not(None)  # switching to pool -> drop template posts
            if new == _MODE_POOL
            else Publication.template_id.is_(None)  # switching to templates -> drop pool posts
        )
        await s.execute(
            update(Publication)
            .where(Publication.status == PublicationStatus.planned, old_type)
            .values(status=PublicationStatus.cancelled)
        )
        await s.commit()
    await _rerender(cq, sessionmaker)
    await cq.answer("Режим: ПУЛ фото" if new == _MODE_POOL else "Режим: Шаблоны")


_CAP_PROMPT = "✍️ Пришли новую подпись (спинтакс) ответом на это сообщение."


async def _save_caption(message: Message, sessionmaker, text: str) -> None:
    async with sessionmaker() as s:
        await set_setting(s, CAPTION, text)
        await s.commit()
    shown = html.escape(text) if text else "— (пусто)"
    examples = "\n".join(f"• {html.escape(spin(text))}" for _ in range(3)) if text else ""
    body = f"✅ Подпись обновлена:\n<code>{shown}</code>"
    if examples:
        body += f"\n\n<b>Примеры того, что уйдёт:</b>\n{examples}"
    await ui.open_panel(message.bot, message.chat.id, body)


@router.callback_query(F.data == "setcap")
async def on_setcap(cq: CallbackQuery) -> None:
    await cq.message.answer(
        _CAP_PROMPT,
        reply_markup=ForceReply(input_field_placeholder="{Привет|Хай}, чай в продаже 🍵 {в лс|в личку}"),
    )
    await cq.answer()


@router.message(F.reply_to_message.func(lambda m: m and (m.text or "").startswith(_CAP_PROMPT[:20])))
async def on_caption_reply(message: Message, sessionmaker) -> None:
    text = (message.text or "").strip()
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    if message.reply_to_message:
        await ui.delete_safe(message.bot, message.chat.id, message.reply_to_message.message_id)
    await _save_caption(message, sessionmaker, text)


@router.message(Command("caption"))
async def on_set_caption(message: Message, sessionmaker) -> None:
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    text = (message.text or "")[len("/caption"):].strip()
    await _save_caption(message, sessionmaker, text)


@router.callback_query(F.data.startswith("tpl:"))
async def on_toggle_template(cq: CallbackQuery, sessionmaker) -> None:
    tpl_id = int(cq.data.split(":")[1])
    async with sessionmaker() as s:
        tpl = await s.get(Template, tpl_id)
        tpl.is_active = not tpl.is_active
        await s.commit()
        active = tpl.is_active
    await _rerender(cq, sessionmaker)
    await cq.answer("Активен" if active else "Отключён")
