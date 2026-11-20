from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..ids import new_ulid
from .base import Base, TimestampMixin
from .event import JsonType

# What we decided about an incoming partner order.
ACCEPTED = "accepted"  # confirmed with the partner and injected as an aggregator-channel order
REJECTED = "rejected"  # declined with the partner; no order enters the sales pipeline


class AggregatorOrder(Base, TimestampMixin):
    """The tie between a delivery partner's order and ours — and the idempotency key.

    One row per ``(provider, external_order_id)``: ingesting the same partner order twice
    returns the order already made instead of a second one. An accepted order points at the
    ``pos_order`` it created; a rejected one never enters the sales pipeline, so its
    ``order_id`` is null. ``raw`` keeps the partner's payload as received, for reconciliation
    against the platform's payout — we never key on the partner's id, it lives here beside
    our own."""

    __tablename__ = "aggregator_order"
    __table_args__ = (
        UniqueConstraint("provider", "external_order_id", name="uq_aggregator_order_ref"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)
    location_id: Mapped[str] = mapped_column(ForeignKey("location.id"), nullable=False, index=True)
    # Null for an order we declined before it entered the sales pipeline.
    order_id: Mapped[str | None] = mapped_column(
        ForeignKey("pos_order.id"), nullable=True, unique=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)  # mock|uber_eats|doordash|menulog
    external_order_id: Mapped[str] = mapped_column(String(200), nullable=False)
    display_id: Mapped[str | None] = mapped_column(String(80), nullable=True)  # human ref, e.g. "UE-7Q2K"
    external_payment_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # accepted|rejected
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)  # why it was rejected
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="AUD")
    raw: Mapped[dict] = mapped_column(JsonType, nullable=False, default=dict)  # the partner payload
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
