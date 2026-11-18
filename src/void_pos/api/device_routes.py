"""Till pairing and the till's view of itself."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..devices import get_location, next_order_number, pair_device
from ..errors import Unauthorized
from .auth import DeviceDep
from .deps import get_session
from .ratelimit import AttemptLimiter, client_address, get_pairing_limiter
from .schemas import DeviceSession, PairRequest, PairResponse

router = APIRouter(prefix="/devices", tags=["devices"])

SessionDep = Annotated[Session, Depends(get_session)]
LimiterDep = Annotated[AttemptLimiter, Depends(get_pairing_limiter)]


@router.post("/pair", response_model=PairResponse)
def pair(
    body: PairRequest, session: SessionDep, request: Request, limiter: LimiterDep
) -> PairResponse:
    """Redeem a one-time pairing code for this till's bearer token. Repeated wrong codes
    get 429 ``too_many_attempts`` with a Retry-After (see ``ratelimit``)."""
    client = client_address(request)
    limiter.check(client)
    try:
        device, token = pair_device(session, body.pairing_code)
    except Unauthorized as exc:
        if exc.code == "invalid_pairing_code":
            limiter.failed(client)
        raise
    session.commit()
    till = DeviceSession.of(
        device,
        get_location(session, device.location_id),
        next_order_number=next_order_number(session, device),
    )
    return PairResponse(**till.model_dump(), token=token)


@router.get("/me", response_model=DeviceSession)
def me(session: SessionDep, device: DeviceDep) -> DeviceSession:
    """The till's identity, location, order-number range and last applied seq."""
    return DeviceSession.of(
        device,
        get_location(session, device.location_id),
        next_order_number=next_order_number(session, device),
    )
