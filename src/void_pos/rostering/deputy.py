"""Deputy integration: the venue's own Deputy install, both directions.

Deputy (https://www.deputy.com) is the rostering and payroll product many Australian
venues already run. Like the Square menu import, this uses the **venue's own** Deputy
install and permanent token, so it belongs in the open core: no Void-hosted middleman, no
shared credentials. The token is only ever sent to the venue's Deputy endpoint.

Two directions, because the roster and the timesheet live at opposite ends of the day:

- **Pull the roster in** (``pull_roster``) — the manager builds the week's roster in Deputy;
  we read it and keep the POS's scheduled shifts in step. This mirrors the Square import
  exactly: read-only, matched by name, idempotent (a Deputy roster id maps to one shift row,
  so re-pulling updates in place).
- **Push approved timesheets out** (``push_timesheets``) — the clock-on/off and the
  manager's approval happen *at the venue, in the POS*, which Deputy can't see. Once a
  timesheet is approved we post it to Deputy for payroll. Idempotent via ``synced_at``: a
  pushed timesheet is never pushed again.

Recommended default is the pull (it is the clean mirror of the proven Square pattern and
needs the least trust — read-only). The push is what makes the POS's own clock data useful,
so both are implemented; a venue can run either or both.

Deputy employees are matched to Void POS staff **by name** (case-insensitive), the same way
the Square import matches items by name — no schema coupling to Deputy ids. Anyone who
doesn't match is reported and skipped, never guessed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol

import httpx

from ..clock import as_aware
from ..errors import InvalidRequest, ServiceUnavailable
from ..models import Staff, Timesheet
from .service import list_clock_entries, list_shifts, list_timesheets, schedule_shift

API_PREFIX = "/api/v1"


class DeputyApi(Protocol):
    """The few calls the sync needs; a test passes a fake with these methods."""

    def employees(self) -> list[dict[str, Any]]: ...

    def rosters(self, *, start: datetime, end: datetime) -> list[dict[str, Any]]: ...

    def create_timesheet(
        self, *, employee_id: int, start_ts: int, end_ts: int, mealbreak_minutes: int
    ) -> dict[str, Any]: ...


class DeputyClient:
    """The venue's Deputy install over HTTPS. The token is only sent to Deputy, never logged."""

    def __init__(self, endpoint: str, token: str, client: httpx.Client | None = None) -> None:
        base = endpoint.rstrip("/")
        self._client = client or httpx.Client(base_url=base, timeout=30)
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "dp-meta-option": "none",
        }

    def _call(self, method: str, path: str, json: dict[str, Any] | None = None) -> Any:
        try:
            response = self._client.request(method, path, json=json, headers=self._headers)
        except httpx.HTTPError as exc:
            raise ServiceUnavailable("can't reach Deputy", code="deputy_unavailable") from exc
        if response.status_code == 401:
            raise InvalidRequest(
                "Deputy refused the access token; check VOIDPOS_DEPUTY_ACCESS_TOKEN",
                code="deputy_unauthorized",
            )
        if response.status_code >= 400:
            raise ServiceUnavailable(
                f"Deputy: request failed ({response.status_code})", code="deputy_error"
            )
        return response.json()

    def employees(self) -> list[dict[str, Any]]:
        data = self._call("GET", f"{API_PREFIX}/resource/Employee")
        return list(data) if isinstance(data, list) else []

    def rosters(self, *, start: datetime, end: datetime) -> list[dict[str, Any]]:
        body = {
            "search": {
                "from": {"field": "StartTime", "type": "ge", "data": int(start.timestamp())},
                "to": {"field": "StartTime", "type": "le", "data": int(end.timestamp())},
            },
            "sort": {"StartTime": "asc"},
        }
        data = self._call("POST", f"{API_PREFIX}/resource/Roster/QUERY", body)
        return list(data) if isinstance(data, list) else []

    def create_timesheet(
        self, *, employee_id: int, start_ts: int, end_ts: int, mealbreak_minutes: int
    ) -> dict[str, Any]:
        body = {
            "Employee": employee_id,
            "StartTime": start_ts,
            "EndTime": end_ts,
            "Mealbreak": mealbreak_minutes,
        }
        data = self._call("POST", f"{API_PREFIX}/resource/Timesheet", body)
        return dict(data) if isinstance(data, dict) else {}


# --- Pure translation (no database) -----------------------------------------------------


@dataclass
class DeputyShift:
    roster_id: str
    employee_name: str
    starts_at: datetime  # UTC, aware
    ends_at: datetime
    area: str | None


def employee_names(employees: Iterable[dict[str, Any]]) -> dict[int, str]:
    """Deputy employee id -> display name, for the ones with both."""
    names: dict[int, str] = {}
    for emp in employees:
        emp_id = emp.get("Id")
        name = (emp.get("DisplayName") or "").strip()
        if isinstance(emp_id, int) and name:
            names[emp_id] = name
    return names


def _unix_to_utc(value: Any) -> datetime | None:
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    return datetime.fromtimestamp(int(value), tz=UTC)


def translate_rosters(
    rosters: Iterable[dict[str, Any]], names: dict[int, str]
) -> tuple[list[DeputyShift], list[str]]:
    """Deputy roster records to shifts keyed by employee name, with notes for the rest."""
    shifts: list[DeputyShift] = []
    notes: list[str] = []
    for roster in rosters:
        roster_id = roster.get("Id")
        employee_id = roster.get("Employee")
        name = names.get(employee_id) if isinstance(employee_id, int) else None
        if not name:
            notes.append(f"roster {roster_id}: no Deputy employee on file; skipped")
            continue
        starts_at = _unix_to_utc(roster.get("StartTime"))
        ends_at = _unix_to_utc(roster.get("EndTime"))
        if starts_at is None or ends_at is None or ends_at <= starts_at:
            notes.append(f"{name}: roster {roster_id} has no usable start/end time; skipped")
            continue
        area = (roster.get("OperationalUnitObject") or {}).get("OperationalUnitName")
        shifts.append(
            DeputyShift(
                roster_id=str(roster_id),
                employee_name=name,
                starts_at=starts_at,
                ends_at=ends_at,
                area=(area or None),
            )
        )
    return shifts, notes


# --- Name matching between Deputy and Void POS staff ------------------------------------


@dataclass
class MatchReport:
    matched: list[str] = field(default_factory=list)  # names in both
    deputy_only: list[str] = field(default_factory=list)  # in Deputy, no Void staff
    void_only: list[str] = field(default_factory=list)  # Void staff, not in Deputy


def _staff_by_name(staff: Iterable[Staff]) -> dict[str, Staff]:
    return {person.name.casefold(): person for person in staff}


def match_employees(
    deputy_names: Iterable[str], staff: list[Staff]
) -> MatchReport:
    by_name = _staff_by_name(staff)
    deputy_set = {name.casefold(): name for name in deputy_names}
    report = MatchReport()
    for key, name in sorted(deputy_set.items()):
        (report.matched if key in by_name else report.deputy_only).append(name)
    for key, person in sorted(by_name.items()):
        if key not in deputy_set:
            report.void_only.append(person.name)
    return report


# --- Sync (orchestration: client + database) --------------------------------------------


@dataclass
class DeputyPullResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class DeputyPushResult:
    pushed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def pull_roster(
    session: Any,
    location_id: str,
    client: DeputyApi,
    *,
    start: datetime,
    end: datetime,
    active_staff: list[Staff],
) -> DeputyPullResult:
    """Read the venue's Deputy roster for a window and upsert it into scheduled shifts.

    ``active_staff`` is the location's staff (passed in so the caller controls the query).
    Idempotent: a Deputy roster id maps to one shift row. Never commits — the caller does.
    """
    names = employee_names(client.employees())
    shifts, notes = translate_rosters(client.rosters(start=start, end=end), names)
    by_name = _staff_by_name(active_staff)
    result = DeputyPullResult(notes=list(notes))

    existing_refs = {
        s.external_ref
        for s in list_shifts(session, location_id)
        if s.source == "deputy" and s.external_ref is not None
    }
    for shift in shifts:
        person = by_name.get(shift.employee_name.casefold())
        if person is None:
            result.notes.append(
                f"{shift.employee_name}: no matching Void POS staff; skipped "
                "(add them with add-staff, same name)"
            )
            continue
        is_update = shift.roster_id in existing_refs
        saved = schedule_shift(
            session,
            location_id=location_id,
            staff_id=person.id,
            starts_at=shift.starts_at,
            ends_at=shift.ends_at,
            role=(shift.area or None) and shift.area[:40],
            source="deputy",
            external_ref=shift.roster_id[:64],
        )
        label = f"{shift.employee_name} {saved.business_date.isoformat()}"
        (result.updated if is_update else result.created).append(label)
    return result


def _worked_span(session: Any, sheet: Timesheet) -> tuple[int, int, int] | None:
    """(start_ts, end_ts, mealbreak_minutes) for a timesheet's day, from its clock entries."""
    entries = [
        e
        for e in list_clock_entries(
            session, sheet.location_id, business_date=sheet.business_date, staff_id=sheet.staff_id
        )
        if e.clock_out_at is not None
    ]
    if not entries:
        return None
    start = min(as_aware(e.clock_in_at) for e in entries)
    end = max(as_aware(e.clock_out_at) for e in entries if e.clock_out_at is not None)
    mealbreak = sum(e.break_minutes for e in entries)
    return int(start.timestamp()), int(end.timestamp()), mealbreak


def push_timesheets(
    session: Any,
    location_id: str,
    client: DeputyApi,
    *,
    business_date: date | None,
    active_staff: list[Staff],
    dry_run: bool = False,
) -> DeputyPushResult:
    """Push approved, not-yet-synced timesheets to Deputy for payroll.

    Idempotent: only timesheets with no ``synced_at`` are sent, and each is stamped on
    success, so re-running pushes nothing already there. Never commits — the caller does.
    """
    emp_by_name = {
        name.casefold(): emp_id for emp_id, name in employee_names(client.employees()).items()
    }
    staff_by_id = {person.id: person for person in active_staff}
    result = DeputyPushResult()
    for sheet in list_timesheets(session, location_id, business_date=business_date, status="approved"):
        if sheet.synced_at is not None:
            continue
        person = staff_by_id.get(sheet.staff_id)
        label = f"{person.name if person else sheet.staff_id} {sheet.business_date.isoformat()}"
        employee_id = emp_by_name.get(person.name.casefold()) if person else None
        if employee_id is None:
            result.skipped.append(f"{label}: no matching Deputy employee; skipped")
            continue
        span = _worked_span(session, sheet)
        if span is None:
            result.skipped.append(f"{label}: nothing clocked; skipped")
            continue
        start_ts, end_ts, mealbreak = span
        if dry_run:
            result.pushed.append(label)
            continue
        response = client.create_timesheet(
            employee_id=employee_id, start_ts=start_ts, end_ts=end_ts, mealbreak_minutes=mealbreak
        )
        ref = response.get("Id")
        sheet.external_ref = str(ref) if ref is not None else None
        sheet.synced_at = as_aware(datetime.now(UTC))
        result.pushed.append(label)
    return result
