"""CLI: create tables and seed the single account row.

    python -m teasender.db.init

Convenient for first setup and tests. For evolving the schema afterwards use
Alembic (`alembic revision --autogenerate` / `alembic upgrade head`).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select

from teasender.db.models import Account, Base
from teasender.db.session import get_engine, get_sessionmaker


async def _run() -> None:
    Path("data").mkdir(exist_ok=True)
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with get_sessionmaker()() as s:
        if await s.scalar(select(Account).limit(1)) is None:
            s.add(Account(label="default"))
            await s.commit()
            print("Created default account row.")
    print("Schema ready.")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
