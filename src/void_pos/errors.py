"""Domain errors.

Services raise these; the API turns every one into the same JSON body::

    {"detail": "<human-readable message>", "code": "<stable machine code>", ...extra}

``detail`` is for people, ``code`` is for clients to branch on, and ``extra`` carries the
facts a client needs to recover (for example ``last_seq`` on a sequence collision).
"""

from __future__ import annotations

from typing import Any


class VoidPosError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.extra = extra

    def to_body(self) -> dict[str, Any]:
        return {"detail": self.message, "code": self.code, **self.extra}


class InvalidRequest(VoidPosError):
    """The request is well-formed JSON but breaks a business rule."""

    status_code = 422
    code = "invalid_request"


class Unauthorized(VoidPosError):
    status_code = 401
    code = "unauthorized"


class Forbidden(VoidPosError):
    status_code = 403
    code = "forbidden"


class NotFound(VoidPosError, LookupError):
    status_code = 404
    code = "not_found"


class Conflict(VoidPosError):
    """The request is valid but clashes with the current state."""

    status_code = 409
    code = "conflict"


class PaymentFailed(VoidPosError):
    status_code = 402
    code = "payment_failed"


class ServiceUnavailable(VoidPosError):
    """A dependency outside the database (a printer, a provider) can't be reached."""

    status_code = 503
    code = "unavailable"
