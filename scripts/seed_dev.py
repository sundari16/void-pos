"""Seed a local Void POS database with one org / location / device and a small menu,
then print the env block the web POS needs (web/.env.local).

This is a *development helper* for running the web UI against a local backend — it is not
part of the application package and never runs in production. It talks to the models
directly (there is no bootstrap endpoint on the API).

Usage (from the repo root, with the backend installed into your venv):

    # defaults to a local sqlite file so you don't need Postgres
    python scripts/seed_dev.py

    # or point at your own database
    VOIDPOS_DATABASE_URL=postgresql+psycopg://localhost/void_pos python scripts/seed_dev.py

Then run the backend against the SAME database and start the web app:

    VOIDPOS_DATABASE_URL=sqlite:///./void_pos_dev.db uvicorn void_pos.main:app
    cd web && npm run dev
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from void_pos.models import Base, Device, Location, MenuItem, Org

DEFAULT_URL = "sqlite:///./void_pos_dev.db"

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

    session.commit()
    return {"location": location.id, "device": device.id}


def main() -> None:
    url = os.environ.get("VOIDPOS_DATABASE_URL", DEFAULT_URL)
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        ids = seed(session)

    print("\nSeeded Void POS dev data.")
    print(f"  database : {url}")
    print(f"  location : {ids['location']}")
    print(f"  device   : {ids['device']}")
    print("\nPaste into web/.env.local:\n")
    print("NEXT_PUBLIC_API_BASE=http://localhost:8000")
    print(f"NEXT_PUBLIC_LOCATION_ID={ids['location']}")
    print(f"NEXT_PUBLIC_DEVICE_ID={ids['device']}")
    print(f"NEXT_PUBLIC_ORDER_NUMBER_START={ORDER_NUMBER_START}")
    print()


if __name__ == "__main__":
    main()
