"""rostering: rostered shifts, clock entries, timesheets

Revision ID: 70db8c95c478
Revises: e5c7a9b1d3f6
Create Date: 2026-11-19 16:40:31.352382

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "70db8c95c478"
down_revision: Union[str, None] = "e5c7a9b1d3f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rostered_shift",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("location_id", sa.String(length=26), nullable=False),
        sa.Column("staff_id", sa.String(length=26), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("external_ref", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["location_id"], ["location.id"]),
        sa.ForeignKeyConstraint(["staff_id"], ["staff.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("location_id", "source", "external_ref", name="uq_shift_external_ref"),
    )
    op.create_index(op.f("ix_rostered_shift_business_date"), "rostered_shift", ["business_date"], unique=False)
    op.create_index(op.f("ix_rostered_shift_location_id"), "rostered_shift", ["location_id"], unique=False)
    op.create_index(op.f("ix_rostered_shift_staff_id"), "rostered_shift", ["staff_id"], unique=False)

    op.create_table(
        "timesheet",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("location_id", sa.String(length=26), nullable=False),
        sa.Column("staff_id", sa.String(length=26), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("scheduled_minutes", sa.Integer(), nullable=False),
        sa.Column("worked_minutes", sa.Integer(), nullable=False),
        sa.Column("break_minutes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("approved_by_staff_id", sa.String(length=26), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_ref", sa.String(length=64), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["approved_by_staff_id"], ["staff.id"]),
        sa.ForeignKeyConstraint(["location_id"], ["location.id"]),
        sa.ForeignKeyConstraint(["staff_id"], ["staff.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("location_id", "staff_id", "business_date", name="uq_timesheet_staff_day"),
    )
    op.create_index(op.f("ix_timesheet_business_date"), "timesheet", ["business_date"], unique=False)
    op.create_index(op.f("ix_timesheet_location_id"), "timesheet", ["location_id"], unique=False)
    op.create_index(op.f("ix_timesheet_staff_id"), "timesheet", ["staff_id"], unique=False)

    # References rostered_shift, so it is created last and dropped first.
    op.create_table(
        "clock_entry",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("location_id", sa.String(length=26), nullable=False),
        sa.Column("staff_id", sa.String(length=26), nullable=False),
        sa.Column("clock_in_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("clock_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("break_minutes", sa.Integer(), nullable=False),
        sa.Column("shift_id", sa.String(length=26), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["location_id"], ["location.id"]),
        sa.ForeignKeyConstraint(["shift_id"], ["rostered_shift.id"]),
        sa.ForeignKeyConstraint(["staff_id"], ["staff.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_clock_entry_business_date"), "clock_entry", ["business_date"], unique=False)
    op.create_index(op.f("ix_clock_entry_location_id"), "clock_entry", ["location_id"], unique=False)
    op.create_index(op.f("ix_clock_entry_shift_id"), "clock_entry", ["shift_id"], unique=False)
    op.create_index(op.f("ix_clock_entry_staff_id"), "clock_entry", ["staff_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_clock_entry_staff_id"), table_name="clock_entry")
    op.drop_index(op.f("ix_clock_entry_shift_id"), table_name="clock_entry")
    op.drop_index(op.f("ix_clock_entry_location_id"), table_name="clock_entry")
    op.drop_index(op.f("ix_clock_entry_business_date"), table_name="clock_entry")
    op.drop_table("clock_entry")
    op.drop_index(op.f("ix_timesheet_staff_id"), table_name="timesheet")
    op.drop_index(op.f("ix_timesheet_location_id"), table_name="timesheet")
    op.drop_index(op.f("ix_timesheet_business_date"), table_name="timesheet")
    op.drop_table("timesheet")
    op.drop_index(op.f("ix_rostered_shift_staff_id"), table_name="rostered_shift")
    op.drop_index(op.f("ix_rostered_shift_location_id"), table_name="rostered_shift")
    op.drop_index(op.f("ix_rostered_shift_business_date"), table_name="rostered_shift")
    op.drop_table("rostered_shift")
