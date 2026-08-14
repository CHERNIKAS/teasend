"""Start menu, status, account pause/resume, and drafts/chats sync."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select

from teasender.bot import ui
from teasender.bot.awaiting import clear_await
from teasender.bot.handlers.chats import render_chats_message
from teasender.bot.handlers.templates import render_templates_message
from teasender.bot.handlers.stats import render_stats_message
from teasender.bot.handlers.tools import render_tools_message
from teasender.bot.keyboards import (
    BTN_CHATS,
    BTN_PAUSE,
    BTN_STATS,
    BTN_STATUS,
    BTN_SYNC,
    BTN_TEMPLATES,
    BTN_TOOLS,
    main_menu_reply,
    status_kb,
)
from teasender.config import Settings
from teasender.core.enums import AccountState, Permission, PublicationStatus
from teasender.db.models import (
    Account,
    Chat,
    JoinQueue,
    Publication,
    Setting,
    Template,
    as_utc,
    utcnow,
)
from teasender.services import chats as chats_svc
from teasender.services import templates as tpl_svc
from teasender.services.settings_store import (
    CAPTION,
    JOIN_CAP,
    KEYWORDS,
    POST_MODE,
    SEND_AFTER,
    SMART_CAP,
    SMART_MODE,
    SMART_SHARE,
    SMART_WINDOW,
    get_setting,
    set_setting,
)

router = Router(name="menu")

_ELIGIBLE = (Permission.allowed, Permission.owner)


@router.message(CommandStart())
@router.message(Command("menu"))
async def on_start(message: Message) -> None:
    clear_await(message.chat.id)
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
            select(func.count()).select_from(Chat).where(Chat.is_enabled.is_(True))
        )
        enabled_chats = await s.scalar(select(func.count()).select_from(Chat))
        active_tpl = await s.scalar(
            select(func.count()).select_from(Template)
            .where(Template.is_active.is_(True))
        )
        next_dt = await s.scalar(
            select(func.min(Publication.scheduled_at))
            .where(Publication.status == PublicationStatus.planned)
        )
        # Chats breakdown by permission label.
        perm_rows = dict((await s.execute(
            select(Chat.permission, func.count()).group_by(Chat.permission)
        )).all())
        allowed_n = perm_rows.get(Permission.allowed, 0) + perm_rows.get(Permission.owner, 0)
        denied_n = perm_rows.get(Permission.denied, 0)
        unknown_n = perm_rows.get(Permission.unknown, 0)
        # Join queue.
        queue_n = await s.scalar(
            select(func.count()).select_from(JoinQueue).where(JoinQueue.status == "pending")
        )
        since = utcnow() - timedelta(hours=24)
        joined_24 = await s.scalar(
            select(func.count()).select_from(JoinQueue)
            .where(JoinQueue.status == "joined", JoinQueue.joined_at >= since)
        )
        joined_total = await s.scalar(
            select(func.count()).select_from(JoinQueue).where(JoinQueue.status == "joined")
        )
        # Settings.
        post_mode = await get_setting(s, POST_MODE, "templates")
        caption = await get_setting(s, CAPTION, "") or ""
        join_cap = await get_setting(s, JOIN_CAP, "5")
        keywords = await get_setting(s, KEYWORDS, "") or ""
        kw_n = len([w for w in keywords.replace("\n", ",").split(",") if w.strip()])
        smart_on = (await get_setting(s, SMART_MODE, "off")) == "on"
        smart_share = await get_setting(s, SMART_SHARE, "7")
        smart_cap = await get_setting(s, SMART_CAP, "2")
        smart_win = await get_setting(s, SMART_WINDOW, "9-22")
        start_iso = await get_setting(s, SEND_AFTER, "") or ""

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
        warn = "\n\n⚠️ <b>Нет чатов с включённой отправкой</b> — рассылка не пойдёт. Откройте «Чаты» → карточка → «📤 Разрешить отправку»."
    elif post_mode != "pool" and active_tpl == 0:
        warn = "\n\n⚠️ <b>Нет активных шаблонов</b> — нечего рассылать. Добавьте посты в канал-источник и нажмите «Синхронизация»."

    content = (
        "🧩 ПУЛ фото " + ("(подпись задана)" if caption else "(без подписи)")
        if post_mode == "pool" else f"📄 Шаблоны · активных: {active_tpl}"
    )
    smart_txt = (
        f"🟢 ВКЛ · доля {smart_share}%, потолок {smart_cap}/д, окно {smart_win}"
        if smart_on else "⚪️ выкл (графики вручную)"
    )
    kw_txt = f"{kw_n} слов" if kw_n else "выкл"

    start_line = ""
    if start_iso:
        try:
            sa = datetime.fromisoformat(start_iso)
            if sa > utcnow():
                start_line = f"\n⏰ <b>Старт отложен до {sa.astimezone(tz).strftime('%d.%m %H:%M')}</b>"
        except ValueError:
            pass

    return (
        f"📊 <b>Статус</b>\n"
        f"Режим: {mode} · Аккаунт: {state_txt}{start_line}\n"
        f"\n"
        f"💬 <b>Чаты:</b> всего {enabled_chats}\n"
        f"📤 Рассылка вкл: <b>{eligible_chats}</b> · ✅ {allowed_n} · ⛔ {denied_n} · ❔ {unknown_n}\n"
        f"\n"
        f"📝 <b>Контент:</b> {content}\n"
        f"\n"
        f"🧠 <b>Умная рассылка:</b> {smart_txt}\n"
        f"\n"
        f"📥 <b>Вступление:</b> в очереди {queue_n}, за сутки {joined_24}/{join_cap} (всего {joined_total})\n"
        f"\n"
        f"🔎 <b>Мониторинг слов:</b> {kw_txt}\n"
        f"\n"
        f"🗓 Запланировано: {planned} · ближайшая: {next_txt}\n"
        f"✔️ Отправлено: {sent} · ❌ Ошибок: {failed}"
        f"{warn}"
    )


@router.message(F.text == BTN_STATUS)
async def on_status_msg(message: Message, sessionmaker, settings: Settings) -> None:
    clear_await(message.chat.id)
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    text = await _build_status(sessionmaker, settings)
    await ui.open_panel(message.bot, message.chat.id, text, status_kb())


@router.callback_query(F.data == "status")
async def on_status_cb(cq: CallbackQuery, sessionmaker, settings: Settings) -> None:
    text = await _build_status(sessionmaker, settings)
    await ui.edit_panel(cq.message, text, status_kb())
    await cq.answer("Обновлено")


@router.callback_query(F.data == "startat")
async def on_startat(cq: CallbackQuery) -> None:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌅 Завтра 09:00", callback_data="startset:tom9")],
        [InlineKeyboardButton(text="🌅 Завтра 11:00", callback_data="startset:tom11")],
        [
            InlineKeyboardButton(text="⏱ +6 ч", callback_data="startset:h6"),
            InlineKeyboardButton(text="⏱ +12 ч", callback_data="startset:h12"),
        ],
        [InlineKeyboardButton(text="▶️ Сейчас (сброс)", callback_data="startset:now")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="status")],
    ])
    await ui.edit_panel(
        cq.message,
        "⏰ <b>Отложить старт рассылки</b>\n"
        "До выбранного времени бот ничего не отправляет (планирование идёт, но посты ждут).",
        kb,
    )
    await cq.answer()


@router.callback_query(F.data.startswith("startset:"))
async def on_startset(cq: CallbackQuery, sessionmaker, settings: Settings) -> None:
    opt = cq.data.split(":")[1]
    tz = ZoneInfo(settings.timezone)
    now_local = utcnow().astimezone(tz)
    value = ""
    if opt == "now":
        value = ""
    elif opt in ("tom9", "tom11"):
        hour = 9 if opt == "tom9" else 11
        d = now_local.date() + timedelta(days=1)
        value = datetime.combine(d, time(hour, 0), tzinfo=tz).astimezone(timezone.utc).isoformat()
    elif opt == "h6":
        value = (utcnow() + timedelta(hours=6)).isoformat()
    elif opt == "h12":
        value = (utcnow() + timedelta(hours=12)).isoformat()
    async with sessionmaker() as s:
        await set_setting(s, SEND_AFTER, value)
        await s.commit()
    text = await _build_status(sessionmaker, settings)
    await ui.edit_panel(cq.message, text, status_kb())
    await cq.answer("Старт сброшен — шлём по расписанию" if not value else "Старт отложен")


@router.message(F.text == BTN_CHATS)
async def on_chats_msg(message: Message, sessionmaker) -> None:
    clear_await(message.chat.id)
    await render_chats_message(message, sessionmaker, filt="allowed", page=0)


@router.message(F.text == BTN_TEMPLATES)
async def on_templates_msg(message: Message, sessionmaker) -> None:
    clear_await(message.chat.id)
    await render_templates_message(message, sessionmaker)


@router.message(F.text == BTN_TOOLS)
async def on_tools_msg(message: Message, sessionmaker) -> None:
    clear_await(message.chat.id)
    await render_tools_message(message, sessionmaker)


@router.message(F.text == BTN_STATS)
async def on_stats_msg(message: Message, sessionmaker) -> None:
    clear_await(message.chat.id)
    await render_stats_message(message, sessionmaker)


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
    clear_await(message.chat.id)
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
    clear_await(message.chat.id)
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    await ui.open_panel(message.bot, message.chat.id, "🔄 Синхронизация…")
    text = await _do_sync(sessionmaker, settings, telegram)
    await ui.set_panel(message.bot, message.chat.id, text)
