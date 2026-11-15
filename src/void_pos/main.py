from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import menu_router, router

app = FastAPI(title="Void POS", version="0.1.0")

# The web POS runs in the browser and calls this API cross-origin, so allow the dev
# origin(s). Override with VOIDPOS_CORS_ORIGINS (comma-separated) in other environments.
_cors_origins = os.environ.get(
    "VOIDPOS_CORS_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(menu_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
