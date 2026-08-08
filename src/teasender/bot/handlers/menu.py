"""Start menu, status, account pause/resume, and drafts/chats sync."""
from __future__ import annotations

from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from teasender.bot import ui
from teasender.bot.handlers.categories import render_categories_message
from teasender.bot.handlers.chats import render_chats_message
from teasender.bot.handlers.templates import render_templates_message
from teasender.bot.keyboards import (
    BTN_CATEGORIES,
    BTN_CHATS,
    BTN_PAUSE,
    BTN_STATUS,
    BTN_SYNC,
    BTN_TEMPLATES,
    main_menu_reply,
    status_kb,
)
from teasender.config import Settings
from teasender.core.enums import AccountState, Permission, PublicationStatus
from teasender.db.models import Account, Chat, Publication, Setting, Template, as_utc, utcnow
from teasender.services import chats as chats_svc
from teasender.services import templates as tpl_svc

router = Router(name="menu")

_ELIGIBLE = (Permission.allowed, Permission.owner)


@router.message(CommandStart())
@router.message(Command("menu"))
async def on_start(message: Message) -> None:
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    await message.answer(
        "🫖 <b>TeaSender</b> — панель управления.\n"
        "Меню под полем ввода. Выберите раздел.",
        parse_mode="HTML",
        reply_markup=main_menu_reply(),
    )


async def _build_status(sessionmaker, settings: Settings) -> str:
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
        eligible_chats = await s.scalar(
            select(func.count()).select_from(Chat)
            .where(Chat.is_enabled.is_(True), Chat.permission.in_(_ELIGIBLE))
        )
        enabled_chats = await s.scalar(
            select(func.count()).select_from(Chat).where(Chat.is_enabled.is_(True))
        )
        active_tpl = await s.scalar(
            select(func.count()).select_from(Template)
            .where(Template.is_active.is_(True))
        )
        next_dt = await s.scalar(
            select(func.min(Publication.scheduled_at))
            .where(Publication.status == PublicationStatus.planned)
        )

    state = acc.state if acc else None
    state_txt = {
        AccountState.active: "🟢 работает",
        AccountState.paused: "⏸️ на паузе",
        AccountState.flood_paused: "🌊 флуд-пауза",
    }.get(state, "❔ —")
    mode = "🔴 БОЕВОЙ" if not settings.dry_run else "🧪 DRY-RUN (без отправки)"

    tz = ZoneInfo(settings.timezone)
    if next_dt is None:
        next_txt = "—"
    elif as_utc(next_dt) <= utcnow():
        next_txt = "сейчас"
    else:
        next_txt = as_utc(next_dt).astimezone(tz).strftime("%d.%m %H:%M")

    warn = ""
    if eligible_chats == 0:
        warn = "\n\n⚠️ <b>Нет разрешённых чатов</b> — рассылка не пойдёт. Откройте «Чаты» → «Не проверенные» и отметьте нужные ✅."
    elif active_tpl == 0:
        warn = "\n\n⚠️ <b>Нет активных шаблонов</b> — нечего рассылать. Добавьте посты в канал-черновик и нажмите «Синхронизация»."

    return (
        f"📊 <b>Статус</b>\n"
        f"Режим: {mode}\n"
        f"Аккаунт: {state_txt}\n"
        f"\n"
        f"✅ Разрешённых чатов: <b>{eligible_chats}</b> (включено всего: {enabled_chats})\n"
        f"📝 Активных шаблонов: <b>{active_tpl}</b>\n"
        f"\n"
        f"🗓 Запланировано: {planned}\n"
        f"➡️ Ближайшая отправка: {next_txt}\n"
        f"✔️ Отправлено: {sent}\n"
        f"❌ Ошибок: {failed}"
        f"{warn}"
    )


@router.message(F.text == BTN_STATUS)
async def on_status_msg(message: Message, sessionmaker, settings: Settings) -> None:
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    text = await _build_status(sessionmaker, settings)
    await ui.open_panel(message.bot, message.chat.id, text, status_kb())


@router.callback_query(F.data == "status")
async def on_status_cb(cq: CallbackQuery, sessionmaker, settings: Settings) -> None:
    text = await _build_status(sessionmaker, settings)
    await ui.edit_panel(cq.message, text, status_kb())
    await cq.answer("Обновлено")


@router.message(F.text == BTN_CHATS)
async def on_chats_msg(message: Message, sessionmaker) -> None:
    await render_chats_message(message, sessionmaker, filt="allowed", page=0)


@router.message(F.text == BTN_TEMPLATES)
async def on_templates_msg(message: Message, sessionmaker) -> None:
    await render_templates_message(message, sessionmaker)


@router.message(F.text == BTN_CATEGORIES)
async def on_categories_msg(message: Message, sessionmaker) -> None:
    await render_categories_message(message, sessionmaker)


@router.callback_query(F.data == "noop")
async def on_noop(cq: CallbackQuery) -> None:
    await cq.answer()


async def _toggle_pause(sessionmaker) -> str:
    async with sessionmaker() as s:
        acc = await s.scalar(select(Account).limit(1))
        if acc is None:
            return "Аккаунт не инициализирован."
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
    return msg


@router.message(F.text == BTN_PAUSE)
async def on_pause_msg(message: Message, sessionmaker, settings: Settings) -> None:
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    await _toggle_pause(sessionmaker)
    text = await _build_status(sessionmaker, settings)
    await ui.open_panel(message.bot, message.chat.id, text, status_kb())


_SOURCE_KEY = "drafts_channel"


async def _get_source(s, settings: Settings) -> str:
    row = await s.get(Setting, _SOURCE_KEY)
    return row.value if row else settings.drafts_channel


def _as_channel(value: str):
    """Pass numeric ids to Telethon as int, usernames/links as str."""
    v = value.strip()
    return int(v) if v.lstrip("-").isdigit() else v


@router.callback_query(F.data == "source")
async def on_source(cq: CallbackQuery, sessionmaker, settings: Settings) -> None:
    async with sessionmaker() as s:
        current = await _get_source(s, settings)
    await ui.edit_panel(
        cq.message,
        f"📡 <b>Источник рассылки</b> (канал-черновик)\n"
        f"Сейчас: <code>{current}</code>\n\n"
        f"Чтобы сменить, отправь:\n"
        f"<code>/source @username</code> или <code>/source -100123456789</code>\n\n"
        f"Аккаунт должен состоять в этом канале.",
    )
    await cq.answer()


@router.message(Command("source"))
async def on_set_source(message: Message, sessionmaker, settings: Settings, telegram) -> None:
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    value = message.text[len("/source"):].strip()
    if not value:
        async with sessionmaker() as s:
            current = await _get_source(s, settings)
        await ui.open_panel(
            message.bot, message.chat.id,
            f"Текущий источник: <code>{current}</code>\n"
            f"Формат: <code>/source @username</code> или <code>/source -100123456789</code>",
        )
        return
    # Validate that the account can actually resolve this channel.
    try:
        entity = await telegram.client.get_entity(_as_channel(value))
        title = getattr(entity, "title", None) or getattr(entity, "username", None) or value
    except Exception as exc:  # noqa: BLE001
        await ui.open_panel(
            message.bot, message.chat.id,
            f"❌ Не удалось открыть канал <code>{value}</code>: {type(exc).__name__}.\n"
            f"Проверь, что аккаунт состоит в нём и адрес верный.",
        )
        return
    async with sessionmaker() as s:
        row = await s.get(Setting, _SOURCE_KEY)
        if row is None:
            s.add(Setting(key=_SOURCE_KEY, value=value))
        else:
            row.value = value
        await s.commit()
    await ui.open_panel(
        message.bot, message.chat.id,
        f"✅ Источник рассылки: <b>{title}</b> (<code>{value}</code>).\n"
        f"Нажми «Синхронизация», чтобы подтянуть шаблоны отсюда.",
    )


async def _do_sync(sessionmaker, settings: Settings, telegram) -> str:
    async with sessionmaker() as s:
        source = await _get_source(s, settings)
        drafts = await telegram.read_drafts(_as_channel(source))
        t_created, t_updated, t_removed = await tpl_svc.sync_templates(s, drafts)
        dialogs = await chats_svc.read_dialogs(telegram)
        c_created, c_updated = await chats_svc.import_dialogs(s, dialogs)
    return (
        f"🔄 <b>Синхронизация завершена</b>\n"
        f"Шаблоны: +{t_created}, обновлено {t_updated}, удалено {t_removed}\n"
        f"Чаты: +{c_created}, обновлено {c_updated}\n\n"
        f"Новые чаты — со статусом «не проверен». Отметьте разрешённые в «Чаты» → «Не проверенные»."
    )


@router.message(Command("purge_channels"))
async def on_purge_channels(message: Message, sessionmaker, telegram) -> None:
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    await ui.open_panel(message.bot, message.chat.id, "🧹 Удаляю broadcast-каналы…")
    async with sessionmaker() as s:
        n = await chats_svc.purge_channels(s, telegram)
    await ui.set_panel(
        message.bot, message.chat.id,
        f"🧹 Удалено broadcast-каналов: <b>{n}</b>.\nОстались только группы/супергруппы.",
    )


@router.message(F.text == BTN_SYNC)
async def on_sync_msg(message: Message, sessionmaker, settings: Settings, telegram) -> None:
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    await ui.open_panel(message.bot, message.chat.id, "🔄 Синхронизация…")
    text = await _do_sync(sessionmaker, settings, telegram)
    await ui.set_panel(message.bot, message.chat.id, text)
