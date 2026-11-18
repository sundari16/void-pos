"""seat and course numbers on order lines

Revision ID: b3a7c5d9e1f2
Revises: 9d4f1e6c2b87
Create Date: 2026-11-18 14:00:00.000000

Additive and nullable: existing lines simply have no seat or course.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b3a7c5d9e1f2"
down_revision: Union[str, None] = "9d4f1e6c2b87"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("order_line") as batch:
        batch.add_column(sa.Column("seat_number", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("course_number", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("order_line") as batch:
        batch.drop_column("course_number")
        batch.drop_column("seat_number")
