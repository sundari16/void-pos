from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from ..ids import new_ulid
from .base import Base, TimestampMixin

# JSONB on PostgreSQL, plain JSON on SQLite (tests).
JsonMap = JSON().with_variant(JSONB(), "postgresql")


class XeroConnection(Base, TimestampMixin):
    """A location's link to its own Xero organisation, for the daily-takings sync.

    One Xero org per location. Holds the tenant (org) id and the rotating OAuth2 refresh
    token — the refresh token is replaced on every token refresh, so it is stored
    recoverably (not hashed, unlike a till token): it is the only way back to the org. It
    is as sensitive as a secret; a leaked database leaks the Xero connection, so the file
    stays on the venue machine. ``account_map`` is the per-venue account-code mapping
    (null = the code defaults in ``accounting.mapping``)."""

    __tablename__ = "xero_connection"
    __table_args__ = (UniqueConstraint("location_id", name="uq_xero_connection_location"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)
    location_id: Mapped[str] = mapped_column(ForeignKey("location.id"), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    refresh_token: Mapped[str] = mapped_column(String(2048), nullable=False)
    account_map: Mapped[dict[str, Any] | None] = mapped_column(JsonMap, nullable=True)
    authorised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
