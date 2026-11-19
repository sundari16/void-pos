from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..ids import new_ulid
from .base import Base, TimestampMixin


class RosteredShift(Base, TimestampMixin):
    """A planned shift: who is expected, from when to when. The scheduled side of a
    timesheet. Built by hand (``source='manual'``) or pulled from the venue's Deputy
    roster (``source='deputy'``, ``external_ref`` = the Deputy roster id, so a re-pull
    updates the same row instead of making a duplicate)."""

    __tablename__ = "rostered_shift"
    __table_args__ = (
        # A Deputy roster maps to one row; re-pulling it updates in place. Manual rows
        # leave external_ref NULL, which no database counts as a duplicate.
        UniqueConstraint("location_id", "source", "external_ref", name="uq_shift_external_ref"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)
    location_id: Mapped[str] = mapped_column(ForeignKey("location.id"), nullable=False, index=True)
    staff_id: Mapped[str] = mapped_column(ForeignKey("staff.id"), nullable=False, index=True)
    # Stored UTC; displayed in the location's timezone.
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # The location-local date the shift starts: what a roster and a timesheet are grouped by.
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    role: Mapped[str | None] = mapped_column(String(40), nullable=True)  # e.g. kitchen, front
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    external_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(300), nullable=True)


class ClockEntry(Base, TimestampMixin):
    """A clock-on/clock-off record: when someone actually worked. The actual side of a
    timesheet. ``clock_out_at`` is NULL while they are still on; a break is recorded as
    unpaid minutes rather than a separate pair of rows."""

    __tablename__ = "clock_entry"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)
    location_id: Mapped[str] = mapped_column(ForeignKey("location.id"), nullable=False, index=True)
    staff_id: Mapped[str] = mapped_column(ForeignKey("staff.id"), nullable=False, index=True)
    clock_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    clock_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    break_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The shift this was worked against, when clocked on for a planned one.
    shift_id: Mapped[str | None] = mapped_column(
        ForeignKey("rostered_shift.id"), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")


class Timesheet(Base, TimestampMixin):
    """One staff member's day: scheduled against actual, totalled into hours and approved.

    Built from the roster and the clock entries of a business date (a projection, like the
    other aggregates), it is a snapshot once approved: a manager's PIN records who approved
    it and when, and it is then frozen. ``paid_minutes`` is attendance minus breaks — what
    payroll pays. ``external_ref``/``synced_at`` mark it pushed to Deputy, so a re-push is a
    no-op."""

    __tablename__ = "timesheet"
    __table_args__ = (
        UniqueConstraint("location_id", "staff_id", "business_date", name="uq_timesheet_staff_day"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)
    location_id: Mapped[str] = mapped_column(ForeignKey("location.id"), nullable=False, index=True)
    staff_id: Mapped[str] = mapped_column(ForeignKey("staff.id"), nullable=False, index=True)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    scheduled_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    worked_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # gross attendance
    break_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")  # draft|approved
    approved_by_staff_id: Mapped[str | None] = mapped_column(
        ForeignKey("staff.id"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)  # Deputy timesheet id
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def paid_minutes(self) -> int:
        """Attendance less unpaid breaks: the hours payroll pays."""
        return max(self.worked_minutes - self.break_minutes, 0)
