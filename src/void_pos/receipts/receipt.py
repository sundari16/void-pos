"""Customer receipts. With an ABN on file the receipt is an Australian tax invoice: the
seller's name and ABN, the date, what was sold with quantities and prices, the GST
included, and the words "Tax invoice". Without one it is a plain receipt."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ..clock import as_aware, utcnow
from ..devices import get_location
from ..models import OrderLine
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
    subtotal_cents: int | None = None  # shown when discounts or surcharges apply
    adjustments: list[ReceiptLine] = field(default_factory=list)  # name and signed amount
    refunds: list[ReceiptPayment] = field(default_factory=list)
    voided: bool = False
    footer: str | None = None


def _local(at: datetime, timezone: str) -> str:
    return as_aware(at).astimezone(ZoneInfo(timezone)).strftime("%d/%m/%Y %H:%M")


def _adjustment_name(kind: str, basis: str, value: int) -> str:
    what = "Discount" if kind == "discount" else "Surcharge"
    if basis == "percent":
        pct = f"{value / 100:.2f}".rstrip("0").rstrip(".")
        return f"{what} {pct}%"
    return what


def build_receipt(session: Session, view: OrderView) -> Receipt:
    order = view.order
    location = get_location(session, order.location_id)
    qty = extended_quantities(view.active_lines)
    children: dict[str, list[OrderLine]] = defaultdict(list)
    active = view.active_lines
    for line in active:
        if line.parent_line_id is not None:
            children[line.parent_line_id].append(line)
    ordered = [
        line
        for item in active
        if item.parent_line_id is None
        for line in (item, *children[item.id])
    ]
    lines = [
        ReceiptLine(
            quantity=line.quantity,
            name=line.name_snapshot,
            amount_cents=line.unit_price_cents * qty[line.id],
            is_modifier=line.parent_line_id is not None,
        )
        for line in ordered
    ]
    payments: list[ReceiptPayment] = []
    for p in view.payments:
        if p.status != "captured":
            continue
        label = PROVIDER_LABELS.get(p.provider, p.provider)
        payments.append(ReceiptPayment(label, p.amount_cents, p.external_ref))
        if p.tip_cents:
            payments.append(ReceiptPayment(f"Tip ({label})", p.tip_cents, None))
    adjustments = [
        ReceiptLine(
            quantity=1,
            name=a.label or _adjustment_name(a.kind, a.basis, a.value),
            amount_cents=view.adjustment_cents.get(a.id, 0),
            is_modifier=False,
        )
        for a in view.adjustments
    ]
    refunds = [
        ReceiptPayment(
            f"Refund ({PROVIDER_LABELS.get(r.provider, r.provider)})", -r.amount_cents,
            r.external_ref,
        )
        for r in view.refunds
    ]
    return Receipt(
        title="TAX INVOICE" if location.abn else "RECEIPT",
        venue_name=location.business_name or location.name,
        address=location.address,
        phone=location.phone,
        abn=format_abn(location.abn) if location.abn else None,
        order_number=order.order_number,
        table_label=order.table_label,
        ordered_at=_local(order.placed_at or order.created_at, location.timezone),
        printed_at=_local(utcnow(), location.timezone),
        lines=lines,
        total_cents=view.total_cents,
        gst_cents=view.gst_cents,
        payments=payments,
        balance_cents=view.balance_cents,
        subtotal_cents=view.subtotal_cents if adjustments else None,
        adjustments=adjustments,
        refunds=refunds,
        voided=order.state == "voided",
        footer=location.receipt_footer,
    )
