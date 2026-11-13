from __future__ import annotations

from ulid import ULID


def new_ulid() -> str:
    """A fresh, lexicographically sortable id a device can mint offline (26 chars)."""
    return str(ULID())
