"""Inline & reply keyboards."""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from teasender.core.enums import AccountState, Permission
from teasender.db.models import Chat

# --- Reply (persistent) main menu ---------------------------------------------
# These texts double as the callable labels AND the message text a tap sends,
# so the menu handlers match on them directly.
BTN_STATUS = "📊 Статус"
BTN_CHATS = "💬 Чаты"
BTN_TEMPLATES = "📝 Шаблоны"
BTN_SYNC = "🔄 Синхронизация"
BTN_PAUSE = "⏯ Пауза / Пуск"


def main_menu_reply() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_STATUS), KeyboardButton(text=BTN_CHATS)],
            [KeyboardButton(text=BTN_TEMPLATES), KeyboardButton(text=BTN_SYNC)],
            [KeyboardButton(text=BTN_PAUSE)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите раздел…",
    )


# --- Status -------------------------------------------------------------------

def status_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="status"),
                InlineKeyboardButton(text="⏯ Пауза/Пуск", callback_data="toggle_pause"),
            ],
            [InlineKeyboardButton(text="💬 Чаты", callback_data="chats:allowed:0")],
        ]
    )


def back_button(cb: str = "status") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=cb)]]
    )


# --- Chats: filters + list ----------------------------------------------------

_FILTERS = [
    ("allowed", "✅ Разрешённые"),
    ("unknown", "❔ Не проверенные"),
    ("denied", "⛔ Запрещённые"),
    ("all", "Все"),
]

_PERM_LABEL = {
    Permission.owner: "🏠 свой",
    Permission.allowed: "✅ разрешён",
    Permission.unknown: "❔ не проверен",
    Permission.denied: "⛔ запрещён",
}


def perm_label(p: Permission) -> str:
    return _PERM_LABEL.get(p, str(p))


def _filter_row(active: str) -> list[InlineKeyboardButton]:
    row = []
    for key, label in _FILTERS:
        mark = "· " if key == active else ""
        row.append(
            InlineKeyboardButton(text=f"{mark}{label}", callback_data=f"chats:{key}:0")
        )
    return row


def chats_list_kb(
    chats: list[Chat], filt: str, page: int, page_size: int, has_next: bool
) -> InlineKeyboardMarkup:
    kb: list[list[InlineKeyboardButton]] = [_filter_row(filt)]

    for c in chats:
        flag = "🔔" if c.is_enabled else "🔕"
        kb.append(
            [
                InlineKeyboardButton(
                    text=f"{perm_label(c.permission)} {flag} · {c.title[:28]}",
                    callback_data=f"chat:{c.id}",
                )
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"chats:{filt}:{page-1}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"chats:{filt}:{page+1}"))
    if nav:
        kb.append(nav)

    # Bulk action makes sense only when filtering the not-yet-approved ones.
    if filt == "unknown" and chats:
        kb.append(
            [
                InlineKeyboardButton(
                    text="✅ Разрешить все на странице",
                    callback_data=f"permpage:{filt}:{page}",
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=kb)


# --- Chat detail: permission, enable, and button-based rule editing -----------

_DAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def chat_detail_kb(chat: Chat, back_filt: str = "allowed", back_page: int = 0) -> InlineKeyboardMarkup:
    cid = chat.id
    toggle_enabled = "🔕 Выключить" if chat.is_enabled else "🔔 Включить"

    day_row = [
        InlineKeyboardButton(
            text=("✅" if chat.days_mask & (1 << i) else "▫️") + name,
            callback_data=f"day:{cid}:{i}",
        )
        for i, name in enumerate(_DAY_NAMES)
    ]

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Разрешить", callback_data=f"perm:{cid}:allowed"),
                InlineKeyboardButton(text="⛔ Запретить", callback_data=f"perm:{cid}:denied"),
            ],
            [InlineKeyboardButton(text=toggle_enabled, callback_data=f"enable:{cid}")],
            # posts per day
            [
                InlineKeyboardButton(text="➖", callback_data=f"ppd:{cid}:-1"),
                InlineKeyboardButton(text=f"Постов/день: {chat.posts_per_day}", callback_data="noop"),
                InlineKeyboardButton(text="➕", callback_data=f"ppd:{cid}:1"),
            ],
            # min interval (minutes)
            [
                InlineKeyboardButton(text="➖30", callback_data=f"intv:{cid}:-30"),
                InlineKeyboardButton(text=f"Интервал: {chat.min_interval_minutes}м", callback_data="noop"),
                InlineKeyboardButton(text="➕30", callback_data=f"intv:{cid}:30"),
            ],
            # window start / end
            [
                InlineKeyboardButton(text="Старт ➖", callback_data=f"win:{cid}:s:-1"),
                InlineKeyboardButton(
                    text=f"{chat.window_start:%H}–{chat.window_end:%H}", callback_data="noop"
                ),
                InlineKeyboardButton(text="Стоп ➕", callback_data=f"win:{cid}:e:1"),
            ],
            [
                InlineKeyboardButton(text="Старт ➕", callback_data=f"win:{cid}:s:1"),
                InlineKeyboardButton(text="Стоп ➖", callback_data=f"win:{cid}:e:-1"),
            ],
            # weekday toggles
            day_row,
            [InlineKeyboardButton(text="⬅️ К списку", callback_data=f"chats:{back_filt}:{back_page}")],
        ]
    )


def account_state_icon(state: AccountState | None) -> str:
    return {
        AccountState.active: "🟢",
        AccountState.paused: "⏸️",
        AccountState.flood_paused: "🌊",
    }.get(state, "❔")
