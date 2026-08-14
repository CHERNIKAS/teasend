"""SQLAlchemy 2.0 models (PostgreSQL).

Core ideas
----------
* A `Template` is not edited in the bot — it points at a message (or album, via
  `grouped_id`) in your private drafts channel. Sending copies that message, so
  premium emoji and albums survive untouched.
* A `Chat` carries the *rules of that chat*: how many posts per day, minimum gap,
  allowed hours/days, and a `permission` gate. The planner never exceeds them.
* A `Publication` is one planned post for (chat, template) at a time. Its status
  makes the pipeline idempotent and recoverable: a crash resumes `planned` rows
  and never re-sends `sent` ones.
* Enums are stored as VARCHAR (portable, migration-friendly) but round-trip to
  the Python enum on load.
"""
from __future__ import annotations

from datetime import datetime, time, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from teasender.core.enums import (
    AccountState,
    LogLevel,
    Permission,
    PublicationStatus,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime | None) -> datetime | None:
    """Normalise a stored datetime to UTC-aware.

    PostgreSQL returns tz-aware datetimes; SQLite drops tzinfo on round-trip.
    Treat a naive value as UTC so comparisons work on both backends.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def enum_col(enum_cls: type) -> SAEnum:
    return SAEnum(enum_cls, native_enum=False, length=20, validate_strings=True)


class Base(DeclarativeBase):
    pass


class Account(Base):
    """The single personal account. Session lives encrypted on disk, not here."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(100), default="default")
    state: Mapped[AccountState] = mapped_column(
        enum_col(AccountState), default=AccountState.active
    )
    pause_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When a flood pause should lift (UTC). NULL when not flood-paused.
    flood_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --- Templates (references into the drafts channel) ----------------------------

class Template(Base):
    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_channel_id: Mapped[int] = mapped_column(BigInteger)
    # For a single message, the message id. For an album, the first message id.
    source_message_id: Mapped[int] = mapped_column(BigInteger)
    # Telegram album grouping id; NULL for a standalone message.
    grouped_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    label: Mapped[str] = mapped_column(String(200))
    preview_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    categories: Mapped[list["Category"]] = relationship(
        secondary="template_categories", back_populates="templates"
    )

    __table_args__ = (
        UniqueConstraint(
            "source_channel_id", "source_message_id", name="uq_template_source"
        ),
    )


# --- Categories ----------------------------------------------------------------

class Category(Base):
    """A named campaign: its own posting schedule, its own templates, its own
    set of chats. The planner schedules each active category independently."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Per-category schedule (mirrors the per-chat rules).
    posts_per_day: Mapped[int] = mapped_column(Integer, default=1)
    window_start: Mapped[time] = mapped_column(Time, default=time(7, 0))
    window_end: Mapped[time] = mapped_column(Time, default=time(0, 0))
    days_mask: Mapped[int] = mapped_column(Integer, default=0b1111111)

    chats: Mapped[list["Chat"]] = relationship(
        secondary="chat_categories", back_populates="categories"
    )
    templates: Mapped[list[Template]] = relationship(
        secondary="template_categories", back_populates="categories"
    )


class ChatCategory(Base):
    __tablename__ = "chat_categories"

    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True
    )


class TemplateCategory(Base):
    __tablename__ = "template_categories"

    template_id: Mapped[int] = mapped_column(
        ForeignKey("templates.id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True
    )


class ChatTemplate(Base):
    """Direct chat<->template assignment. One template = always that one; several
    = the planner mixes them randomly across the chat's daily posts."""

    __tablename__ = "chat_templates"

    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True
    )
    template_id: Mapped[int] = mapped_column(
        ForeignKey("templates.id", ondelete="CASCADE"), primary_key=True
    )


# --- Chats (with per-chat posting rules) ---------------------------------------

class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    title: Mapped[str] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_member: Mapped[bool] = mapped_column(Boolean, default=True)

    # Permission gate.
    permission: Mapped[Permission] = mapped_column(
        enum_col(Permission), default=Permission.unknown
    )
    permission_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # "Отправка" gate: off by default so a freshly imported chat never posts
    # until you explicitly enable it.
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    # Per-chat rules (the heart of "different schedule per chat").
    posts_per_day: Mapped[int] = mapped_column(Integer, default=1)
    min_interval_minutes: Mapped[int] = mapped_column(Integer, default=360)
    window_start: Mapped[time] = mapped_column(Time, default=time(9, 0))
    window_end: Mapped[time] = mapped_column(Time, default=time(22, 0))
    days_mask: Mapped[int] = mapped_column(Integer, default=0b1111111)  # Mon..Sun

    last_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Activity tracking for smart broadcasting.
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    activity_msgs: Mapped[int] = mapped_column(Integer, default=0)
    activity_window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Posting rules read from the chat's description / pinned message.
    rule_min_interval_h: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rule_ads_forbidden: Mapped[bool] = mapped_column(Boolean, default=False)
    rule_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Exempt this chat from smart broadcasting -> use its own per-chat schedule.
    smart_exempt: Mapped[bool] = mapped_column(Boolean, default=False)
    # Exclude this chat from lead monitoring (keywords/replies/mentions).
    monitor_muted: Mapped[bool] = mapped_column(Boolean, default=False)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    categories: Mapped[list[Category]] = relationship(
        secondary="chat_categories", back_populates="chats"
    )
    # Templates assigned to this chat (see ChatTemplate).
    templates: Mapped[list["Template"]] = relationship(secondary="chat_templates")


# --- Publications (planned / sent posts) ---------------------------------------

class Publication(Base):
    __tablename__ = "publications"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    # NULL = a "pool" post assembled at send time (random photos + caption).
    template_id: Mapped[int | None] = mapped_column(
        ForeignKey("templates.id", ondelete="CASCADE"), nullable=True
    )
    # Which category (campaign) planned this post; NULL = per-chat fallback.
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    status: Mapped[PublicationStatus] = mapped_column(
        enum_col(PublicationStatus), default=PublicationStatus.planned
    )
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    chat: Mapped[Chat] = relationship()
    template: Mapped[Template] = relationship()


# --- Logs & settings -----------------------------------------------------------

class LogEntry(Base):
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    level: Mapped[LogLevel] = mapped_column(enum_col(LogLevel), default=LogLevel.info)
    event: Mapped[str] = mapped_column(String(64))
    chat_id: Mapped[int | None] = mapped_column(
        ForeignKey("chats.id", ondelete="SET NULL"), nullable=True
    )
    publication_id: Mapped[int | None] = mapped_column(
        ForeignKey("publications.id", ondelete="SET NULL"), nullable=True
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class JoinQueue(Base):
    """Chats queued for rate-limited auto-join (@username or invite link)."""

    __tablename__ = "join_queue"

    id: Mapped[int] = mapped_column(primary_key=True)
    ref: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|joined|failed|skipped|requested
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
