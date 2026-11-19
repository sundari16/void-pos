"""A receipt as ESC/POS bytes for a thermal receipt printer."""

from __future__ import annotations

import textwrap

from ..printing.escpos import (
    ALIGN_CENTER,
    ALIGN_LEFT,
    BOLD_OFF,
    BOLD_ON,
    CUT,
    INIT,
    LF,
    SIZE_DOUBLE,
    SIZE_NORMAL,
    _render_line,
    needs_image,
)
from ..printing.raster import Rasterizer
from .receipt import Receipt


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


def _row(left: str, right: str, columns: int, rasterizer: Rasterizer | None) -> bytes:
    """Text on the left, an amount flush right. A name in another script prints as an
    image on its own line, with the amount on the line after."""
    if needs_image(left):
        return _render_line(left, rasterizer) + _row("", right, columns, rasterizer)
    rows = textwrap.wrap(left, columns, subsequent_indent="   ") or [""]
    last = rows.pop()
    if len(last) + 1 + len(right) > columns:
        rows.append(last)
        last = ""
    rows.append(last + " " * (columns - len(last) - len(right)) + right)
    return b"".join(row.encode("ascii") + LF for row in rows)


def encode_receipt(
    receipt: Receipt, *, columns: int = 42, rasterizer: Rasterizer | None = None
) -> bytes:
    rule = b"-" * columns + LF
    out = bytearray(INIT)
    out += ALIGN_CENTER + BOLD_ON + _render_line(receipt.venue_name, rasterizer) + BOLD_OFF
    for text in (receipt.address, receipt.phone):
        if text:
            out += _render_line(text, rasterizer)
    if receipt.abn:
        out += f"ABN {receipt.abn}".encode("ascii") + LF
    out += LF + BOLD_ON + receipt.title.encode("ascii") + LF + BOLD_OFF
    if receipt.voided:
        out += SIZE_DOUBLE + b"VOID" + LF + SIZE_NORMAL
    out += ALIGN_LEFT
    heading = f"Order #{receipt.order_number}"
    if receipt.table_label:
        heading += f"  Table {receipt.table_label}"
    out += _render_line(heading, rasterizer)
    out += receipt.ordered_at.encode("ascii") + LF + rule
    for line in receipt.lines:
        if line.is_modifier:
            label = f"  + {line.name}" if line.quantity == 1 else f"  + {line.quantity}x {line.name}"
            amount = money(line.amount_cents) if line.amount_cents else ""
        else:
            label, amount = f"{line.quantity}x {line.name}", money(line.amount_cents)
        out += _row(label, amount, columns, rasterizer)
    out += rule
    if receipt.subtotal_cents is not None:
        out += _row("Subtotal", money(receipt.subtotal_cents), columns, None)
        for adj in receipt.adjustments:
            out += _row(adj.name, money(adj.amount_cents), columns, rasterizer)
    out += BOLD_ON + _row("TOTAL", money(receipt.total_cents), columns, None) + BOLD_OFF
    out += _row("Includes GST", money(receipt.gst_cents), columns, None)
    for payment in receipt.payments:
        out += _row(payment.label, money(payment.amount_cents), columns, None)
        if payment.reference:
            out += _render_line(f"  Ref {payment.reference}", rasterizer)
    for refund in receipt.refunds:
        out += _row(refund.label, money(refund.amount_cents), columns, None)
        if refund.reference:
            out += _render_line(f"  Ref {refund.reference}", rasterizer)
    if receipt.balance_cents and not receipt.voided:
        out += BOLD_ON + _row("Still owing", money(receipt.balance_cents), columns, None) + BOLD_OFF
    if receipt.footer:
        out += LF + ALIGN_CENTER + _render_line(receipt.footer, rasterizer) + ALIGN_LEFT
    out += LF + ALIGN_CENTER + f"Printed {receipt.printed_at}".encode("ascii") + LF
    out += LF * 3 + CUT
    return bytes(out)
