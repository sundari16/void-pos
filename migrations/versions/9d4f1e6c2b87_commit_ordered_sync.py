"""commit-ordered sync: per-location counter and event.sync_seq

Revision ID: 9d4f1e6c2b87
Revises: 7c2e9b41a5d3
Create Date: 2026-11-18 13:30:00.000000

Existing events stay unnumbered; the first pull at each location numbers them in arrival
order, so no backfill is needed here.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "9d4f1e6c2b87"
down_revision: Union[str, None] = "7c2e9b41a5d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sync_counter",
        sa.Column("location_id", sa.String(length=26), nullable=False),
        sa.Column("last_seq", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["location_id"], ["location.id"]),
        sa.PrimaryKeyConstraint("location_id"),
    )
    op.execute("INSERT INTO sync_counter (location_id, last_seq) SELECT id, 0 FROM location")
    with op.batch_alter_table("event") as batch:
        batch.add_column(sa.Column("sync_seq", sa.BigInteger(), nullable=True))
        batch.create_unique_constraint("uq_event_location_sync_seq", ["location_id", "sync_seq"])


def downgrade() -> None:
    with op.batch_alter_table("event") as batch:
        batch.drop_constraint("uq_event_location_sync_seq", type_="unique")
        batch.drop_column("sync_seq")
    op.drop_table("sync_counter")
