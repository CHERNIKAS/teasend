"""Start menu, status, account pause/resume, and drafts/chats sync."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from teasender.bot.keyboards import main_menu
from teasender.config import Settings
from teasender.core.enums import AccountState, PublicationStatus
from teasender.db.models import Account, Chat, Publication
from teasender.services import chats as chats_svc
from teasender.services import templates as tpl_svc

router = Router(name="menu")


@router.message(CommandStart())
async def on_start(message: Message) -> None:
    await message.answer(
        "TeaSender — панель управления.\nВыберите раздел:", reply_markup=main_menu()
    )


@router.callback_query(F.data == "menu")
async def on_menu(cq: CallbackQuery) -> None:
    await cq.message.edit_text("Меню:", reply_markup=main_menu())
    await cq.answer()


@router.callback_query(F.data == "status")
async def on_status(cq: CallbackQuery, sessionmaker) -> None:
    async with sessionmaker() as s:
        acc = await s.scalar(select(Account).limit(1))
        planned = await s.scalar(
            select(func.count()).select_from(Publication)
            .where(Publication.status == PublicationStatus.planned)
        )
        sent = await s.scalar(
            select(func.count()).select_from(Publication)
            .where(Publication.status == PublicationStatus.sent)
        )
        failed = await s.scalar(
            select(func.count()).select_from(Publication)
            .where(Publication.status == PublicationStatus.failed)
        )
        allowed_chats = await s.scalar(
            select(func.count()).select_from(Chat)
            .where(Chat.is_enabled.is_(True))
        )
    state = acc.state.value if acc else "—"
    text = (
        f"📊 <b>Статус</b>\n"
        f"Аккаунт: {state}\n"
        f"Запланировано: {planned}\n"
        f"Отправлено: {sent}\n"
        f"Ошибок: {failed}\n"
        f"Активных чатов: {allowed_chats}"
    )
    await cq.message.edit_text(text, parse_mode="HTML", reply_markup=main_menu())
    await cq.answer()


@router.callback_query(F.data == "toggle_pause")
async def on_toggle_pause(cq: CallbackQuery, sessionmaker) -> None:
    async with sessionmaker() as s:
        acc = await s.scalar(select(Account).limit(1))
        if acc.state == AccountState.active:
            acc.state = AccountState.paused
            acc.pause_reason = "manual"
            msg = "⏸️ Поставлено на паузу."
        else:
            acc.state = AccountState.active
            acc.pause_reason = None
            acc.flood_until = None
            msg = "▶️ Возобновлено."
        await s.commit()
    await cq.answer(msg, show_alert=True)


@router.callback_query(F.data == "sync")
async def on_sync(cq: CallbackQuery, sessionmaker, settings: Settings, telegram) -> None:
    await cq.answer("Синхронизация…")
    async with sessionmaker() as s:
        drafts = await telegram.read_drafts(settings.drafts_channel)
        t_created, t_updated = await tpl_svc.sync_templates(s, drafts)
        dialogs = await chats_svc.read_dialogs(telegram)
        c_created, c_updated = await chats_svc.import_dialogs(s, dialogs)
    await cq.message.edit_text(
        f"🔄 Готово.\n"
        f"Шаблоны: +{t_created}, обновлено {t_updated}\n"
        f"Чаты: +{c_created}, обновлено {c_updated}\n\n"
        f"Новые чаты добавлены со статусом «не проверен» — отметьте разрешённые "
        f"в разделе «Чаты».",
        reply_markup=main_menu(),
    )
