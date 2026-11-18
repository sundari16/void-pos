"""Seed a local Void POS database with one org / location / till and a small menu, then
print a pairing code to enter on the web POS.

This is a *development helper* for running the web UI against a local backend. It is not
part of the application package and never runs in production; a real venue is set up
with the ``void-pos`` CLI or the admin API.

Usage (from the repo root, with the backend installed into your venv):

    # defaults to a local sqlite file so you don't need Postgres
    python scripts/seed_dev.py

    # or point at your own (migrated) database
    VOIDPOS_DATABASE_URL=postgresql+psycopg://localhost/void_pos python scripts/seed_dev.py

Then run the backend against the SAME database and start the web app:

    VOIDPOS_DATABASE_URL=sqlite:///./void_pos_dev.db uvicorn void_pos.main:app
    cd web && npm run dev

Open the web POS and enter the pairing code. Re-running the script is safe: it reuses the
seeded rows and prints a fresh code (each code works once).
"""

from __future__ import annotations

import os
from datetime import timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from void_pos.devices import issue_pairing_code
from void_pos.models import Base, Device, Location, MenuItem, Org

DEFAULT_URL = "sqlite:///./void_pos_dev.db"
PAIRING_TTL = timedelta(hours=1)  # generous for a dev box

ORG_NAME = "Madras Curry House"
LOCATION_NAME = "Brisbane CBD"
DEVICE_NAME = "Till 1"
ORDER_NUMBER_START = 1000
ORDER_NUMBER_END = 1999

# (name, cents, category, tax_rate, kitchen_name)
MENU: list[tuple[str, int, str, float, str | None]] = [
    ("Masala Dosa", 1850, "Mains", 0.0, "மசாலா தோசை"),
    ("Butter Chicken", 2200, "Mains", 0.0, None),
    ("Paneer Tikka", 1900, "Mains", 0.0, None),
    ("Lamb Biryani", 2400, "Mains", 0.0, None),
    ("Vegetable Samosa", 700, "Sides", 0.0, None),
    ("Garlic Naan", 450, "Sides", 0.0, None),
    ("Papadum", 300, "Sides", 0.0, None),
    ("Mango Lassi", 650, "Drinks", 0.0, None),
    ("Masala Chai", 400, "Drinks", 0.0, None),
    ("Sparkling Water", 350, "Drinks", 0.0, None),
    ("Gulab Jamun", 600, "Dessert", 0.0, None),
]


def seed(session: Session) -> dict[str, str]:
    """Create (or reuse) the dev venue and issue a fresh pairing code for its till."""
    org = session.scalars(select(Org).where(Org.name == ORG_NAME)).first()
    if org is None:
        org = Org(name=ORG_NAME)
        session.add(org)
        session.flush()

    location = session.scalars(
        select(Location).where(Location.org_id == org.id, Location.name == LOCATION_NAME)
    ).first()
    if location is None:
        location = Location(org_id=org.id, name=LOCATION_NAME)
        session.add(location)
        session.flush()

    device = session.scalars(
        select(Device).where(Device.location_id == location.id, Device.name == DEVICE_NAME)
    ).first()
    if device is None:
        device = Device(
            location_id=location.id,
            name=DEVICE_NAME,
            order_number_start=ORDER_NUMBER_START,
            order_number_end=ORDER_NUMBER_END,
        )
        session.add(device)
        session.flush()

    existing = session.scalars(
        select(MenuItem).where(MenuItem.location_id == location.id)
    ).all()
    if not existing:
        for name, cents, category, tax, kitchen in MENU:
            session.add(
                MenuItem(
                    location_id=location.id,
                    name=name,
                    base_price_cents=cents,
                    category=category,
                    tax_rate=tax,
                    kitchen_name=kitchen,
                )
            )

    code = issue_pairing_code(session, device, ttl=PAIRING_TTL)
    session.commit()
    return {"location": location.id, "device": device.id, "pairing_code": code.code}


def main() -> None:
    url = os.environ.get("VOIDPOS_DATABASE_URL", DEFAULT_URL)
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        ids = seed(session)

    print("\nSeeded Void POS dev data.")
    print(f"  database     : {url}")
    print(f"  location     : {ids['location']}")
    print(f"  till         : {DEVICE_NAME} ({ids['device']})")
    print(f"  orders       : {ORDER_NUMBER_START}-{ORDER_NUMBER_END}")
    print(f"  pairing code : {ids['pairing_code']}  (works once, for the next hour)")
    print("\nStart the API on this database, open the web POS and enter the pairing code.\n")


if __name__ == "__main__":
    main()
