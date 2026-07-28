"""Test harness: point the app at a throwaway SQLite DB and dummy secrets."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

_TMP = Path(__file__).parent / "_tmp"
_TMP.mkdir(exist_ok=True)

os.environ.update(
    API_ID="1",
    API_HASH="dummy",
    BOT_TOKEN="123456:dummy_token_value",
    ADMIN_USER_IDS="111,222",
    DRAFTS_CHANNEL="@drafts",
    DATABASE_URL=f"sqlite+aiosqlite:///{(_TMP / 'test.db').as_posix()}",
    SECRET_KEY_FILE=str(_TMP / "secret.key"),
    SESSION_FILE=str(_TMP / "session.enc"),
    TIMEZONE="Europe/Istanbul",
    DRY_RUN="false",
    GLOBAL_MIN_SEND_INTERVAL="0",
)


@pytest.fixture()
async def sessionmaker():
    # Fresh DB per test.
    db = _TMP / "test.db"
    if db.exists():
        db.unlink()

    from teasender.config import get_settings
    from teasender.db.models import Account, Base
    from teasender.db.session import get_engine, get_sessionmaker, reset_engine

    get_settings.cache_clear()
    reset_engine()

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = get_sessionmaker()
    async with sm() as s:
        s.add(Account(label="default"))
        await s.commit()
    yield sm
    await engine.dispose()
