"""Who is calling: the venue admin, or one paired till.

Every request but ``/health`` and ``/devices/pair`` sends ``Authorization: Bearer <token>``.
The admin token comes from ``VOIDPOS_ADMIN_TOKEN``; a till's token comes from pairing. A
till only ever sees its own location: its location, device id and order numbering come
from its token, never from the request body.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..devices import authenticate_device
from ..errors import Forbidden, Unauthorized
from ..models import Device
from .deps import get_session

_bearer = HTTPBearer(auto_error=False, description="A till token from pairing, or the admin token")


@dataclass(frozen=True)
class Principal:
    device: Device | None  # None means the venue admin

    @property
    def is_admin(self) -> bool:
        return self.device is None

    def require_location(self, location_id: str) -> None:
        """Admins see every location; a till sees only its own."""
        if self.device is not None and self.device.location_id != location_id:
            raise Forbidden("this till belongs to another location", code="wrong_location")

    @property
    def location_id(self) -> str | None:
        return self.device.location_id if self.device is not None else None


def get_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal:
    if credentials is None or not credentials.credentials:
        raise Unauthorized("send Authorization: Bearer <token>", code="missing_token")
    token = credentials.credentials
    admin = settings.admin_token
    if admin is not None and hmac.compare_digest(
        token.encode("utf-8"), admin.get_secret_value().encode("utf-8")
    ):
        return Principal(device=None)
    device = authenticate_device(session, token)
    if device is None:
        raise Unauthorized(
            "that token isn't valid; the till may need pairing again", code="invalid_token"
        )
    return Principal(device=device)


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def require_device(principal: PrincipalDep) -> Device:
    if principal.device is None:
        raise Forbidden("this action needs a paired till, not the admin token", code="till_only")
    return principal.device


def require_admin(
    principal: PrincipalDep, settings: Annotated[Settings, Depends(get_settings)]
) -> Principal:
    if settings.admin_token is None:
        raise Forbidden(
            "admin access is off: set VOIDPOS_ADMIN_TOKEN on the server", code="admin_disabled"
        )
    if not principal.is_admin:
        raise Forbidden("this action needs the admin token", code="admin_only")
    return principal


DeviceDep = Annotated[Device, Depends(require_device)]
AdminDep = Annotated[Principal, Depends(require_admin)]
