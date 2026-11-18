"""a till never reuses an order number

Revision ID: 7c2e9b41a5d3
Revises: 3f1c2a9d7b10
Create Date: 2026-11-18 12:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "7c2e9b41a5d3"
down_revision: Union[str, None] = "3f1c2a9d7b10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("pos_order") as batch:
        batch.create_unique_constraint("uq_order_device_number", ["device_id", "order_number"])


def downgrade() -> None:
    with op.batch_alter_table("pos_order") as batch:
        batch.drop_constraint("uq_order_device_number", type_="unique")
