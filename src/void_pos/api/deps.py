from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_sessionmaker
from ..printing import NetworkPrinter, PillowRasterizer, Printer, Rasterizer


def get_session() -> Iterator[Session]:
    """One session per request. Routes commit explicitly; anything uncommitted when the
    request ends (an error, a printer that didn't answer) is rolled back on close."""
    with get_sessionmaker()() as session:
        yield session


def get_printer(settings: Annotated[Settings, Depends(get_settings)]) -> Printer | None:
    """The kitchen printer, or None when none is configured (orders still project)."""
    if settings.kitchen_printer_host:
        return NetworkPrinter(settings.kitchen_printer_host, settings.kitchen_printer_port)
    return None


def get_rasterizer(settings: Annotated[Settings, Depends(get_settings)]) -> Rasterizer | None:
    if settings.kitchen_font_path:
        return PillowRasterizer(settings.kitchen_font_path)
    return None


def get_receipt_printer(settings: Annotated[Settings, Depends(get_settings)]) -> Printer | None:
    """The customer receipt printer, or None when none is configured."""
    if settings.receipt_printer_host:
        return NetworkPrinter(settings.receipt_printer_host, settings.receipt_printer_port)
    return None
