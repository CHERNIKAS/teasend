"""Tiny key/value settings backed by the `settings` table."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from teasender.db.models import Setting

POST_MODE = "post_mode"   # "templates" | "pool"
CAPTION = "caption"       # caption text for pool mode
SOURCE = "drafts_channel"  # source / pool channel override


async def get_setting(session: AsyncSession, key: str, default: str | None = None) -> str | None:
    row = await session.get(Setting, key)
    return row.value if row else default


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value


def as_channel(value: str):
    """Numeric ids -> int for Telethon; usernames/links stay strings."""
    v = value.strip()
    return int(v) if v.lstrip("-").isdigit() else v
