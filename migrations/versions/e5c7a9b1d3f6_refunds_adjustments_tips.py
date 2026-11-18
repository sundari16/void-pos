"""refunds, discounts and surcharges, tips, removed lines

Revision ID: e5c7a9b1d3f6
Revises: d4b6f8a0c2e4
Create Date: 2026-11-19 10:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e5c7a9b1d3f6"
down_revision: Union[str, None] = "d4b6f8a0c2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOW = sa.text("(CURRENT_TIMESTAMP)")


def upgrade() -> None:
    op.create_table(
        "order_adjustment",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("order_id", sa.String(length=26), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("basis", sa.String(length=10), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=True),
        sa.Column("approved_by", sa.String(length=26), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["pos_order.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_order_adjustment_order_id"), "order_adjustment", ["order_id"])
    op.create_table(
        "refund",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("order_id", sa.String(length=26), nullable=False),
        sa.Column("payment_id", sa.String(length=26), nullable=False),
        sa.Column("location_id", sa.String(length=26), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("external_ref", sa.String(length=200), nullable=True),
        sa.Column("approved_by", sa.String(length=26), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["location_id"], ["location.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["pos_order.id"]),
        sa.ForeignKeyConstraint(["payment_id"], ["payment.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_refund_order_id"), "refund", ["order_id"])
    op.create_index(op.f("ix_refund_payment_id"), "refund", ["payment_id"])
    op.create_index(op.f("ix_refund_location_id"), "refund", ["location_id"])
    op.create_index(op.f("ix_refund_business_date"), "refund", ["business_date"])
    with op.batch_alter_table("order_line") as batch:
        batch.add_column(sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("payment") as batch:
        batch.add_column(
            sa.Column("tip_cents", sa.Integer(), server_default="0", nullable=False)
        )
    with op.batch_alter_table("location") as batch:
        batch.add_column(sa.Column("holiday_surcharge_bp", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("location") as batch:
        batch.drop_column("holiday_surcharge_bp")
    with op.batch_alter_table("payment") as batch:
        batch.drop_column("tip_cents")
    with op.batch_alter_table("order_line") as batch:
        batch.drop_column("removed_at")
    for index in ("business_date", "location_id", "payment_id", "order_id"):
        op.drop_index(op.f(f"ix_refund_{index}"), table_name="refund")
    op.drop_table("refund")
    op.drop_index(op.f("ix_order_adjustment_order_id"), table_name="order_adjustment")
    op.drop_table("order_adjustment")
