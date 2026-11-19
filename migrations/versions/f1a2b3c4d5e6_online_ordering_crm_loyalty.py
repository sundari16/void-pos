"""online ordering: customer marketing opt-in, order delivery address and note

Revision ID: f1a2b3c4d5e6
Revises: e5c7a9b1d3f6
Create Date: 2026-11-19 12:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e5c7a9b1d3f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("customer") as batch:
        batch.add_column(
            sa.Column(
                "marketing_opt_in", sa.Boolean(), server_default=sa.false(), nullable=False
            )
        )
    with op.batch_alter_table("pos_order") as batch:
        batch.add_column(sa.Column("delivery_address", sa.String(length=400), nullable=True))
        batch.add_column(sa.Column("customer_note", sa.String(length=400), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("pos_order") as batch:
        batch.drop_column("customer_note")
        batch.drop_column("delivery_address")
    with op.batch_alter_table("customer") as batch:
        batch.drop_column("marketing_opt_in")
