"""Venue setup and till pairing.

An admin registers a till with a private order-number range and gets a one-time pairing
code (e.g. ``K7QM-4XPA``, valid for minutes). The till redeems the code once for a
long-lived bearer token. Only SHA-256 hashes of codes and tokens are stored: both are
high-entropy random secrets, so a fast hash is enough and a leaked database doesn't
leak working credentials.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clock import as_aware, is_valid_timezone, utcnow
from ..errors import Conflict, InvalidRequest, NotFound, Unauthorized
from ..models import Device, Location, Org

# No 0/O, 1/I/L: codes are read off one screen and typed into another.
PAIRING_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PAIRING_CODE_LENGTH = 8


@dataclass(frozen=True)
class PairingCode:
    code: str  # shown once, formatted XXXX-XXXX
    expires_at: datetime


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def normalize_pairing_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def create_location(
    session: Session, *, org_name: str, name: str, timezone: str = "Australia/Brisbane"
) -> Location:
    """Create a location, reusing the org of the same name if it exists."""
    if not is_valid_timezone(timezone):
        raise InvalidRequest(f"unknown timezone {timezone!r}", code="invalid_timezone")
    org = session.scalars(select(Org).where(Org.name == org_name)).first()
    if org is None:
        org = Org(name=org_name)
        session.add(org)
        session.flush()
    location = Location(org_id=org.id, name=name, timezone=timezone)
    session.add(location)
    session.flush()
    return location


def list_locations(session: Session) -> list[Location]:
    return list(session.scalars(select(Location).order_by(Location.name)).all())


def get_location(session: Session, location_id: str) -> Location:
    location = session.get(Location, location_id)
    if location is None:
        raise NotFound("location not found")
    return location


def get_device(session: Session, device_id: str) -> Device:
    device = session.get(Device, device_id)
    if device is None:
        raise NotFound("device not found")
    return device


def list_devices(session: Session, location_id: str) -> list[Device]:
    get_location(session, location_id)
    return list(
        session.scalars(
            select(Device).where(Device.location_id == location_id).order_by(Device.name)
        ).all()
    )


def register_device(
    session: Session,
    *,
    location_id: str,
    name: str,
    order_number_start: int,
    order_number_end: int,
    pairing_ttl: timedelta,
) -> tuple[Device, PairingCode]:
    """Register a till with its own order-number range and issue its first pairing code."""
    get_location(session, location_id)
    if order_number_start < 1 or order_number_end < order_number_start:
        raise InvalidRequest(
            "the order-number range must start at 1 or more and end at or after its start",
            code="invalid_range",
        )
    clash = session.scalars(
        select(Device).where(
            Device.location_id == location_id,
            Device.order_number_start <= order_number_end,
            Device.order_number_end >= order_number_start,
        )
    ).first()
    if clash is not None:
        raise Conflict(
            f"order numbers {order_number_start}-{order_number_end} overlap {clash.name} "
            f"({clash.order_number_start}-{clash.order_number_end})",
            code="range_overlap",
        )
    device = Device(
        location_id=location_id,
        name=name,
        order_number_start=order_number_start,
        order_number_end=order_number_end,
    )
    session.add(device)
    session.flush()
    return device, issue_pairing_code(session, device, ttl=pairing_ttl)


def issue_pairing_code(session: Session, device: Device, *, ttl: timedelta) -> PairingCode:
    """A fresh one-time code. Redeeming it replaces the till's token, so this is also how
    a till that lost its storage gets back in."""
    if device.revoked_at is not None:
        raise Conflict("this till is revoked; register a new one instead", code="device_revoked")
    raw = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(PAIRING_CODE_LENGTH))
    expires_at = utcnow() + ttl
    device.pairing_code_hash = hash_secret(raw)
    device.pairing_expires_at = expires_at
    session.flush()
    half = PAIRING_CODE_LENGTH // 2
    return PairingCode(code=f"{raw[:half]}-{raw[half:]}", expires_at=expires_at)


def pair_device(session: Session, code: str) -> tuple[Device, str]:
    """Redeem a pairing code for the till's bearer token (returned once, never stored)."""
    normalized = normalize_pairing_code(code)
    device = session.scalars(
        select(Device).where(Device.pairing_code_hash == hash_secret(normalized))
    ).first()
    if device is None or device.pairing_expires_at is None:
        raise Unauthorized("that pairing code isn't valid", code="invalid_pairing_code")
    if device.revoked_at is not None:
        raise Unauthorized("this till is revoked", code="device_revoked")
    if as_aware(device.pairing_expires_at) <= utcnow():
        raise Unauthorized(
            "that pairing code has expired; ask for a new one", code="pairing_code_expired"
        )
    token = secrets.token_urlsafe(32)
    device.token_hash = hash_secret(token)
    device.paired_at = utcnow()
    device.pairing_code_hash = None
    device.pairing_expires_at = None
    session.flush()
    return device, token


def authenticate_device(session: Session, token: str) -> Device | None:
    device = session.scalars(
        select(Device).where(Device.token_hash == hash_secret(token))
    ).first()
    if device is None or device.revoked_at is not None:
        return None
    return device


def revoke_device(session: Session, device_id: str) -> Device:
    """Cut a till off: its token stops working at once and it can't be re-paired."""
    device = get_device(session, device_id)
    if device.revoked_at is None:
        device.revoked_at = utcnow()
    device.token_hash = None
    device.pairing_code_hash = None
    device.pairing_expires_at = None
    session.flush()
    return device
