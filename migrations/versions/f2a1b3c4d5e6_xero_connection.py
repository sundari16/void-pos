"""xero connection for the daily-takings sync

Revision ID: f2a1b3c4d5e6
Revises: e5c7a9b1d3f6
Create Date: 2026-11-19 16:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f2a1b3c4d5e6"
down_revision: Union[str, None] = "e5c7a9b1d3f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOW = sa.text("(CURRENT_TIMESTAMP)")


def upgrade() -> None:
    op.create_table(
        "xero_connection",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("location_id", sa.String(length=26), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_name", sa.String(length=200), nullable=True),
        sa.Column("refresh_token", sa.String(length=2048), nullable=False),
        sa.Column(
            "account_map",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
        sa.Column("authorised_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["location_id"], ["location.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("location_id", name="uq_xero_connection_location"),
    )


def downgrade() -> None:
    op.drop_table("xero_connection")
