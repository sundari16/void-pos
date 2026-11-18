"""Regressions from the review of the table-service build."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
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
from void_pos.config import settings
from void_pos.errors import InvalidRequest
from void_pos.ids import new_ulid
from void_pos.menu import create_item
from void_pos.receipts import normalize_abn
from void_pos.staff import create_staff

ROOT = Path(__file__).resolve().parents[1]
Ctx = tuple[TestClient, Venue, CapturePrinter, Session]


@pytest.fixture
def ctx() -> Iterator[Ctx]:
    engine = make_engine()
    printer = CapturePrinter()
    with Session(engine) as s:
        venue = seed_venue(s)
        with api(engine, printer=printer) as client:
            yield client, venue, printer, s


def _event(venue: Venue, seq: int, type_: str, payload: dict[str, Any], at: str = "08:00") -> dict[str, Any]:
    return {
        "event_id": new_ulid(),
        "device_id": venue.device_id,
        "seq": seq,
        "location_id": venue.location_id,
        "business_date": "2026-11-18",
        "occurred_at": f"2026-11-18T{at}:{seq:02d}+10:00",
        "type": type_,
        "payload": payload,
    }


def _push(c: TestClient, venue: Venue, *events: dict[str, Any]) -> list[dict[str, Any]]:
    r = c.post("/sync/push", json={"events": list(events)}, headers=auth(venue.token))
    assert r.status_code == 200, r.text
    return list(r.json()["acks"])


def test_a_synced_void_needs_a_real_managers_approval(ctx: Ctx) -> None:
    c, venue, _, s = ctx
    order = c.post("/orders", json=order_body(venue, seq=1), headers=auth(venue.token)).json()
    asha = create_staff(s, location_id=venue.location_id, name="Asha", role="manager", pin="4821")
    s.commit()

    def void(seq: int, **approval: Any) -> dict[str, Any]:
        payload = {"order_id": order["id"], "reason": "Left", **approval}
        return _push(c, venue, _event(venue, seq, "order.voided", payload))[0]

    for seq, approval in enumerate(
        [{}, {"approved_by": new_ulid(), "approved_by_name": "Forged Boss"},
         {"approved_by": asha.id, "approved_by_name": "Forged Boss"}],
        start=2,
    ):
        ack = void(seq, **approval)
        assert (ack["status"], ack["code"]) == ("rejected", "manager_approval_required"), approval
    assert c.get(f"/orders/{order['id']}", headers=auth(venue.token)).json()["state"] == "open"
    assert void(2, approved_by=asha.id, approved_by_name="Asha")["status"] == "applied"


def test_an_order_has_at_most_a_thousand_lines(ctx: Ctx) -> None:
    c, venue, _, s = ctx
    naan = create_item(s, location_id=venue.location_id, name="Naan", base_price_cents=450).id
    s.commit()
    order = c.post("/orders", json=order_body(venue, seq=1), headers=auth(venue.token)).json()

    def batch(n: int) -> list[dict[str, Any]]:
        return [
            {"id": new_ulid(), "menu_item_id": naan, "name_snapshot": "Naan", "unit_price_cents": 450}
            for _ in range(n)
        ]

    url = f"/orders/{order['id']}/lines"
    assert c.post(url, json={**cmd(2), "lines": batch(500)}, headers=auth(venue.token)).status_code == 200
    long = c.post(url, json={**cmd(3), "lines": batch(499)}, headers=auth(venue.token))
    assert (long.status_code, long.json()["code"]) == (422, "order_too_long")
    ack = _push(c, venue, _event(venue, 3, "order.lines_added", {"order_id": order["id"], "lines": batch(499)}))[0]
    assert (ack["status"], ack["code"]) == ("rejected", "order_too_long")
    assert c.post(url, json={**cmd(3), "lines": batch(498)}, headers=auth(venue.token)).status_code == 200


def test_additions_for_an_order_not_here_yet_can_be_sent_again(ctx: Ctx) -> None:
    c, venue, _, _ = ctx
    placed = {k: v for k, v in order_body(venue, seq=1).items() if k not in ("event_id", "seq")}
    extra = [{"id": new_ulid(), "name_snapshot": "Chai", "unit_price_cents": 400}]
    add = _event(venue, 2, "order.lines_added", {"order_id": placed["order_id"], "lines": extra})
    first = _push(c, venue, add)[0]
    assert (first["status"], first["code"]) == ("rejected", "unknown_order")
    acks = _push(c, venue, _event(venue, 1, "order.placed", placed), add)
    assert [a["status"] for a in acks] == ["applied", "applied"]
    read = c.get(f"/orders/{placed['order_id']}", headers=auth(venue.token)).json()
    assert read["total_cents"] == 3700 + 400


def test_a_void_ticket_lists_only_what_the_kitchen_had(ctx: Ctx) -> None:
    c, venue, printer, _ = ctx
    till = auth(venue.token)
    order = c.post("/orders", json=order_body(venue, seq=1), headers=till).json()
    c.post(f"/orders/{order['id']}/send-to-kitchen", json=cmd(2), headers=till)
    extra = [{"id": new_ulid(), "name_snapshot": "Gulab Jamun", "unit_price_cents": 600}]
    c.post(f"/orders/{order['id']}/lines", json={**cmd(3), "lines": extra}, headers=till)
    printer.sent.clear()
    c.post(f"/orders/{order['id']}/void", json={**cmd(4), "reason": "Left"}, headers=till)
    assert b"VOID #1000" in printer.sent and b"Extra hot" in printer.sent
    assert b"Gulab Jamun" not in printer.sent


def test_the_receipt_shows_when_the_till_rang_it_up(ctx: Ctx) -> None:
    c, venue, _, _ = ctx
    placed = {k: v for k, v in order_body(venue, seq=1).items() if k not in ("event_id", "seq")}
    _push(c, venue, _event(venue, 1, "order.placed", placed, at="07:15"))  # synced later
    receipt = c.get(f"/orders/{placed['order_id']}/receipt", headers=auth(ADMIN_TOKEN)).json()
    assert receipt["ordered_at"] == "18/11/2026 07:15"


def test_abns_are_ascii_digits() -> None:
    for text in ("٥١٨٢٤٧٥٣٥٥٦", "5182475355²"):
        with pytest.raises(InvalidRequest):
            normalize_abn(text)


def test_the_migration_backfills_from_the_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = f"sqlite:///{tmp_path / 'upgrade.db'}"
    monkeypatch.setattr(settings, "database_url", url)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "c8e2f4a6b0d1")

    engine = sa.create_engine(url)
    o, loc, dev, a, b = "O" * 26, "L" * 26, "D" * 26, "A" * 26, "B" * 26
    statements = [
        f"INSERT INTO org (id, name) VALUES ('{o}', 'MCH')",
        (f"INSERT INTO location (id, org_id, name, timezone) VALUES ('{loc}', '{o}', 'CBD', "
        "'Australia/Brisbane')"),
        (f"INSERT INTO device (id, location_id, name, order_number_start, order_number_end, "
        f"last_seq) VALUES ('{dev}', '{loc}', 'Till', 1, 9, 3)"),
        *(
            f"INSERT INTO pos_order (id, location_id, device_id, order_number, business_date, "
            f"channel, fulfillment, state) VALUES ('{oid}', '{loc}', '{dev}', {n}, "
            f"'2026-11-18', 'pos', 'pickup', '{state}')"
            for oid, n, state in ((a, 1, "sent"), (b, 2, "paid"))
        ),
        *(
            f"INSERT INTO order_line (id, order_id, quantity, name_snapshot, unit_price_cents, "
            f"tax_rate_snapshot) VALUES ('{lid}', '{oid}', 1, 'x', 100, 0)"
            for lid, oid in (("1" * 26, a), ("2" * 26, b))
        ),
        *(
            f"INSERT INTO event (event_id, device_id, seq, location_id, business_date, "
            f"occurred_at, received_at, type, payload) VALUES ('{eid}', '{dev}', {seq}, '{loc}', "
            f"'2026-11-18', '{at}', '{at}', '{kind}', '{{\"order_id\": \"{oid}\"}}')"
            for eid, seq, at, kind, oid in (
                ("E1" + "0" * 24, 1, "2026-11-17 21:00:00", "order.placed", a),
                ("E2" + "0" * 24, 2, "2026-11-17 21:05:00", "order.sent", a),
                ("E3" + "0" * 24, 3, "2026-11-17 21:10:00", "order.placed", b),
            )
        ),
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(sa.text(statement))
    command.upgrade(config, "head")

    with engine.connect() as conn:
        sent = dict(conn.execute(sa.text("SELECT order_id, sent_at FROM order_line")).all())
        placed = dict(conn.execute(sa.text("SELECT id, placed_at FROM pos_order")).all())
    engine.dispose()
    assert sent["A" * 26] is not None and str(sent["A" * 26]).startswith("2026-11-17 21:05")
    assert sent["B" * 26] is None  # paid at the counter, never sent: the kitchen hasn't had it
    assert str(placed["B" * 26]).startswith("2026-11-17 21:10")
