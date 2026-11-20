"""Rostering, time-clocking and timesheets.

Three things, per location and staff member:

- **Rostered shifts** — what was planned (scheduled). Added by hand or pulled from the
  venue's Deputy roster.
- **Clock entries** — what happened (actual): a clock-on, then a clock-off with any unpaid
  break. Someone can only be clocked on once at a time.
- **Timesheets** — one per staff member per business date, built from the roster and the
  clock entries of that day: scheduled minutes against worked minutes, totalled into the
  paid hours (attendance minus breaks). A manager's PIN approves it; once approved it is a
  frozen snapshot, ready to push to Deputy for payroll.

Times are stored UTC; the business date is the location's own calendar day, so a shift that
runs past midnight UTC still belongs to the right trading day in Brisbane.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clock import as_aware, local_date, utcnow
from ..errors import Conflict, Forbidden, InvalidRequest, NotFound
from ..models import ClockEntry, Location, RosteredShift, Staff, Timesheet
from ..staff import approving_manager

SHIFT_SOURCES = ("manual", "deputy")


def _minutes(start: datetime, end: datetime) -> int:
    """Whole minutes between two stored (UTC) times; SQLite hands them back naive."""
    return int((as_aware(end) - as_aware(start)).total_seconds() // 60)


def _staff_at(session: Session, location_id: str, staff_id: str) -> Staff:
    person = session.get(Staff, staff_id)
    if person is None or person.location_id != location_id:
        raise NotFound("staff member not found at this location")
    return person


def _location(session: Session, location_id: str) -> Location:
    location = session.get(Location, location_id)
    if location is None:
        raise NotFound("location not found")
    return location


# --- Rostered shifts (the scheduled side) -----------------------------------------------


def schedule_shift(
    session: Session,
    *,
    location_id: str,
    staff_id: str,
    starts_at: datetime,
    ends_at: datetime,
    role: str | None = None,
    notes: str | None = None,
    source: str = "manual",
    external_ref: str | None = None,
) -> RosteredShift:
    """Roster ``staff_id`` on from ``starts_at`` to ``ends_at`` (both UTC, aware).

    With an ``external_ref`` (a Deputy roster id) the shift is upserted on
    (location, source, external_ref), so pulling the same roster twice updates one row
    instead of duplicating it. Manual shifts always create a new row.
    """
    if source not in SHIFT_SOURCES:
        raise InvalidRequest(f"source must be one of {', '.join(SHIFT_SOURCES)}", code="invalid_source")
    location = _location(session, location_id)
    _staff_at(session, location_id, staff_id)
    if as_aware(ends_at) <= as_aware(starts_at):
        raise InvalidRequest("a shift must end after it starts", code="invalid_shift_times")
    business_date = local_date(location.timezone, starts_at)

    shift: RosteredShift | None = None
    if external_ref is not None:
        shift = session.scalars(
            select(RosteredShift).where(
                RosteredShift.location_id == location_id,
                RosteredShift.source == source,
                RosteredShift.external_ref == external_ref,
            )
        ).first()
    if shift is None:
        shift = RosteredShift(location_id=location_id, source=source, external_ref=external_ref)
        session.add(shift)
    shift.staff_id = staff_id
    shift.starts_at = starts_at
    shift.ends_at = ends_at
    shift.business_date = business_date
    shift.role = role
    shift.notes = notes
    session.flush()
    return shift


def list_shifts(
    session: Session,
    location_id: str,
    *,
    business_date: date | None = None,
    staff_id: str | None = None,
) -> list[RosteredShift]:
    stmt = select(RosteredShift).where(RosteredShift.location_id == location_id)
    if business_date is not None:
        stmt = stmt.where(RosteredShift.business_date == business_date)
    if staff_id is not None:
        stmt = stmt.where(RosteredShift.staff_id == staff_id)
    return list(session.scalars(stmt.order_by(RosteredShift.starts_at)).all())


# --- Clock entries (the actual side) ----------------------------------------------------


def _open_entry(session: Session, location_id: str, staff_id: str) -> ClockEntry | None:
    return session.scalars(
        select(ClockEntry).where(
            ClockEntry.location_id == location_id,
            ClockEntry.staff_id == staff_id,
            ClockEntry.clock_out_at.is_(None),
        )
    ).first()


def clock_in(
    session: Session,
    *,
    location_id: str,
    staff_id: str,
    at: datetime | None = None,
    shift_id: str | None = None,
) -> ClockEntry:
    """Start a clock entry. Refuses a second one while the first is still open."""
    location = _location(session, location_id)
    _staff_at(session, location_id, staff_id)
    if _open_entry(session, location_id, staff_id) is not None:
        raise Conflict("this staff member is already clocked on", code="already_clocked_on")
    when = as_aware(at) if at is not None else utcnow()
    if shift_id is not None:
        shift = session.get(RosteredShift, shift_id)
        if shift is None or shift.location_id != location_id or shift.staff_id != staff_id:
            raise NotFound("rostered shift not found for this staff member")
    entry = ClockEntry(
        location_id=location_id,
        staff_id=staff_id,
        clock_in_at=when,
        business_date=local_date(location.timezone, when),
        shift_id=shift_id,
    )
    session.add(entry)
    session.flush()
    return entry


def clock_out(
    session: Session,
    *,
    location_id: str,
    staff_id: str,
    at: datetime | None = None,
    break_minutes: int = 0,
) -> ClockEntry:
    """Close the open clock entry, recording any unpaid break."""
    _staff_at(session, location_id, staff_id)
    entry = _open_entry(session, location_id, staff_id)
    if entry is None:
        raise Conflict("this staff member isn't clocked on", code="not_clocked_on")
    when = as_aware(at) if at is not None else utcnow()
    if when <= as_aware(entry.clock_in_at):
        raise InvalidRequest("clock-off must be after clock-on", code="invalid_clock_times")
    if break_minutes < 0:
        raise InvalidRequest("a break can't be negative", code="invalid_break")
    if break_minutes >= _minutes(entry.clock_in_at, when):
        raise InvalidRequest("the break is longer than the shift", code="invalid_break")
    entry.clock_out_at = when
    entry.break_minutes = break_minutes
    session.flush()
    return entry


def list_clock_entries(
    session: Session,
    location_id: str,
    *,
    business_date: date | None = None,
    staff_id: str | None = None,
) -> list[ClockEntry]:
    stmt = select(ClockEntry).where(ClockEntry.location_id == location_id)
    if business_date is not None:
        stmt = stmt.where(ClockEntry.business_date == business_date)
    if staff_id is not None:
        stmt = stmt.where(ClockEntry.staff_id == staff_id)
    return list(session.scalars(stmt.order_by(ClockEntry.clock_in_at)).all())


# --- Timesheets (scheduled + actual -> hours) -------------------------------------------


def get_timesheet(session: Session, timesheet_id: str) -> Timesheet:
    sheet = session.get(Timesheet, timesheet_id)
    if sheet is None:
        raise NotFound("timesheet not found")
    return sheet


def build_timesheet(
    session: Session, *, location_id: str, staff_id: str, business_date: date
) -> Timesheet:
    """Build (or rebuild, while still a draft) one staff member's timesheet for a day.

    Scheduled minutes come from the roster; worked minutes and breaks from the closed clock
    entries. An approved timesheet is frozen, so rebuilding one is refused.
    """
    _staff_at(session, location_id, staff_id)
    scheduled = sum(
        _minutes(s.starts_at, s.ends_at)
        for s in list_shifts(session, location_id, business_date=business_date, staff_id=staff_id)
    )
    worked = 0
    breaks = 0
    for entry in list_clock_entries(
        session, location_id, business_date=business_date, staff_id=staff_id
    ):
        if entry.clock_out_at is None:
            continue  # still on; a draft counts only finished entries
        worked += _minutes(entry.clock_in_at, entry.clock_out_at)
        breaks += entry.break_minutes

    sheet = session.scalars(
        select(Timesheet).where(
            Timesheet.location_id == location_id,
            Timesheet.staff_id == staff_id,
            Timesheet.business_date == business_date,
        )
    ).first()
    if sheet is not None and sheet.status == "approved":
        raise Conflict("this timesheet is approved and can't be rebuilt", code="timesheet_approved")
    if sheet is None:
        sheet = Timesheet(
            location_id=location_id, staff_id=staff_id, business_date=business_date
        )
        session.add(sheet)
    sheet.scheduled_minutes = scheduled
    sheet.worked_minutes = worked
    sheet.break_minutes = breaks
    sheet.status = "draft"
    session.flush()
    return sheet


def build_timesheets_for_date(
    session: Session, location_id: str, business_date: date
) -> list[Timesheet]:
    """Build a draft timesheet for every staff member rostered or clocked on that day."""
    staff_ids: set[str] = {
        s.staff_id for s in list_shifts(session, location_id, business_date=business_date)
    }
    staff_ids |= {
        e.staff_id for e in list_clock_entries(session, location_id, business_date=business_date)
    }
    sheets = [
        build_timesheet(
            session, location_id=location_id, staff_id=staff_id, business_date=business_date
        )
        for staff_id in staff_ids
    ]
    return sorted(sheets, key=lambda s: s.staff_id)


def approve_timesheet(session: Session, timesheet_id: str, *, manager_pin: str | None) -> Timesheet:
    """Approve a draft timesheet with a manager's PIN; records who approved it and when.

    Refuses a day with anyone still clocked on, and refuses re-approving. A location with no
    manager yet can't approve — add one first, since approval must be attributable.
    """
    sheet = get_timesheet(session, timesheet_id)
    if sheet.status == "approved":
        raise Conflict("this timesheet is already approved", code="timesheet_approved")
    if _open_entry(session, sheet.location_id, sheet.staff_id) is not None:
        raise Conflict(
            "this staff member is still clocked on; clock off before approving",
            code="still_clocked_on",
        )
    manager = approving_manager(session, sheet.location_id, manager_pin)
    if manager is None:
        raise Forbidden(
            "a manager must approve timesheets; add one with add-staff first",
            code="no_manager",
        )
    sheet.status = "approved"
    sheet.approved_by_staff_id = manager.id
    sheet.approved_at = utcnow()
    session.flush()
    return sheet


def list_timesheets(
    session: Session,
    location_id: str,
    *,
    business_date: date | None = None,
    status: str | None = None,
) -> list[Timesheet]:
    stmt = select(Timesheet).where(Timesheet.location_id == location_id)
    if business_date is not None:
        stmt = stmt.where(Timesheet.business_date == business_date)
    if status is not None:
        stmt = stmt.where(Timesheet.status == status)
    return list(session.scalars(stmt.order_by(Timesheet.business_date, Timesheet.staff_id)).all())
