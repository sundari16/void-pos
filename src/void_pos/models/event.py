from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from ..ids import new_ulid
from .base import Base

# JSONB on Postgres, plain JSON elsewhere (e.g. sqlite in tests).
JsonType = JSON().with_variant(JSONB(), "postgresql")


class Event(Base):
    """Append-only event log — the source of truth. Every aggregate is a projection of
    these rows. Apply is idempotent on event_id; (device_id, seq) is unique per device."""

    __tablename__ = "event"
    __table_args__ = (UniqueConstraint("device_id", "seq", name="uq_event_device_seq"),)

    event_id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)
    device_id: Mapped[str] = mapped_column(ForeignKey("device.id"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    location_id: Mapped[str] = mapped_column(ForeignKey("location.id"), nullable=False, index=True)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)
