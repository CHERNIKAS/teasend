"""Telethon wrapper: connect with the decrypted session and copy announcements.

Copying (not forwarding) is deliberate: we re-send the *content* of your draft
message so it appears as your own post, not a "Forwarded from" card. Custom
(premium) emoji survive because we carry the message entities across, and albums
survive because Telethon's send_file accepts a media list.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.custom import Message

from teasender.config import Settings
from teasender.core.security import load_cipher, load_session


class NotAuthorized(RuntimeError):
    pass


@dataclass(slots=True)
class DraftTemplate:
    source_channel_id: int
    source_message_id: int
    grouped_id: int | None
    preview_text: str


class TelegramService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        cipher = load_cipher(settings.secret_key_file)
        session = load_session(cipher, settings.session_file)
        self._client = TelegramClient(
            StringSession(session), settings.api_id, settings.api_hash
        )

    @property
    def client(self) -> TelegramClient:
        return self._client

    async def start(self) -> None:
        await self._client.connect()
        if not await self._client.is_user_authorized():
            raise NotAuthorized("session not authorized; run python -m teasender.tools.login")
        # Warm the entity cache: a StringSession doesn't persist access hashes
        # across processes, so resolving a chat by its bare id later would raise
        # ValueError. Loading dialogs once populates the cache for all of them.
        try:
            await self._client.get_dialogs()
        except Exception:  # noqa: BLE001 - non-fatal cache warm-up
            pass

    async def close(self) -> None:
        await self._client.disconnect()

    # --- drafts -> templates ---------------------------------------------------

    async def read_drafts(self, channel: str | int, limit: int = 200) -> list[DraftTemplate]:
        """Read the drafts channel and collapse albums into one template each.

        For an album the anchor is the *earliest* message (lowest id) so sending,
        which walks forward from the anchor, captures the whole group; the caption
        is taken from whichever album member actually carries text."""
        entity = await self._client.get_entity(channel)
        channel_id = entity.id
        # grouped_id -> {"min_id": int, "text": str}
        groups: dict[int, dict] = {}
        out: list[DraftTemplate] = []

        async for msg in self._client.iter_messages(entity, limit=limit):
            if not (msg.message or msg.media):
                continue
            gid = msg.grouped_id
            if gid is None:
                out.append(
                    DraftTemplate(
                        source_channel_id=channel_id,
                        source_message_id=msg.id,
                        grouped_id=None,
                        preview_text=(msg.message or "[медиа без текста]")[:200],
                    )
                )
                continue
            g = groups.setdefault(gid, {"min_id": msg.id, "text": ""})
            g["min_id"] = min(g["min_id"], msg.id)
            if not g["text"] and msg.message:
                g["text"] = msg.message

        for gid, g in groups.items():
            out.append(
                DraftTemplate(
                    source_channel_id=channel_id,
                    source_message_id=g["min_id"],
                    grouped_id=gid,
                    preview_text=(g["text"] or "[альбом]")[:200],
                )
            )
        return out

    # --- sending ---------------------------------------------------------------

    async def _load_source(self, channel_id: int, message_id: int, grouped_id: int | None):
        entity = await self._client.get_entity(channel_id)
        if grouped_id is None:
            msg = await self._client.get_messages(entity, ids=message_id)
            return entity, [msg]
        # Album: gather the contiguous grouped messages around the anchor.
        ids = list(range(message_id, message_id + 10))
        msgs = await self._client.get_messages(entity, ids=ids)
        group = [m for m in msgs if m is not None and m.grouped_id == grouped_id]
        return entity, group or [await self._client.get_messages(entity, ids=message_id)]

    async def _input_entity(self, tg_id: int):
        """Resolve a chat for sending. On a cache miss (Telethon raises
        ValueError), refresh dialogs once and retry — new/uncached chats resolve
        after that."""
        try:
            return await self._client.get_input_entity(tg_id)
        except ValueError:
            await self._client.get_dialogs()
            return await self._client.get_input_entity(tg_id)

    async def send_pool_album(
        self,
        target_tg_id: int,
        pool_channel: str | int,
        caption: str,
        min_photos: int = 2,
        max_photos: int = 4,
    ) -> int:
        """Assemble a fresh album from random photos in the pool channel.

        Reads the pool channel, picks between min_photos and max_photos random
        photos, and posts them as one album with `caption`. Different subset each
        time — that's the per-send uniqueness."""
        entity = await self._client.get_entity(pool_channel)
        photos = [
            m async for m in self._client.iter_messages(entity, limit=200)
            if m.photo is not None
        ]
        if not photos:
            raise ValueError("photo pool is empty")

        k = min(random.randint(min_photos, max_photos), len(photos))
        chosen = random.sample(photos, k)
        target = await self._input_entity(target_tg_id)

        if k == 1:
            sent = await self._client.send_file(target, chosen[0].media, caption=caption or "")
            return sent.id
        media = [m.media for m in chosen]
        captions = [caption or ""] + [""] * (k - 1)  # caption on the first photo
        sent = await self._client.send_file(target, media, caption=captions)
        first = sent[0] if isinstance(sent, list) else sent
        return first.id

    async def copy_to(
        self, target_tg_id: int, channel_id: int, message_id: int, grouped_id: int | None
    ) -> int:
        """Copy a draft (single message or album) to `target_tg_id`.

        Returns the sent message id. Raises Telethon errors (FloodWait etc.) to
        the caller, which decides how to back off.
        """
        _src_entity, msgs = await self._load_source(channel_id, message_id, grouped_id)
        target = await self._input_entity(target_tg_id)

        if len(msgs) == 1:
            m: Message = msgs[0]
            if m.media:
                sent = await self._client.send_file(
                    target,
                    m.media,
                    caption=m.message or "",
                    formatting_entities=m.entities,
                )
            else:
                sent = await self._client.send_message(
                    target, m.message, formatting_entities=m.entities
                )
            return sent.id

        # Album: send all media as one grouped post; caption goes on the first.
        media = [m.media for m in msgs if m.media]
        captions = [m.message or "" for m in msgs if m.media]
        sent = await self._client.send_file(target, media, caption=captions)
        first = sent[0] if isinstance(sent, list) else sent
        return first.id
