"""on-demand delivery dispatch (courier jobs)

Revision ID: a1d9f3e7c204
Revises: e5c7a9b1d3f6
Create Date: 2026-11-20 10:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a1d9f3e7c204"
down_revision: Union[str, None] = "e5c7a9b1d3f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOW = sa.text("(CURRENT_TIMESTAMP)")


def upgrade() -> None:
    op.create_table(
        "delivery",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("order_id", sa.String(length=26), nullable=False),
        sa.Column("location_id", sa.String(length=26), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("external_ref", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("fee_cents", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("courier_name", sa.String(length=120), nullable=True),
        sa.Column("tracking_url", sa.String(length=500), nullable=True),
        sa.Column("dropoff_name", sa.String(length=120), nullable=False),
        sa.Column("dropoff_phone", sa.String(length=40), nullable=False),
        sa.Column("dropoff_address", sa.String(length=300), nullable=False),
        sa.Column("dropoff_notes", sa.String(length=300), nullable=True),
        sa.Column("cancel_reason", sa.String(length=200), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("picked_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["location_id"], ["location.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["pos_order.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_delivery_order_id"), "delivery", ["order_id"])
    op.create_index(op.f("ix_delivery_location_id"), "delivery", ["location_id"])
    op.create_index(op.f("ix_delivery_business_date"), "delivery", ["business_date"])


def downgrade() -> None:
    op.drop_index(op.f("ix_delivery_business_date"), table_name="delivery")
    op.drop_index(op.f("ix_delivery_location_id"), table_name="delivery")
    op.drop_index(op.f("ix_delivery_order_id"), table_name="delivery")
    op.drop_table("delivery")
