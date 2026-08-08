"""Spintax expansion: {a|b|c} -> one random option, with nesting.

    "{Привет|Здравствуйте}, {в продаже|есть} чай {🍵|🫖}"

Innermost braces resolve first, so nested spintax like "{При{вет|вки}|Хай}" works.
Unbalanced/plain text passes through unchanged.
"""
from __future__ import annotations

import random
import re

_INNER = re.compile(r"\{([^{}]*)\}")


def spin(text: str | None) -> str:
    if not text:
        return ""
    out = text
    # Resolve one innermost group at a time until none remain.
    for _ in range(1000):  # safety bound against pathological input
        m = _INNER.search(out)
        if m is None:
            break
        choice = random.choice(m.group(1).split("|"))
        out = out[: m.start()] + choice + out[m.end() :]
    return out
