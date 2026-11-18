"""Shared test helpers.

Tests run on in-memory SQLite. Set ``VOIDPOS_TEST_DATABASE_URL`` (a PostgreSQL URL) to run
the same suite on PostgreSQL; each engine then gets a schema of its own, so sessions a
test leaves open can never block the next test.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from void_pos.api.deps import get_printer, get_session
from void_pos.clock import utcnow
from void_pos.config import Settings, get_settings
from void_pos.devices import hash_secret
from void_pos.ids import new_ulid
from void_pos.main import app
from void_pos.models import Base, Device, Location, MenuItem, Org
from void_pos.printing import PrinterUnavailable

ADMIN_TOKEN = "admin-token-for-the-test-suite"
TILL_TOKEN = "till-1-token-for-the-test-suite"
TAMIL_DOSA = "மசாலா தோசை"


def make_engine() -> Engine:
    url = os.environ.get("VOIDPOS_TEST_DATABASE_URL")
    if url:
        schema = f"test_{new_ulid().lower()}"
        bootstrap = create_engine(url)
        with bootstrap.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        bootstrap.dispose()
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    else:
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        # Enforce foreign keys like PostgreSQL does, so a dangling reference fails here too.
        event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    return engine


@dataclass(frozen=True)
class Venue:
    org_id: str
    location_id: str
    device_id: str
    item_id: str
    token: str


def add_till(
    session: Session,
    location_id: str,
    *,
    name: str,
    token: str,
    order_range: tuple[int, int],
) -> Device:
    device = Device(
        location_id=location_id,
        name=name,
        order_number_start=order_range[0],
        order_number_end=order_range[1],
        token_hash=hash_secret(token),
        paired_at=utcnow(),
    )
    session.add(device)
    session.flush()
    return device


def seed_venue(
    session: Session,
    *,
    location_name: str = "Brisbane CBD",
    token: str = TILL_TOKEN,
    order_range: tuple[int, int] = (1000, 1999),
) -> Venue:
    """An org, a location, one paired till and one menu item, committed."""
    org = Org(name="Madras Curry House")
    session.add(org)
    session.flush()
    location = Location(org_id=org.id, name=location_name)
    session.add(location)
    session.flush()
    device = add_till(session, location.id, name="Till 1", token=token, order_range=order_range)
    item = MenuItem(
        location_id=location.id,
        name="Masala Dosa",
        kitchen_name=TAMIL_DOSA,
        category="Mains",
        base_price_cents=1850,
    )
    session.add(item)
    session.commit()
    return Venue(org.id, location.id, device.id, item.id, token)


class CapturePrinter:
    def __init__(self) -> None:
        self.sent = bytearray()
        self.jobs = 0

    def send(self, data: bytes) -> None:
        self.sent += data
        self.jobs += 1


class DownPrinter:
    def send(self, data: bytes) -> None:
        raise PrinterUnavailable("the kitchen printer at 10.0.0.9:9100 can't be reached")


@contextmanager
def api(
    engine: Engine, *, printer: Any = None, admin_token: str | None = ADMIN_TOKEN
) -> Iterator[TestClient]:
    """A client for the real app wired to ``engine``, a fake printer and test settings."""
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    test_settings = Settings(_env_file=None, admin_token=admin_token)  # type: ignore[call-arg]

    def _session() -> Iterator[Session]:
        with sessions() as s:
            yield s

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_printer] = lambda: printer
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def cmd(seq: int) -> dict[str, Any]:
    """The ids a till mints for one command."""
    return {"event_id": new_ulid(), "seq": seq}


def order_body(
    venue: Venue,
    *,
    seq: int,
    order_number: int = 1000,
    quantity: int = 2,
    price: int = 1850,
    with_modifier: bool = True,
    **extra: Any,
) -> dict[str, Any]:
    item_line = new_ulid()
    lines: list[dict[str, Any]] = [
        {
            "id": item_line,
            "menu_item_id": venue.item_id,
            "quantity": quantity,
            "name_snapshot": "Masala Dosa",
            "kitchen_name_snapshot": TAMIL_DOSA,
            "unit_price_cents": price,
            "category_snapshot": "Mains",
        }
    ]
    if with_modifier:
        lines.append(
            {
                "id": new_ulid(),
                "parent_line_id": item_line,
                "name_snapshot": "Extra hot",
                "unit_price_cents": 0,
            }
        )
    return {
        **cmd(seq),
        "order_id": new_ulid(),
        "order_number": order_number,
        "channel": "pos",
        "fulfillment": "dine_in",
        "lines": lines,
        **extra,
    }
