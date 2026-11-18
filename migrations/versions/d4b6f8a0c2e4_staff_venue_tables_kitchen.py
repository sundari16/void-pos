"""staff, venue receipt details, table labels, kitchen-sent lines

Revision ID: d4b6f8a0c2e4
Revises: c8e2f4a6b0d1
Create Date: 2026-11-18 20:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d4b6f8a0c2e4"
down_revision: Union[str, None] = "c8e2f4a6b0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "staff",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("location_id", sa.String(length=26), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("pin_hash", sa.String(length=200), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["location_id"], ["location.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_staff_location_id"), "staff", ["location_id"], unique=False)
    with op.batch_alter_table("location") as batch:
        batch.add_column(sa.Column("business_name", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("abn", sa.String(length=11), nullable=True))
        batch.add_column(sa.Column("address", sa.String(length=300), nullable=True))
        batch.add_column(sa.Column("phone", sa.String(length=40), nullable=True))
        batch.add_column(sa.Column("receipt_footer", sa.String(length=300), nullable=True))
    with op.batch_alter_table("pos_order") as batch:
        batch.add_column(sa.Column("table_label", sa.String(length=40), nullable=True))
    with op.batch_alter_table("order_line") as batch:
        batch.add_column(sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
    # Lines of orders the kitchen already has were sent when the order was.
    op.execute(
        "UPDATE order_line SET sent_at = (SELECT pos_order.updated_at FROM pos_order "
        "WHERE pos_order.id = order_line.order_id) WHERE order_id IN "
        "(SELECT id FROM pos_order WHERE state IN ('sent', 'paid', 'voided'))"
    )


def downgrade() -> None:
    with op.batch_alter_table("order_line") as batch:
        batch.drop_column("sent_at")
    with op.batch_alter_table("pos_order") as batch:
        batch.drop_column("table_label")
    with op.batch_alter_table("location") as batch:
        batch.drop_column("receipt_footer")
        batch.drop_column("phone")
        batch.drop_column("address")
        batch.drop_column("abn")
        batch.drop_column("business_name")
    op.drop_index(op.f("ix_staff_location_id"), table_name="staff")
    op.drop_table("staff")
