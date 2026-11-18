"""Map every failure onto one JSON error shape (see ``void_pos.errors``)."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from ..errors import VoidPosError

log = logging.getLogger("void_pos.api")


def _validation_message(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        parts.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
    return "; ".join(parts) or "invalid request"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(VoidPosError)
    async def _domain(_: Request, exc: VoidPosError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=jsonable_encoder(exc.to_body()),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        body = {
            "detail": _validation_message(exc),
            "code": "invalid_request",
            "errors": jsonable_encoder(exc.errors(), custom_encoder={Exception: str}),
        }
        return JSONResponse(status_code=422, content=body)

    @app.exception_handler(IntegrityError)
    async def _integrity(_: Request, exc: IntegrityError) -> JSONResponse:
        # A unique or foreign-key constraint caught a race the service checks missed.
        log.warning("integrity error: %s", exc.orig)
        body = {"detail": "the change conflicts with existing data", "code": "conflict"}
        return JSONResponse(status_code=409, content=body)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", exc_info=exc)
        body = {"detail": "internal error", "code": "internal_error"}
        return JSONResponse(status_code=500, content=body)
