"""V1 end-to-end check against a running Void POS API: the features ``smoke_test.py`` and
the browser tests don't cover — online storefront, CRM, loyalty, marketing, the request
board, the mock aggregator webhook, the mock delivery courier, voice intake, closeout and
export. Standard library only. Runs every check, then prints a summary:

    set -a; . ./.env; set +a
    python scripts/v1_smoke.py --api http://localhost:8000

Loyalty is checked when the server has it on (VOIDPOS_LOYALTY_ENABLED); the aggregator
webhook when an aggregator secret is given. Exits 1 if any check failed.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import time
import urllib.error
import urllib.request
from typing import Any

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
RESULTS: list[tuple[str, bool, str]] = []


def ulid() -> str:
    t = int(time.time() * 1000)
    head = "".join(CROCKFORD[(t >> (5 * i)) & 31] for i in reversed(range(10)))
    return head + "".join(CROCKFORD[b % 32] for b in secrets.token_bytes(16))


class Client:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def call(
        self, method: str, path: str, *, token: str | None = None, body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                raw = res.read()
                return res.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as err:
            raw = err.read()
            try:
                return err.code, json.loads(raw) if raw else None
            except ValueError:
                return err.code, raw.decode(errors="replace")[:300]


def check(name: str, status: int, body: Any, expected: int) -> Any:
    ok = status == expected
    detail = "" if ok else f"HTTP {status} (wanted {expected}): {str(body)[:300]}"
    RESULTS.append((name, ok, detail))
    print(f"{'ok  ' if ok else 'FAIL'}  {name}{'  - ' + detail if detail else ''}")
    return body if ok else None


def expect(name: str, ok: bool, detail: str) -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'ok  ' if ok else 'FAIL'}  {name}  - {detail}")


def skip(name: str, why: str) -> None:
    print(f"skip  {name}  - {why}")


def wait_ready(c: Client, seconds: int) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            if c.call("GET", "/health/ready")[0] == 200:
                return
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise SystemExit(f"FAIL  API at {c.base} not ready after {seconds}s")
        time.sleep(1)


def line(item_id: str, name: str, price: int, qty: int, category: str) -> dict[str, Any]:
    return {
        "id": ulid(), "menu_item_id": item_id, "quantity": qty, "name_snapshot": name,
        "unit_price_cents": price, "category_snapshot": category, "tax_rate_snapshot": 0.1,
    }


def run(api: str, admin: str, aggregator_secret: str | None) -> None:
    c = Client(api)

    # Venue setup: a location with an address (couriers collect there), a till, two dishes.
    loc = check("setup: create location", *c.call("POST", "/admin/locations", token=admin, body={
        "org_name": "V1 Smoke Org", "name": f"V1 {ulid()[-6:]}"}), 201)
    if not loc:
        return
    lid = loc["id"]
    check("setup: venue address", *c.call("PATCH", f"/admin/locations/{lid}", token=admin, body={
        "address": "3049 Moggill Rd, Bellbowrie QLD 4070", "phone": "07 3202 0000"}), 200)
    dev = check("setup: register till", *c.call(
        "POST", f"/admin/locations/{lid}/devices", token=admin,
        body={"name": "V1 Till", "order_number_start": 1, "order_number_end": 999}), 201)
    paired = check("setup: pair till", *c.call(
        "POST", "/devices/pair", body={"pairing_code": dev["pairing_code"]}), 200)
    till = paired["token"]
    dosa = check("setup: add Masala Dosa", *c.call(
        "POST", f"/locations/{lid}/menu/items", token=admin,
        body={"name": "Masala Dosa", "base_price_cents": 2200, "category": "Mains",
              "tax_rate": 0.1}), 201)
    lassi = check("setup: add Mango Lassi", *c.call(
        "POST", f"/locations/{lid}/menu/items", token=admin,
        body={"name": "Mango Lassi", "base_price_cents": 660, "category": "Drinks",
              "tax_rate": 0.1}), 201)
    seq = 0

    def nxt() -> int:
        nonlocal seq
        seq += 1
        return seq

    # Online storefront, CRM and loyalty.
    front = check("online: storefront", *c.call("GET", f"/online/locations/{lid}"), 200)
    loyalty_on = bool(front and front["loyalty"]["enabled"])
    web = check("online: place pickup order (new, opted-in customer)", *c.call(
        "POST", f"/online/locations/{lid}/orders", body={
            "order_id": ulid(),
            "customer": {"name": "Priya", "phone": "0400000001",
                         "email": "priya@example.com", "marketing_opt_in": True},
            "fulfillment": "pickup",
            "lines": [line(dosa["id"], "Masala Dosa", 2200, 1, "Mains")]}), 200)
    if web:
        wid = web["order"]["id"]
        if front and not front["online_pay_enabled"]:
            check("online: card intent without Stripe keys is 503", *c.call(
                "POST", f"/online/orders/{wid}/stripe-intent", body={"payment_id": ulid()}), 503)
        check("online: read the order back", *c.call("GET", f"/online/orders/{wid}"), 200)
        check("online: till takes cash for the web order", *c.call(
            "POST", f"/orders/{wid}/pay", token=till,
            body={"event_id": ulid(), "seq": nxt(), "payment_id": ulid(), "method": "cash"}), 200)
        cid = web["customer_id"]
        hist = check("crm: customer order history", *c.call(
            "GET", f"/online/customers/{cid}/orders"), 200)
        if hist:
            expect("crm: history lists the order", len(hist["orders"]) >= 1,
                   f"{len(hist['orders'])} order(s)")
        loy = check("loyalty: balance", *c.call("GET", f"/online/customers/{cid}/loyalty"), 200)
        if loy and loyalty_on:
            expect("loyalty: points earned on the paid web order", loy["balance"] > 0,
                   f"balance={loy['balance']}")
        elif loy:
            skip("loyalty: points earned", "loyalty is off on this server")

    # Marketing through the configured sender ("log" by default sends nothing real).
    camp = f"/admin/locations/{lid}/campaigns"
    dry = check("marketing: SMS campaign dry run", *c.call("POST", camp, token=admin, body={
        "channel": "sms", "body": "Hi {name}, 10% off dosa this week", "dry_run": True}), 200)
    if dry:
        expect("marketing: dry run reaches the opted-in customer", dry["total"] >= 1,
               f"total={dry['total']} sent={dry['sent']}")
    check("marketing: SMS campaign send", *c.call("POST", camp, token=admin, body={
        "channel": "sms", "body": "Hi {name}, 10% off dosa"}), 200)

    # Request board.
    req = check("board: submit a request (till)", *c.call("POST", "/board/requests", token=till,
                body={"title": "Split by seat", "body": "Split a bill by seat number",
                      "author": "Asha"}), 201)
    if req:
        rid = req["id"]
        check("board: upvote", *c.call("POST", f"/board/requests/{rid}/upvote", token=till), 200)
        check("board: upvote again is idempotent", *c.call(
            "POST", f"/board/requests/{rid}/upvote", token=till), 200)
        check("board: a till can't set status", *c.call(
            "POST", f"/board/requests/{rid}/status", token=till, body={"status": "planned"}), 403)
        check("board: admin sets status", *c.call(
            "POST", f"/board/requests/{rid}/status", token=admin, body={"status": "planned"}), 200)

    # Delivery aggregator webhook (the open core's mock partner).
    if aggregator_secret:
        hook = f"/aggregators/mock/locations/{lid}/orders"
        order = {"id": f"MOCK-{ulid()[-8:]}", "display_id": "MK-7Q2K", "customer_name": "Sam",
                 "notes": "no onions",
                 "items": [{"name": "Masala Dosa", "unit_price_cents": 2200, "quantity": 1,
                            "category": "Mains", "tax_rate": 0.1}]}
        check("aggregator: wrong secret is 403", *c.call(
            "POST", hook, body=order, headers={"X-Void-Aggregator-Secret": "wrong"}), 403)
        ing = check("aggregator: mock order ingested", *c.call(
            "POST", hook, body=order, headers={"X-Void-Aggregator-Secret": aggregator_secret}), 200)
        rep = check("aggregator: replay", *c.call(
            "POST", hook, body=order, headers={"X-Void-Aggregator-Secret": aggregator_secret}), 200)
        if ing and rep:
            expect("aggregator: replay returns the same order",
                   rep["replayed"] is True and rep["order_id"] == ing["order_id"],
                   f"replayed={rep['replayed']}")
        check("aggregator: menu sync-out (admin)", *c.call(
            "POST", f"/aggregators/mock/locations/{lid}/menu", token=admin), 200)
    else:
        skip("aggregator webhook", "no --aggregator-secret / VOIDPOS_AGGREGATOR_WEBHOOK_SECRET")

    # On-demand delivery (the open core's mock courier).
    dorder = check("delivery: till places a delivery order", *c.call(
        "POST", "/orders", token=till, body={
            "event_id": ulid(), "seq": nxt(), "order_id": ulid(), "order_number": 50,
            "channel": "pos", "fulfillment": "delivery",
            "lines": [line(lassi["id"], "Mango Lassi", 660, 2, "Drinks")]}), 200)
    if dorder:
        body = {"delivery_id": ulid(), "dropoff_name": "Lee", "dropoff_phone": "0400000002",
                "dropoff_address": "1 Moggill Rd, Bellbowrie QLD 4070"}
        path = f"/orders/{dorder['id']}/delivery"
        if check("delivery: dispatch the courier", *c.call("POST", path, token=till, body=body), 200):
            check("delivery: dispatch replay", *c.call("POST", path, token=till, body=body), 200)
            check("delivery: track", *c.call("GET", path, token=till), 200)

    # Voice intake (the POS side; the telephony stack is separate).
    vbody = {"items": [{"name": "Masala Dosa", "quantity": 2}, {"name": "Mango Lassi"}],
             "customer_phone": "0400000003", "customer_name": "Ravi", "order_id": ulid()}
    q = check("voice: quote (places nothing)", *c.call(
        "POST", f"/voice/locations/{lid}/quote", body=vbody), 200)
    if q:
        expect("voice: quote is priced from the menu", q["total_cents"] == 2 * 2200 + 660,
               f"total={q['total_cents']} gst={q['gst_cents']}")
    v = check("voice: place the order", *c.call(
        "POST", f"/voice/locations/{lid}/orders", body=vbody), 200)
    if v:
        expect("voice: on the voice channel", v["channel"] == "voice", f"channel={v['channel']}")

    # Closeout and export.
    if dorder:
        rep = check("closeout: report", *c.call(
            "GET", f"/reports/{lid}/closeout?business_date={dorder['business_date']}",
            token=till), 200)
        if rep:
            print(f"      gross={rep['gross_sales_cents']} by provider "
                  f"{rep['payments_by_provider']} orders={rep['orders']}")
    check("export: till can't export", *c.call("GET", f"/export/{lid}", token=till), 403)
    check("export: admin exports", *c.call("GET", f"/export/{lid}", token=admin), 200)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--admin-token", default=os.environ.get("VOIDPOS_ADMIN_TOKEN"))
    parser.add_argument(
        "--aggregator-secret", default=os.environ.get("VOIDPOS_AGGREGATOR_WEBHOOK_SECRET")
    )
    parser.add_argument("--wait", type=int, default=60, help="seconds to wait for readiness")
    args = parser.parse_args()
    if not args.admin_token:
        parser.error("pass --admin-token or set VOIDPOS_ADMIN_TOKEN")
    wait_ready(Client(args.api), args.wait)
    run(args.api, args.admin_token, args.aggregator_secret)
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{'PASS' if not failed else 'FAIL'}  {len(RESULTS) - len(failed)}/{len(RESULTS)} checks")
    for name, _, detail in failed:
        print(f"  FAIL {name}: {detail}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
