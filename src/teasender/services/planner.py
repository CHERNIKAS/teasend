"""Day planner.

For each eligible chat it creates `Publication` rows for today, strictly within
that chat's own rules: posts-per-day, minimum interval, allowed hour window and
weekdays. The per-chat spread is a *rule*, not a disguise — a chat that permits
one post a day gets exactly one.

Idempotent: re-running the planner tops up to the daily target without
duplicating what is already planned or sent.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from teasender.core.enums import Permission, PublicationStatus
from teasender.db.models import Chat, Publication, Template, as_utc

_ELIGIBLE = (Permission.allowed, Permission.owner)
_ACTIVE_STATUSES = (
    PublicationStatus.planned,
    PublicationStatus.sending,
    PublicationStatus.sent,
)


def _local_day_bounds_utc(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start_local = datetime.combine(day, time(0, 0), tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _combine_utc(day: date, t: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, t, tzinfo=tz).astimezone(timezone.utc)


def _eligible_templates(chat: Chat, templates: list[Template]) -> list[Template]:
    chat_cat_ids = {c.id for c in chat.categories}
    out: list[Template] = []
    for t in templates:
        t_cat_ids = {c.id for c in t.categories}
        # A template with no categories is global; otherwise it must overlap.
        if not t_cat_ids or (t_cat_ids & chat_cat_ids):
            out.append(t)
    return out


async def plan_day(session: AsyncSession, tz_name: str, now: datetime | None = None) -> int:
    tz = ZoneInfo(tz_name)
    now_local = (now or datetime.now(timezone.utc)).astimezone(tz)
    today = now_local.date()
    weekday = now_local.weekday()  # Mon=0

    templates = list(
        (
            await session.scalars(
                select(Template)
                .where(Template.is_active.is_(True))
                .options(selectinload(Template.categories))
            )
        ).all()
    )
    if not templates:
        return 0

    chats = list(
        (
            await session.scalars(
                select(Chat)
                .where(Chat.is_enabled.is_(True), Chat.permission.in_(_ELIGIBLE))
                .options(selectinload(Chat.categories))
            )
        ).all()
    )

    day_start_utc, day_end_utc = _local_day_bounds_utc(today, tz)
    planned_total = 0

    for chat in chats:
        if not (chat.days_mask & (1 << weekday)):
            continue

        eligible = _eligible_templates(chat, templates)
        if not eligible:
            continue

        already = await session.scalar(
            select(func.count())
            .select_from(Publication)
            .where(
                Publication.chat_id == chat.id,
                Publication.status.in_(_ACTIVE_STATUSES),
                and_(
                    Publication.scheduled_at >= day_start_utc,
                    Publication.scheduled_at < day_end_utc,
                ),
            )
        )
        remaining = chat.posts_per_day - (already or 0)
        if remaining <= 0:
            continue

        slots = _slot_times(chat, today, tz, now_local, remaining)
        for i, slot_utc in enumerate(slots):
            template = eligible[(already + i) % len(eligible)]
            session.add(
                Publication(
                    chat_id=chat.id,
                    template_id=template.id,
                    scheduled_at=slot_utc,
                    status=PublicationStatus.planned,
                )
            )
            planned_total += 1

    await session.commit()
    return planned_total


def _slot_times(
    chat: Chat, day: date, tz: ZoneInfo, now_local: datetime, count: int
) -> list[datetime]:
    """Evenly spaced UTC slot times within the chat's window, honouring the
    minimum interval and never before now or the last-send + interval."""
    interval = timedelta(minutes=chat.min_interval_minutes)
    window_start = _combine_utc(day, chat.window_start, tz)
    window_end = _combine_utc(day, chat.window_end, tz)
    now_utc = now_local.astimezone(timezone.utc)

    earliest = max(window_start, now_utc)
    if chat.last_sent_at is not None:
        earliest = max(earliest, as_utc(chat.last_sent_at) + interval)
    if earliest > window_end:
        return []

    span = (window_end - earliest).total_seconds()
    max_fit = int(span // interval.total_seconds()) + 1
    actual = min(count, max_fit)
    if actual <= 0:
        return []
    if actual == 1:
        return [earliest]

    gap = max(interval.total_seconds(), span / (actual - 1))
    slots = []
    for i in range(actual):
        t = earliest + timedelta(seconds=gap * i)
        if t > window_end:
            break
        slots.append(t)
    return slots
