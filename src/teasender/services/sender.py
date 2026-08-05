"""Sender: deliver due publications from the account, respecting all limits.

Behaviour on trouble is conservative by design:
  * FloodWait / PeerFlood  -> auto-pause the account for the requested time and
    notify; we never try to push through a limit.
  * Write-forbidden / banned -> mark that publication failed and notify; the chat
    is left for you to review (we don't silently keep retrying).
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from teasender.config import Settings
from teasender.core.enums import AccountState, Permission, PublicationStatus
from teasender.db.models import Account, Chat, Publication, Template, as_utc, utcnow
from teasender.services.notify import Notifier

log = logging.getLogger("teasender.sender")

_ELIGIBLE = (Permission.allowed, Permission.owner)

# Errors that mean "you fundamentally can't post here" — deny the chat instead
# of retrying it day after day.
_WRITE_FORBIDDEN = {
    "ChatWriteForbiddenError",
    "UserBannedInChannelError",
    "ChannelPrivateError",
    "ChatAdminRequiredError",
    "UserDeactivatedError",
    "ChatRestrictedError",
}


class Sender:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        telegram_service,
        settings: Settings,
        notifier: Notifier,
    ) -> None:
        self._sm = sessionmaker
        self._tg = telegram_service
        self._settings = settings
        self._notifier = notifier
        self._last_send_at: datetime | None = None

    async def _cancel_stale(self) -> None:
        """Never send posts left over from previous days (a forgotten start must
        not dump yesterday's queue). Cancel anything scheduled before today."""
        tz = ZoneInfo(self._settings.timezone)
        now_local = utcnow().astimezone(tz)
        day_start = datetime.combine(
            now_local.date(), time(0, 0), tzinfo=tz
        ).astimezone(timezone.utc)
        async with self._sm() as s:
            res = await s.execute(
                update(Publication)
                .where(
                    Publication.status == PublicationStatus.planned,
                    Publication.scheduled_at < day_start,
                )
                .values(status=PublicationStatus.cancelled)
            )
            await s.commit()
            if res.rowcount:
                log.info("cancelled %d stale (pre-today) publications", res.rowcount)

    async def run_due_once(self) -> None:
        """Send every publication that is due now, one at a time."""
        await self._cancel_stale()
        if not await self._account_ready():
            async with self._sm() as s:
                acc = await s.scalar(select(Account).limit(1))
            state = acc.state.value if acc else "no-account"
            log.info("sender idle: account not ready (state=%s)", state)
            return

        async with self._sm() as s:
            due = await s.scalar(
                select(func.count()).select_from(Publication).where(
                    Publication.status == PublicationStatus.planned,
                    Publication.scheduled_at <= utcnow(),
                )
            )
        if not due:
            log.info("sender idle: 0 publications due now")
            return
        log.info("sender: %d publication(s) due — sending", due)

        while True:
            pub = await self._claim_next_due()
            if pub is None:
                return
            await self._process(pub)
            if not await self._account_ready():  # a flood pause may have triggered
                return

    # --- account gate ----------------------------------------------------------

    async def _account_ready(self) -> bool:
        async with self._sm() as s:
            acc = await s.scalar(select(Account).limit(1))
            if acc is None:
                return False
            if acc.state == AccountState.active:
                return True
            if acc.state == AccountState.flood_paused and acc.flood_until:
                if utcnow() >= as_utc(acc.flood_until):
                    acc.state = AccountState.active
                    acc.pause_reason = None
                    acc.flood_until = None
                    await s.commit()
                    await self._notifier.send("▶️ Пауза снята, публикации возобновлены.")
                    return True
            return False

    async def _pause_flood(self, seconds: int, reason: str) -> None:
        until = utcnow() + timedelta(seconds=seconds)
        async with self._sm() as s:
            acc = await s.scalar(select(Account).limit(1))
            acc.state = AccountState.flood_paused
            acc.pause_reason = reason
            acc.flood_until = until
            await s.commit()
        await self._notifier.send(
            f"⏸️ Аккаунт на паузе до {until:%H:%M %d.%m} (UTC).\nПричина: {reason}"
        )

    # --- claiming --------------------------------------------------------------

    async def _claim_next_due(self) -> Publication | None:
        async with self._sm() as s:
            pub = await s.scalar(
                select(Publication)
                .where(
                    Publication.status == PublicationStatus.planned,
                    Publication.scheduled_at <= utcnow(),
                )
                .order_by(Publication.scheduled_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if pub is None:
                return None
            pub.status = PublicationStatus.sending
            pub.attempts += 1
            await s.commit()
            return pub

    # --- processing one publication --------------------------------------------

    async def _process(self, pub: Publication) -> None:
        async with self._sm() as s:
            chat = await s.get(Chat, pub.chat_id)
            template = await s.get(Template, pub.template_id)

        if chat is None or template is None:
            log.warning("pub %s: chat/template missing -> failed", pub.id)
            await self._finish(pub.id, PublicationStatus.failed, error="chat/template missing")
            return
        if not self._rule_ok(chat):
            log.info("pub %s: rule no longer allows (chat=%s) -> skipped", pub.id, chat.title)
            await self._finish(pub.id, PublicationStatus.skipped, error="rule no longer allows")
            return

        await self._respect_global_interval()

        if self._settings.dry_run:
            log.info("[DRY_RUN] would post template %s -> chat %s", template.id, chat.title)
            await self._finish(pub.id, PublicationStatus.skipped, error="dry-run")
            return

        try:
            msg_id = await self._tg.copy_to(
                chat.tg_chat_id,
                template.source_channel_id,
                template.source_message_id,
                template.grouped_id,
            )
        except Exception as exc:  # noqa: BLE001 - classified below
            await self._handle_send_error(pub.id, chat, exc)
            return

        self._last_send_at = utcnow()
        log.info("pub %s: sent to chat %s (msg_id=%s)", pub.id, chat.title, msg_id)
        await self._finish(pub.id, PublicationStatus.sent, tg_message_id=msg_id)
        await self._bump_chat(chat.id, ok=True)
        await self._notifier.send(f"✅ Доставлено в «{chat.title}»")

    def _rule_ok(self, chat: Chat) -> bool:
        # Spacing is decided at planning time (random slots across the window);
        # here we only re-check the permission gate in case it changed.
        return chat.is_enabled and chat.permission in _ELIGIBLE

    async def _respect_global_interval(self) -> None:
        import asyncio

        if self._last_send_at is None:
            return
        elapsed = (utcnow() - self._last_send_at).total_seconds()
        wait = self._settings.global_min_send_interval - elapsed
        if wait > 0:
            await asyncio.sleep(wait)

    async def _handle_send_error(self, pub_id: int, chat: Chat, exc: Exception) -> None:
        name = type(exc).__name__
        seconds = getattr(exc, "seconds", None)

        if name == "FloodWaitError" and seconds:
            await self._finish(pub_id, PublicationStatus.planned)  # retry later
            if self._settings.auto_pause_on_flood:
                await self._pause_flood(int(seconds), f"FloodWait {seconds}s")
            return
        if name == "PeerFloodError":
            await self._finish(pub_id, PublicationStatus.planned)
            if self._settings.auto_pause_on_flood:
                await self._pause_flood(3600, "PeerFlood (спам-лимит Telegram)")
            return

        # Permanent "can't post here" problems: stop hammering the chat, deny it.
        if name in _WRITE_FORBIDDEN:
            await self._finish(pub_id, PublicationStatus.failed, error=f"{name}: {exc}")
            await self._deny_chat(chat.id)
            await self._notifier.send(
                f"⛔ В «{chat.title}» писать нельзя ({name}) — чат авто-запрещён."
            )
            return

        # Other non-retryable delivery problems.
        await self._finish(pub_id, PublicationStatus.failed, error=f"{name}: {exc}")
        await self._bump_chat(chat.id, ok=False)
        await self._notifier.send(f"⚠️ Не доставлено в «{chat.title}»: {name}")

    async def _deny_chat(self, chat_id) -> None:
        async with self._sm() as s:
            chat = await s.get(Chat, chat_id)
            if chat is not None:
                chat.permission = Permission.denied
                chat.is_enabled = False
                chat.fail_count += 1
                await s.commit()

    # --- persistence helpers ---------------------------------------------------

    async def _finish(self, pub_id, status, tg_message_id=None, error=None) -> None:
        async with self._sm() as s:
            pub = await s.get(Publication, pub_id)
            pub.status = status
            if tg_message_id is not None:
                pub.tg_message_id = tg_message_id
            if error is not None:
                pub.error = error
            if status == PublicationStatus.sent:
                pub.sent_at = utcnow()
            await s.commit()

    async def _bump_chat(self, chat_id, *, ok: bool) -> None:
        async with self._sm() as s:
            chat = await s.get(Chat, chat_id)
            if ok:
                chat.success_count += 1
                chat.last_sent_at = utcnow()
            else:
                chat.fail_count += 1
            await s.commit()
