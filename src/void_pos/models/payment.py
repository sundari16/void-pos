from __future__ import annotations

from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..ids import new_ulid
from .base import Base, TimestampMixin


class Payment(Base, TimestampMixin):
    """Keyed on our own ULID; the provider's id lives in external_ref, never as the key.
    Keeps the door open to terminal-agnostic / multi-provider payments."""

    __tablename__ = "payment"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)
    order_id: Mapped[str] = mapped_column(ForeignKey("pos_order.id"), nullable=False, index=True)
    location_id: Mapped[str] = mapped_column(ForeignKey("location.id"), nullable=False, index=True)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)  # stripe|cash|external
    external_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)  # toward the order
    tip_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="AUD")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="captured")


class Refund(Base, TimestampMixin):
    """Money given back against a payment, through the same provider: counted out of the
    till, done on the card terminal (its reference kept), or refunded through Stripe."""

    __tablename__ = "refund"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)
    order_id: Mapped[str] = mapped_column(ForeignKey("pos_order.id"), nullable=False, index=True)
    payment_id: Mapped[str] = mapped_column(ForeignKey("payment.id"), nullable=False, index=True)
    location_id: Mapped[str] = mapped_column(ForeignKey("location.id"), nullable=False, index=True)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    external_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(26), nullable=True)
