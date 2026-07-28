"""Async engine and session factory.

Primary target is PostgreSQL (asyncpg). SQLite (aiosqlite) is supported for the
test suite; there we enable foreign keys, which SQLite leaves off by default.
"""
from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from teasender.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _configure_sqlite(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        url = get_settings().database_url
        _engine = create_async_engine(url, future=True, pool_pre_ping=True)
        if url.startswith("sqlite"):
            event.listen(_engine.sync_engine, "connect", _configure_sqlite)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _sessionmaker


def reset_engine() -> None:
    """Test helper: drop cached engine/sessionmaker so a new URL takes effect."""
    global _engine, _sessionmaker
    _engine = None
    _sessionmaker = None
