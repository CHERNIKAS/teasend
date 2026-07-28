"""Sync templates from the drafts channel into the DB (upsert by source)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from teasender.db.models import Template
from teasender.telegram.client import DraftTemplate


async def sync_templates(session: AsyncSession, drafts: list[DraftTemplate]) -> tuple[int, int]:
    """Insert new drafts as templates, refresh preview text of existing ones.

    Returns (created, updated). Existing templates are matched by
    (source_channel_id, source_message_id) and never duplicated.
    """
    created = updated = 0
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
    await session.commit()
    return created, updated
