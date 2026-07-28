"""Shared enumerations."""
from __future__ import annotations

import enum


class Permission(str, enum.Enum):
    """Whether we may post announcements to a chat.

    The scheduler only ever targets `allowed`/`owner`. Everything else is
    excluded — this is the guardrail that keeps posting rule-respecting.
    """

    owner = "owner"        # your own chat/channel
    allowed = "allowed"    # chat rules permit announcements
    unknown = "unknown"    # default after import; not eligible
    denied = "denied"      # ads forbidden / asked not to post


class PublicationStatus(str, enum.Enum):
    planned = "planned"    # scheduled, not yet due
    sending = "sending"    # picked up by the sender
    sent = "sent"
    failed = "failed"
    skipped = "skipped"    # rule no longer allows it at send time
    cancelled = "cancelled"


class LogLevel(str, enum.Enum):
    info = "info"
    warning = "warning"
    error = "error"


class AccountState(str, enum.Enum):
    active = "active"
    paused = "paused"        # manual pause
    flood_paused = "flood_paused"  # auto-paused after a spam limit
