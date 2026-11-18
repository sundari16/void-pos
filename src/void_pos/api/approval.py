"""Manager approval on a till: check the PIN, throttling wrong guesses per till."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from ..errors import Forbidden
from ..models import Device, Staff
from ..staff import approving_manager
from .ratelimit import AttemptLimiter, get_pin_limiter

PinLimiterDep = Annotated[AttemptLimiter, Depends(get_pin_limiter)]


def approve(
    session: Session, device: Device, pin: str | None, limiter: AttemptLimiter
) -> Staff | None:
    """The approving manager, or None if the location has no managers yet."""
    key = f"device:{device.id}"
    limiter.check(key)
    try:
        return approving_manager(session, device.location_id, pin)
    except Forbidden as exc:
        if exc.code == "wrong_pin":
            limiter.failed(key)
        raise
