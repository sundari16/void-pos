"""Kitchen printing: build an ESC/POS ticket from an order and send it to a printer.

Non-Latin item names are rendered as raster images so basic ESC/POS printers can print
Tamil, Thai or Chinese tickets.
"""

from __future__ import annotations

from .escpos import encode_ticket, needs_image, raster_image
from .pillow_rasterizer import PillowRasterizer
from .raster import Raster, Rasterizer
from .service import print_order
from .ticket import KitchenTicket, TicketLine, build_kitchen_ticket
from .transport import NetworkPrinter, Printer, PrinterUnavailable

__all__ = [
    "KitchenTicket",
    "NetworkPrinter",
    "PillowRasterizer",
    "Printer",
    "PrinterUnavailable",
    "Raster",
    "Rasterizer",
    "TicketLine",
    "build_kitchen_ticket",
    "encode_ticket",
    "needs_image",
    "print_order",
    "raster_image",
]
