"""Inline keyboards."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from teasender.core.enums import Permission
from teasender.db.models import Chat


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статус", callback_data="status")],
            [InlineKeyboardButton(text="💬 Чаты", callback_data="chats:0")],
            [InlineKeyboardButton(text="📝 Шаблоны", callback_data="templates")],
            [InlineKeyboardButton(text="🔄 Синхронизация", callback_data="sync")],
            [InlineKeyboardButton(text="⏸️ Пауза / ▶️ Продолжить", callback_data="toggle_pause")],
        ]
    )


def back_button(cb: str = "menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=cb)]]
    )


_PERM_LABEL = {
    Permission.owner: "🏠 свой",
    Permission.allowed: "✅ разрешён",
    Permission.unknown: "❔ не проверен",
    Permission.denied: "⛔ запрещён",
}


def chat_detail_kb(chat: Chat) -> InlineKeyboardMarkup:
    toggle_enabled = "🔕 Выключить" if chat.is_enabled else "🔔 Включить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Разрешить", callback_data=f"perm:{chat.id}:allowed"),
                InlineKeyboardButton(text="⛔ Запретить", callback_data=f"perm:{chat.id}:denied"),
            ],
            [InlineKeyboardButton(text=toggle_enabled, callback_data=f"enable:{chat.id}")],
            [InlineKeyboardButton(text="⬅️ К списку", callback_data="chats:0")],
        ]
    )


def perm_label(p: Permission) -> str:
    return _PERM_LABEL.get(p, str(p))
