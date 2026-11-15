from __future__ import annotations

from fastapi import FastAPI

from .api import menu_router, router

app = FastAPI(title="Void POS", version="0.1.0")
app.include_router(router)
app.include_router(menu_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
