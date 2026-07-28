"""Application configuration."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Telegram personal account
    api_id: int
    api_hash: str

    # Bot panel
    bot_token: str = Field(..., min_length=10)
    # NoDecode: skip JSON pre-parse so the validator receives the raw string.
    admin_user_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    drafts_channel: str

    # Storage
    database_url: str = "postgresql+asyncpg://teasender:teasender@localhost:5432/teasender"
    secret_key_file: Path = Path("./data/secret.key")
    session_file: Path = Path("./data/session.enc")

    timezone: str = "UTC"

    # Safety
    dry_run: bool = True
    global_min_send_interval: int = 45
    auto_pause_on_flood: bool = True

    @field_validator("admin_user_ids", mode="before")
    @classmethod
    def _split_ids(cls, v: object) -> object:
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [int(part) for part in v.split(",") if part.strip()]
        if isinstance(v, int):
            return [v]
        return v

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_user_ids


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
