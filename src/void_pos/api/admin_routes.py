"""Venue setup, admin only: locations, tills and their pairing codes, staff."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..devices import (
    create_location,
    get_device,
    issue_pairing_code,
    list_devices,
    list_locations,
    register_device,
    revoke_device,
)
from ..staff import create_staff, deactivate_staff, list_staff, set_pin
from .auth import AdminDep
from .deps import get_session
from .schemas import (
    DeviceCreated,
    DeviceIn,
    DeviceOut,
    LocationIn,
    LocationOut,
    PairingCodeOut,
    PinIn,
    StaffIn,
    StaffOut,
)

router = APIRouter(prefix="/admin", tags=["admin"])

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _ttl(settings: Settings) -> timedelta:
    return timedelta(minutes=settings.pairing_code_ttl_minutes)


@router.post("/locations", response_model=LocationOut, status_code=status.HTTP_201_CREATED)
def add_location(body: LocationIn, session: SessionDep, _: AdminDep) -> LocationOut:
    location = create_location(
        session, org_name=body.org_name, name=body.name, timezone=body.timezone
    )
    session.commit()
    return LocationOut.model_validate(location)


@router.get("/locations", response_model=list[LocationOut])
def read_locations(session: SessionDep, _: AdminDep) -> list[LocationOut]:
    return [LocationOut.model_validate(loc) for loc in list_locations(session)]


@router.post(
    "/locations/{location_id}/devices",
    response_model=DeviceCreated,
    status_code=status.HTTP_201_CREATED,
)
def add_device(
    location_id: str, body: DeviceIn, session: SessionDep, settings: SettingsDep, _: AdminDep
) -> DeviceCreated:
    device, code = register_device(
        session,
        location_id=location_id,
        name=body.name,
        order_number_start=body.order_number_start,
        order_number_end=body.order_number_end,
        pairing_ttl=_ttl(settings),
    )
    session.commit()
    return DeviceCreated(
        device=DeviceOut.of(device), pairing_code=code.code, expires_at=code.expires_at
    )


@router.get("/locations/{location_id}/devices", response_model=list[DeviceOut])
def read_devices(location_id: str, session: SessionDep, _: AdminDep) -> list[DeviceOut]:
    return [DeviceOut.of(d) for d in list_devices(session, location_id)]


@router.post("/devices/{device_id}/pairing-code", response_model=PairingCodeOut)
def new_pairing_code(
    device_id: str, session: SessionDep, settings: SettingsDep, _: AdminDep
) -> PairingCodeOut:
    code = issue_pairing_code(session, get_device(session, device_id), ttl=_ttl(settings))
    session.commit()
    return PairingCodeOut(pairing_code=code.code, expires_at=code.expires_at)


@router.post("/devices/{device_id}/revoke", response_model=DeviceOut)
def revoke(device_id: str, session: SessionDep, _: AdminDep) -> DeviceOut:
    device = revoke_device(session, device_id)
    session.commit()
    return DeviceOut.of(device)


@router.post(
    "/locations/{location_id}/staff", response_model=StaffOut, status_code=status.HTTP_201_CREATED
)
def add_staff(location_id: str, body: StaffIn, session: SessionDep, _: AdminDep) -> StaffOut:
    person = create_staff(
        session, location_id=location_id, name=body.name, role=body.role, pin=body.pin
    )
    session.commit()
    return StaffOut.model_validate(person)


@router.get("/locations/{location_id}/staff", response_model=list[StaffOut])
def read_staff(
    location_id: str, session: SessionDep, _: AdminDep, include_inactive: bool = False
) -> list[StaffOut]:
    people = list_staff(session, location_id, include_inactive=include_inactive)
    return [StaffOut.model_validate(p) for p in people]


@router.post("/staff/{staff_id}/pin", response_model=StaffOut)
def change_pin(staff_id: str, body: PinIn, session: SessionDep, _: AdminDep) -> StaffOut:
    person = set_pin(session, staff_id, body.pin)
    session.commit()
    return StaffOut.model_validate(person)


@router.post("/staff/{staff_id}/deactivate", response_model=StaffOut)
def deactivate(staff_id: str, session: SessionDep, _: AdminDep) -> StaffOut:
    person = deactivate_staff(session, staff_id)
    session.commit()
    return StaffOut.model_validate(person)
