"""menu change history

Revision ID: c8e2f4a6b0d1
Revises: b3a7c5d9e1f2
Create Date: 2026-11-18 14:30:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c8e2f4a6b0d1"
down_revision: Union[str, None] = "b3a7c5d9e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "menu_change",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("location_id", sa.String(length=26), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("entity", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.String(length=26), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("before", _JSON, nullable=True),
        sa.Column("after", _JSON, nullable=True),
        sa.Column("reverts_id", sa.String(length=26), nullable=True),
        sa.Column("actor", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["location_id"], ["location.id"]),
        sa.ForeignKeyConstraint(["reverts_id"], ["menu_change.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("location_id", "number", name="uq_menu_change_location_number"),
        sa.UniqueConstraint("reverts_id", name="uq_menu_change_reverts"),
    )
    op.create_index(op.f("ix_menu_change_location_id"), "menu_change", ["location_id"], unique=False)
    op.create_index(op.f("ix_menu_change_entity_id"), "menu_change", ["entity_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_menu_change_entity_id"), table_name="menu_change")
    op.drop_index(op.f("ix_menu_change_location_id"), table_name="menu_change")
    op.drop_table("menu_change")
