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
from datetime import time, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from void_pos.devices import issue_pairing_code
from void_pos.menu.modifier_import import import_modifiers, parse_modifier_csv
from void_pos.models import Base, Device, Location, MenuItem, Org
from void_pos.receipts import update_venue
from void_pos.staff import create_staff, list_staff

DEFAULT_URL = "sqlite:///./void_pos_dev.db"
PAIRING_TTL = timedelta(hours=1)  # generous for a dev box

ORG_NAME = "Madras Curry House"
LOCATION_NAME = "Brisbane CBD"
DEVICE_NAME = "Till 1"
ORDER_NUMBER_START = 1000
ORDER_NUMBER_END = 1999

# (name, cents, category, tax_rate, kitchen_name)
MENU: list[tuple[str, int, str, float, str | None]] = [
    ("Masala Dosa", 1850, "Mains", 0.1, "மசாலா தோசை"),
    ("Butter Chicken", 2200, "Mains", 0.1, None),
    ("Paneer Tikka", 1900, "Mains", 0.1, None),
    ("Lamb Biryani", 2400, "Mains", 0.1, None),
    ("Vegetable Samosa", 700, "Sides", 0.1, None),
    ("Garlic Naan", 450, "Sides", 0.1, None),
    ("Papadum", 300, "Sides", 0.1, None),
    ("Mango Lassi", 650, "Drinks", 0.1, None),
    ("Masala Chai", 400, "Drinks", 0.1, None),
    ("Sparkling Water", 350, "Drinks", 0.1, None),
    ("Gulab Jamun", 600, "Dessert", 0.1, None),
]
DEV_MANAGER_PIN = "1234"  # dev data only: approves voids and sold-out on the dev till
LUNCH_ONLY = ("Thali", 2600, "Mains", time(11), time(15))  # sold 11:00 to 15:00 local

MODIFIERS = """group,min,max,modifier,price,kitchen_name,items
Spice level,1,1,Mild,0,,Masala Dosa;Butter Chicken;Lamb Biryani
Spice level,,,Medium,0,,
Spice level,,,Hot,0,காரம்,
Extras,0,3,Extra butter,1.00,,Garlic Naan;Masala Dosa;Butter Chicken
Extras,,,Extra chutney,0.50,,
Extras,,,Add egg,2.00,,
"""


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
        name, cents, category, start, end = LUNCH_ONLY
        session.add(
            MenuItem(
                location_id=location.id,
                name=name,
                base_price_cents=cents,
                category=category,
                available_from=start,
                available_to=end,
                tax_rate=0.1,
            )
        )
        session.flush()
    import_modifiers(session, location.id, parse_modifier_csv(MODIFIERS))
    if not list_staff(session, location.id):
        create_staff(
            session, location_id=location.id, name="Dev Manager", role="manager", pin=DEV_MANAGER_PIN
        )
    # The ATO's worked-example ABN: passes the check, belongs to nobody real.
    update_venue(
        session,
        location.id,
        {"business_name": ORG_NAME, "abn": "51 824 753 556", "receipt_footer": "Dev data only"},
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
    print(f"  manager PIN  : {DEV_MANAGER_PIN}  (dev only: voids and sold-out)")
    print("\nStart the API on this database, open the web POS and enter the pairing code.\n")


if __name__ == "__main__":
    main()
