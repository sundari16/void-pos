from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..ids import new_ulid
from .base import Base, TimestampMixin


class LoyaltyLedger(Base, TimestampMixin):
    """Loyalty as an append-only ledger. Balance is SUM(points_delta) — never a stored
    field — so full export and dispute handling keep the history."""

    __tablename__ = "loyalty_ledger"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)
    location_id: Mapped[str] = mapped_column(ForeignKey("location.id"), nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customer.id"), nullable=False, index=True)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("pos_order.id"), nullable=True)
    points_delta: Mapped[int] = mapped_column(Integer, nullable=False)  # +earn / -redeem
    reason: Mapped[str] = mapped_column(String(80), nullable=False)
