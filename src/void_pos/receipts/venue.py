"""The venue details a customer receipt shows, and the ABN check."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..devices import get_location
from ..errors import InvalidRequest
from ..models import Location

VENUE_FIELDS = ("business_name", "abn", "address", "phone", "receipt_footer")
_WEIGHTS = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)


def normalize_abn(text: str) -> str:
    """An Australian Business Number as 11 digits, or InvalidRequest. The check: take 1
    from the first digit, weight the digits, and the sum divides by 89."""
    digits = "".join(ch for ch in text if not ch.isspace())
    if len(digits) != 11 or any(ch not in "0123456789" for ch in digits):
        raise InvalidRequest("an ABN is 11 digits", code="invalid_abn")
    values = [int(d) for d in digits]
    values[0] -= 1
    if sum(v * w for v, w in zip(values, _WEIGHTS, strict=True)) % 89:
        raise InvalidRequest("that ABN doesn't pass the ABN check; check the digits", code="invalid_abn")
    return digits


def format_abn(abn: str) -> str:
    return f"{abn[:2]} {abn[2:5]} {abn[5:8]} {abn[8:]}"


def update_venue(session: Session, location_id: str, fields: dict[str, Any]) -> Location:
    """Set the receipt details given; a blank value clears one."""
    location = get_location(session, location_id)
    for key, value in fields.items():
        if key not in VENUE_FIELDS:
            continue
        value = value.strip() if isinstance(value, str) else value
        if key == "abn" and value:
            value = normalize_abn(value)
        setattr(location, key, value or None)
    session.flush()
    return location
