from __future__ import annotations

import io
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from support import make_engine
from void_pos import cli
from void_pos.clock import as_aware
from void_pos.config import settings
from void_pos.devices import create_location
from void_pos.errors import Conflict, Forbidden, InvalidRequest
from void_pos.models import Base, Timesheet
from void_pos.rostering import (
    approve_timesheet,
    build_timesheet,
    clock_in,
    clock_out,
    list_shifts,
    list_timesheets,
    match_employees,
    pull_roster,
    push_timesheets,
    schedule_shift,
)
from void_pos.rostering.deputy import DeputyClient, employee_names
from void_pos.staff import create_staff, list_staff

BNE = ZoneInfo("Australia/Brisbane")


def _bne(y: int, mo: int, d: int, hh: int, mm: int) -> datetime:
    """A Brisbane wall-clock time as a UTC instant (what the services store)."""
    return datetime(y, mo, d, hh, mm, tzinfo=BNE).astimezone(UTC)


def _unix(dt: datetime) -> int:
    return int(dt.timestamp())


def _venue(session: Session, *, manager_pin: str = "2468") -> tuple[str, str, str]:
    """A location with a manager (Asha) and a staff member (Ben). Returns their ids."""
    loc = create_location(session, org_name="MCH", name="CBD")
    asha = create_staff(session, location_id=loc.id, name="Asha", role="manager", pin=manager_pin)
    ben = create_staff(session, location_id=loc.id, name="Ben", role="staff", pin="1357")
    return loc.id, asha.id, ben.id


class FakeDeputy:
    """The venue's Deputy, in memory. Implements the DeputyApi the sync calls."""

    def __init__(
        self, employees: list[dict[str, Any]], rosters: list[dict[str, Any]]
    ) -> None:
        self._employees = employees
        self._rosters = rosters
        self.created: list[dict[str, Any]] = []

    def employees(self) -> list[dict[str, Any]]:
        return self._employees

    def rosters(self, *, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return self._rosters

    def create_timesheet(
        self, *, employee_id: int, start_ts: int, end_ts: int, mealbreak_minutes: int
    ) -> dict[str, Any]:
        self.created.append(
            {
                "employee_id": employee_id,
                "start_ts": start_ts,
                "end_ts": end_ts,
                "mealbreak": mealbreak_minutes,
            }
        )
        return {"Id": len(self.created)}


# --- Service: roster + clock -> timesheet -----------------------------------------------


def test_build_timesheet_from_roster_and_clock() -> None:
    with Session(make_engine()) as s:
        loc_id, asha, _ = _venue(s)
        bd = date(2026, 11, 20)
        schedule_shift(
            s, location_id=loc_id, staff_id=asha,
            starts_at=_bne(2026, 11, 20, 9, 0), ends_at=_bne(2026, 11, 20, 17, 0), role="kitchen",
        )
        clock_in(s, location_id=loc_id, staff_id=asha, at=_bne(2026, 11, 20, 9, 5))
        clock_out(s, location_id=loc_id, staff_id=asha, at=_bne(2026, 11, 20, 17, 10), break_minutes=30)
        sheet = build_timesheet(s, location_id=loc_id, staff_id=asha, business_date=bd)
        assert (sheet.scheduled_minutes, sheet.worked_minutes, sheet.break_minutes, sheet.paid_minutes) == (
            480, 485, 30, 455,
        )
        assert sheet.status == "draft"
        # The shift's business date is the Brisbane day even though the UTC start is the 19th.
        shift = list_shifts(s, loc_id)[0]
        assert shift.business_date == bd
        assert as_aware(shift.starts_at).date() == date(2026, 11, 19)


def test_clock_guards_one_open_entry_and_order() -> None:
    with Session(make_engine()) as s:
        loc_id, asha, _ = _venue(s)
        clock_in(s, location_id=loc_id, staff_id=asha, at=_bne(2026, 11, 20, 9, 0))
        with pytest.raises(Conflict, match="already clocked on"):
            clock_in(s, location_id=loc_id, staff_id=asha, at=_bne(2026, 11, 20, 9, 1))
        with pytest.raises(InvalidRequest, match="after clock-on"):
            clock_out(s, location_id=loc_id, staff_id=asha, at=_bne(2026, 11, 20, 8, 0))
        clock_out(s, location_id=loc_id, staff_id=asha, at=_bne(2026, 11, 20, 17, 0))
        with pytest.raises(Conflict, match="isn't clocked on"):
            clock_out(s, location_id=loc_id, staff_id=asha, at=_bne(2026, 11, 20, 18, 0))


def test_build_rejects_a_break_longer_than_the_shift() -> None:
    with Session(make_engine()) as s:
        loc_id, asha, _ = _venue(s)
        clock_in(s, location_id=loc_id, staff_id=asha, at=_bne(2026, 11, 20, 9, 0))
        with pytest.raises(InvalidRequest, match="longer than the shift"):
            clock_out(
                s, location_id=loc_id, staff_id=asha, at=_bne(2026, 11, 20, 9, 30), break_minutes=45
            )


def test_approve_records_the_manager_and_freezes_the_sheet() -> None:
    with Session(make_engine()) as s:
        loc_id, asha, ben = _venue(s, manager_pin="2468")
        bd = date(2026, 11, 20)
        clock_in(s, location_id=loc_id, staff_id=ben, at=_bne(2026, 11, 20, 9, 0))
        clock_out(s, location_id=loc_id, staff_id=ben, at=_bne(2026, 11, 20, 17, 0))
        sheet = build_timesheet(s, location_id=loc_id, staff_id=ben, business_date=bd)
        with pytest.raises(Forbidden):
            approve_timesheet(s, sheet.id, manager_pin="0000")  # not a manager's PIN
        with pytest.raises(Forbidden):
            approve_timesheet(s, sheet.id, manager_pin=None)  # none given
        approved = approve_timesheet(s, sheet.id, manager_pin="2468")
        assert approved.status == "approved"
        assert approved.approved_by_staff_id == asha and approved.approved_at is not None
        with pytest.raises(Conflict, match="approved"):
            build_timesheet(s, location_id=loc_id, staff_id=ben, business_date=bd)


def test_approve_refuses_while_still_clocked_on() -> None:
    with Session(make_engine()) as s:
        loc_id, _, ben = _venue(s)
        bd = date(2026, 11, 20)
        clock_in(s, location_id=loc_id, staff_id=ben, at=_bne(2026, 11, 20, 9, 0))
        sheet = build_timesheet(s, location_id=loc_id, staff_id=ben, business_date=bd)
        with pytest.raises(Conflict, match="still clocked on"):
            approve_timesheet(s, sheet.id, manager_pin="2468")


def test_approve_needs_a_manager_to_exist() -> None:
    with Session(make_engine()) as s:
        loc = create_location(s, org_name="MCH", name="CBD")
        sol = create_staff(s, location_id=loc.id, name="Sol", role="staff", pin="4242")
        bd = date(2026, 11, 20)
        clock_in(s, location_id=loc.id, staff_id=sol.id, at=_bne(2026, 11, 20, 9, 0))
        clock_out(s, location_id=loc.id, staff_id=sol.id, at=_bne(2026, 11, 20, 17, 0))
        sheet = build_timesheet(s, location_id=loc.id, staff_id=sol.id, business_date=bd)
        with pytest.raises(Forbidden, match="manager must approve"):
            approve_timesheet(s, sheet.id, manager_pin=None)


# --- Deputy HTTP client -----------------------------------------------------------------


def test_deputy_client_sends_the_token_and_hits_the_right_paths() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/resource/Employee"):
            return httpx.Response(200, json=[{"Id": 1, "DisplayName": "Asha"}])
        if path.endswith("/resource/Roster/QUERY"):
            return httpx.Response(200, json=[{"Id": 10, "Employee": 1, "StartTime": 100, "EndTime": 200}])
        if path.endswith("/resource/Timesheet"):
            return httpx.Response(200, json={"Id": 77})
        return httpx.Response(404, json={})

    client = httpx.Client(
        base_url="https://mch.au.deputy.com", transport=httpx.MockTransport(handler)
    )
    deputy = DeputyClient("https://mch.au.deputy.com/", "dep_tok", client)
    assert employee_names(deputy.employees()) == {1: "Asha"}
    rosters = deputy.rosters(start=datetime(2026, 11, 20, tzinfo=UTC), end=datetime(2026, 11, 27, tzinfo=UTC))
    assert rosters[0]["Id"] == 10
    assert deputy.create_timesheet(employee_id=1, start_ts=100, end_ts=200, mealbreak_minutes=0) == {"Id": 77}
    assert all(r.headers["Authorization"] == "Bearer dep_tok" for r in seen)

    refused = httpx.Client(
        base_url="https://mch.au.deputy.com",
        transport=httpx.MockTransport(lambda r: httpx.Response(401, json={})),
    )
    with pytest.raises(InvalidRequest, match="VOIDPOS_DEPUTY_ACCESS_TOKEN"):
        DeputyClient("https://mch.au.deputy.com", "bad", refused).employees()


# --- Deputy pull (roster in) ------------------------------------------------------------


def test_pull_roster_matches_by_name_and_is_idempotent() -> None:
    with Session(make_engine()) as s:
        loc_id, asha, _ben = _venue(s)
        employees = [
            {"Id": 1, "DisplayName": "Asha"},
            {"Id": 2, "DisplayName": "Ben"},
            {"Id": 3, "DisplayName": "Cleo"},  # a Deputy employee with no Void POS staff
        ]
        rosters = [
            {"Id": 10, "Employee": 1, "StartTime": _unix(_bne(2026, 11, 20, 9, 0)),
             "EndTime": _unix(_bne(2026, 11, 20, 17, 0)),
             "OperationalUnitObject": {"OperationalUnitName": "Kitchen"}},
            {"Id": 11, "Employee": 2, "StartTime": _unix(_bne(2026, 11, 20, 12, 0)),
             "EndTime": _unix(_bne(2026, 11, 20, 20, 0))},
            {"Id": 12, "Employee": 3, "StartTime": _unix(_bne(2026, 11, 20, 9, 0)),
             "EndTime": _unix(_bne(2026, 11, 20, 17, 0))},
            {"Id": 13, "Employee": 99, "StartTime": _unix(_bne(2026, 11, 20, 9, 0)),
             "EndTime": _unix(_bne(2026, 11, 20, 17, 0))},  # unknown employee
        ]
        fake = FakeDeputy(employees, rosters)
        window = {"start": _bne(2026, 11, 20, 0, 0), "end": _bne(2026, 11, 21, 0, 0)}
        res = pull_roster(s, loc_id, fake, active_staff=list_staff(s, loc_id), **window)
        assert sorted(res.created) == ["Asha 2026-11-20", "Ben 2026-11-20"]
        assert any("Cleo" in n for n in res.notes)
        assert any("no Deputy employee" in n for n in res.notes)

        shifts = list_shifts(s, loc_id)
        assert len(shifts) == 2
        assert all(x.source == "deputy" and x.external_ref for x in shifts)
        asha_shift = next(x for x in shifts if x.staff_id == asha)
        assert asha_shift.role == "Kitchen" and asha_shift.business_date == date(2026, 11, 20)

        res2 = pull_roster(s, loc_id, fake, active_staff=list_staff(s, loc_id), **window)
        assert res2.created == [] and sorted(res2.updated) == ["Asha 2026-11-20", "Ben 2026-11-20"]
        assert len(list_shifts(s, loc_id)) == 2  # updated in place, not duplicated


# --- Deputy push (approved timesheets out) ----------------------------------------------


def _approved_sheet(session: Session, loc_id: str, staff_id: str, bd: date) -> str:
    clock_in(session, location_id=loc_id, staff_id=staff_id, at=_bne(2026, 11, 20, 9, 0))
    clock_out(session, location_id=loc_id, staff_id=staff_id, at=_bne(2026, 11, 20, 17, 0), break_minutes=30)
    sheet = build_timesheet(session, location_id=loc_id, staff_id=staff_id, business_date=bd)
    approve_timesheet(session, sheet.id, manager_pin="2468")
    return sheet.id


def test_push_timesheets_idempotent_and_dry_run_touches_nothing() -> None:
    with Session(make_engine()) as s:
        loc_id, asha, _ = _venue(s)
        bd = date(2026, 11, 20)
        sheet_id = _approved_sheet(s, loc_id, asha, bd)
        fake = FakeDeputy([{"Id": 1, "DisplayName": "Asha"}], [])
        staff = list_staff(s, loc_id)

        dry = push_timesheets(s, loc_id, fake, business_date=bd, active_staff=staff, dry_run=True)
        assert dry.pushed == ["Asha 2026-11-20"]
        assert fake.created == []  # a dry run never POSTs to Deputy
        assert s.get(Timesheet, sheet_id).synced_at is None  # type: ignore[union-attr]

        res = push_timesheets(s, loc_id, fake, business_date=bd, active_staff=staff)
        assert len(res.pushed) == 1 and len(fake.created) == 1
        posted = fake.created[0]
        assert posted["employee_id"] == 1 and posted["mealbreak"] == 30
        assert posted["start_ts"] == _unix(_bne(2026, 11, 20, 9, 0))
        assert posted["end_ts"] == _unix(_bne(2026, 11, 20, 17, 0))
        synced = next(t for t in list_timesheets(s, loc_id) if t.id == sheet_id)
        assert synced.synced_at is not None and synced.external_ref == "1"

        again = push_timesheets(s, loc_id, fake, business_date=bd, active_staff=staff)
        assert again.pushed == [] and len(fake.created) == 1  # already synced


def test_push_skips_a_staff_member_deputy_has_no_match_for() -> None:
    with Session(make_engine()) as s:
        loc_id, _, ben = _venue(s)
        _approved_sheet(s, loc_id, ben, date(2026, 11, 20))
        fake = FakeDeputy([{"Id": 1, "DisplayName": "Asha"}], [])  # no Ben
        res = push_timesheets(
            s, loc_id, fake, business_date=date(2026, 11, 20), active_staff=list_staff(s, loc_id)
        )
        assert res.pushed == [] and fake.created == []
        assert any("no matching Deputy employee" in n for n in res.skipped)


def test_match_employees_reports_both_sides() -> None:
    with Session(make_engine()) as s:
        loc_id, _, _ = _venue(s)
        report = match_employees(["Asha", "Cleo"], list_staff(s, loc_id))
        assert report.matched == ["Asha"]
        assert report.deputy_only == ["Cleo"]
        assert report.void_only == ["Ben"]


# --- CLI --------------------------------------------------------------------------------


def _seed_cli_venue(url: str) -> tuple[str, str, str]:
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        loc_id, asha, ben = _venue(s)
        s.commit()
    engine.dispose()
    return loc_id, asha, ben


def test_rostering_cli_flow(
    tmp_path: Any, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"sqlite:///{tmp_path / 'venue.db'}"
    loc_id, asha, _ = _seed_cli_venue(url)
    base = ["--database-url", url]

    assert cli.main([*base, "roster-add", "--location", loc_id, "--staff", asha,
                     "--start", "2026-11-20 09:00", "--end", "2026-11-20 17:00", "--role", "kitchen"]) == 0
    assert "Rostered Asha" in capsys.readouterr().out

    assert cli.main([*base, "clock-in", "--location", loc_id, "--staff", asha, "--at", "2026-11-20 09:05"]) == 0
    capsys.readouterr()
    assert cli.main([*base, "clock-out", "--location", loc_id, "--staff", asha,
                     "--at", "2026-11-20 17:10", "--break", "30"]) == 0
    assert "clocked off" in capsys.readouterr().out

    assert cli.main([*base, "timesheet-build", "--location", loc_id, "--date", "2026-11-20"]) == 0
    out = capsys.readouterr().out
    assert "sched 8.00h" in out and "paid 7.58h" in out

    assert cli.main([*base, "timesheet-list", "--location", loc_id]) == 0
    ts_id = capsys.readouterr().out.split()[0]

    monkeypatch.setattr("sys.stdin", io.StringIO("2468\n"))
    assert cli.main([*base, "timesheet-approve", "--timesheet", ts_id]) == 0
    assert "Approved Asha" in capsys.readouterr().out


def test_deputy_cli_connect_and_sync(
    tmp_path: Any, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"sqlite:///{tmp_path / 'venue.db'}"
    loc_id, _asha, _ben = _seed_cli_venue(url)
    base = ["--database-url", url]

    employees = [{"Id": 1, "DisplayName": "Asha"}, {"Id": 2, "DisplayName": "Ben"}]
    rosters = [
        {"Id": 10, "Employee": 1, "StartTime": _unix(_bne(2026, 11, 20, 9, 0)),
         "EndTime": _unix(_bne(2026, 11, 20, 17, 0))},
        {"Id": 11, "Employee": 2, "StartTime": _unix(_bne(2026, 11, 20, 12, 0)),
         "EndTime": _unix(_bne(2026, 11, 20, 20, 0))},
    ]
    fake = FakeDeputy(employees, rosters)
    monkeypatch.setattr(settings, "deputy_endpoint", "https://mch.au.deputy.com")
    monkeypatch.setattr(settings, "deputy_access_token", SecretStr("dep_tok"))
    monkeypatch.setattr(cli, "DeputyClient", lambda endpoint, token: fake)

    assert cli.main([*base, "deputy-connect", "--location", loc_id]) == 0
    out = capsys.readouterr().out
    assert "2 employee(s)" in out and "matched 2" in out

    assert cli.main([*base, "deputy-sync", "--location", loc_id,
                     "--from", "2026-11-20", "--to", "2026-11-20", "--dry-run"]) == 0
    assert "Would pull" in capsys.readouterr().out
    # The dry run changed nothing.
    assert cli.main([*base, "roster-list", "--location", loc_id]) == 0
    assert capsys.readouterr().out == "No shifts.\n"

    assert cli.main([*base, "deputy-sync", "--location", loc_id, "--from", "2026-11-20", "--to", "2026-11-20"]) == 0
    assert "Did pull the roster" in capsys.readouterr().out
    assert cli.main([*base, "roster-list", "--location", loc_id]) == 0
    assert "[deputy]" in capsys.readouterr().out


def test_deputy_cli_push(
    tmp_path: Any, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"sqlite:///{tmp_path / 'venue.db'}"
    loc_id, asha, _ = _seed_cli_venue(url)
    engine = create_engine(url)
    with Session(engine) as s:
        _approved_sheet(s, loc_id, asha, date(2026, 11, 20))
        s.commit()
    engine.dispose()
    base = ["--database-url", url]

    fake = FakeDeputy([{"Id": 1, "DisplayName": "Asha"}], [])
    monkeypatch.setattr(settings, "deputy_endpoint", "https://mch.au.deputy.com")
    monkeypatch.setattr(settings, "deputy_access_token", SecretStr("dep_tok"))
    monkeypatch.setattr(cli, "DeputyClient", lambda endpoint, token: fake)

    assert cli.main([*base, "deputy-sync", "--location", loc_id, "--push"]) == 0
    out = capsys.readouterr().out
    assert "Did push 1 approved timesheet" in out
    assert len(fake.created) == 1 and fake.created[0]["employee_id"] == 1
