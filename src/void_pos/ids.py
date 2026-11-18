from __future__ import annotations

from typing import Annotated

from pydantic import StringConstraints
from ulid import ULID

# Crockford base32, upper case, 26 chars: what `new_ulid` and the web POS both mint.
ULID_PATTERN = r"^[0-9A-HJKMNP-TV-Z]{26}$"

Ulid = Annotated[str, StringConstraints(pattern=ULID_PATTERN)]


def new_ulid() -> str:
    """A fresh, lexicographically sortable id a device can mint offline (26 chars)."""
    return str(ULID())
