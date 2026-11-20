"""aggregator order ingestion: the partner-order tie and idempotency key

Revision ID: f1a2b3c4d5e6
Revises: e5c7a9b1d3f6
Create Date: 2026-11-19 12:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e5c7a9b1d3f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOW = sa.text("(CURRENT_TIMESTAMP)")
_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "aggregator_order",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("location_id", sa.String(length=26), nullable=False),
        sa.Column("order_id", sa.String(length=26), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("external_order_id", sa.String(length=200), nullable=False),
        sa.Column("display_id", sa.String(length=80), nullable=True),
        sa.Column("external_payment_ref", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("raw", _JSON, nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["location_id"], ["location.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["pos_order.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id"),
        sa.UniqueConstraint("provider", "external_order_id", name="uq_aggregator_order_ref"),
    )
    op.create_index(
        op.f("ix_aggregator_order_location_id"), "aggregator_order", ["location_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_aggregator_order_location_id"), table_name="aggregator_order")
    op.drop_table("aggregator_order")
