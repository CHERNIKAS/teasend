"""Inline & reply keyboards."""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from teasender.core.enums import AccountState, Permission
from teasender.db.models import Chat, Template

# --- Reply (persistent) main menu ---------------------------------------------
BTN_STATUS = "📊 Статус"
BTN_CHATS = "💬 Чаты"
BTN_TEMPLATES = "📝 Шаблоны"
BTN_SYNC = "🔄 Синхронизация"
BTN_PAUSE = "⏯ Пауза / Пуск"
BTN_HIDE = "⌨️ Свернуть меню"


def main_menu_reply() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_STATUS), KeyboardButton(text=BTN_CHATS)],
            [KeyboardButton(text=BTN_TEMPLATES), KeyboardButton(text=BTN_SYNC)],
            [KeyboardButton(text=BTN_PAUSE), KeyboardButton(text=BTN_HIDE)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите раздел…",
    )


# --- Status -------------------------------------------------------------------

def status_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔄 Обновить", callback_data="status")]]
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
_FILTERS2 = [
    ("deleted", "🗑 Удаляли"),
    ("restricted", "🚫 Ограничили"),
]

_PERM_LABEL = {
    Permission.owner: "🏠 свой",
    Permission.allowed: "✅ разрешён",
    Permission.unknown: "❔ не проверен",
    Permission.denied: "⛔ запрещён",
}


def perm_label(p: Permission) -> str:
    return _PERM_LABEL.get(p, str(p))


def _filter_rows(active: str) -> list[list[InlineKeyboardButton]]:
    def _btn(key, label):
        mark = "· " if key == active else ""
        return InlineKeyboardButton(text=f"{mark}{label}", callback_data=f"chats:{key}:0")

    return [
        [_btn(k, lbl) for k, lbl in _FILTERS],
        [_btn(k, lbl) for k, lbl in _FILTERS2],
    ]


def chats_list_kb(
    chats: list[Chat], filt: str, page: int, page_size: int, has_next: bool
) -> InlineKeyboardMarkup:
    kb: list[list[InlineKeyboardButton]] = list(_filter_rows(filt))

    for c in chats:
        kb.append(
            [
                InlineKeyboardButton(
                    text=f"{'📤' if c.is_enabled else '📵'} {perm_label(c.permission)} · {c.title[:26]}",
                    callback_data=f"chat:{c.id}:{filt}:{page}",
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

    if chats:
        if filt in ("unknown", "denied", "all"):
            kb.append([InlineKeyboardButton(
                text="✅ Разрешить все на странице",
                callback_data=f"permpage:{filt}:{page}:allow",
            )])
        if filt in ("unknown", "allowed", "all"):
            kb.append([InlineKeyboardButton(
                text="⛔ Запретить все на странице",
                callback_data=f"permpage:{filt}:{page}:deny",
            )])

    return InlineKeyboardMarkup(inline_keyboard=kb)


# --- Chat detail: permission, enable, button rule editing, templates ----------

_DAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def chat_detail_kb(
    chat: Chat, tpl_count: int, back_filt: str = "allowed", back_page: int = 0
) -> InlineKeyboardMarkup:
    cid = chat.id
    tpl_label = f"🧩 Шаблоны чата: {tpl_count}" if tpl_count else "🧩 Шаблоны: по умолчанию"

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
                InlineKeyboardButton(
                    text="📵 Выключить отправку" if chat.is_enabled else "📤 Разрешить отправку",
                    callback_data=f"send:{cid}",
                ),
            ],
            [
                InlineKeyboardButton(text="✅ В разрешённые", callback_data=f"perm:{cid}:allowed"),
                InlineKeyboardButton(text="⛔ В запрещённые", callback_data=f"perm:{cid}:denied"),
            ],
            [InlineKeyboardButton(text=tpl_label, callback_data=f"ctpl:{cid}")],
            [
                InlineKeyboardButton(text="➖", callback_data=f"ppd:{cid}:-1"),
                InlineKeyboardButton(text=f"Постов/день: {chat.posts_per_day}", callback_data="noop"),
                InlineKeyboardButton(text="➕", callback_data=f"ppd:{cid}:1"),
            ],
            [
                InlineKeyboardButton(text="➖", callback_data=f"win:{cid}:s:-1"),
                InlineKeyboardButton(text=f"Старт: {chat.window_start:%H}:00", callback_data="noop"),
                InlineKeyboardButton(text="➕", callback_data=f"win:{cid}:s:1"),
            ],
            [
                InlineKeyboardButton(text="➖", callback_data=f"win:{cid}:e:-1"),
                InlineKeyboardButton(text=f"Стоп: {chat.window_end:%H}:00", callback_data="noop"),
                InlineKeyboardButton(text="➕", callback_data=f"win:{cid}:e:1"),
            ],
            day_row,
            [InlineKeyboardButton(text="🚀 Отправить сейчас (тест)", callback_data=f"asktest:{cid}")],
            [
                InlineKeyboardButton(
                    text="📋 Применить к текущему списку",
                    callback_data=f"askapply:{cid}",
                )
            ],
            [InlineKeyboardButton(text="⬅️ К списку", callback_data=f"chats:{back_filt}:{back_page}")],
        ]
    )


def chat_templates_kb(
    chat_id: int, templates: list[Template], assigned_ids: set[int]
) -> InlineKeyboardMarkup:
    kb: list[list[InlineKeyboardButton]] = []
    for t in templates:
        mark = "✅" if t.id in assigned_ids else "⬜️"
        kb.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {(t.preview_text or t.label or '')[:38]}",
                    callback_data=f"ctpltgl:{chat_id}:{t.id}",
                )
            ]
        )
    kb.append([InlineKeyboardButton(text="⬅️ К чату", callback_data=f"chat:{chat_id}")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def account_state_icon(state: AccountState | None) -> str:
    return {
        AccountState.active: "🟢",
        AccountState.paused: "⏸️",
        AccountState.flood_paused: "🌊",
    }.get(state, "❔")
