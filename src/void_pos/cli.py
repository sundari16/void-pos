"""Venue setup from the command line.

    void-pos create-location --org "Madras Curry House" --name "Brisbane CBD"
    void-pos add-device --location <id> --name "Till 1" --range 1000-1999
    void-pos pairing-code --device <id>
    void-pos import-menu --location <id> --file menu.csv [--dry-run]
    void-pos import-modifiers --location <id> --file modifiers.csv [--dry-run]
    void-pos import-square --location <id> [--dry-run]   (VOIDPOS_SQUARE_ACCESS_TOKEN)
    void-pos add-staff --location <id> --name "Asha" --role manager   (asks for the PIN)
    void-pos venue-details --location <id> --abn "51 824 753 556" --address "..."
    void-pos send-campaign --location <id> --channel sms --body "Hi {name}!" [--dry-run]
    void-pos aggregator-order --location <id> --file order.json [--reject] [--dry-run]
    void-pos aggregator-sync-menu --location <id> [--provider mock]
    void-pos delivery-dispatch --location <id> --order <id> --name "..." --phone "..." --address "..."
    void-pos delivery-status --delivery <id> --status picked_up
    void-pos roster-add --location <id> --staff <id> --start "2026-11-20 09:00" --end "..."
    void-pos clock-in --location <id> --staff <id>      (and clock-out --break 30)
    void-pos timesheet-build --location <id> --date 2026-11-20
    void-pos timesheet-approve --timesheet <id>         (asks for a manager's PIN)
    void-pos deputy-connect --location <id>             (VOIDPOS_DEPUTY_*)
    void-pos deputy-sync --location <id> [--dry-run]    (pull roster; --push sends timesheets)
    void-pos xero-connect --location <id>   (connect the venue's Xero org; VOIDPOS_XERO_*)
    void-pos xero-accounts --location <id> [--file map.json]   (account-code mapping)
    void-pos xero-push --location <id> --business-date 2026-11-15 [--dry-run]
    void-pos voice-order --location <id> --phone "+61400000000" \
        --item "Masala Dosa:2:Extra hot" --item "Mango Lassi"   (simulates an inbound call)
    void-pos print-test

Inside the Docker install, prefix with ``docker compose exec api``. The database must be
migrated first (the API container does that on start; locally run ``alembic upgrade head``).
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from .accounting import (
    XeroClient,
    account_map_for,
    build_authorize_url,
    build_takings,
    complete_connection,
    new_state,
    push_daily_takings,
    set_account_map,
)
from .aggregators import (
    build_provider as build_aggregator_provider,
)
from .aggregators import (
    ingest_order,
    reject_order,
    sync_menu_out,
)
from .clock import as_aware, local_date
from .config import settings
from .delivery import (
    DeliveryParty,
    dispatch_delivery,
    update_delivery_status,
)
from .delivery import (
    build_provider as build_delivery_provider,
)
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
from .ids import new_ulid
from .marketing import make_sender, send_campaign
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
from .voice import (
    DraftItem,
    DraftModifier,
    InboundCall,
    ScriptedVoiceAgent,
    VoiceOrderDraft,
    run_voice_call,
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


def _voice_item(spec: str) -> DraftItem:
    """Parse a ``--item`` spec ``NAME[:QTY[:MOD,MOD,...]]`` into a draft item."""
    name, _, rest = spec.partition(":")
    name = name.strip()
    if not name:
        raise VoidPosError(f"an --item needs a name: {spec!r}", code="invalid_voice_item")
    qty_text, _, mods_text = rest.partition(":")
    qty_text = qty_text.strip()
    try:
        quantity = int(qty_text) if qty_text else 1
    except ValueError as exc:
        raise VoidPosError(
            f"the quantity in {spec!r} isn't a number", code="invalid_voice_item"
        ) from exc
    if quantity < 1:
        raise VoidPosError(f"the quantity in {spec!r} must be 1 or more", code="invalid_voice_item")
    modifiers = [DraftModifier(name=m.strip()) for m in mods_text.split(",") if m.strip()]
    return DraftItem(name=name, quantity=quantity, modifiers=modifiers)


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

    connect = sub.add_parser(
        "xero-connect", help="connect this location to the venue's own Xero organisation"
    )
    connect.add_argument("--location", required=True)
    connect.add_argument(
        "--redirect-url",
        help="the URL Xero redirected to (with ?code=...); omit to be prompted for it",
    )

    accounts = sub.add_parser(
        "xero-accounts", help="set or show the Xero account-code mapping for a location"
    )
    accounts.add_argument("--location", required=True)
    accounts.add_argument(
        "--file", help="a JSON account map to store; omit to print the mapping in force"
    )

    push = sub.add_parser(
        "xero-push", help="push a day's takings to Xero (one invoice per business date)"
    )
    push.add_argument("--location", required=True)
    push.add_argument("--business-date", required=True, help="YYYY-MM-DD, the location's calendar")
    push.add_argument(
        "--dry-run", action="store_true", help="print the summary and Xero payload, send nothing"
    )

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

    camp = sub.add_parser(
        "send-campaign", help="send an SMS/email campaign to opted-in customers"
    )
    camp.add_argument("--location", required=True)
    camp.add_argument("--channel", choices=("sms", "email"), required=True)
    camp.add_argument("--body", help="the message; {name} is filled per customer")
    camp.add_argument("--file", help="read the body from a file (or - for standard input)")
    camp.add_argument("--subject", help="email subject line")
    camp.add_argument("--limit", type=int, help="cap the number of recipients")
    camp.add_argument("--dry-run", action="store_true", help="show the audience, send nothing")

    agg = sub.add_parser(
        "aggregator-order", help="simulate an incoming aggregator order (the mock provider)"
    )
    agg.add_argument("--location", required=True)
    agg.add_argument("--provider", default="mock", help="platform slug; the open core ships 'mock'")
    agg.add_argument("--file", required=True, help="a JSON order file, or - for standard input")
    agg.add_argument("--reject", action="store_true", help="decline the order instead of ingesting it")
    agg.add_argument("--reason", help="why it was rejected (with --reject)")
    agg.add_argument("--dry-run", action="store_true", help="do it, then roll back; show what would happen")

    sm = sub.add_parser(
        "aggregator-sync-menu", help="publish the venue's menu to an aggregator (the mock provider)"
    )
    sm.add_argument("--location", required=True)
    sm.add_argument("--provider", default="mock", help="platform slug; the open core ships 'mock'")

    disp = sub.add_parser(
        "delivery-dispatch", help="dispatch a courier for an own-channel delivery order"
    )
    disp.add_argument("--location", required=True)
    disp.add_argument("--order", required=True, help="the delivery order's id")
    disp.add_argument("--name", required=True, help="who the courier delivers to")
    disp.add_argument("--phone", required=True)
    disp.add_argument("--address", required=True, help="the dropoff address")
    disp.add_argument("--notes", help="dropoff instructions (buzzer, gate code, ...)")
    disp.add_argument("--provider", default="mock", help="courier provider (open core: mock)")
    disp.add_argument("--delivery-id", help="defaults to a fresh ULID")
    disp.add_argument("--order-value-cents", type=int, help="declared value; default: order total")

    dstat = sub.add_parser(
        "delivery-status", help="record a courier status callback (simulate the platform)"
    )
    dstat.add_argument("--delivery", required=True, help="the delivery id")
    dstat.add_argument(
        "--status",
        required=True,
        choices=("requested", "accepted", "picked_up", "delivered", "canceled", "failed"),
    )
    dstat.add_argument("--courier", help="the courier's name, once assigned")
    dstat.add_argument("--tracking", help="a live tracking URL")
    dstat.add_argument("--fee-cents", type=int, help="the courier fee, in cents")

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

    vo = sub.add_parser(
        "voice-order",
        help="simulate an inbound voice order end to end (no telephony; mocks the voice stack)",
    )
    vo.add_argument("--location", required=True, help="location id")
    vo.add_argument("--phone", required=True, help="the caller's phone number")
    vo.add_argument("--name", help="the caller's name")
    vo.add_argument(
        "--fulfillment", choices=("pickup", "delivery"), default="pickup"
    )
    vo.add_argument(
        "--item",
        required=True,
        action="append",
        metavar="NAME[:QTY[:MOD,MOD]]",
        help='an item the caller asked for; repeat for more, e.g. --item "Masala Dosa:2:Extra hot"',
    )
    vo.add_argument("--address", help="delivery address (for --fulfillment delivery)")
    vo.add_argument("--note", help="a note for the kitchen")
    vo.add_argument(
        "--decline",
        action="store_true",
        help="the caller says no at the read-back; nothing is placed",
    )
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
    if args.command == "xero-connect":
        return _xero_connect(session, args)
    if args.command == "xero-accounts":
        get_location(session, args.location)
        if not args.file:
            current = account_map_for(session, args.location).to_dict()
            return "Xero account map in force:\n" + json.dumps(current, indent=2)
        data = json.loads(_read_text(args.file))
        if not isinstance(data, dict):
            raise VoidPosError("the account map must be a JSON object", code="xero_bad_account_map")
        conn = set_account_map(session, args.location, data)
        return f"Stored the Xero account map for {conn.tenant_name or args.location}."
    if args.command == "xero-push":
        get_location(session, args.location)
        push_date = _business_date(args.business_date)
        if args.dry_run:
            takings = build_takings(session, args.location, push_date)
            return "\n".join(
                takings.summary_lines()
                + ["", "Xero invoice payload:", json.dumps(takings.invoice_payload(), indent=2)]
            )
        if not settings.xero_enabled:
            raise VoidPosError(
                "set VOIDPOS_XERO_CLIENT_ID and VOIDPOS_XERO_CLIENT_SECRET in .env first",
                code="xero_not_set_up",
            )
        invoice = push_daily_takings(
            session, args.location, push_date, client=_xero_client()
        )
        verb = "Created" if invoice["created"] else "Updated"
        xero_lines = [
            f"{verb} Xero invoice {invoice['invoice_number']} ({invoice['invoice_id']})",
            f"  Total ${invoice['total_cents'] / 100:.2f} incl. GST ${invoice['tax_cents'] / 100:.2f}",
        ]
        xero_lines += [
            f"  Paid ${p['amount_cents'] / 100:.2f} to {p['account_code']} ({p['provider']})"
            for p in invoice["payments"]
        ]
        xero_lines += [f"  ! {note}" for note in invoice["issues"]]
        return "\n".join(xero_lines)
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
    if args.command == "delivery-dispatch":
        get_location(session, args.location)
        outcome = dispatch_delivery(
            session,
            args.location,
            args.order,
            build_delivery_provider(args.provider),
            delivery_id=args.delivery_id or new_ulid(),
            dropoff=DeliveryParty(
                name=args.name, phone=args.phone, address=args.address, notes=args.notes
            ),
            order_value_cents=args.order_value_cents,
        )
        d = outcome.delivery
        fee = f"{d.fee_cents} cents" if d.fee_cents is not None else "—"
        return (
            f"Dispatched delivery {d.id}: {d.status} via {d.provider} "
            f"(courier ref {d.external_ref or '—'}, fee {fee})\n"
            f"  Track: {d.tracking_url or '—'}"
        )
    if args.command == "delivery-status":
        outcome = update_delivery_status(
            session,
            args.delivery,
            status=args.status,
            courier_name=args.courier,
            tracking_url=args.tracking,
            fee_cents=args.fee_cents,
        )
        return f"Delivery {outcome.delivery.id} is now {outcome.delivery.status}"
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
    if args.command == "voice-order":
        get_location(session, args.location)
        draft = VoiceOrderDraft(
            items=[_voice_item(spec) for spec in args.item],
            customer_phone=args.phone,
            fulfillment=args.fulfillment,
            customer_name=args.name,
            note=args.note,
            delivery_address=args.address,
            order_id=new_ulid(),
        )
        agent = ScriptedVoiceAgent(draft, accept=not args.decline)
        call = InboundCall(location_id=args.location, from_number=args.phone)
        call_result = run_voice_call(session, agent, call)
        lines = [f"Read-back: {call_result.confirmation.read_back}"]
        if call_result.placed and call_result.order is not None:
            order = call_result.order
            lines.append(
                f"Placed voice order #{order.order_number} "
                f"({order.state}) on the {order.channel} channel."
            )
            lines.append(f"Order id: {order.id}")
        else:
            lines.append("The caller declined at the read-back; nothing was placed.")
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
    if args.command == "send-campaign":
        get_location(session, args.location)
        if args.body and args.file:
            raise VoidPosError("give --body or --file, not both", code="invalid_args")
        body = _read_text(args.file) if args.file else args.body
        if not body or not body.strip():
            raise VoidPosError("the campaign needs a message (--body or --file)", code="empty_body")
        sender = make_sender(settings, args.channel)
        campaign = send_campaign(
            session,
            location_id=args.location,
            channel=args.channel,
            body=body.strip(),
            subject=args.subject,
            sender=sender,
            limit=args.limit,
            dry_run=args.dry_run,
        )
        verb = "Would reach" if args.dry_run else "Reached"
        return (
            f"{verb} {campaign.total} opted-in {args.channel} customer(s): "
            f"{campaign.sent} sent, {campaign.failed} failed, {campaign.skipped} skipped."
        )
    if args.command == "aggregator-order":
        get_location(session, args.location)
        provider = build_aggregator_provider(args.provider)
        try:
            raw = json.loads(_read_text(args.file))
        except json.JSONDecodeError as exc:
            raise VoidPosError(
                f"the order file isn't valid JSON: {exc}", code="invalid_aggregator_order"
            ) from exc
        if not isinstance(raw, dict):
            raise VoidPosError(
                "the order file must be a JSON object", code="invalid_aggregator_order"
            )
        if args.reject:
            rejected = reject_order(
                session, provider, args.location, raw,
                reason=args.reason or "the venue can't make this order",
            )
            verb = "Would reject" if args.dry_run else "Rejected"
            again = " (already handled)" if rejected.replayed else ""
            return f"{verb} {provider.name} order {rejected.tie.external_order_id}{again}: {rejected.tie.reason}"
        ingested = ingest_order(session, provider, args.location, raw)
        order = ingested.order
        verb = "Would ingest" if args.dry_run else "Ingested"
        again = " (already handled)" if ingested.replayed else ""
        return (
            f"{verb} {provider.name} order {ingested.tie.external_order_id}{again} as "
            f"order #{order.order_number} ({order.state}) on the {order.channel} channel."
        )
    if args.command == "aggregator-sync-menu":
        get_location(session, args.location)
        provider = build_aggregator_provider(args.provider)
        published = sync_menu_out(session, provider, args.location)
        return (
            f"{published.status.capitalize()} {published.item_count} items to "
            f"{published.provider} (ref {published.external_ref})."
        )
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


def _xero_client() -> XeroClient:
    assert settings.xero_client_id is not None and settings.xero_client_secret is not None
    return XeroClient(
        client_id=settings.xero_client_id.get_secret_value(),
        client_secret=settings.xero_client_secret.get_secret_value(),
        redirect_uri=settings.xero_redirect_uri,
    )


def _code_and_state(redirect: str) -> tuple[str, str | None]:
    """The ``code`` (and ``state`` if present) from a pasted Xero redirect URL, or the code
    itself if just the code was pasted."""
    text = redirect.strip()
    query = parse_qs(urlparse(text).query)
    if "code" in query:
        state = query.get("state", [None])[0]
        return query["code"][0], state
    if text and "=" not in text and "/" not in text:
        return text, None
    raise VoidPosError("couldn't find a code in that redirect URL", code="xero_no_code")


def _xero_connect(session: Session, args: argparse.Namespace) -> str:
    get_location(session, args.location)
    if not settings.xero_enabled:
        raise VoidPosError(
            "set VOIDPOS_XERO_CLIENT_ID and VOIDPOS_XERO_CLIENT_SECRET in .env first",
            code="xero_not_set_up",
        )
    client = _xero_client()
    state = new_state()
    if args.redirect_url:
        code, _ = _code_and_state(args.redirect_url)
    else:
        url = build_authorize_url(settings, state=state)
        print(
            "Open this URL, sign in to Xero and authorise the organisation:\n\n"
            f"  {url}\n\nThen paste the full URL you were redirected to and press Enter:",
            file=sys.stderr,
        )
        code, got_state = _code_and_state(sys.stdin.readline())
        if got_state is not None and got_state != state:
            raise VoidPosError(
                "the redirect's state didn't match; start the connect again",
                code="xero_state_mismatch",
            )
    conn = complete_connection(session, args.location, client=client, code=code)
    return f"Connected {conn.tenant_name or conn.tenant_id} to this location."


def _business_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise VoidPosError(
            f"--business-date must be YYYY-MM-DD, not {value!r}", code="invalid_business_date"
        ) from exc


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
