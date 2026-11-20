"""Connect a venue's Xero org and push a day's takings to it.

Reads only the reporting projections (``shift_closeout``) and the stored ``XeroConnection``;
never touches the event model. The push is idempotent: it finds the one invoice for the
(location, business date) reference, clears its payments, updates its lines, and re-applies
the payments — so a re-run converges to the same state and never double-counts.
"""

from __future__ import annotations

import secrets
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clock import utcnow
from ..config import Settings
from ..devices import get_location
from ..errors import InvalidRequest, VoidPosError
from ..models import XeroConnection
from ..reporting import shift_closeout
from .mapping import AccountMap
from .summary import DailyTakings, build_daily_takings
from .xero import XeroClient, authorize_url


class XeroNotConnected(VoidPosError):
    """No Xero organisation has been connected for this location yet."""

    status_code = 400
    code = "xero_not_connected"


def new_state() -> str:
    return secrets.token_urlsafe(24)


def build_authorize_url(settings: Settings, *, state: str) -> str:
    """The Xero sign-in URL for the venue, from the app's client id and redirect in .env."""
    if settings.xero_client_id is None:
        raise InvalidRequest("set VOIDPOS_XERO_CLIENT_ID in .env first", code="xero_not_set_up")
    return authorize_url(
        client_id=settings.xero_client_id.get_secret_value(),
        redirect_uri=settings.xero_redirect_uri,
        scopes=settings.xero_scopes,
        state=state,
    )


def complete_connection(
    session: Session, location_id: str, *, client: XeroClient, code: str
) -> XeroConnection:
    """Finish OAuth: swap the code for tokens, pick the org, store the connection."""
    get_location(session, location_id)
    auth = client.exchange_code(code)
    client.access_token = auth.access_token
    orgs = client.connections()
    if not orgs:
        raise InvalidRequest(
            "no Xero organisation was authorised; try connecting again", code="xero_no_org"
        )
    org = orgs[0]
    conn = load_connection(session, location_id, required=False)
    if conn is None:
        conn = XeroConnection(location_id=location_id)
        session.add(conn)
    conn.tenant_id = str(org["tenantId"])
    conn.tenant_name = org.get("tenantName")
    conn.refresh_token = auth.refresh_token
    conn.authorised_at = utcnow()
    session.flush()
    return conn


def load_connection(
    session: Session, location_id: str, *, required: bool = True
) -> XeroConnection | None:
    conn = session.scalars(
        select(XeroConnection).where(XeroConnection.location_id == location_id)
    ).first()
    if conn is None and required:
        raise XeroNotConnected(
            "this location isn't connected to Xero; run void-pos xero-connect first"
        )
    return conn


def set_account_map(
    session: Session, location_id: str, data: dict[str, Any]
) -> XeroConnection:
    """Store a per-venue account-code mapping (validated against the known keys)."""
    conn = load_connection(session, location_id)
    assert conn is not None
    AccountMap.from_dict(data)  # validates keys, raises on an unknown one
    conn.account_map = data
    session.flush()
    return conn


def account_map_for(session: Session, location_id: str) -> AccountMap:
    conn = load_connection(session, location_id, required=False)
    return AccountMap.from_dict(conn.account_map if conn else None)


def build_takings(session: Session, location_id: str, business_date: date) -> DailyTakings:
    """The daily-takings invoice + payments for a location and date, from reporting data and
    the location's account map (code defaults if it has none). Reads only; makes no Xero call,
    so ``--dry-run`` works offline."""
    closeout = shift_closeout(session, location_id, business_date)
    return build_daily_takings(closeout, account_map_for(session, location_id))


def push_daily_takings(
    session: Session, location_id: str, business_date: date, *, client: XeroClient
) -> dict[str, Any]:
    """Push the day's takings to Xero and return a result summary. Idempotent per
    (location, business date)."""
    conn = load_connection(session, location_id)
    assert conn is not None
    takings = build_daily_takings(shift_closeout(session, location_id, business_date), account_map_for(session, location_id))
    if not takings.lines:
        raise InvalidRequest(
            f"no takings to push for {takings.business_date}", code="xero_nothing_to_push"
        )

    # Refresh for a fresh access token. Xero rotates the refresh token on every refresh, so
    # persist the new one at once — before any call that could fail — or the connection is
    # lost on the next run.
    client.tenant_id = conn.tenant_id
    auth = client.refresh(conn.refresh_token)
    conn.refresh_token = auth.refresh_token
    session.commit()

    payload = takings.invoice_payload()
    payload["Contact"] = {"ContactID": client.get_or_create_contact(takings.contact_name)}

    existing = client.find_invoice(takings.reference)
    created = not existing
    if existing:
        if existing.get("Status") == "PAID":
            # Clear payments so the invoice is editable again, then re-apply fresh. This is
            # what keeps a re-push from double-counting.
            for payment in existing.get("Payments") or []:
                if payment.get("PaymentID"):
                    client.delete_payment(str(payment["PaymentID"]))
        payload["InvoiceID"] = existing["InvoiceID"]

    invoice = client.upsert_invoice(payload)
    invoice_id = str(invoice["InvoiceID"])

    paid: list[dict[str, Any]] = []
    for request in takings.payments:
        payment = client.create_payment(
            invoice_id=invoice_id,
            account_code=request.account_code,
            amount_cents=request.amount_cents,
            date=takings.business_date,
            reference=takings.reference,
        )
        paid.append(
            {
                "provider": request.provider,
                "account_code": request.account_code,
                "amount_cents": request.amount_cents,
                "payment_id": payment.get("PaymentID"),
            }
        )

    return {
        "created": created,
        "reference": takings.reference,
        "invoice_id": invoice_id,
        "invoice_number": invoice.get("InvoiceNumber", takings.reference),
        "total_cents": takings.total_cents,
        "tax_cents": takings.tax_cents,
        "payments": paid,
        "issues": takings.issues,
    }
