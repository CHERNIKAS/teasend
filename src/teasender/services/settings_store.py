"""Tiny key/value settings backed by the `settings` table."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from teasender.db.models import Setting

POST_MODE = "post_mode"   # "templates" | "pool"
CAPTION = "caption"       # caption text for pool mode
SOURCE = "drafts_channel"  # source / pool channel override
KEYWORDS = "keywords"     # comma/newline separated monitor keywords
JOIN_CAP = "join_cap"     # max auto-joins per 24h
JOIN_ON = "join_on"       # "on" | "off" — auto-join enabled

# Smart broadcasting
SMART_MODE = "smart_mode"          # "on" | "off"
SMART_SHARE = "smart_share"        # % of chat traffic our ads may take
SMART_CAP = "smart_cap"            # max posts/day per chat
SMART_DEAD_DAYS = "smart_dead"     # silence (days) => quiet chat -> probe mode
SMART_PROBE_DAYS = "smart_probe"   # probe interval (days) for quiet chats
SMART_MIN_INT_H = "smart_minint"   # min hours between our posts in a chat
SMART_WINDOW = "smart_window"      # allowed hours "HH-HH"

SMART_DEFAULTS = {
    SMART_SHARE: "7",
    SMART_CAP: "2",
    SMART_DEAD_DAYS: "5",
    SMART_PROBE_DAYS: "7",
    SMART_MIN_INT_H: "3",
    SMART_WINDOW: "9-22",
}


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
