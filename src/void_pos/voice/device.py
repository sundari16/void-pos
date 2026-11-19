"""The per-location "voice device".

Every order in the pipeline needs a device: a device owns a private order-number range and
a per-device sequence, which together make writes idempotent and collision-free (see
``models.Device`` and ``events.apply``). A phone order has no paired till, so each location
gets one server-side **voice device**, created lazily on the first voice order.

It is identified by a reserved order-number band well above any till's, and it holds no
token — nothing outside the server can authenticate as it; only the voice-intake service
drives it. Locking its row (``for_update``) while a voice order allocates its number and
sequence keeps two concurrent calls from colliding.

The band sits just below the online-ordering "web device" band so the two channels never
share numbers if both are installed.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..devices import get_location, next_order_number
from ..models import Device

VOICE_DEVICE_NAME = "Voice line"
# Reserved, far above any till range; one band below the web device's (2_000_000_000+).
VOICE_ORDER_NUMBER_START = 1_900_000_000
VOICE_ORDER_NUMBER_END = 1_999_999_999


def voice_device(session: Session, location_id: str, *, for_update: bool = False) -> Device:
    """This location's voice device, created on first use. ``for_update`` locks the row so
    concurrent voice orders allocate order numbers and sequences one at a time."""
    get_location(session, location_id)  # 404s for an unknown location
    stmt = select(Device).where(
        Device.location_id == location_id,
        Device.order_number_start == VOICE_ORDER_NUMBER_START,
    )
    if for_update:
        stmt = stmt.with_for_update()
    device = session.scalars(stmt).first()
    if device is None:
        device = Device(
            location_id=location_id,
            name=VOICE_DEVICE_NAME,
            order_number_start=VOICE_ORDER_NUMBER_START,
            order_number_end=VOICE_ORDER_NUMBER_END,
        )
        session.add(device)
        session.flush()
        if for_update:  # re-read with the lock held now the row exists
            device = session.scalars(stmt).one()
    return device


def next_voice_number(session: Session, device: Device) -> int:
    return next_order_number(session, device)
