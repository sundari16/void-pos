from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..ids import new_ulid
from .base import Base, TimestampMixin


class Delivery(Base, TimestampMixin):
    """A courier job for one own-channel delivery order (pickup = venue, dropoff =
    customer). Keyed on our own ULID the dispatching device minted; the courier
    platform's own delivery id lives in ``external_ref``, never as the key — the same rule
    the Payment model follows, so the open core stays provider-neutral and a venue can move
    between courier platforms without rewriting history.

    Not projected from the event log: like the aggregator tie row, a delivery mirrors the
    state of an external job (statuses stream in from the courier) rather than being a fact
    the venue owns. Its status moves through a small state machine (see ``delivery.states``);
    the dropoff is snapshotted at dispatch so the record stands even if the order changes.
    """

    __tablename__ = "delivery"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)
    order_id: Mapped[str] = mapped_column(ForeignKey("pos_order.id"), nullable=False, index=True)
    location_id: Mapped[str] = mapped_column(ForeignKey("location.id"), nullable=False, index=True)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    provider: Mapped[str] = mapped_column(String(40), nullable=False)  # mock|uber_direct
    # The courier platform's delivery id. Nullable: a provider that is still finding a
    # courier may not have one yet. Never the primary key (see the class docstring).
    external_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="requested")

    fee_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)  # integer cents
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="AUD")
    courier_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tracking_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Dropoff snapshot: who the courier delivers to, taken at dispatch time.
    dropoff_name: Mapped[str] = mapped_column(String(120), nullable=False)
    dropoff_phone: Mapped[str] = mapped_column(String(40), nullable=False)
    dropoff_address: Mapped[str] = mapped_column(String(300), nullable=False)
    dropoff_notes: Mapped[str | None] = mapped_column(String(300), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    picked_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
