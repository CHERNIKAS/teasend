"""Sync templates from the drafts channel into the DB (upsert by source)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from teasender.db.models import Template
from teasender.telegram.client import DraftTemplate


async def sync_templates(session: AsyncSession, drafts: list[DraftTemplate]) -> tuple[int, int, int]:
    """Mirror the drafts channel into templates.

    Inserts new drafts, refreshes existing ones, and removes templates whose
    source message is gone from the channel. Matched by
    (source_channel_id, source_message_id).

    Returns (created, updated, removed). If the channel returned nothing (e.g. a
    read error) no deletion happens — we never wipe on an empty read.
    """
    created = updated = removed = 0
    for d in drafts:
        existing = await session.scalar(
            select(Template).where(
                Template.source_channel_id == d.source_channel_id,
                Template.source_message_id == d.source_message_id,
            )
        )
        if existing is None:
            session.add(
                Template(
                    source_channel_id=d.source_channel_id,
                    source_message_id=d.source_message_id,
                    grouped_id=d.grouped_id,
                    label=d.preview_text[:60] or f"msg {d.source_message_id}",
                    preview_text=d.preview_text,
                )
            )
            created += 1
        else:
            existing.preview_text = d.preview_text
            existing.grouped_id = d.grouped_id
            updated += 1

    if drafts:
        channel_ids = {d.source_channel_id for d in drafts}
        seen = {(d.source_channel_id, d.source_message_id) for d in drafts}
        stale = list((await session.scalars(
            select(Template).where(Template.source_channel_id.in_(channel_ids))
        )).all())
        for t in stale:
            if (t.source_channel_id, t.source_message_id) not in seen:
                await session.delete(t)
                removed += 1

    await session.commit()
    return created, updated, removed
