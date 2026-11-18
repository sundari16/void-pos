from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from ..ids import new_ulid
from .base import Base

# JSONB on Postgres, plain JSON elsewhere (e.g. sqlite in tests).
JsonType = JSON().with_variant(JSONB(), "postgresql")


class Event(Base):
    """Append-only event log — the source of truth. Every aggregate is a projection of
    these rows. Apply is idempotent on event_id; (device_id, seq) is unique per device;
    ``sync_seq`` is the location's commit-ordered pull position (see sync/service.py)."""

    __tablename__ = "event"
    __table_args__ = (
        UniqueConstraint("device_id", "seq", name="uq_event_device_seq"),
        UniqueConstraint("location_id", "sync_seq", name="uq_event_location_sync_seq"),
    )

    event_id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)
    device_id: Mapped[str] = mapped_column(ForeignKey("device.id"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    location_id: Mapped[str] = mapped_column(ForeignKey("location.id"), nullable=False, index=True)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)
    # Server arrival time; orders events that are waiting for a sync position.
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, index=True
    )
    # Position in the location's pull order, given after the event commits (NULL until then).
    sync_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class SyncCounter(Base):
    """The last ``sync_seq`` handed out at a location. Its row lock serialises sequencing."""

    __tablename__ = "sync_counter"

    location_id: Mapped[str] = mapped_column(ForeignKey("location.id"), primary_key=True)
    last_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
