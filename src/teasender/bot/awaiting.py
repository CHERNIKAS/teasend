"""Track what free-text input we're waiting for, per chat.

Used instead of ForceReply (which hides the reply menu). The prompt is a normal
message, the menu stays, and the next plain text is interpreted by state.
"""
from __future__ import annotations

_AWAIT: dict[int, str] = {}


def set_await(chat_id: int, kind: str) -> None:
    _AWAIT[chat_id] = kind


def pop_await(chat_id: int) -> str | None:
    return _AWAIT.pop(chat_id, None)


def clear_await(chat_id: int) -> None:
    _AWAIT.pop(chat_id, None)
