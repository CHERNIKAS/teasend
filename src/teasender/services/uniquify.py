"""Caption uniquification.

Appends a short random run of zero-width (invisible) characters to the end of a
caption so each sent copy has a different content hash — this defeats naive
"identical message" detection without changing how the text looks.

Why append at the END: message entities (bold, links, premium emoji) are
offset-based. Inserting characters mid-text would shift those offsets and corrupt
formatting. Appending after all text leaves every entity offset intact.
"""
from __future__ import annotations

import random

# Zero-width space, zero-width non-joiner, word joiner, zero-width joiner.
_INVISIBLE = ["​", "‌", "⁠", "‍"]


def uniquify(text: str | None) -> str:
    tail = "".join(random.choice(_INVISIBLE) for _ in range(random.randint(2, 6)))
    return (text or "") + tail
