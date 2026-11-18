"""Venue setup from the command line.

    void-pos create-location --org "Madras Curry House" --name "Brisbane CBD"
    void-pos add-device --location <id> --name "Till 1" --range 1000-1999
    void-pos pairing-code --device <id>
    void-pos import-menu --location <id> --file menu.csv [--dry-run]
    void-pos import-modifiers --location <id> --file modifiers.csv [--dry-run]
    void-pos print-test

Inside the Docker install, prefix with ``docker compose exec api``. The database must be
migrated first (the API container does that on start; locally run ``alembic upgrade head``).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from .clock import local_date
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
from .menu.importer import import_menu, parse_menu_csv
from .menu.modifier_import import import_modifiers, parse_modifier_csv
from .models import Device
from .printing import (
    KitchenTicket,
    NetworkPrinter,
    PillowRasterizer,
    TicketLine,
    encode_ticket,
    needs_image,
)

TEST_LINES = ("Print test", "மசாலா தோசை")  # Masala Dosa in Tamil: checks the font path


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

    imp = sub.add_parser("import-menu", help="create or update menu items from a CSV file")
    imp.add_argument("--location", required=True)
    imp.add_argument("--file", required=True, help="a UTF-8 CSV file, or - for standard input")
    imp.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")

    mods = sub.add_parser(
        "import-modifiers", help="create or update modifier groups from a CSV file"
    )
    mods.add_argument("--location", required=True)
    mods.add_argument("--file", required=True, help="a UTF-8 CSV file, or - for standard input")
    mods.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")

    pt = sub.add_parser("print-test", help="send a test ticket to the kitchen printer")
    pt.add_argument("--host", help="defaults to VOIDPOS_KITCHEN_PRINTER_HOST")
    pt.add_argument("--port", type=int, help="defaults to VOIDPOS_KITCHEN_PRINTER_PORT (9100)")
    pt.add_argument("--font", help="defaults to VOIDPOS_KITCHEN_FONT_PATH")
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
    if args.command == "import-menu":
        get_location(session, args.location)
        menu_rows = parse_menu_csv(_read_text(args.file))
        result = import_menu(session, args.location, menu_rows, dry_run=args.dry_run)
        verb = "Would" if args.dry_run else "Did"
        summary = (
            f"{verb} create {len(result.created)}, update {len(result.updated)}, "
            f"leave {len(result.unchanged)} unchanged."
        )
        lines = [summary]
        lines += [f"  + {name}" for name in result.created]
        lines += [f"  ~ {name}" for name in result.updated]
        return "\n".join(lines)
    if args.command == "import-modifiers":
        get_location(session, args.location)
        groups = parse_modifier_csv(_read_text(args.file))
        mod_result = import_modifiers(session, args.location, groups, dry_run=args.dry_run)
        verb = "Would" if args.dry_run else "Did"
        summary = (
            f"{verb} create {len(mod_result.created)}, update {len(mod_result.updated)}, "
            f"attach {len(mod_result.attached)}, leave {len(mod_result.unchanged)} unchanged."
        )
        lines = [summary]
        lines += [f"  + {name}" for name in mod_result.created]
        lines += [f"  ~ {name}" for name in mod_result.updated]
        lines += [f"  = {link}" for link in mod_result.attached]
        return "\n".join(lines)
    raise AssertionError(args.command)


def _read_text(path: str) -> str:
    raw = sys.stdin.buffer.read() if path == "-" else Path(path).read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise VoidPosError(
            "the file isn't UTF-8; in Excel use Save As > CSV UTF-8", code="invalid_menu_file"
        ) from exc


def print_test(args: argparse.Namespace) -> str:
    """Send a test ticket (with a Tamil line) to the kitchen printer, without the database."""
    host = args.host or settings.kitchen_printer_host
    if not host:
        raise VoidPosError("set VOIDPOS_KITCHEN_PRINTER_HOST or pass --host", code="no_printer")
    port = args.port or settings.kitchen_printer_port
    font = args.font or settings.kitchen_font_path
    ticket = KitchenTicket(
        order_number=0,
        channel="test",
        fulfillment="print test",
        business_date=local_date("Australia/Brisbane"),
        lines=[TicketLine(text=text, quantity=1, is_modifier=False) for text in TEST_LINES],
    )
    data = encode_ticket(ticket, rasterizer=PillowRasterizer(font) if font else None)
    NetworkPrinter(host, port).send(data)
    note = ""
    if not font and any(needs_image(text) for text in TEST_LINES):
        note = (
            "\nNo font set: the Tamil line went as plain text and may print as garbage. "
            "Set VOIDPOS_KITCHEN_FONT_PATH to a font with Tamil glyphs."
        )
    return f"Sent a {len(data)}-byte test ticket to {host}:{port}.{note}"


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output = print_test(args) if args.command == "print-test" else _with_database(args)
    except VoidPosError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 1
    print(output)
    return 0


def _with_database(args: argparse.Namespace) -> str:
    engine = create_engine(args.database_url or settings.database_url)
    try:
        with Session(engine) as session:
            output = run(
                session, args, pairing_ttl=timedelta(minutes=settings.pairing_code_ttl_minutes)
            )
            if getattr(args, "dry_run", False):
                session.rollback()
            else:
                session.commit()
        return output
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
