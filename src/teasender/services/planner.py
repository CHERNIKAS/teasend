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
from teasender.services.settings_store import (
    POST_MODE,
    SMART_CAP,
    SMART_DEAD_DAYS,
    SMART_DEFAULTS,
    SMART_MIN_INT_H,
    SMART_MODE,
    SMART_PROBE_DAYS,
    SMART_SHARE,
    SMART_WINDOW,
    get_setting,
)

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
    pool: bool = False,
) -> int:
    """Top up one chat's daily quota for a given schedule source. Quota is
    counted per (chat, category) so a chat in several campaigns gets each one.

    In pool mode the publication carries no template (template_id NULL); the post
    is assembled from the photo pool at send time."""
    weekday = now_local.weekday()
    if not (sched.days_mask & (1 << weekday)):
        return 0
    if not pool and not templates:
        return 0

    now_utc = now_local.astimezone(timezone.utc)

    # Chat rule from its description/pin: never post more often than allowed.
    if chat.rule_min_interval_h:
        last_post = await session.scalar(
            select(func.max(Publication.scheduled_at)).where(
                Publication.chat_id == chat.id,
                Publication.status != PublicationStatus.cancelled,
            )
        )
        if last_post is not None and (now_utc - as_utc(last_post)) < timedelta(hours=chat.rule_min_interval_h):
            return 0  # too soon per the chat's rule
        slots = _slot_times(sched, day, tz, now_local, 1)  # one post, respect the rule
        for slot_utc in slots:
            session.add(Publication(
                chat_id=chat.id,
                template_id=None if pool else random.choice(templates).id,
                category_id=category_id,
                scheduled_at=slot_utc,
                status=PublicationStatus.planned,
            ))
        return len(slots)

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
                template_id=None if pool else random.choice(templates).id,
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

    pool_mode = (await get_setting(session, POST_MODE, "templates")) == "pool"

    # --- Smart mode: cadence driven by each chat's activity, not manual rules ---
    if (await get_setting(session, SMART_MODE, "off")) == "on":
        active_templates = list((await session.scalars(
            select(Template).where(Template.is_active.is_(True)).order_by(Template.id)
        )).all())
        if not pool_mode and not active_templates:
            return 0
        return await _plan_smart(
            session, tz, now_local, day_start_utc, day_end_utc,
            active_templates, pool_mode,
        )

    # --- Pool mode: schedule every enabled chat; post assembled at send time ---
    if pool_mode:
        planned_total = 0
        chats = list(
            (await session.scalars(
                select(Chat).where(Chat.is_enabled.is_(True))
            )).all()
        )
        for chat in chats:
            planned_total += await _schedule_chat(
                session, chat, chat, [], None,
                today, tz, now_local, day_start_utc, day_end_utc, pool=True,
            )
        await session.commit()
        return planned_total

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
            if not chat.is_enabled:  # "отправка" toggle is the gate
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
                .where(Chat.is_enabled.is_(True))
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


def _parse_window(raw: str) -> tuple[time, time]:
    try:
        a, b = raw.split("-")
        return time(int(a) % 24, 0), time(int(b) % 24, 0)
    except Exception:  # noqa: BLE001
        return time(9, 0), time(22, 0)


async def _smart_setting(session, key: str) -> str:
    return await get_setting(session, key, SMART_DEFAULTS.get(key, "0"))


async def _plan_smart(
    session: AsyncSession,
    tz: ZoneInfo,
    now_local: datetime,
    day_start_utc: datetime,
    day_end_utc: datetime,
    active_templates: list[Template],
    pool_mode: bool,
) -> int:
    share = float(await _smart_setting(session, SMART_SHARE)) / 100.0
    cap = float(await _smart_setting(session, SMART_CAP))
    dead_days = float(await _smart_setting(session, SMART_DEAD_DAYS))
    probe_days = float(await _smart_setting(session, SMART_PROBE_DAYS))
    min_int_h = float(await _smart_setting(session, SMART_MIN_INT_H))
    win_start_t, win_end_t = _parse_window(await _smart_setting(session, SMART_WINDOW))

    now_utc = now_local.astimezone(timezone.utc)
    today = now_local.date()
    w_start = _combine_utc(today, win_start_t, tz)
    w_end = _combine_utc(today, win_end_t, tz)
    if w_end <= w_start:
        w_end = _combine_utc(today, time(0, 0), tz) + timedelta(days=1) - timedelta(seconds=1)
    earliest = max(w_start, now_utc)

    chats = list((await session.scalars(
        select(Chat).where(Chat.is_enabled.is_(True)).options(selectinload(Chat.templates))
    )).all())

    planned = 0
    for chat in chats:
        # Chats opted out of smart mode use their own per-chat schedule.
        if chat.smart_exempt:
            planned += await _schedule_chat(
                session, chat, chat, _chat_templates(chat, active_templates), None,
                today, tz, now_local, day_start_utc, day_end_utc, pool=pool_mode,
            )
            continue
        if earliest >= w_end:
            continue  # smart window closed for today
        # Already have a future post queued? then it's covered.
        pending = await session.scalar(
            select(func.count()).select_from(Publication).where(
                Publication.chat_id == chat.id,
                Publication.status == PublicationStatus.planned,
                Publication.scheduled_at >= now_utc,
            )
        )
        if pending:
            continue

        # Last time we posted here (any non-cancelled publication).
        last_post = await session.scalar(
            select(func.max(Publication.scheduled_at)).where(
                Publication.chat_id == chat.id,
                Publication.status != PublicationStatus.cancelled,
            )
        )

        # Activity rate (messages/day).
        last_act = as_utc(chat.last_activity_at)
        if chat.activity_window_start and chat.activity_msgs:
            days = max(0.5, (now_utc - as_utc(chat.activity_window_start)).total_seconds() / 86400)
            rate = chat.activity_msgs / days
        else:
            rate = 0.0

        dead = last_act is None or (now_utc - last_act) >= timedelta(days=dead_days)
        if dead:
            interval_h = probe_days * 24  # quiet chat: just probe periodically
        else:
            ppd = min(cap, share * rate)
            interval_h = probe_days * 24 if ppd <= 0 else max(min_int_h, 24.0 / ppd)

        # Never post more often than the chat's own rule allows.
        if chat.rule_min_interval_h:
            interval_h = max(interval_h, float(chat.rule_min_interval_h))

        if last_post is not None and (now_utc - as_utc(last_post)) < timedelta(hours=interval_h):
            continue  # not due yet

        # Ride activity: if the chat spoke recently, post soon; else small delay.
        recent = last_act is not None and (now_utc - last_act) < timedelta(minutes=30)
        lo, hi = (60, 600) if recent else (300, 3600)  # seconds
        slot = earliest + timedelta(seconds=random.uniform(lo, hi))
        if slot >= w_end:
            slot = earliest

        templates = _chat_templates(chat, active_templates)
        if not pool_mode and not templates:
            continue
        session.add(Publication(
            chat_id=chat.id,
            template_id=None if pool_mode else random.choice(templates).id,
            scheduled_at=slot,
            status=PublicationStatus.planned,
        ))
        planned += 1

    await session.commit()
    return planned


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
