"""Statistics screen: how many posts chats receive, grouped by volume."""
from __future__ import annotations

import html
from datetime import timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select

from teasender.bot import ui
from teasender.core.enums import PublicationStatus
from teasender.db.models import Chat, Publication, utcnow

router = Router(name="stats")

PAGE = 12
_DAYS = 7  # window for "posts per week"

# bucket key -> (label, predicate on weekly count)
_BUCKETS = [
    ("most", "🔥 Больше всего (>7/нед)", lambda n: n > 7),
    ("mid", "🙂 Средне (3–7/нед)", lambda n: 3 <= n <= 7),
    ("low", "💤 Мало (1–2/нед)", lambda n: 1 <= n <= 2),
    ("zero", "🚫 Не получают (0)", lambda n: n == 0),
]
_BUCKET_LABEL = {k: lbl for k, lbl, _ in _BUCKETS}


async def _weekly_counts(sessionmaker) -> tuple[list[tuple[int, str, int]], int]:
    """Return [(chat_id, title, weekly_count)] for all chats, plus total sent."""
    since = utcnow() - timedelta(days=_DAYS)
    async with sessionmaker() as s:
        sent_rows = dict((await s.execute(
            select(Publication.chat_id, func.count())
            .where(Publication.status == PublicationStatus.sent, Publication.sent_at >= since)
            .group_by(Publication.chat_id)
        )).all())
        chats = list((await s.execute(select(Chat.id, Chat.title))).all())
    out = [(cid, title, sent_rows.get(cid, 0)) for cid, title in chats]
    return out, sum(sent_rows.values())


def _bucket_of(n: int) -> str:
    for key, _, pred in _BUCKETS:
        if pred(n):
            return key
    return "zero"


async def _summary_payload(sessionmaker):
    data, total = await _weekly_counts(sessionmaker)
    counts = {k: 0 for k, _, _ in _BUCKETS}
    for _, _, n in data:
        counts[_bucket_of(n)] += 1
    top = sorted(data, key=lambda r: r[2], reverse=True)[:5]

    text = (
        "📈 <b>Статистика</b> (за 7 дней)\n"
        f"Всего постов отправлено: <b>{total}</b> · чатов: {len(data)}\n\n"
        "<b>Группы по объёму:</b>\n"
        + "\n".join(f"{lbl}: <b>{counts[k]}</b>" for k, lbl, _ in _BUCKETS)
    )
    if top and top[0][2] > 0:
        text += "\n\n<b>Топ‑5 по постам:</b>\n" + "\n".join(
            f"• {html.escape(t[:28])} — {n}" for _, t, n in top if n > 0
        )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{lbl} · {counts[k]}", callback_data=f"stbk:{k}:0")]
        for k, lbl, _ in _BUCKETS
    ] + [[InlineKeyboardButton(text="🔄 Обновить", callback_data="stats")]])
    return text, kb


async def render_stats_message(message: Message, sessionmaker) -> None:
    await ui.delete_safe(message.bot, message.chat.id, message.message_id)
    text, kb = await _summary_payload(sessionmaker)
    await ui.open_panel(message.bot, message.chat.id, text, kb)


@router.callback_query(F.data == "stats")
async def on_stats(cq: CallbackQuery, sessionmaker) -> None:
    text, kb = await _summary_payload(sessionmaker)
    await ui.edit_panel(cq.message, text, kb)
    await cq.answer()


@router.callback_query(F.data.startswith("stbk:"))
async def on_bucket(cq: CallbackQuery, sessionmaker) -> None:
    _, key, page_s = cq.data.split(":")
    page = int(page_s)
    data, _ = await _weekly_counts(sessionmaker)
    rows = sorted(
        [(cid, t, n) for cid, t, n in data if _bucket_of(n) == key],
        key=lambda r: r[2], reverse=True,
    )
    total = len(rows)
    chunk = rows[page * PAGE:(page + 1) * PAGE]

    lines = [f"{html.escape(t[:32])} — {n}" for _, t, n in chunk] or ["— пусто —"]
    text = f"<b>{_BUCKET_LABEL.get(key, key)}</b> · {total} чатов\n\n" + "\n".join(lines)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"stbk:{key}:{page-1}"))
    if (page + 1) * PAGE < total:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"stbk:{key}:{page+1}"))
    kb_rows = ([nav] if nav else []) + [[InlineKeyboardButton(text="⬅️ Назад", callback_data="stats")]]
    await ui.edit_panel(cq.message, text, InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cq.answer()
