"""Customer receipts. With an ABN on file the receipt is an Australian tax invoice: the
seller's name and ABN, the date, what was sold with quantities and prices, the GST
included, and the words "Tax invoice". Without one it is a plain receipt."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ..clock import as_aware, utcnow
from ..devices import get_location
from ..orders.views import OrderView
from ..totals import extended_quantities
from .venue import format_abn

PROVIDER_LABELS = {"cash": "Cash", "external": "Card on terminal", "stripe": "Card"}


@dataclass(frozen=True)
class ReceiptLine:
    quantity: int
    name: str
    amount_cents: int  # the line's extended amount
    is_modifier: bool


@dataclass(frozen=True)
class ReceiptPayment:
    label: str
    amount_cents: int
    reference: str | None


@dataclass(frozen=True)
class Receipt:
    title: str  # TAX INVOICE or RECEIPT
    venue_name: str
    address: str | None
    phone: str | None
    abn: str | None  # formatted, e.g. "51 824 753 556"
    order_number: int
    table_label: str | None
    ordered_at: str  # local, e.g. "18/11/2026 19:45"
    printed_at: str
    lines: list[ReceiptLine]
    total_cents: int
    gst_cents: int
    payments: list[ReceiptPayment] = field(default_factory=list)
    balance_cents: int = 0
    voided: bool = False
    footer: str | None = None


def _local(at: datetime, timezone: str) -> str:
    return as_aware(at).astimezone(ZoneInfo(timezone)).strftime("%d/%m/%Y %H:%M")


def build_receipt(session: Session, view: OrderView) -> Receipt:
    order = view.order
    location = get_location(session, order.location_id)
    qty = extended_quantities(view.lines)
    lines = [
        ReceiptLine(
            quantity=line.quantity,
            name=line.name_snapshot,
            amount_cents=line.unit_price_cents * qty[line.id],
            is_modifier=line.parent_line_id is not None,
        )
        for line in view.lines
    ]
    payments = [
        ReceiptPayment(
            label=PROVIDER_LABELS.get(p.provider, p.provider),
            amount_cents=p.amount_cents,
            reference=p.external_ref,
        )
        for p in view.payments
        if p.status == "captured"
    ]
    return Receipt(
        title="TAX INVOICE" if location.abn else "RECEIPT",
        venue_name=location.business_name or location.name,
        address=location.address,
        phone=location.phone,
        abn=format_abn(location.abn) if location.abn else None,
        order_number=order.order_number,
        table_label=order.table_label,
        ordered_at=_local(order.created_at, location.timezone),
        printed_at=_local(utcnow(), location.timezone),
        lines=lines,
        total_cents=view.total_cents,
        gst_cents=view.gst_cents,
        payments=payments,
        balance_cents=view.balance_cents,
        voided=order.state == "voided",
        footer=location.receipt_footer,
    )
