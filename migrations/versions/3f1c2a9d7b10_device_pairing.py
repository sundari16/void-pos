"""device pairing: hashed pairing code and bearer token

Revision ID: 3f1c2a9d7b10
Revises: ede82cbcfa9d
Create Date: 2026-11-18 11:30:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "3f1c2a9d7b10"
down_revision: Union[str, None] = "ede82cbcfa9d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = ("token_hash", "pairing_code_hash", "pairing_expires_at", "paired_at", "revoked_at")


def upgrade() -> None:
    # batch mode so the same migration runs on SQLite (dev) and PostgreSQL (venue).
    with op.batch_alter_table("device") as batch:
        batch.add_column(sa.Column("token_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("pairing_code_hash", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column("pairing_expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(sa.Column("paired_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_unique_constraint("uq_device_token_hash", ["token_hash"])
        batch.create_unique_constraint("uq_device_pairing_code_hash", ["pairing_code_hash"])


def downgrade() -> None:
    with op.batch_alter_table("device") as batch:
        batch.drop_constraint("uq_device_pairing_code_hash", type_="unique")
        batch.drop_constraint("uq_device_token_hash", type_="unique")
        for column in _COLUMNS:
            batch.drop_column(column)
