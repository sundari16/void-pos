from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_sessionmaker
from ..printing import NetworkPrinter, PillowRasterizer, Printer, Rasterizer


def get_session() -> Iterator[Session]:
    with get_sessionmaker()() as session:
        yield session


class NullPrinter:
    """Default printer: discards bytes until a kitchen printer is configured."""

    def send(self, data: bytes) -> None:
        return None


def get_printer() -> Printer:
    if settings.kitchen_printer_host:
        return NetworkPrinter(settings.kitchen_printer_host, settings.kitchen_printer_port)
    return NullPrinter()


def get_rasterizer() -> Rasterizer | None:
    if settings.kitchen_font_path:
        return PillowRasterizer(settings.kitchen_font_path)
    return None
