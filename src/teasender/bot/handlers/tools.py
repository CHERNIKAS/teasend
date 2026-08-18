"""Leads & auto-join screen: keywords to monitor, join queue and daily cap."""
from __future__ import annotations

import html
from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, func, select

from teasender.bot import ui
from teasender.bot.awaiting import pop_await, set_await
from teasender.bot.keyboards import main_menu_reply
from teasender.core.enums import PublicationStatus
from teasender.db.models import Chat, JoinQueue, Publication, utcnow
from teasender.services import monitor
from teasender.services.settings_store import (
    JOIN_CAP,
    JOIN_ON,
    KEYWORDS,
    SMART_CAP,
    SMART_DEAD_DAYS,
    SMART_DEFAULTS,
    SMART_MIN_INT_H,
    SMART_MODE,
    SMART_PROBE_DAYS,
    SMART_SHARE,
    SMART_WINDOW,
    get_setting,
    set_setting,
)

router = Router(name="tools")

_KW_PROMPT = "🔎 Пришли ключевые слова через запятую <b>следующим сообщением</b>.\n<i>Отмена — нажми любой пункт меню.</i>"
_JOIN_PROMPT = "📥 Пришли чаты (@username или ссылки, по одному в строке) <b>следующим сообщением</b>.\n<i>Отмена — нажми любой пункт меню.</i>"


async def _state(sessionmaker):
    async with sessionmaker() as s:
        cap = int((await get_setting(s, JOIN_CAP, "5")) or "5")
        join_on = (await get_setting(s, JOIN_ON, "on")) == "on"
        kw = await get_setting(s, KEYWORDS, "") or ""
        pending = await s.scalar(
            select(func.count()).select_from(JoinQueue).where(JoinQueue.status == "pending")
        )
        since = utcnow() - timedelta(hours=24)
        joined24 = await s.scalar(
            select(func.count()).select_from(JoinQueue)
            .where(JoinQueue.status == "joined", JoinQueue.joined_at >= since)
        )
    return cap, join_on, kw, pending or 0, joined24 or 0


def _payload(cap: int, join_on: bool, kw: str, pending: int, joined24: int):
    kw_show = html.escape(kw) if kw else "— (выкл)"
    text = (
        "🔗 <b>Лиды и вступление</b>\n\n"
        f"🔎 Мониторю слова: {kw_show}\n"
        "<i>Пингую, когда в чатах отвечают на твой пост, тегают тебя или пишут эти слова.</i>\n\n"
        f"📥 Автовступление: {'🟢 ВКЛ' if join_on else '⚪️ выкл'} · в очереди <b>{pending}</b>, за сутки {joined24}/{cap}\n"
        "<i>Бот вступает равномерно, не превышая лимит в день.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧠 Умная рассылка", callback_data="smart")],
        [InlineKeyboardButton(text="🔍 Проверить доступ (баны/кики)", callback_data="scanaccess")],
        [InlineKeyboardButton(text="📜 Сканировать правила чатов", callback_data="scanrules")],
        [InlineKeyboardButton(text="✍️ Задать слова", callback_data="setkw")],
        [InlineKeyboardButton(text="➕ Добавить чаты в очередь", callback_data="addjoin")],
        [
            InlineKeyboardButton(text="➖", callback_data="jcap:-1"),
            InlineKeyboardButton(text=f"Лимит вступлений/сутки: {cap}", callback_data="noop"),
            InlineKeyboardButton(text="➕", callback_data="jcap:1"),
        ],
        [InlineKeyboardButton(
            text="⚪️ Выключить автовступление" if join_on else "🟢 Включить автовступление",
            callback_data="jointoggle",
        )],
    ])
    return text, kb


# --- Smart broadcasting settings ---------------------------------------------

_SMART_LIMITS = {
    SMART_SHARE: (1, 100),
    SMART_CAP: (0, 20),
    SMART_DEAD_DAYS: (1, 30),
    SMART_PROBE_DAYS: (1, 30),
    SMART_MIN_INT_H: (1, 48),
}


async def _smart_state(sessionmaker):
    async with sessionmaker() as s:
        st = {"on": (await get_setting(s, SMART_MODE, "off")) == "on"}
        for key in (SMART_SHARE, SMART_CAP, SMART_DEAD_DAYS, SMART_PROBE_DAYS, SMART_MIN_INT_H):
            st[key] = int(float(await get_setting(s, key, SMART_DEFAULTS[key])))
        st[SMART_WINDOW] = await get_setting(s, SMART_WINDOW, SMART_DEFAULTS[SMART_WINDOW])
    return st


def _smart_payload(st: dict):
    on = st["on"]
    try:
        win_a, win_b = (int(x) for x in st[SMART_WINDOW].split("-"))
    except Exception:  # noqa: BLE001
        win_a, win_b = 9, 22
    text = (
        f"🧠 <b>Умная рассылка: {'🟢 ВКЛ' if on else '⚪️ выкл'}</b>\n"
        "Бот сам подбирает частоту по активности каждого чата — ручные графики не нужны.\n\n"
        f"• Доля рекламы от потока: <b>{st[SMART_SHARE]}%</b>\n"
        f"• Потолок постов/день на чат: <b>{st[SMART_CAP]}</b>\n"
        f"• Тихий чат: тишина ≥ <b>{st[SMART_DEAD_DAYS]}</b> дн → пробник\n"
        f"• Пробник раз в: <b>{st[SMART_PROBE_DAYS]}</b> дн\n"
        f"• Мин. интервал: <b>{st[SMART_MIN_INT_H]}</b> ч\n"
        f"• Окно: <b>{st[SMART_WINDOW]}</b>\n\n"
        "<i>Тихим/мёртвым — один пробник, потом ждём активность.</i>"
    )

    def row(label, key):
        return [
            InlineKeyboardButton(text="➖", callback_data=f"sm:{key}:-1"),
            InlineKeyboardButton(text=f"{label}: {st[key]}", callback_data="noop"),
            InlineKeyboardButton(text="➕", callback_data=f"sm:{key}:1"),
        ]

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚪️ Выключить" if on else "🟢 Включить", callback_data="sm:mode:0")],
        row("Доля %", SMART_SHARE),
        row("Потолок/день", SMART_CAP),
        row("Тихий, дн", SMART_DEAD_DAYS),
        row("Пробник, дн", SMART_PROBE_DAYS),
        row("Интервал, ч", SMART_MIN_INT_H),
        [
            InlineKeyboardButton(text="➖", callback_data="sm:wins:-1"),
            InlineKeyboardButton(text=f"Старт: {win_a:02d}:00", callback_data="noop"),
            InlineKeyboardButton(text="➕", callback_data="sm:wins:1"),
        ],
        [
            InlineKeyboardButton(text="➖", callback_data="sm:wine:-1"),
            InlineKeyboardButton(text=f"Стоп: {win_b:02d}:00", callback_data="noop"),
            InlineKeyboardButton(text="➕", callback_data="sm:wine:1"),
        ],
        [InlineKeyboardButton(text="🔮 Предпросмотр рассылки", callback_data="smartprev")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="tools")],
    ])
    return text, kb


_CAT_ICON = {"active": "🟢", "quiet": "💤", "own": "⚙️"}
_PREV_PAGE = 15


@router.callback_query(F.data == "smartprev")
async def on_smart_preview(cq: CallbackQuery, sessionmaker) -> None:
    from teasender.services.planner import analyze_smart
    async with sessionmaker() as s:
        a = await analyze_smart(s)
    bc = a["by_cat"]
    warm = f"\n\n⚠️ Без данных активности (нужен прогрев): <b>{a['warmup']}</b> чатов — им пока только пробники." if a["warmup"] else ""
    text = (
        "🔮 <b>Предпросмотр умной рассылки</b>\n"
        f"Чатов в работе: <b>{a['chats']}</b>\n"
        f"Ожидаемо постов/день всего: <b>{a['total_per_day']:.0f}</b>\n\n"
        f"🟢 активные (по доле): {bc['active']}\n"
        f"💤 тихие/мёртвые (пробник): {bc['quiet']}\n"
        f"⚙️ свои настройки: {bc['own']}"
        f"{warm}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📋 Список всех чатов ({a['chats']})", callback_data="smartlist:0")],
        [InlineKeyboardButton(text="🔄 Пересчитать", callback_data="smartprev")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="smart")],
    ])
    await ui.edit_panel(cq.message, text, kb)
    await cq.answer()


@router.callback_query(F.data.startswith("smartlist:"))
async def on_smart_list(cq: CallbackQuery, sessionmaker) -> None:
    from teasender.services.planner import analyze_smart
    page = int(cq.data.split(":")[1])
    async with sessionmaker() as s:
        a = await analyze_smart(s)
    rows = a["rows"]
    total = len(rows)
    chunk = rows[page * _PREV_PAGE:(page + 1) * _PREV_PAGE]
    lines = [
        f"{_CAT_ICON.get(cat, '•')} {html.escape(title[:32])} — {exp:.1f}/д"
        for title, exp, cat in chunk
    ] or ["— пусто —"]
    text = (
        f"📋 <b>Все чаты в рассылке</b> ({total}) · ~{a['total_per_day']:.0f} постов/день\n"
        f"<i>🟢 активный · 💤 пробник · ⚙️ свои настройки</i>\n\n"
        + "\n".join(lines)
    )
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"smartlist:{page-1}"))
    if (page + 1) * _PREV_PAGE < total:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"smartlist:{page+1}"))
    kb_rows = ([nav] if nav else []) + [[InlineKeyboardButton(text="⬅️ Назад", callback_data="smartprev")]]
    await ui.edit_panel(cq.message, text, InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cq.answer()


@router.callback_query(F.data == "tools")
async def on_tools_back(cq: CallbackQuery, sessionmaker) -> None:
    await _rerender(cq, sessionmaker)
    await cq.answer()


@router.callback_query(F.data == "smart")
async def on_smart(cq: CallbackQuery, sessionmaker) -> None:
    text, kb = _smart_payload(await _smart_state(sessionmaker))
    await ui.edit_panel(cq.message, text, kb)
    await cq.answer()


@router.callback_query(F.data.startswith("sm:"))
async def on_smart_edit(cq: CallbackQuery, sessionmaker) -> None:
    _, field, delta_s = cq.data.split(":")
    delta = int(delta_s)
    async with sessionmaker() as s:
        if field == "mode":
            cur = await get_setting(s, SMART_MODE, "off")
            await set_setting(s, SMART_MODE, "off" if cur == "on" else "on")
        elif field in _SMART_LIMITS:
            lo, hi = _SMART_LIMITS[field]
            cur = int(float(await get_setting(s, field, SMART_DEFAULTS[field])))
            await set_setting(s, field, str(max(lo, min(hi, cur + delta))))
        elif field in ("wins", "wine"):
            raw = await get_setting(s, SMART_WINDOW, SMART_DEFAULTS[SMART_WINDOW])
            try:
                a, b = (int(x) for x in raw.split("-"))
            except Exception:  # noqa: BLE001
                a, b = 9, 22
            if field == "wins":
                a = (a + delta) % 24
            else:
                b = (b + delta) % 24
            await set_setting(s, SMART_WINDOW, f"{a}-{b}")
        await s.commit()
    text, kb = _smart_payload(await _smart_state(sessionmaker))
    await ui.edit_panel(cq.message, text, kb)
    await cq.answer()


async def render_tools_message(message: Message, sessionmaker) -> None:
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    text, kb = _payload(*await _state(sessionmaker))
    await ui.open_panel(message.bot, message.chat.id, text, kb)


async def _rerender(cq: CallbackQuery, sessionmaker) -> None:
    text, kb = _payload(*await _state(sessionmaker))
    await ui.edit_panel(cq.message, text, kb)


@router.callback_query(F.data.startswith("jcap:"))
async def on_cap(cq: CallbackQuery, sessionmaker) -> None:
    delta = int(cq.data.split(":")[1])
    async with sessionmaker() as s:
        cap = int((await get_setting(s, JOIN_CAP, "5")) or "5")
        cap = max(0, min(50, cap + delta))
        await set_setting(s, JOIN_CAP, str(cap))
        await s.commit()
    await _rerender(cq, sessionmaker)
    await cq.answer()


@router.callback_query(F.data == "jointoggle")
async def on_join_toggle(cq: CallbackQuery, sessionmaker) -> None:
    async with sessionmaker() as s:
        cur = await get_setting(s, JOIN_ON, "on")
        new = "off" if cur == "on" else "on"
        await set_setting(s, JOIN_ON, new)
        await s.commit()
    await _rerender(cq, sessionmaker)
    await cq.answer("Автовступление включено" if new == "on" else "Автовступление выключено")


@router.callback_query(F.data == "setkw")
async def on_setkw(cq: CallbackQuery) -> None:
    set_await(cq.message.chat.id, "kw")
    await cq.message.answer(_KW_PROMPT, parse_mode="HTML")
    await cq.answer()


@router.callback_query(F.data == "addjoin")
async def on_addjoin(cq: CallbackQuery) -> None:
    set_await(cq.message.chat.id, "join")
    await cq.message.answer(_JOIN_PROMPT, parse_mode="HTML")
    await cq.answer()


async def _save_keywords(message: Message, sessionmaker, raw: str) -> None:
    words = monitor.parse_keywords(raw)
    async with sessionmaker() as s:
        await set_setting(s, KEYWORDS, ", ".join(words))
        await s.commit()
    monitor.set_keywords(words)
    shown = ", ".join(words) if words else "— (выкл)"
    await message.answer(
        f"✅ Мониторю слова: {html.escape(shown)}",
        parse_mode="HTML", reply_markup=main_menu_reply(),
    )


async def _add_join(message: Message, sessionmaker, raw: str) -> None:
    refs = [r.strip() for r in raw.replace(",", "\n").splitlines() if r.strip()]
    added = skipped = 0
    async with sessionmaker() as s:
        for ref in refs:
            # Skip anything already in the queue (pending/joined/skipped/failed).
            exists = await s.scalar(select(JoinQueue).where(JoinQueue.ref == ref))
            if exists is None:
                s.add(JoinQueue(ref=ref))
                added += 1
            else:
                skipped += 1
        await s.commit()
    dup = f" · пропущено дублей: {skipped}" if skipped else ""
    await message.answer(
        f"✅ Добавлено в очередь: {added}{dup}. Бот вступит по лимиту (см. «🛠 Инструменты»).",
        reply_markup=main_menu_reply(),
    )


@router.message(F.text & ~F.text.startswith("/"))
async def on_text_input(message: Message, sessionmaker) -> None:
    """Catch free text when we're awaiting input (keywords / join list / caption).
    Registered last; menu buttons and commands are consumed by earlier routers."""
    kind = pop_await(message.chat.id)
    if not kind:
        return
    text = message.text or ""
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    if kind == "kw":
        await _save_keywords(message, sessionmaker, text)
    elif kind == "join":
        await _add_join(message, sessionmaker, text)
    elif kind == "caption":
        from teasender.bot.handlers.templates import save_caption_input
        await save_caption_input(message, sessionmaker, text)
    elif kind == "startat":
        from teasender.bot.handlers.menu import save_start_input
        await save_start_input(message, sessionmaker, text)


@router.message(Command("keywords"))
async def cmd_keywords(message: Message, sessionmaker) -> None:
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    await _save_keywords(message, sessionmaker, (message.text or "")[len("/keywords"):].strip())


@router.message(Command("join"))
async def cmd_join(message: Message, sessionmaker) -> None:
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    await _add_join(message, sessionmaker, (message.text or "")[len("/join"):].strip())


async def _run_access(bot, chat_id: int, sessionmaker, telegram) -> None:
    from teasender.services.accesscheck import scan_access
    await ui.open_panel(bot, chat_id, "🔍 Проверяю доступ к чатам (участник/бан/права)…")
    res = await scan_access(sessionmaker, telegram, limit=50)
    st = res["by_status"]
    detail = " · ".join(f"{k}: {v}" for k, v in st.items()) or "—"
    await bot.send_message(
        chat_id,
        f"🔍 Проверено чатов: {res['checked']}\n"
        f"Проблемных (отключено): {res['problems']}\n"
        f"По статусам: {detail}\n"
        f"Осталось непроверенных: {res['remaining']}"
        + ("\n\nНажми ещё раз, чтобы продолжить." if res["remaining"] else ""),
        reply_markup=main_menu_reply(),
    )


@router.callback_query(F.data == "scanaccess")
async def on_scan_access(cq: CallbackQuery, sessionmaker, telegram) -> None:
    await cq.answer("Проверяю…")
    await _run_access(cq.bot, cq.message.chat.id, sessionmaker, telegram)


@router.message(Command("scan_access"))
async def cmd_scan_access(message: Message, sessionmaker, telegram) -> None:
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    await _run_access(message.bot, message.chat.id, sessionmaker, telegram)


async def _run_scan(bot, chat_id: int, sessionmaker, telegram) -> None:
    from teasender.services.rulescan import scan_batch
    await ui.open_panel(bot, chat_id, "📜 Читаю описания и закрепы чатов… (это займёт минуту)")
    res = await scan_batch(sessionmaker, telegram, limit=60)
    await bot.send_message(
        chat_id,
        f"📜 Проверено чатов: {res['scanned']}\n"
        f"Найдено правил: {res['rules']} · отключено (реклама запрещена): {res['ads_off']}\n"
        f"Осталось непроверенных: {res['remaining']}"
        + ("\n\nНажми ещё раз, чтобы продолжить." if res["remaining"] else ""),
        reply_markup=main_menu_reply(),
    )


@router.callback_query(F.data == "scanrules")
async def on_scan_rules(cq: CallbackQuery, sessionmaker, telegram) -> None:
    await cq.answer("Сканирую…")
    await _run_scan(cq.bot, cq.message.chat.id, sessionmaker, telegram)


@router.message(Command("scan_rules"))
async def cmd_scan_rules(message: Message, sessionmaker, telegram) -> None:
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    await _run_scan(message.bot, message.chat.id, sessionmaker, telegram)


@router.message(Command("cleanup_bad"))
async def cmd_cleanup_bad(message: Message, sessionmaker, telegram) -> None:
    """Delete already-sent old-style posts (whole album, no caption = template
    sends). These were the leftover queue from before pool mode."""
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    await ui.open_panel(message.bot, message.chat.id, "🧹 Удаляю старые посты без подписи…")
    async with sessionmaker() as s:
        rows = list((await s.execute(
            select(Publication.id, Publication.tg_message_id, Chat.tg_chat_id)
            .join(Chat, Chat.id == Publication.chat_id)
            .where(
                Publication.status == PublicationStatus.sent,
                Publication.template_id.is_not(None),
                Publication.tg_message_id.is_not(None),
            )
        )).all())

    deleted = failed = 0
    done_ids: list[int] = []
    for pub_id, msg_id, chat_tg in rows:
        try:
            await telegram.delete_post(chat_tg, msg_id)
            deleted += 1
            done_ids.append(pub_id)
        except Exception:  # noqa: BLE001
            failed += 1

    if done_ids:
        async with sessionmaker() as s:
            for pid in done_ids:
                p = await s.get(Publication, pid)
                if p:
                    p.status = PublicationStatus.cancelled
                    p.error = "deleted"
            await s.commit()

    await ui.open_panel(
        message.bot, message.chat.id,
        f"🧹 Готово. Удалено постов: {deleted}, не удалось: {failed}.\n"
        f"(Не удалось — там, где аккаунт больше не участник.)",
    )
