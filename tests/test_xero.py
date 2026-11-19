from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from support import make_engine
from void_pos import cli
from void_pos.accounting import (
    AccountMap,
    XeroClient,
    XeroNotConnected,
    build_daily_takings,
    complete_connection,
    push_daily_takings,
)
from void_pos.accounting.summary import reference_for
from void_pos.clock import utcnow
from void_pos.devices import create_location
from void_pos.errors import InvalidRequest
from void_pos.events import ingest_event
from void_pos.ids import new_ulid
from void_pos.models import Base, Device, XeroConnection
from void_pos.reporting import shift_closeout

TODAY = date(2026, 11, 15)
T0 = datetime(2026, 11, 15, 10, 0, tzinfo=UTC)


# --- the day's summary (pure) --------------------------------------------------------

def _closeout(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "location_id": "LOC",
        "business_date": "2026-11-15",
        "gross_sales_cents": 5060,
        "paid_sales_cents": 5060,
        "gst_cents": 460,
        "sales_ex_gst_cents": 4600,
        "discounts_cents": 0,
        "surcharges_cents": 0,
        "open_balance_cents": 0,
        "tips_cents": 0,
        "tips_by_provider": {},
        "refunds_cents": 0,
        "refunds_by_provider": {},
        "refund_sales_cents": 0,
        "refund_tips_cents": 0,
        "refund_gst_cents": 0,
        "net_tips_cents": 0,
        "net_sales_cents": 5060,
        "net_gst_cents": 460,
        "payments_by_provider": {"cash": 4400, "external": 660},
        "revenue_by_class_cents": {"Mains": 4400, "Drinks": 660},
    }
    base.update(over)
    return base


def test_summary_apportions_actual_gst_and_balances_to_the_tender() -> None:
    takings = build_daily_takings(_closeout(), AccountMap())

    sales = {line.description: line for line in takings.lines}
    assert (sales["Sales — Mains"].amount_cents, sales["Sales — Mains"].tax_cents) == (4000, 400)
    assert (sales["Sales — Drinks"].amount_cents, sales["Sales — Drinks"].tax_cents) == (600, 60)
    assert all(line.account_code == "200" for line in takings.lines)
    # Invoice GST == the books' GST; sub-total + GST == tendered; no rounding needed.
    assert (takings.sub_total_cents, takings.tax_cents, takings.total_cents) == (4600, 460, 5060)
    assert "Rounding" not in sales
    assert [(p.provider, p.account_code, p.amount_cents) for p in takings.payments] == [
        ("cash", "090", 4400),
        ("external", "090", 660),
    ]
    assert takings.reference == reference_for("LOC", "2026-11-15") == "VPOS-LOC-20261115"


def test_summary_handles_tips_refunds_discounts_and_gst_free_classes() -> None:
    closeout = _closeout(
        gst_cents=460,
        discounts_cents=200,
        surcharges_cents=150,
        tips_cents=500,
        net_tips_cents=400,
        refund_sales_cents=330,
        refund_gst_cents=30,
        net_gst_cents=430,
        payments_by_provider={"cash": 4400, "stripe": 660},
        tips_by_provider={"stripe": 500},
        refunds_by_provider={"stripe": 430},  # 330 sale + 100 tip
        refund_tips_cents=100,
        revenue_by_class_cents={"Mains": 4400, "Groceries": 660},
    )
    account_map = AccountMap(gst_free_classes=["Groceries"], clearing_accounts={"cash": "091", "stripe": "092"})
    takings = build_daily_takings(closeout, account_map)
    lines = {line.description: line for line in takings.lines}

    # All GST lands on the taxable class; the GST-free class carries none.
    assert (lines["Sales — Mains"].tax_cents, lines["Sales — Mains"].tax_type) == (460, "OUTPUT")
    assert (lines["Sales — Groceries"].tax_cents, lines["Sales — Groceries"].tax_type) == (
        0,
        "EXEMPTOUTPUT",
    )
    # Adjustments and tips are No-Tax; refunds reverse sale + its exact GST.
    assert (lines["Discounts"].amount_cents, lines["Discounts"].tax_type) == (-200, None)
    assert (lines["Surcharges"].amount_cents, lines["Surcharges"].tax_type) == (150, None)
    assert (lines["Tips"].amount_cents, lines["Tips"].tax_type) == (400, None)
    assert (lines["Refunds"].amount_cents, lines["Refunds"].tax_cents) == (-300, -30)
    # GST ends at net GST; total matches the money banked per provider.
    assert takings.tax_cents == 430
    tendered = {p.provider: p.amount_cents for p in takings.payments}
    assert tendered == {"cash": 4400, "stripe": 660 + 500 - 430}
    assert tendered["stripe"] == 730
    assert takings.total_cents == 4400 + 730
    assert [p.account_code for p in takings.payments] == ["091", "092"]
    assert takings.total_cents == takings.sub_total_cents + takings.tax_cents


def test_summary_rounding_absorbs_an_open_balance() -> None:
    # A part-paid order: more money was taken than the settled sales recognise.
    closeout = _closeout(
        open_balance_cents=450,
        payments_by_provider={"cash": 4400, "external": 660, "stripe": 200},
    )
    takings = build_daily_takings(closeout, AccountMap())
    rounding = next(line for line in takings.lines if line.description == "Rounding")
    assert rounding.amount_cents == 200  # the extra tender, squared onto the invoice
    assert rounding.tax_type is None
    assert takings.total_cents == 4400 + 660 + 200
    assert any("unsettled orders" in note for note in takings.issues)


def test_summary_flags_a_provider_whose_refunds_exceed_takings() -> None:
    closeout = _closeout(
        payments_by_provider={"cash": 4400, "stripe": 100},
        refunds_by_provider={"stripe": 300},
        refunds_cents=300,
        refund_sales_cents=300,
        refund_gst_cents=27,
    )
    takings = build_daily_takings(closeout, AccountMap())
    assert [p.provider for p in takings.payments] == ["cash"]  # stripe skipped (net negative)
    assert any("refunds exceed takings" in note for note in takings.issues)


# --- against real reporting data -----------------------------------------------------

def _seed_settled_day(session: Session) -> str:
    loc = create_location(session, org_name="Madras Curry House", name="Brisbane CBD").id
    device = Device(
        location_id=loc, name="Till 1", order_number_start=1000, order_number_end=1999
    )
    session.add(device)
    session.flush()

    def ev(seq: int, type_: str, payload: dict[str, Any]) -> None:
        ingest_event(
            session,
            {
                "event_id": new_ulid(),
                "device_id": device.id,
                "seq": seq,
                "location_id": loc,
                "business_date": TODAY,
                "occurred_at": T0 + timedelta(seconds=seq),
                "type": type_,
                "payload": payload,
            },
        )

    a, b = new_ulid(), new_ulid()
    ev(1, "order.placed", {
        "order_id": a, "order_number": 1000, "channel": "pos", "fulfillment": "dine_in",
        "lines": [{"id": new_ulid(), "quantity": 2, "name_snapshot": "Thali",
                   "unit_price_cents": 2200, "category_snapshot": "Mains", "tax_rate_snapshot": 0.1}]})
    ev(2, "order.paid", {"order_id": a, "payment": {
        "id": new_ulid(), "provider": "cash", "amount_cents": 4400}})
    ev(3, "order.placed", {
        "order_id": b, "order_number": 1001, "channel": "web", "fulfillment": "pickup",
        "lines": [{"id": new_ulid(), "quantity": 1, "name_snapshot": "Lassi",
                   "unit_price_cents": 660, "category_snapshot": "Drinks", "tax_rate_snapshot": 0.1}]})
    ev(4, "order.paid", {"order_id": b, "payment": {
        "id": new_ulid(), "provider": "external", "external_ref": "term-1", "amount_cents": 660}})
    session.commit()
    return loc


def test_build_takings_reconciles_against_the_closeout() -> None:
    with Session(make_engine()) as s:
        loc = _seed_settled_day(s)
        closeout = shift_closeout(s, loc, TODAY)
        takings = build_daily_takings(closeout, AccountMap())

        assert takings.tax_cents == closeout["net_gst_cents"] == 460
        assert takings.total_cents == closeout["gross_sales_cents"] == 5060
        assert takings.sub_total_cents + takings.tax_cents == takings.total_cents
        assert sum(p.amount_cents for p in takings.payments) == takings.total_cents
        assert {p.provider for p in takings.payments} == {"cash", "external"}


# --- the Xero client + push (mocked Xero) --------------------------------------------

class FakeXero:
    """Xero's API in memory, behind an httpx.MockTransport."""

    def __init__(self, *, reject_token: bool = False) -> None:
        self.reject_token = reject_token
        self.token_seq = 0
        self.payment_seq = 0
        self.contact_id: str | None = None
        self.invoices: dict[str, dict[str, Any]] = {}
        self.by_number: dict[str, dict[str, Any]] = {}
        self.deleted_payments: list[str] = []
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path, method = request.url.host, request.url.path, request.method
        if host == "identity.xero.com" and path == "/connect/token":
            return self._token(request)
        if host == "api.xero.com" and path == "/connections":
            return httpx.Response(200, json=[
                {"tenantId": "ten-1", "tenantName": "Madras Curry House Pty Ltd",
                 "tenantType": "ORGANISATION"}])
        if path == "/api.xro/2.0/Contacts":
            return self._contacts(request, method)
        if path == "/api.xro/2.0/Invoices":
            return self._invoices(request, method)
        if path == "/api.xro/2.0/Payments":
            return self._create_payment(request)
        if path.startswith("/api.xro/2.0/Payments/"):
            return self._delete_payment(path.rsplit("/", 1)[1])
        return httpx.Response(404, json={"Message": f"no stub for {method} {path}"})

    def _token(self, request: httpx.Request) -> httpx.Response:
        if self.reject_token:
            return httpx.Response(400, json={"error": "invalid_grant"})
        self.token_seq += 1
        return httpx.Response(200, json={
            "access_token": f"at-{self.token_seq}", "refresh_token": f"rt-{self.token_seq}",
            "expires_in": 1800, "token_type": "Bearer", "scope": "accounting.transactions"})

    def _contacts(self, request: httpx.Request, method: str) -> httpx.Response:
        if method == "GET":
            if self.contact_id:
                return httpx.Response(200, json={"Contacts": [
                    {"ContactID": self.contact_id, "Name": "Daily Takings"}]})
            return httpx.Response(200, json={"Contacts": []})
        self.contact_id = "contact-1"
        name = json.loads(request.content)["Contacts"][0]["Name"]
        return httpx.Response(200, json={"Contacts": [{"ContactID": "contact-1", "Name": name}]})

    def _invoices(self, request: httpx.Request, method: str) -> httpx.Response:
        if method == "GET":
            ref = request.url.params["where"].split("==")[1].strip().strip('"')
            inv = self.by_number.get(ref)
            return httpx.Response(200, json={"Invoices": [inv] if inv else []})
        payload = json.loads(request.content)["Invoices"][0]
        if payload.get("InvoiceID"):
            inv = self.invoices[payload["InvoiceID"]]
            inv.update(payload)
        else:
            iid = f"inv-{len(self.invoices) + 1}"
            inv = {**payload, "InvoiceID": iid, "Payments": []}
            self.invoices[iid] = inv
            self.by_number[payload["InvoiceNumber"]] = inv
        inv.setdefault("Status", payload.get("Status", "AUTHORISED"))
        return httpx.Response(200, json={"Invoices": [inv]})

    def _create_payment(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)["Payments"][0]
        self.payment_seq += 1
        pid = f"pay-{self.payment_seq}"
        inv = self.invoices[payload["Invoice"]["InvoiceID"]]
        payment = {"PaymentID": pid, "Amount": payload["Amount"], "Account": payload["Account"]}
        inv["Payments"].append(payment)
        inv["Status"] = "PAID"
        return httpx.Response(200, json={"Payments": [payment]})

    def _delete_payment(self, payment_id: str) -> httpx.Response:
        self.deleted_payments.append(payment_id)
        for inv in self.invoices.values():
            inv["Payments"] = [p for p in inv["Payments"] if p["PaymentID"] != payment_id]
            if not inv["Payments"] and inv.get("Status") == "PAID":
                inv["Status"] = "AUTHORISED"
        return httpx.Response(200, json={"Payments": [{"PaymentID": payment_id, "Status": "DELETED"}]})


def _client(fake: FakeXero, **kw: Any) -> XeroClient:
    http = httpx.Client(transport=httpx.MockTransport(fake.handler))
    return XeroClient(
        client_id="cid", client_secret="csec", redirect_uri="http://cb", http=http, **kw
    )


def _connect(session: Session, location_id: str) -> None:
    session.add(XeroConnection(
        location_id=location_id, tenant_id="ten-1", tenant_name="Madras Curry House Pty Ltd",
        refresh_token="rt-seed", authorised_at=utcnow()))
    session.commit()


def test_the_token_call_sends_basic_auth_and_connections_need_no_tenant() -> None:
    fake = FakeXero()
    client = _client(fake)
    auth = client.exchange_code("code-1")
    assert auth.access_token == "at-1" and auth.refresh_token == "rt-1"
    token_req = next(r for r in fake.requests if r.url.path == "/connect/token")
    assert token_req.headers["Authorization"].startswith("Basic ")
    assert b"grant_type=authorization_code" in token_req.content and b"code=code-1" in token_req.content

    client.access_token = auth.access_token
    assert client.connections()[0]["tenantId"] == "ten-1"
    conn_req = next(r for r in fake.requests if r.url.path == "/connections")
    assert "Xero-Tenant-Id" not in conn_req.headers


def test_a_refused_sign_in_is_an_auth_error() -> None:
    client = _client(FakeXero(reject_token=True))
    with pytest.raises(InvalidRequest, match="xero-connect"):
        client.refresh("rt-old")


def test_complete_connection_stores_the_org_and_refresh_token() -> None:
    with Session(make_engine()) as s:
        loc = create_location(s, org_name="MCH", name="CBD").id
        s.commit()
        conn = complete_connection(s, loc, client=_client(FakeXero()), code="abc")
        assert conn.tenant_id == "ten-1"
        assert conn.tenant_name == "Madras Curry House Pty Ltd"
        assert conn.refresh_token == "rt-1"


def test_push_creates_one_invoice_pays_clearing_accounts_and_is_idempotent() -> None:
    engine = make_engine()
    with Session(engine) as s:
        loc = _seed_settled_day(s)
        _connect(s, loc)
        fake = FakeXero()

        first = push_daily_takings(s, loc, TODAY, client=_client(fake, tenant_id="ten-1"))
        assert first["created"] is True
        assert first["total_cents"] == 5060 and first["tax_cents"] == 460
        assert {(p["provider"], p["account_code"], p["amount_cents"]) for p in first["payments"]} == {
            ("cash", "090", 4400), ("external", "090", 660)}
        invoice = fake.invoices[first["invoice_id"]]
        assert invoice["Status"] == "PAID" and len(invoice["Payments"]) == 2
        # The rotated refresh token is persisted, so the next day still works.
        conn = s.scalars(
            select(XeroConnection).where(XeroConnection.location_id == loc)
        ).first()
        assert conn is not None and conn.refresh_token == "rt-1"

        second = push_daily_takings(s, loc, TODAY, client=_client(fake, tenant_id="ten-1"))
        assert second["created"] is False
        assert second["invoice_id"] == first["invoice_id"]
        # Exactly one invoice; its two old payments were cleared before re-paying — no double count.
        assert len(fake.invoices) == 1 and len(fake.deleted_payments) == 2
        assert len(fake.invoices[first["invoice_id"]]["Payments"]) == 2


def test_push_without_a_connection_is_a_clear_error() -> None:
    with Session(make_engine()) as s:
        loc = _seed_settled_day(s)
        with pytest.raises(XeroNotConnected, match="xero-connect"):
            push_daily_takings(s, loc, TODAY, client=_client(FakeXero(), tenant_id="ten-1"))


def test_push_surfaces_an_auth_failure() -> None:
    with Session(make_engine()) as s:
        loc = _seed_settled_day(s)
        _connect(s, loc)
        with pytest.raises(InvalidRequest, match="xero-connect"):
            push_daily_takings(s, loc, TODAY, client=_client(FakeXero(reject_token=True), tenant_id="ten-1"))


# --- the CLI -------------------------------------------------------------------------

def _file_db(tmp_path: Path) -> tuple[str, str]:
    url = f"sqlite:///{tmp_path / 'venue.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        loc = _seed_settled_day(s)
    engine.dispose()
    return url, loc


def test_xero_push_dry_run_prints_the_summary_and_payload(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    url, loc = _file_db(tmp_path)
    args = ["--database-url", url, "xero-push", "--location", loc,
            "--business-date", "2026-11-15", "--dry-run"]
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert "Daily takings" in out and "Sales — Mains" in out
    assert '"Type": "ACCREC"' in out and f"VPOS-{loc}-20261115" in out
    assert "Invoice total:      50.60" in out


def test_xero_accounts_shows_defaults_and_stores_an_override(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    url, loc = _file_db(tmp_path)
    assert cli.main(["--database-url", url, "xero-accounts", "--location", loc]) == 0
    assert '"sales_account": "200"' in capsys.readouterr().out

    engine = create_engine(url)
    with Session(engine) as s:
        _connect(s, loc)
    engine.dispose()
    mapfile = tmp_path / "map.json"
    mapfile.write_text('{"sales_account": "201", "clearing_accounts": {"cash": "091"}}')
    assert cli.main(["--database-url", url, "xero-accounts", "--location", loc,
                     "--file", str(mapfile)]) == 0
    assert "Stored the Xero account map" in capsys.readouterr().out
    assert cli.main(["--database-url", url, "xero-accounts", "--location", loc]) == 0
    assert '"sales_account": "201"' in capsys.readouterr().out


def test_xero_accounts_rejects_an_unknown_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    url, loc = _file_db(tmp_path)
    engine = create_engine(url)
    with Session(engine) as s:
        _connect(s, loc)
    engine.dispose()
    mapfile = tmp_path / "bad.json"
    mapfile.write_text('{"sales_acct": "201"}')
    assert cli.main(["--database-url", url, "xero-accounts", "--location", loc,
                     "--file", str(mapfile)]) == 1
    assert "unknown account-map key" in capsys.readouterr().err


def test_account_map_round_trips_through_json() -> None:
    account_map = AccountMap(
        sales_account="201", class_accounts={"Mains": "202"}, gst_free_classes=["Groceries"],
        clearing_accounts={"cash": "091", "stripe": "092"})
    assert AccountMap.from_dict(account_map.to_dict()) == account_map


def test_settings_expose_xero_enabled() -> None:
    from void_pos.config import Settings

    off = Settings(_env_file=None)  # type: ignore[call-arg]
    assert off.xero_enabled is False
    on = Settings(_env_file=None, xero_client_id="cid", xero_client_secret="csec")  # type: ignore[call-arg]
    assert on.xero_enabled is True
