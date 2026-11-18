from __future__ import annotations

from .raster import Raster, Rasterizer
from .ticket import KitchenTicket

ESC = b"\x1b"
GS = b"\x1d"
LF = b"\n"

INIT = ESC + b"@"
CUT = GS + b"V\x00"
ALIGN_LEFT = ESC + b"a\x00"
ALIGN_CENTER = ESC + b"a\x01"
BOLD_ON = ESC + b"E\x01"
BOLD_OFF = ESC + b"E\x00"
SIZE_DOUBLE = GS + b"!\x11"
SIZE_NORMAL = GS + b"!\x00"

RULE = b"-" * 32


def needs_image(text: str) -> bool:
    """A basic ESC/POS printer prints ASCII directly; anything else must be rasterized."""
    return not text.isascii()


def raster_image(raster: Raster) -> bytes:
    """Encode a raster as the ESC/POS ``GS v 0`` bit-image command."""
    xl = raster.bytes_per_row & 0xFF
    xh = (raster.bytes_per_row >> 8) & 0xFF
    yl = raster.height & 0xFF
    yh = (raster.height >> 8) & 0xFF
    return GS + b"v0" + bytes([0, xl, xh, yl, yh]) + raster.data


def _render_line(text: str, rasterizer: Rasterizer | None) -> bytes:
    if needs_image(text):
        if rasterizer is not None:
            return raster_image(rasterizer.render(text)) + LF
        # No rasterizer: best-effort UTF-8 passthrough (may misprint on a basic printer).
        return text.encode("utf-8") + LF
    return text.encode("ascii") + LF


def encode_ticket(ticket: KitchenTicket, rasterizer: Rasterizer | None = None) -> bytes:
    """Encode a kitchen ticket as ESC/POS bytes. Lines in a non-Latin script are sent as
    raster images (via ``rasterizer``) so basic printers can render them."""
    out = bytearray()
    out += INIT
    out += ALIGN_CENTER + BOLD_ON + SIZE_DOUBLE
    if ticket.void_reason is not None:
        out += f"VOID #{ticket.order_number}".encode("ascii") + LF
        out += SIZE_NORMAL + b"DO NOT MAKE" + LF
    else:
        out += f"{ticket.heading} #{ticket.order_number}".encode("ascii") + LF
    if ticket.table_label:
        out += SIZE_DOUBLE + _render_line(f"Table {ticket.table_label}", rasterizer)
    out += SIZE_NORMAL + BOLD_OFF + ALIGN_LEFT
    out += f"{ticket.channel} / {ticket.fulfillment}".encode("ascii") + LF
    out += ticket.business_date.isoformat().encode("ascii") + LF
    if ticket.void_reason:
        out += _render_line(f"Reason: {ticket.void_reason}", rasterizer)
    out += RULE + LF
    for line in ticket.lines:
        if line.is_modifier:
            label = line.text if line.quantity == 1 else f"{line.quantity}x {line.text}"
            out += b"  + " + _render_line(label, rasterizer)
        else:
            out += _render_line(f"{line.quantity}x {line.text}", rasterizer)
    out += LF * 3
    out += CUT
    return bytes(out)
