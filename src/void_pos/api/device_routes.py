"""Till pairing and the till's view of itself."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..devices import get_location, pair_device
from .auth import DeviceDep
from .deps import get_session
from .schemas import DeviceSession, PairRequest, PairResponse

router = APIRouter(prefix="/devices", tags=["devices"])

SessionDep = Annotated[Session, Depends(get_session)]


@router.post("/pair", response_model=PairResponse)
def pair(body: PairRequest, session: SessionDep) -> PairResponse:
    """Redeem a one-time pairing code for this till's bearer token."""
    device, token = pair_device(session, body.pairing_code)
    session.commit()
    location = get_location(session, device.location_id)
    return PairResponse(**DeviceSession.of(device, location).model_dump(), token=token)


@router.get("/me", response_model=DeviceSession)
def me(session: SessionDep, device: DeviceDep) -> DeviceSession:
    """The till's identity, location, order-number range and last applied seq."""
    return DeviceSession.of(device, get_location(session, device.location_id))
