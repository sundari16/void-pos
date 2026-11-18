from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from support import (
    ADMIN_TOKEN,
    CapturePrinter,
    Venue,
    api,
    auth,
    cmd,
    make_engine,
    order_body,
    seed_venue,
)
from void_pos.api.deps import get_receipt_printer
from void_pos.cli import main
from void_pos.devices import create_location
from void_pos.errors import InvalidRequest
from void_pos.ids import new_ulid
from void_pos.main import app
from void_pos.models import Base
from void_pos.receipts import Receipt, ReceiptLine, encode_receipt, money, normalize_abn

ADMIN = auth(ADMIN_TOKEN)
ABN = "51 824 753 556"  # the ATO's worked example; passes the ABN check
Ctx = tuple[TestClient, Venue, Session]


@pytest.fixture
def ctx() -> Iterator[Ctx]:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
        with api(engine) as client:
            yield client, venue, s


def test_abns_are_checked() -> None:
    assert normalize_abn(ABN) == "51824753556"
    for bad in ("51 824 753 557", "1234", "5182475355a"):
        with pytest.raises(InvalidRequest):
            normalize_abn(bad)
    assert money(123456) == "$1,234.56" and money(-50) == "-$0.50"


def _order(c: TestClient, venue: Venue, **extra: Any) -> dict[str, Any]:
    body = order_body(venue, seq=1, **extra)
    body["lines"][0]["tax_rate_snapshot"] = 0.1
    body["lines"][1]["unit_price_cents"] = 150  # a paid modifier: 2 x 150
    r = c.post("/orders", json=body, headers=auth(venue.token))
    assert r.status_code == 200, r.text
    return dict(r.json())


def test_a_plain_receipt_then_a_tax_invoice(ctx: Ctx) -> None:
    c, venue, _ = ctx
    order = _order(c, venue, table_label="7")
    c.post(
        f"/orders/{order['id']}/pay",
        json={**cmd(2), "payment_id": new_ulid(), "method": "external", "external_ref": "APPR 0042",
              "amount_cents": 3000},
        headers=auth(venue.token),
    )
    plain = c.get(f"/orders/{order['id']}/receipt", headers=auth(venue.token)).json()
    assert (plain["title"], plain["venue_name"], plain["abn"]) == ("RECEIPT", "Brisbane CBD", None)
    assert plain["table_label"] == "7" and plain["order_number"] == 1000
    assert [(x["name"], x["amount_cents"], x["is_modifier"]) for x in plain["lines"]] == [
        ("Masala Dosa", 3700, False),
        ("Extra hot", 300, True),
    ]
    assert (plain["total_cents"], plain["gst_cents"], plain["balance_cents"]) == (4000, 336, 1000)
    assert plain["payments"] == [
        {"label": "Card on terminal", "amount_cents": 3000, "reference": "APPR 0042"}
    ]

    details = c.patch(
        f"/admin/locations/{venue.location_id}",
        json={"business_name": "Madras Curry House", "abn": ABN, "address": "1 Queen St, Brisbane",
              "receipt_footer": "Nandri!"},
        headers=ADMIN,
    )
    assert details.status_code == 200, details.text
    assert details.json()["abn"] == "51824753556"
    invoice = c.get(f"/orders/{order['id']}/receipt", headers=auth(venue.token)).json()
    assert (invoice["title"], invoice["venue_name"], invoice["abn"]) == (
        "TAX INVOICE",
        "Madras Curry House",
        ABN,
    )
    assert invoice["footer"] == "Nandri!"

    bad = c.patch(f"/admin/locations/{venue.location_id}", json={"abn": "12 345"}, headers=ADMIN)
    assert (bad.status_code, bad.json()["code"]) == (422, "invalid_abn")
    cleared = c.patch(f"/admin/locations/{venue.location_id}", json={"abn": ""}, headers=ADMIN)
    assert cleared.json()["abn"] is None
    till = c.patch(f"/admin/locations/{venue.location_id}", json={"abn": ""}, headers=auth(venue.token))
    assert till.status_code == 403


def test_printing_a_receipt(ctx: Ctx) -> None:
    c, venue, s = ctx
    order = _order(c, venue)
    url = f"/orders/{order['id']}/print-receipt"
    none = c.post(url, headers=auth(venue.token))
    assert (none.status_code, none.json()["code"]) == (503, "no_receipt_printer")

    c.patch(f"/admin/locations/{venue.location_id}", json={"abn": ABN}, headers=ADMIN)
    printer = CapturePrinter()
    app.dependency_overrides[get_receipt_printer] = lambda: printer
    printed = c.post(url, headers=auth(venue.token))
    assert printed.status_code == 200, printed.text
    data = bytes(printer.sent)
    for text in (b"TAX INVOICE", b"ABN 51 824 753 556", b"$40.00", b"Includes GST", b"$3.36",
                 b"Still owing", b"+ Extra hot"):
        assert text in data, text
    assert c.post(url, headers=auth(venue.token)).status_code == 200  # reprints are fine

    other = seed_venue(s, location_name="Gold Coast", token="gold-coast-till-token-rcpt")
    assert c.get(f"/orders/{order['id']}/receipt", headers=auth(other.token)).status_code == 404


def test_receipt_rows_line_up() -> None:
    receipt = Receipt(
        title="RECEIPT", venue_name="MCH", address=None, phone=None, abn=None, order_number=7,
        table_label=None, ordered_at="18/11/2026 19:45", printed_at="18/11/2026 20:10",
        lines=[
            ReceiptLine(1, "Dosa", 1850, False),
            ReceiptLine(1, "A very long dish name that will not fit on one row", 2200, False),
        ],
        total_cents=4050, gst_cents=0,
    )
    text = encode_receipt(receipt, columns=32).decode("ascii", errors="replace")
    rows = text.split("\n")
    assert "1x Dosa" + " " * (32 - len("1x Dosa") - len("$18.50")) + "$18.50" in rows
    # A long name wraps to the paper width; the amount goes flush right on the last row,
    # or on a row of its own when it doesn't fit beside it.
    assert "1x A very long dish name that" in rows
    assert "   will not fit on one row" in rows
    assert " " * (32 - len("$22.00")) + "$22.00" in rows
    assert all(len(r) <= 32 for r in rows if r.isascii() and "\x1b" not in r and "\x1d" not in r)


def test_venue_details_from_the_command_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    url = f"sqlite:///{tmp_path / 'venue.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        loc = create_location(s, org_name="MCH", name="CBD").id
        s.commit()
    engine.dispose()
    args = ["--database-url", url, "venue-details", "--location", loc]
    assert main([*args, "--abn", ABN, "--business-name", "Madras Curry House"]) == 0
    out = capsys.readouterr().out
    assert "Madras Curry House" in out and "ABN: 51 824 753 556" in out
    assert main([*args, "--abn", "123"]) == 1
    assert "11 digits" in capsys.readouterr().err
