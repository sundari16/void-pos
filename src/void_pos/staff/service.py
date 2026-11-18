"""Staff PINs and manager approval.

A PIN is 4 to 8 digits, unique among a location's active staff so it identifies one person,
and stored as a salted scrypt hash. Approval: once a location has an active manager, a void
or switching a dish off from a till needs a manager's PIN, and the event or change records
who approved it. A location with no manager yet (a fresh install) needs none, so set one up
before going live.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import Conflict, Forbidden, InvalidRequest, NotFound
from ..models import Location, Staff

ROLES = ("staff", "manager")
PIN_PATTERN = re.compile(r"^[0-9]{4,8}$")
_N, _R, _P = 2**12, 8, 1  # cheap enough to check every manager at a venue per approval


def _hash(pin: str, salt: bytes) -> bytes:
    return hashlib.scrypt(pin.encode("ascii"), salt=salt, n=_N, r=_R, p=_P, dklen=32)


def hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    return f"scrypt${salt.hex()}${_hash(pin, salt).hex()}"


def pin_matches(pin: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt" or not PIN_PATTERN.match(pin):
        return False
    return hmac.compare_digest(_hash(pin, bytes.fromhex(salt)).hex(), digest)


def _check_pin(session: Session, location_id: str, pin: str, *, other_than: str | None) -> None:
    if not PIN_PATTERN.match(pin):
        raise InvalidRequest("a PIN is 4 to 8 digits", code="invalid_pin")
    for person in _active(session, location_id):
        if person.id != other_than and pin_matches(pin, person.pin_hash):
            raise Conflict("someone at this venue already uses that PIN", code="pin_taken")


def _active(session: Session, location_id: str, role: str | None = None) -> list[Staff]:
    stmt = select(Staff).where(Staff.location_id == location_id, Staff.active.is_(True))
    if role is not None:
        stmt = stmt.where(Staff.role == role)
    return list(session.scalars(stmt.order_by(Staff.name)).all())


def create_staff(session: Session, *, location_id: str, name: str, role: str, pin: str) -> Staff:
    if session.get(Location, location_id) is None:
        raise NotFound("location not found")
    if role not in ROLES:
        raise InvalidRequest(f"role must be one of {', '.join(ROLES)}", code="invalid_role")
    name = name.strip()
    if not name:
        raise InvalidRequest("a name is needed", code="invalid_request")
    _check_pin(session, location_id, pin, other_than=None)
    person = Staff(location_id=location_id, name=name, role=role, pin_hash=hash_pin(pin))
    session.add(person)
    session.flush()
    return person


def get_staff(session: Session, staff_id: str) -> Staff:
    person = session.get(Staff, staff_id)
    if person is None:
        raise NotFound("staff member not found")
    return person


def list_staff(session: Session, location_id: str, *, include_inactive: bool = False) -> list[Staff]:
    if include_inactive:
        stmt = select(Staff).where(Staff.location_id == location_id).order_by(Staff.name)
        return list(session.scalars(stmt).all())
    return _active(session, location_id)


def set_pin(session: Session, staff_id: str, pin: str) -> Staff:
    person = get_staff(session, staff_id)
    _check_pin(session, person.location_id, pin, other_than=person.id)
    person.pin_hash = hash_pin(pin)
    session.flush()
    return person


def deactivate_staff(session: Session, staff_id: str) -> Staff:
    person = get_staff(session, staff_id)
    person.active = False
    session.flush()
    return person


def managers_required(session: Session, location_id: str) -> bool:
    return bool(_active(session, location_id, "manager"))


def approving_manager(session: Session, location_id: str, pin: str | None) -> Staff | None:
    """The manager whose PIN this is, or None when the location has no managers yet.
    Raises when approval is needed and the PIN is missing or isn't a manager's."""
    managers = _active(session, location_id, "manager")
    if not managers:
        return None
    if not pin:
        raise Forbidden("a manager's PIN is needed for this", code="manager_pin_required")
    for manager in managers:
        if pin_matches(pin, manager.pin_hash):
            return manager
    raise Forbidden("that PIN isn't a manager's", code="wrong_pin")
