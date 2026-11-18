"""End-to-end smoke test against a running Void POS API, the way a venue uses it.

Sets up a location and a till with the admin token, pairs the till, then places an order,
sends it to the kitchen, pays, replays the payment and reads the closeout. Standard
library only, so it runs anywhere Python does:

    python scripts/smoke_test.py --api http://localhost:8000 --admin-token="$VOIDPOS_ADMIN_TOKEN"

Exits 0 when every step passes, 1 on the first failure.
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


def ulid() -> str:
    t = int(time.time() * 1000)
    head = "".join(CROCKFORD[(t >> (5 * i)) & 31] for i in reversed(range(10)))
    return head + "".join(CROCKFORD[b % 32] for b in secrets.token_bytes(16))


class Client:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def call(
        self, method: str, path: str, *, token: str | None = None, body: Any = None
    ) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=15) as res:
                raw = res.read()
                return res.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as err:
            raw = err.read()
            return err.code, json.loads(raw) if raw else None


def wait_ready(client: Client, seconds: int) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            status, _ = client.call("GET", "/health/ready")
            if status == 200:
                return
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise SystemExit(f"FAIL  API at {client.base} not ready after {seconds}s")
        time.sleep(1)


def step(name: str, status: int, body: Any, expected: int) -> Any:
    if status != expected:
        print(f"FAIL  {name}: HTTP {status} (wanted {expected}): {body}")
        raise SystemExit(1)
    print(f"ok    {name}")
    return body


def run(api: str, admin_token: str, wait: int) -> None:
    c = Client(api)
    wait_ready(c, wait)
    admin = admin_token

    loc = step(
        "create location",
        *c.call("POST", "/admin/locations", token=admin, body={
            "org_name": "Smoke Test Org", "name": f"Smoke {ulid()[-6:]}",
        }),
        201,
    )
    created = step(
        "register till",
        *c.call("POST", f"/admin/locations/{loc['id']}/devices", token=admin, body={
            "name": "Smoke Till", "order_number_start": 1, "order_number_end": 999,
        }),
        201,
    )
    paired = step(
        "pair till",
        *c.call("POST", "/devices/pair", body={"pairing_code": created["pairing_code"]}),
        200,
    )
    till = paired["token"]
    item = step(
        "add menu item",
        *c.call("POST", f"/locations/{loc['id']}/menu/items", token=admin, body={
            "name": "Masala Dosa", "base_price_cents": 1850, "category": "Mains",
        }),
        201,
    )
    step("till reads menu", *c.call("GET", f"/locations/{loc['id']}/menu/items", token=till), 200)

    line = ulid()
    order = step(
        "place order",
        *c.call("POST", "/orders", token=till, body={
            "event_id": ulid(), "seq": 1, "order_id": ulid(), "order_number": 1,
            "channel": "pos", "fulfillment": "dine_in",
            "lines": [
                {"id": line, "menu_item_id": item["id"], "quantity": 2,
                 "name_snapshot": "Masala Dosa", "unit_price_cents": 1850},
                {"id": ulid(), "parent_line_id": line, "name_snapshot": "Extra hot",
                 "unit_price_cents": 0},
            ],
        }),
        200,
    )
    step(
        "send to kitchen",
        *c.call("POST", f"/orders/{order['id']}/send-to-kitchen", token=till,
                body={"event_id": ulid(), "seq": 2}),
        200,
    )
    pay = {"event_id": ulid(), "seq": 3, "payment_id": ulid(), "method": "cash"}
    paid = step("pay cash", *c.call("POST", f"/orders/{order['id']}/pay", token=till, body=pay), 200)
    again = step(
        "replay payment", *c.call("POST", f"/orders/{order['id']}/pay", token=till, body=pay), 200
    )
    if not again["replayed"] or again["payment_id"] != paid["payment_id"]:
        print(f"FAIL  replayed payment should return the original: {again}")
        raise SystemExit(1)

    report = step(
        "closeout",
        *c.call("GET", f"/reports/{loc['id']}/closeout?business_date={order['business_date']}",
                token=till),
        200,
    )
    if report["gross_sales_cents"] != 3700:
        print(f"FAIL  closeout gross should be 3700: {report}")
        raise SystemExit(1)
    step("till can't export", *c.call("GET", f"/export/{loc['id']}", token=till), 403)
    step("admin exports", *c.call("GET", f"/export/{loc['id']}", token=admin), 200)
    print("PASS  smoke test")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--admin-token", default=os.environ.get("VOIDPOS_ADMIN_TOKEN"))
    parser.add_argument("--wait", type=int, default=60, help="seconds to wait for readiness")
    args = parser.parse_args()
    if not args.admin_token:
        parser.error("pass --admin-token or set VOIDPOS_ADMIN_TOKEN")
    run(args.api, args.admin_token, args.wait)


if __name__ == "__main__":
    main()
