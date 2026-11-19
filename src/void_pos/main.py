from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from .api import (
    admin_router,
    board_router,
    device_router,
    install_error_handlers,
    menu_router,
    online_router,
    router,
)
from .api.deps import get_session
from .config import settings
from .errors import ServiceUnavailable

app = FastAPI(title="Void POS", version="0.2.0")
install_error_handlers(app)

# The web POS runs in the browser and calls this API cross-origin; allow its origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(device_router)
app.include_router(router)
app.include_router(menu_router)
app.include_router(admin_router)
app.include_router(board_router)
app.include_router(online_router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Liveness: the process is up."""
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def ready(session: Annotated[Session, Depends(get_session)]) -> dict[str, str]:
    """Readiness: the database answers. Docker's healthcheck polls this."""
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:
        raise ServiceUnavailable("the database isn't reachable", code="database_unavailable") from exc
    return {"status": "ok", "database": "ok"}
