"""Day planner.

For each eligible chat it creates `Publication` rows for today, strictly within
that chat's own rules: posts-per-day, minimum interval, allowed hour window and
weekdays. The per-chat spread is a *rule*, not a disguise — a chat that permits
one post a day gets exactly one.

Idempotent: re-running the planner tops up to the daily target without
duplicating what is already planned or sent.
"""
from __future__ import annotations

import random
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


def _chat_templates(chat: Chat, active_templates: list[Template]) -> list[Template]:
    """Templates this chat should post. Its own assigned+active ones; if it has
    none assigned yet, fall back to the first active template globally."""
    assigned = [t for t in chat.templates if t.is_active]
    if assigned:
        return assigned
    return active_templates[:1]


async def plan_day(session: AsyncSession, tz_name: str, now: datetime | None = None) -> int:
    tz = ZoneInfo(tz_name)
    now_local = (now or datetime.now(timezone.utc)).astimezone(tz)
    today = now_local.date()
    weekday = now_local.weekday()  # Mon=0

    active_templates = list(
        (
            await session.scalars(
                select(Template)
                .where(Template.is_active.is_(True))
                .order_by(Template.id)
            )
        ).all()
    )
    if not active_templates:
        return 0

    chats = list(
        (
            await session.scalars(
                select(Chat)
                .where(Chat.is_enabled.is_(True), Chat.permission.in_(_ELIGIBLE))
                .options(selectinload(Chat.templates))
            )
        ).all()
    )

    day_start_utc, day_end_utc = _local_day_bounds_utc(today, tz)
    planned_total = 0

    for chat in chats:
        if not (chat.days_mask & (1 << weekday)):
            continue

        eligible = _chat_templates(chat, active_templates)
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
        for slot_utc in slots:
            # One assigned template -> always it; several -> mix randomly.
            template = random.choice(eligible)
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
    """`count` random UTC times spread across the chat's window today.

    The remaining window is split into `count` equal segments and one random
    moment is picked inside each. That gives exactly the requested number of
    posts, spaced across the day, with each chat getting its own unpredictable
    times — reshuffled every day the planner runs. No fixed minimum interval:
    the segmenting keeps posts apart, and the account-wide send throttle
    (`global_min_send_interval`) prevents bursts."""
    window_start = _combine_utc(day, chat.window_start, tz)
    window_end = _combine_utc(day, chat.window_end, tz)
    now_utc = now_local.astimezone(timezone.utc)

    earliest = max(window_start, now_utc)
    if earliest >= window_end or count <= 0:
        return []

    span = (window_end - earliest).total_seconds()
    seg = span / count
    return [
        earliest + timedelta(seconds=random.uniform(i * seg, (i + 1) * seg))
        for i in range(count)
    ]
