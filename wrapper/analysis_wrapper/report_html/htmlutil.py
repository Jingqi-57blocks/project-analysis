"""Tiny deterministic HTML helpers shared across the report generator.

No templating engine and no third-party HTML library: the report is presentation
glue over already-structured data, so a handful of escaping helpers keep every
emitted byte auditable and stable.
"""

from __future__ import annotations

import html
import re
import unicodedata

__all__ = ["esc", "attr", "slugify", "SlugAllocator"]


def esc(text: object) -> str:
    """HTML-escape text for use in element bodies and attributes."""
    return html.escape("" if text is None else str(text), quote=True)


def attr(value: object) -> str:
    """Escape a value for a double-quoted attribute."""
    return html.escape("" if value is None else str(value), quote=True)


_SLUG_STRIP = re.compile(r"[^\w一-鿿 -]", re.UNICODE)
_SLUG_SPACE = re.compile(r"[\s-]+", re.UNICODE)


def slugify(text: str) -> str:
    """GitHub-flavoured heading slug: lowercase, keep word/CJK chars, dash-join.

    Deterministic and language-agnostic; CJK characters are preserved verbatim so
    zh-CN headings anchor stably instead of collapsing to empty strings.
    """
    normalized = unicodedata.normalize("NFC", text).strip().lower()
    normalized = _SLUG_STRIP.sub("", normalized)
    normalized = _SLUG_SPACE.sub("-", normalized).strip("-")
    return normalized or "section"


class SlugAllocator:
    """Allocate unique slugs in document order (``x``, ``x-1``, ``x-2`` ...).

    A single allocator instance must back both the rendered anchors and the
    content map so the two agree byte-for-byte.
    """

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def allocate(self, text: str) -> str:
        base = slugify(text)
        seen = self._counts.get(base, 0)
        self._counts[base] = seen + 1
        return base if seen == 0 else f"{base}-{seen}"
