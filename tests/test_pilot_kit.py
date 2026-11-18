"""Venue setup tools for a pilot: menu import from a spreadsheet, and a printer test."""

from __future__ import annotations

import io
import socket
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from support import make_engine
from void_pos.cli import main
from void_pos.devices import create_location
from void_pos.errors import InvalidRequest
from void_pos.menu import list_changes, list_items
from void_pos.menu.importer import import_menu, parse_menu_csv
from void_pos.models import Base

SHEET = """﻿name,price,category,kitchen_name,tax_rate,is_available
Masala Dosa,18.50,Mains,மசாலா தோசை,10%,yes
Garlic Naan,$4.50,Sides,,0.1,
Mango Lassi,6.5,Drinks,,,no
,,,,,
"""


def test_a_spreadsheet_parses_in_dollars_with_tamil_names() -> None:
    rows = parse_menu_csv(SHEET)
    assert [(r.name, r.base_price_cents, r.tax_rate, r.is_available) for r in rows] == [
        ("Masala Dosa", 1850, 0.1, True),
        ("Garlic Naan", 450, 0.1, True),
        ("Mango Lassi", 650, 0.0, False),
    ]
    assert rows[0].kitchen_name == "மசாலா தோசை"
    assert rows[1].category == "Sides" and rows[1].kitchen_name is None


def test_the_export_format_imports_too() -> None:
    exported = "id,name,kitchen_name,category,base_price_cents,tax_rate,is_available\nX,Chai,,Drinks,400,0.0,True\n"
    assert parse_menu_csv(exported)[0].base_price_cents == 400


def test_every_bad_row_is_reported_and_nothing_is_parsed() -> None:
    bad = "name,price\nChai,4.005\n,3\nChai,4\nLassi,abc\n"
    with pytest.raises(InvalidRequest) as exc:
        parse_menu_csv(bad)
    assert exc.value.extra["problems"] == [
        "row 2: price '4.005' has fractions of a cent",
        "row 3: name is missing",
        "row 4: 'Chai' is also on row 2",
        "row 5: price 'abc' isn't a number",
    ]


def test_a_sheet_without_the_needed_columns_is_refused() -> None:
    with pytest.raises(InvalidRequest, match="name column and a price"):
        parse_menu_csv("item,cost\nChai,4\n")


@pytest.fixture
def location() -> Iterator[tuple[Session, str]]:
    with Session(make_engine()) as s:
        loc = create_location(s, org_name="Madras Curry House", name="Brisbane CBD")
        s.commit()
        yield s, loc.id


def test_import_creates_then_updates_by_name(location: tuple[Session, str]) -> None:
    s, loc = location
    first = import_menu(s, loc, parse_menu_csv(SHEET))
    assert (len(first.created), first.updated) == (3, [])

    repriced = SHEET.replace("18.50", "19.00").replace("Mango Lassi", "mango lassi")
    second = import_menu(s, loc, parse_menu_csv(repriced))
    assert second.created == []
    assert second.updated == ["Masala Dosa", "mango lassi"]  # price; name's letter case
    assert second.unchanged == ["Garlic Naan"]
    assert len(list_items(s, loc)) == 3
    assert [c.action for c in list_changes(s, loc)][:2] == ["update", "update"]


def test_a_dry_run_changes_nothing(location: tuple[Session, str]) -> None:
    s, loc = location
    result = import_menu(s, loc, parse_menu_csv(SHEET), dry_run=True)
    assert len(result.created) == 3
    assert list_items(s, loc) == []


@pytest.fixture
def db_url(tmp_path: Path) -> tuple[str, str]:
    url = f"sqlite:///{tmp_path / 'venue.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        loc = create_location(s, org_name="Madras Curry House", name="Brisbane CBD")
        s.commit()
        loc_id = loc.id
    engine.dispose()
    return url, loc_id


def test_import_menu_from_the_command_line(
    db_url: tuple[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    url, loc = db_url
    sheet = tmp_path / "menu.csv"
    sheet.write_bytes(SHEET.encode("utf-8"))

    assert main(["--database-url", url, "import-menu", "--location", loc, "--file", str(sheet), "--dry-run"]) == 0
    assert "Would create 3, update 0, leave 0 unchanged." in capsys.readouterr().out
    assert main(["--database-url", url, "import-menu", "--location", loc, "--file", str(sheet)]) == 0
    assert "Did create 3" in capsys.readouterr().out
    assert main(["--database-url", url, "import-menu", "--location", loc, "--file", str(sheet)]) == 0
    assert "Did create 0, update 0, leave 3 unchanged." in capsys.readouterr().out


def test_import_menu_reads_standard_input(
    db_url: tuple[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    url, loc = db_url
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(SHEET.encode("utf-8"))))
    assert main(["--database-url", url, "import-menu", "--location", loc, "--file", "-"]) == 0
    assert "Did create 3" in capsys.readouterr().out


def test_a_bad_sheet_exits_with_the_row_problems(
    db_url: tuple[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    url, loc = db_url
    sheet = tmp_path / "menu.csv"
    sheet.write_bytes(b"name,price\nChai,four\n")
    assert main(["--database-url", url, "import-menu", "--location", loc, "--file", str(sheet)]) == 1
    assert "row 2: price 'four' isn't a number" in capsys.readouterr().err


def test_a_non_utf8_sheet_gets_a_clear_hint(
    db_url: tuple[str, str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    url, loc = db_url
    sheet = tmp_path / "menu.csv"
    sheet.write_bytes("name,price\nCrème brûlée,9\n".encode("cp1252"))
    assert main(["--database-url", url, "import-menu", "--location", loc, "--file", str(sheet)]) == 1
    assert "CSV UTF-8" in capsys.readouterr().err


@pytest.fixture
def printer() -> Iterator[tuple[int, Callable[[], bytes]]]:
    """A stand-in for a network ESC/POS printer: accepts one job on a local port. The
    second value waits for the job to finish arriving and returns it."""
    received = bytearray()
    server = socket.create_server(("127.0.0.1", 0))
    port = server.getsockname()[1]

    def accept() -> None:
        conn, _ = server.accept()
        with conn:
            while chunk := conn.recv(65536):
                received.extend(chunk)

    def job() -> bytes:
        thread.join(timeout=5)
        return bytes(received)

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    yield port, job
    thread.join(timeout=5)
    server.close()


def test_print_test_reaches_the_printer(
    printer: tuple[int, Callable[[], bytes]], capsys: pytest.CaptureFixture[str]
) -> None:
    port, job = printer
    assert main(["print-test", "--host", "127.0.0.1", "--port", str(port)]) == 0
    out = capsys.readouterr().out
    assert f"test ticket to 127.0.0.1:{port}" in out
    assert "No font set" in out  # the Tamil line needs a font to print properly
    received = job()
    assert received.startswith(b"\x1b@") and b"ORDER #0" in received


def test_print_test_explains_an_unreachable_printer(capsys: pytest.CaptureFixture[str]) -> None:
    with socket.socket() as probe:  # a port nothing listens on
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    assert main(["print-test", "--host", "127.0.0.1", "--port", str(free_port)]) == 1
    assert "can't be reached" in capsys.readouterr().err


def test_print_test_needs_a_printer_address(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["print-test"]) == 1
    assert "VOIDPOS_KITCHEN_PRINTER_HOST" in capsys.readouterr().err
