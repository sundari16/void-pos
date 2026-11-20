"""Turn a shift closeout into a Xero daily-takings invoice and its payments.

Pure functions: the reporting closeout dict in, a :class:`DailyTakings` out, and from that
the Xero invoice body and the per-provider payment requests. No I/O, so the maths is unit
tested on its own and the CLI's ``--dry-run`` prints exactly what a real push would send.

Shape of the Xero document (see ``accounting`` package doc and docs/self-host.md):

- one ACCREC invoice, ``LineAmountTypes = Exclusive``, dated the business date;
- a line per revenue class (ex-GST) with the mapped sales account, the GST tax type and an
  explicit ``TaxAmount``: the day's **actual** GST (``gst_cents``) apportioned across the
  taxable classes by their share of sales, so the invoice's GST equals the venue's books —
  we don't re-derive it from a rate, and classes the venue marks GST-free carry none;
- No-Tax lines for discounts, surcharges and tips (their GST effect is already inside the
  apportioned sales GST), and a refunds line that reverses sales and their exact GST;
- a No-Tax **rounding** line that squares the invoice total to the money actually tendered
  (nil on a fully settled day; non-nil only with unsettled orders or an apportionment cent);
- one payment per provider to its clearing/bank account: takings + tips − refunds.

Net result: the invoice total equals the day's tendered money exactly and its GST equals
``net_gst_cents`` (``gst_cents`` − ``refund_gst_cents``). All money is integer cents until
the Xero payload, which Xero wants as decimal dollars.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .mapping import AccountMap

REFERENCE_PREFIX = "VPOS"


def reference_for(location_id: str, business_date: str) -> str:
    """The stable invoice number / reference for a (location, business date). Re-pushing the
    same day finds and updates this one Xero invoice, so a day is never double-counted."""
    return f"{REFERENCE_PREFIX}-{location_id}-{business_date.replace('-', '')}"


def _apportion(total: int, weights: list[int]) -> list[int]:
    """Split ``total`` across ``weights`` by share, largest-remainder, so the parts sum to
    ``total`` exactly. ``weights`` must be non-negative with a positive sum."""
    base = sum(weights)
    raw = [(total * w) // base for w in weights]
    shortfall = total - sum(raw)
    # Hand the leftover cents to the largest weights first (deterministic, stable order).
    order = sorted(range(len(weights)), key=lambda i: (-weights[i], i))
    for i in order[:shortfall]:
        raw[i] += 1
    return raw


def _dollars(cents: int) -> str:
    """Integer cents as a decimal-dollar string for the Xero payload."""
    return str((Decimal(cents) / 100).quantize(Decimal("0.01")))


@dataclass
class TakingsLine:
    description: str
    account_code: str
    amount_cents: int  # ex-GST (signed)
    tax_type: str | None  # None = No Tax
    tax_cents: int = 0  # explicit GST for this line (signed)

    def to_xero(self) -> dict[str, Any]:
        line: dict[str, Any] = {
            "Description": self.description,
            "Quantity": "1",
            "UnitAmount": _dollars(self.amount_cents),
            "AccountCode": self.account_code,
            "TaxType": self.tax_type or "NONE",
        }
        if self.tax_type is not None:
            line["TaxAmount"] = _dollars(self.tax_cents)
        return line


@dataclass
class PaymentRequest:
    provider: str
    account_code: str
    amount_cents: int  # > 0; takings + tips − refunds for the provider


@dataclass
class DailyTakings:
    location_id: str
    business_date: str  # YYYY-MM-DD
    reference: str
    contact_name: str
    lines: list[TakingsLine]
    payments: list[PaymentRequest]
    sub_total_cents: int  # Σ line ex-GST
    tax_cents: int  # Σ line GST
    total_cents: int  # money tendered; invoice total
    issues: list[str] = field(default_factory=list)

    def invoice_payload(self) -> dict[str, Any]:
        """The Xero ACCREC invoice body (without payments)."""
        return {
            "Type": "ACCREC",
            "Contact": {"Name": self.contact_name},
            "Date": self.business_date,
            "DueDate": self.business_date,
            "InvoiceNumber": self.reference,
            "Reference": self.reference,
            "Status": "AUTHORISED",
            "LineAmountTypes": "Exclusive",
            "LineItems": [line.to_xero() for line in self.lines],
        }

    def summary_lines(self) -> list[str]:
        """Human-readable summary for the CLI (``--dry-run`` and the push report)."""
        out = [
            f"Daily takings for {self.location_id} on {self.business_date}",
            f"  Reference: {self.reference}   Contact: {self.contact_name}",
            "  Lines (ex-GST):",
        ]
        for line in self.lines:
            tax = "" if line.tax_type is None else f"  GST {_dollars(line.tax_cents)} [{line.tax_type}]"
            out.append(
                f"    {line.description:<24} {_dollars(line.amount_cents):>10}  "
                f"acct {line.account_code}{tax}"
            )
        out += [
            f"  Sub-total (ex-GST): {_dollars(self.sub_total_cents)}",
            f"  GST:                {_dollars(self.tax_cents)}",
            f"  Invoice total:      {_dollars(self.total_cents)}",
            "  Payments:",
        ]
        for pay in self.payments:
            out.append(
                f"    {pay.provider:<24} {_dollars(pay.amount_cents):>10}  acct {pay.account_code}"
            )
        out += [f"  ! {note}" for note in self.issues]
        return out


def build_daily_takings(closeout: dict[str, Any], account_map: AccountMap) -> DailyTakings:
    """Build the invoice + payments from a ``shift_closeout`` dict. Pure; no Xero calls."""
    location_id = str(closeout["location_id"])
    business_date = str(closeout["business_date"])
    issues: list[str] = []

    lines: list[TakingsLine] = _sales_lines(closeout, account_map)

    # Discounts, surcharges and tips carry no GST of their own here: discount/surcharge GST is
    # already inside the sales GST (computed on order totals), and tips are GST-free.
    discounts = int(closeout.get("discounts_cents", 0))
    if discounts:
        lines.append(TakingsLine("Discounts", account_map.discounts_account, -discounts, None, 0))
    surcharges = int(closeout.get("surcharges_cents", 0))
    if surcharges:
        lines.append(TakingsLine("Surcharges", account_map.surcharges_account, surcharges, None, 0))
    net_tips = int(closeout.get("net_tips_cents", 0))
    if net_tips:
        lines.append(TakingsLine("Tips", account_map.tips_account, net_tips, None, 0))

    # Refunds reverse sales and their exact GST (from reporting), so invoice GST ends at net GST.
    refund_sales = int(closeout.get("refund_sales_cents", 0))
    if refund_sales:
        refund_gst = int(closeout.get("refund_gst_cents", 0))
        lines.append(
            TakingsLine(
                "Refunds",
                account_map.sales_account,
                -(refund_sales - refund_gst),
                account_map.tax_type,
                -refund_gst,
            )
        )

    # Money actually tendered, by provider: takings + tips − refunds.
    payments = _payment_requests(closeout, account_map, issues)
    total = sum(pay.amount_cents for pay in payments)

    sub_total = sum(line.amount_cents for line in lines)
    tax = sum(line.tax_cents for line in lines)
    rounding = total - sub_total - tax
    if rounding:
        lines.append(TakingsLine("Rounding", account_map.rounding_account, rounding, None, 0))
        sub_total += rounding

    if int(closeout.get("open_balance_cents", 0)):
        issues.append(
            "the day has unsettled orders (open balance "
            f"{_dollars(int(closeout['open_balance_cents']))}); they aren't in this invoice, and "
            "any part-payment on them lands on the rounding line — settle them before pushing"
        )

    return DailyTakings(
        location_id=location_id,
        business_date=business_date,
        reference=reference_for(location_id, business_date),
        contact_name=account_map.contact_name,
        lines=lines,
        payments=payments,
        sub_total_cents=sub_total,
        tax_cents=tax,
        total_cents=total,
        issues=issues,
    )


def _sales_lines(closeout: dict[str, Any], account_map: AccountMap) -> list[TakingsLine]:
    """One ex-GST line per revenue class, largest first. The day's actual GST
    (``gst_cents``) is apportioned across the taxable classes by their share of sales, so the
    sales GST on the invoice equals the venue's books; GST-free classes carry none."""
    by_class: dict[str, int] = closeout.get("revenue_by_class_cents", {})
    classes = sorted(
        ((c, g) for c, g in by_class.items() if g > 0), key=lambda kv: (-kv[1], kv[0])
    )
    taxable = [(c, g) for c, g in classes if c not in account_map.gst_free_classes]
    taxable_total = sum(g for _, g in taxable)
    gst_pool = int(closeout.get("gst_cents", 0))
    shares = (
        _apportion(gst_pool, [g for _, g in taxable])
        if taxable_total > 0 and gst_pool
        else [0] * len(taxable)
    )
    class_gst = dict(zip((c for c, _ in taxable), shares, strict=True))

    lines: list[TakingsLine] = []
    for revenue_class, gross in classes:
        gst = class_gst.get(revenue_class, 0)
        lines.append(
            TakingsLine(
                description=f"Sales — {revenue_class}",
                account_code=account_map.account_for_class(revenue_class),
                amount_cents=gross - gst,
                tax_type=account_map.tax_type_for_class(revenue_class),
                tax_cents=gst,
            )
        )
    return lines


def _payment_requests(
    closeout: dict[str, Any], account_map: AccountMap, issues: list[str]
) -> list[PaymentRequest]:
    payments_by_provider: dict[str, int] = closeout.get("payments_by_provider", {})
    tips_by_provider: dict[str, int] = closeout.get("tips_by_provider", {})
    refunds_by_provider: dict[str, int] = closeout.get("refunds_by_provider", {})
    providers = sorted(set(payments_by_provider) | set(tips_by_provider) | set(refunds_by_provider))
    requests: list[PaymentRequest] = []
    for provider in providers:
        net = (
            payments_by_provider.get(provider, 0)
            + tips_by_provider.get(provider, 0)
            - refunds_by_provider.get(provider, 0)
        )
        if net == 0:
            continue
        if net < 0:
            issues.append(
                f"{provider}: refunds exceed takings ({_dollars(net)}); can't make a negative "
                "Xero payment — record this tender's credit/overpayment in Xero by hand"
            )
            continue
        requests.append(
            PaymentRequest(provider, account_map.clearing_for(provider), net)
        )
    return requests
