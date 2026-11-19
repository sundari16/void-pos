"""Venue setup from the command line.

    void-pos create-location --org "Madras Curry House" --name "Brisbane CBD"
    void-pos add-device --location <id> --name "Till 1" --range 1000-1999
    void-pos pairing-code --device <id>
    void-pos import-menu --location <id> --file menu.csv [--dry-run]
    void-pos import-modifiers --location <id> --file modifiers.csv [--dry-run]
    void-pos import-square --location <id> [--dry-run]   (VOIDPOS_SQUARE_ACCESS_TOKEN)
    void-pos add-staff --location <id> --name "Asha" --role manager   (asks for the PIN)
    void-pos venue-details --location <id> --abn "51 824 753 556" --address "..."
    void-pos roster-add --location <id> --staff <id> --start "2026-11-20 09:00" --end "..."
    void-pos clock-in --location <id> --staff <id>      (and clock-out --break 30)
    void-pos timesheet-build --location <id> --date 2026-11-20
    void-pos timesheet-approve --timesheet <id>         (asks for a manager's PIN)
    void-pos deputy-connect --location <id>             (VOIDPOS_DEPUTY_*)
    void-pos deputy-sync --location <id> [--dry-run]    (pull roster; --push sends timesheets)
    void-pos print-test

Inside the Docker install, prefix with ``docker compose exec api``. The database must be
migrated first (the API container does that on start; locally run ``alembic upgrade head``).
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from .clock import as_aware, local_date
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
from .menu.square import SquareCatalog, translate
from .models import Device
from .printing import (
    KitchenTicket,
    NetworkPrinter,
    PillowRasterizer,
    TicketLine,
    encode_ticket,
    needs_image,
)
from .receipts import format_abn, update_venue
from .rostering import (
    DeputyClient,
    approve_timesheet,
    build_timesheets_for_date,
    clock_in,
    clock_out,
    list_shifts,
    list_timesheets,
    match_employees,
    pull_roster,
    push_timesheets,
    schedule_shift,
)
from .staff import create_staff, deactivate_staff, get_staff, list_staff

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

    square = sub.add_parser(
        "import-square", help="bring the menu over from Square (items, prices, GST, modifiers)"
    )
    square.add_argument("--location", required=True)
    square.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")

    venue = sub.add_parser("venue-details", help="set what customer receipts show")
    venue.add_argument("--location", required=True)
    venue.add_argument("--business-name", help="defaults to the location's name")
    venue.add_argument("--abn", help="11 digits; receipts become tax invoices")
    venue.add_argument("--address")
    venue.add_argument("--phone")
    venue.add_argument("--footer", help="a line at the bottom, e.g. Thank you!")

    staff = sub.add_parser(
        "add-staff", help="add a staff member; the PIN is asked for (or read from standard input)"
    )
    staff.add_argument("--location", required=True)
    staff.add_argument("--name", required=True)
    staff.add_argument("--role", choices=("staff", "manager"), default="staff")

    who = sub.add_parser("list-staff", help="list a location's active staff")
    who.add_argument("--location", required=True)

    off = sub.add_parser("deactivate-staff", help="stop a staff member's PIN working")
    off.add_argument("--staff", required=True, help="staff id")

    mods = sub.add_parser(
        "import-modifiers", help="create or update modifier groups from a CSV file"
    )
    mods.add_argument("--location", required=True)
    mods.add_argument("--file", required=True, help="a UTF-8 CSV file, or - for standard input")
    mods.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")

    radd = sub.add_parser("roster-add", help="roster a staff member on for a shift")
    radd.add_argument("--location", required=True)
    radd.add_argument("--staff", required=True, help="staff id")
    radd.add_argument("--start", required=True, help="local time, YYYY-MM-DD HH:MM")
    radd.add_argument("--end", required=True, help="local time, YYYY-MM-DD HH:MM")
    radd.add_argument("--role", help="what they're on, e.g. kitchen or front")

    rls = sub.add_parser("roster-list", help="list rostered shifts")
    rls.add_argument("--location", required=True)
    rls.add_argument("--date", help="a business date, YYYY-MM-DD (default: all)")

    cin = sub.add_parser("clock-in", help="clock a staff member on")
    cin.add_argument("--location", required=True)
    cin.add_argument("--staff", required=True, help="staff id")
    cin.add_argument("--at", help="local time, YYYY-MM-DD HH:MM (default: now)")

    cout = sub.add_parser("clock-out", help="clock a staff member off")
    cout.add_argument("--location", required=True)
    cout.add_argument("--staff", required=True, help="staff id")
    cout.add_argument("--at", help="local time, YYYY-MM-DD HH:MM (default: now)")
    cout.add_argument(
        "--break", dest="break_minutes", type=int, default=0, help="unpaid break minutes"
    )

    tsb = sub.add_parser(
        "timesheet-build", help="build draft timesheets for a day from roster + clock data"
    )
    tsb.add_argument("--location", required=True)
    tsb.add_argument("--date", help="a business date, YYYY-MM-DD (default: today)")

    tsa = sub.add_parser(
        "timesheet-approve", help="approve a timesheet (asks for a manager's PIN)"
    )
    tsa.add_argument("--timesheet", required=True, help="timesheet id")

    tsl = sub.add_parser("timesheet-list", help="list timesheets")
    tsl.add_argument("--location", required=True)
    tsl.add_argument("--date", help="a business date, YYYY-MM-DD (default: all)")
    tsl.add_argument("--status", choices=("draft", "approved"), help="only this status")

    dcon = sub.add_parser(
        "deputy-connect", help="check the Deputy token and show how employees match staff"
    )
    dcon.add_argument("--location", required=True)

    dsync = sub.add_parser(
        "deputy-sync",
        help="pull the Deputy roster in (default), or push approved timesheets out (--push)",
    )
    dsync.add_argument("--location", required=True)
    dsync.add_argument("--push", action="store_true", help="push approved timesheets instead of pulling")
    dsync.add_argument("--from", dest="date_from", help="pull: first business date, YYYY-MM-DD")
    dsync.add_argument("--to", dest="date_to", help="pull: last business date, YYYY-MM-DD")
    dsync.add_argument("--date", help="push: only this business date, YYYY-MM-DD (default: all)")
    dsync.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")

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
    if args.command == "venue-details":
        given = {
            "business_name": args.business_name,
            "abn": args.abn,
            "address": args.address,
            "phone": args.phone,
            "receipt_footer": args.footer,
        }
        loc = update_venue(
            session, args.location, {k: v for k, v in given.items() if v is not None}
        )
        abn = format_abn(loc.abn) if loc.abn else "none (plain receipts)"
        return (
            f"{loc.business_name or loc.name}\n  ABN: {abn}\n  Address: {loc.address or '-'}\n"
            f"  Phone: {loc.phone or '-'}\n  Footer: {loc.receipt_footer or '-'}"
        )
    if args.command == "add-staff":
        get_location(session, args.location)
        person = create_staff(
            session, location_id=args.location, name=args.name, role=args.role, pin=_read_pin()
        )
        return f"Added {person.role} {person.name}: {person.id}"
    if args.command == "list-staff":
        get_location(session, args.location)
        people = list_staff(session, args.location)
        return "\n".join(f"{p.id}  {p.role:<8} {p.name}" for p in people) or "No staff yet."
    if args.command == "deactivate-staff":
        person = deactivate_staff(session, args.staff)
        return f"Deactivated {person.name}; their PIN no longer works"
    if args.command == "import-square":
        get_location(session, args.location)
        if settings.square_access_token is None:
            raise VoidPosError(
                "set VOIDPOS_SQUARE_ACCESS_TOKEN in .env first", code="square_not_set_up"
            )
        catalog = SquareCatalog(settings.square_access_token.get_secret_value())
        menu = translate(list(catalog.objects()))
        if not menu.items:
            raise VoidPosError("Square returned no priced items", code="square_empty")
        # Groups attach to items by name, so the items go in first; a dry run rolls the
        # whole import back at the end.
        items = import_menu(session, args.location, menu.items)
        loaded = import_modifiers(session, args.location, menu.groups)
        verb = "Would" if args.dry_run else "Did"
        lines = [
            (f"{verb} create {len(items.created)} items, update {len(items.updated)}, "
            f"leave {len(items.unchanged)} unchanged."),
            (f"{verb} create {len(loaded.created)} modifier groups and options, update "
            f"{len(loaded.updated)}, attach {len(loaded.attached)}."),
        ]
        lines += [f"  + {name}" for name in items.created + loaded.created]
        lines += [f"  ~ {name}" for name in items.updated + loaded.updated]
        lines += [f"  ! {note}" for note in menu.notes]
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
    if args.command == "roster-add":
        loc = get_location(session, args.location)
        shift = schedule_shift(
            session,
            location_id=args.location,
            staff_id=args.staff,
            starts_at=_parse_local_dt(args.start, loc.timezone),
            ends_at=_parse_local_dt(args.end, loc.timezone),
            role=args.role,
        )
        return (
            f"Rostered {get_staff(session, shift.staff_id).name} on {shift.business_date}: "
            f"{_fmt_time(shift.starts_at, loc.timezone)}–{_fmt_time(shift.ends_at, loc.timezone)}"
            f"{f' ({shift.role})' if shift.role else ''}  {shift.id}"
        )
    if args.command == "roster-list":
        loc = get_location(session, args.location)
        business_date = _parse_date(args.date) if args.date else None
        names = _staff_names(session, args.location)
        rows = [
            f"{s.business_date}  {names.get(s.staff_id, s.staff_id):<16} "
            f"{_fmt_time(s.starts_at, loc.timezone)}–{_fmt_time(s.ends_at, loc.timezone)}  "
            f"{(s.role or ''):<8} [{s.source}]"
            for s in list_shifts(session, args.location, business_date=business_date)
        ]
        return "\n".join(rows) or "No shifts."
    if args.command == "clock-in":
        loc = get_location(session, args.location)
        at = _parse_local_dt(args.at, loc.timezone) if args.at else None
        entry = clock_in(session, location_id=args.location, staff_id=args.staff, at=at)
        return (
            f"{get_staff(session, entry.staff_id).name} clocked on at "
            f"{_fmt_time(entry.clock_in_at, loc.timezone)} ({entry.business_date})"
        )
    if args.command == "clock-out":
        loc = get_location(session, args.location)
        at = _parse_local_dt(args.at, loc.timezone) if args.at else None
        entry = clock_out(
            session,
            location_id=args.location,
            staff_id=args.staff,
            at=at,
            break_minutes=args.break_minutes,
        )
        out_at = entry.clock_out_at or entry.clock_in_at  # clock_out always sets it
        worked = _hours(_span_minutes(entry.clock_in_at, out_at) - entry.break_minutes)
        return (
            f"{get_staff(session, entry.staff_id).name} clocked off at "
            f"{_fmt_time(out_at, loc.timezone)}; worked {worked}"
            f"{f' (break {entry.break_minutes}m)' if entry.break_minutes else ''}"
        )
    if args.command == "timesheet-build":
        loc = get_location(session, args.location)
        business_date = _parse_date(args.date) if args.date else local_date(loc.timezone)
        sheets = build_timesheets_for_date(session, args.location, business_date)
        if not sheets:
            return f"No roster or clock data for {business_date}."
        names = _staff_names(session, args.location)
        lines = [f"Built {len(sheets)} timesheet(s) for {business_date}:"]
        lines += [
            f"  {names.get(s.staff_id, s.staff_id):<16} sched {_hours(s.scheduled_minutes)}  "
            f"worked {_hours(s.worked_minutes)}  paid {_hours(s.paid_minutes)}  [{s.status}]"
            for s in sheets
        ]
        return "\n".join(lines)
    if args.command == "timesheet-approve":
        sheet = approve_timesheet(session, args.timesheet, manager_pin=_read_manager_pin())
        names = _staff_names(session, sheet.location_id)
        approver = names.get(sheet.approved_by_staff_id or "", "?")
        return (
            f"Approved {names.get(sheet.staff_id, sheet.staff_id)} for {sheet.business_date}: "
            f"paid {_hours(sheet.paid_minutes)} (approved by {approver})"
        )
    if args.command == "timesheet-list":
        get_location(session, args.location)
        business_date = _parse_date(args.date) if args.date else None
        names = _staff_names(session, args.location)
        rows = [
            f"{s.id}  {s.business_date}  {names.get(s.staff_id, s.staff_id):<16} "
            f"paid {_hours(s.paid_minutes)}  [{s.status}]{'  synced→Deputy' if s.synced_at else ''}"
            for s in list_timesheets(session, args.location, business_date=business_date, status=args.status)
        ]
        return "\n".join(rows) or "No timesheets."
    if args.command == "deputy-connect":
        get_location(session, args.location)
        client = _deputy_client()
        employees = client.employees()
        deputy_names = [
            (e.get("DisplayName") or "").strip() for e in employees if (e.get("DisplayName") or "").strip()
        ]
        report = match_employees(deputy_names, list_staff(session, args.location))
        return (
            f"Connected to Deputy: {len(employees)} employee(s).\n"
            f"  matched {len(report.matched)}: {', '.join(report.matched) or '-'}\n"
            f"  in Deputy only: {', '.join(report.deputy_only) or '-'}\n"
            f"  Void POS only: {', '.join(report.void_only) or '-'}"
        )
    if args.command == "deputy-sync":
        loc = get_location(session, args.location)
        client = _deputy_client()
        staff = list_staff(session, args.location)
        verb = "Would" if args.dry_run else "Did"
        if args.push:
            business_date = _parse_date(args.date) if args.date else None
            pushed = push_timesheets(
                session, args.location, client, business_date=business_date,
                active_staff=staff, dry_run=args.dry_run,
            )
            lines = [f"{verb} push {len(pushed.pushed)} approved timesheet(s) to Deputy."]
            lines += [f"  → {label}" for label in pushed.pushed]
            lines += [f"  ! {note}" for note in pushed.skipped]
            return "\n".join(lines)
        from_date = _parse_date(args.date_from) if args.date_from else local_date(loc.timezone)
        to_date = _parse_date(args.date_to) if args.date_to else from_date + timedelta(days=7)
        start, end = _local_day_bounds(from_date, to_date, loc.timezone)
        pulled = pull_roster(
            session, args.location, client, start=start, end=end, active_staff=staff
        )
        lines = [
            (
                f"{verb} pull the roster for {from_date}..{to_date}: "
                f"create {len(pulled.created)}, update {len(pulled.updated)}."
            )
        ]
        lines += [f"  + {label}" for label in pulled.created]
        lines += [f"  ~ {label}" for label in pulled.updated]
        lines += [f"  ! {note}" for note in pulled.notes]
        return "\n".join(lines)
    raise AssertionError(args.command)


def _read_pin() -> str:
    """The PIN from a prompt (typed twice, not echoed), or one line of standard input."""
    if not sys.stdin.isatty():
        return sys.stdin.readline().strip()
    pin = getpass.getpass("PIN (4 to 8 digits): ")
    if getpass.getpass("Same PIN again: ") != pin:
        raise VoidPosError("the two PINs don't match", code="invalid_pin")
    return pin


def _read_manager_pin() -> str:
    """A manager's PIN to approve a timesheet: a prompt (not echoed) or one line of stdin."""
    if not sys.stdin.isatty():
        return sys.stdin.readline().strip()
    return getpass.getpass("Manager PIN: ")


def _parse_local_dt(text: str, tz: str) -> datetime:
    """A local wall-clock time (YYYY-MM-DD HH:MM in the venue's timezone) as UTC."""
    try:
        naive = datetime.strptime(text.strip(), "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise VoidPosError(
            f"use YYYY-MM-DD HH:MM, e.g. 2026-11-20 09:00 (got {text!r})", code="invalid_datetime"
        ) from exc
    return naive.replace(tzinfo=ZoneInfo(tz)).astimezone(UTC)


def _parse_date(text: str) -> date:
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise VoidPosError(f"use a date, YYYY-MM-DD (got {text!r})", code="invalid_date") from exc


def _fmt_time(value: datetime, tz: str) -> str:
    return as_aware(value).astimezone(ZoneInfo(tz)).strftime("%H:%M")


def _span_minutes(start: datetime, end: datetime) -> int:
    return int((as_aware(end) - as_aware(start)).total_seconds() // 60)


def _hours(minutes: int) -> str:
    return f"{minutes / 60:.2f}h"


def _staff_names(session: Session, location_id: str) -> dict[str, str]:
    """id -> name for a location's staff, including deactivated ones, so names resolve."""
    return {
        p.id: p.name for p in list_staff(session, location_id, include_inactive=True)
    }


def _local_day_bounds(from_date: date, to_date: date, tz: str) -> tuple[datetime, datetime]:
    """The UTC instants bounding the local days ``from_date``..``to_date`` inclusive."""
    zone = ZoneInfo(tz)
    start = datetime.combine(from_date, time.min, tzinfo=zone).astimezone(UTC)
    end = datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
    return start, end


def _deputy_client() -> DeputyClient:
    endpoint = settings.deputy_endpoint
    token = settings.deputy_access_token
    if endpoint is None or token is None:
        raise VoidPosError(
            "set VOIDPOS_DEPUTY_ENDPOINT and VOIDPOS_DEPUTY_ACCESS_TOKEN in .env first",
            code="deputy_not_set_up",
        )
    return DeputyClient(endpoint, token.get_secret_value())


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
