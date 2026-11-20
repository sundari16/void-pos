"""The per-location web-ordering device.

Every order needs a device: the device owns the order-number range and the per-device
sequence that make the write path idempotent and collision-free. The storefront has no
paired till, so each location gets one server-side ``Device`` dedicated to the ``web``
channel, with its own reserved high number band (so it never overlaps a till's range). It
has no token — it is never reachable from outside; the storefront service drives it.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..devices import get_location
from ..models import Device

WEB_DEVICE_NAME = "Online ordering"
# A reserved band well clear of typical till ranges (1000s). order_number is a 32-bit int.
WEB_ORDER_NUMBER_START = 2_000_000_000
WEB_ORDER_NUMBER_END = 2_147_483_647


def web_device(session: Session, location_id: str, *, for_update: bool = False) -> Device:
    """The location's web-ordering device, created on first use. ``for_update`` locks the
    row until commit so concurrent online checkouts allocate order numbers and sequences
    one at a time."""
    get_location(session, location_id)
    device = session.scalars(
        select(Device)
        .where(
            Device.location_id == location_id,
            Device.order_number_start == WEB_ORDER_NUMBER_START,
        )
        .order_by(Device.id)
    ).first()
    if device is None:
        device = Device(
            location_id=location_id,
            name=WEB_DEVICE_NAME,
            order_number_start=WEB_ORDER_NUMBER_START,
            order_number_end=WEB_ORDER_NUMBER_END,
        )
        session.add(device)
        session.flush()
    if for_update:
        locked = session.get(Device, device.id, with_for_update=True)
        assert locked is not None
        return locked
    return device
