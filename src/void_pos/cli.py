"""Venue setup from the command line.

    void-pos create-location --org "Madras Curry House" --name "Brisbane CBD"
    void-pos add-device --location <id> --name "Till 1" --range 1000-1999
    void-pos pairing-code --device <id>

Inside the Docker install, prefix with ``docker compose exec api``. The database must be
migrated first (the API container does that on start; locally run ``alembic upgrade head``).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from .config import settings
from .devices import (
    PairingCode,
    create_location,
    get_device,
    get_location,
    issue_pairing_code,
    list_devices,
    list_locations,
    register_device,
    revoke_device,
)
from .errors import VoidPosError
from .models import Device


def _range(text: str) -> tuple[int, int]:
    start, sep, end = text.partition("-")
    try:
        if not sep:
            raise ValueError(text)
        return int(start), int(end)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use START-END, for example 1000-1999") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="void-pos", description="Void POS venue setup")
    parser.add_argument("--database-url", help="defaults to VOIDPOS_DATABASE_URL")
    sub = parser.add_subparsers(dest="command", required=True)

    loc = sub.add_parser("create-location", help="create a location (and its org)")
    loc.add_argument("--org", required=True, help="organisation name; reused if it exists")
    loc.add_argument("--name", required=True)
    loc.add_argument("--timezone", default="Australia/Brisbane")

    sub.add_parser("list-locations", help="list every location")

    dev = sub.add_parser("add-device", help="register a till and print its pairing code")
    dev.add_argument("--location", required=True, help="location id")
    dev.add_argument("--name", required=True)
    dev.add_argument(
        "--range", required=True, type=_range, help="its order numbers, e.g. 1000-1999"
    )

    ls = sub.add_parser("list-devices", help="list the tills at a location")
    ls.add_argument("--location", required=True)

    code = sub.add_parser("pairing-code", help="issue a new pairing code for a till")
    code.add_argument("--device", required=True)

    rev = sub.add_parser("revoke-device", help="cut a till off immediately")
    rev.add_argument("--device", required=True)
    return parser


def _expiry(session: Session, device: Device, code: PairingCode) -> str:
    tz = get_location(session, device.location_id).timezone
    return code.expires_at.astimezone(ZoneInfo(tz)).strftime("%H:%M %Z")


def _device_line(device: Device) -> str:
    if device.revoked_at:
        state = "revoked"
    elif device.token_hash:
        state = "paired"
    else:
        state = "not paired"
    return (
        f"{device.id}  {device.name}  #{device.order_number_start}-{device.order_number_end}  "
        f"{state}  last seq {device.last_seq}"
    )


def run(session: Session, args: argparse.Namespace, *, pairing_ttl: timedelta) -> str:
    if args.command == "create-location":
        location = create_location(
            session, org_name=args.org, name=args.name, timezone=args.timezone
        )
        return f"Created location {location.name}: {location.id}"
    if args.command == "list-locations":
        rows = [f"{loc.id}  {loc.name}  {loc.timezone}" for loc in list_locations(session)]
        return "\n".join(rows) or "No locations yet."
    if args.command == "add-device":
        start, end = args.range
        device, code = register_device(
            session,
            location_id=args.location,
            name=args.name,
            order_number_start=start,
            order_number_end=end,
            pairing_ttl=pairing_ttl,
        )
        return (
            f"Registered {device.name}: {device.id}\n"
            f"Pairing code: {code.code} (expires {_expiry(session, device, code)})"
        )
    if args.command == "list-devices":
        rows = [_device_line(d) for d in list_devices(session, args.location)]
        return "\n".join(rows) or "No tills at this location yet."
    if args.command == "pairing-code":
        device = get_device(session, args.device)
        code = issue_pairing_code(session, device, ttl=pairing_ttl)
        return f"Pairing code for {device.name}: {code.code} (expires {_expiry(session, device, code)})"
    if args.command == "revoke-device":
        device = revoke_device(session, args.device)
        return f"Revoked {device.name} at {device.revoked_at:%Y-%m-%d %H:%M} UTC"
    raise AssertionError(args.command)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    engine = create_engine(args.database_url or settings.database_url)
    try:
        with Session(engine) as session:
            output = run(
                session, args, pairing_ttl=timedelta(minutes=settings.pairing_code_ttl_minutes)
            )
            session.commit()
    except VoidPosError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
