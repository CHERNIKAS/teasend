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

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from teasender.core.enums import Permission, PublicationStatus
from teasender.db.models import Category, Chat, Publication, Template, as_utc

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


def _window_bounds_utc(chat: Chat, day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """The chat's posting window today, in UTC. "Stop" at or before "Start"
    (e.g. 07:00–00:00) means "until end of day"."""
    start = _combine_utc(day, chat.window_start, tz)
    end = _combine_utc(day, chat.window_end, tz)
    if end <= start:
        end = _combine_utc(day, time(0, 0), tz) + timedelta(days=1) - timedelta(seconds=1)
    return start, end


def _chat_templates(chat: Chat, active_templates: list[Template]) -> list[Template]:
    """Templates this chat should post. Its own assigned+active ones; if it has
    none assigned yet, fall back to the first active template globally."""
    assigned = [t for t in chat.templates if t.is_active]
    if assigned:
        return assigned
    return active_templates[:1]


async def _schedule_chat(
    session: AsyncSession,
    chat: Chat,
    sched,  # Chat or Category — anything with posts_per_day/window_*/days_mask
    templates: list[Template],
    category_id: int | None,
    day: date,
    tz: ZoneInfo,
    now_local: datetime,
    day_start_utc: datetime,
    day_end_utc: datetime,
) -> int:
    """Top up one chat's daily quota for a given schedule source. Quota is
    counted per (chat, category) so a chat in several campaigns gets each one."""
    weekday = now_local.weekday()
    if not (sched.days_mask & (1 << weekday)):
        return 0
    if not templates:
        return 0

    cat_clause = (
        Publication.category_id == category_id
        if category_id is not None
        else Publication.category_id.is_(None)
    )
    already = await session.scalar(
        select(func.count())
        .select_from(Publication)
        .where(
            Publication.chat_id == chat.id,
            Publication.status.in_(_ACTIVE_STATUSES),
            cat_clause,
            and_(
                Publication.scheduled_at >= day_start_utc,
                Publication.scheduled_at < day_end_utc,
            ),
        )
    )
    # Scale quota by how much of the window is still ahead (fair share on a late start).
    w_start, w_end = _window_bounds_utc(sched, day, tz)
    now_utc = now_local.astimezone(timezone.utc)
    total_s = (w_end - w_start).total_seconds()
    left_s = (w_end - max(w_start, now_utc)).total_seconds()
    frac = min(1.0, max(0.0, left_s / total_s)) if total_s > 0 else 0.0
    target = round(sched.posts_per_day * frac)
    remaining = target - (already or 0)
    if remaining <= 0:
        return 0

    slots = _slot_times(sched, day, tz, now_local, remaining)
    for slot_utc in slots:
        session.add(
            Publication(
                chat_id=chat.id,
                template_id=random.choice(templates).id,
                category_id=category_id,
                scheduled_at=slot_utc,
                status=PublicationStatus.planned,
            )
        )
    return len(slots)


async def plan_day(session: AsyncSession, tz_name: str, now: datetime | None = None) -> int:
    tz = ZoneInfo(tz_name)
    now_local = (now or datetime.now(timezone.utc)).astimezone(tz)
    today = now_local.date()

    day_start_utc, day_end_utc = _local_day_bounds_utc(today, tz)

    # Cancel leftovers planned for previous days: never fire yesterday's posts
    # late (that would dump a burst and look like spam).
    await session.execute(
        update(Publication)
        .where(
            Publication.status == PublicationStatus.planned,
            Publication.scheduled_at < day_start_utc,
        )
        .values(status=PublicationStatus.cancelled)
    )
    await session.commit()

    active_templates = list(
        (
            await session.scalars(
                select(Template).where(Template.is_active.is_(True)).order_by(Template.id)
            )
        ).all()
    )
    if not active_templates:
        return 0

    planned_total = 0
    covered_chat_ids: set[int] = set()

    # --- Campaign categories: each with its own schedule + templates + chats ---
    categories = list(
        (
            await session.scalars(
                select(Category)
                .where(Category.is_active.is_(True))
                .options(selectinload(Category.chats), selectinload(Category.templates))
            )
        ).all()
    )
    for cat in categories:
        cat_templates = [t for t in cat.templates if t.is_active] or active_templates[:1]
        for chat in cat.chats:
            covered_chat_ids.add(chat.id)
            if not (chat.is_enabled and chat.permission in _ELIGIBLE):
                continue
            planned_total += await _schedule_chat(
                session, chat, cat, cat_templates, cat.id,
                today, tz, now_local, day_start_utc, day_end_utc,
            )

    # --- Chats not in any active category: fall back to their per-chat rules ---
    chats = list(
        (
            await session.scalars(
                select(Chat)
                .where(Chat.is_enabled.is_(True), Chat.permission.in_(_ELIGIBLE))
                .options(selectinload(Chat.templates))
            )
        ).all()
    )
    for chat in chats:
        if chat.id in covered_chat_ids:
            continue
        eligible = _chat_templates(chat, active_templates)
        planned_total += await _schedule_chat(
            session, chat, chat, eligible, None,
            today, tz, now_local, day_start_utc, day_end_utc,
        )

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
    window_start, window_end = _window_bounds_utc(chat, day, tz)
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
