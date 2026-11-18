from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..ids import new_ulid
from .base import Base, TimestampMixin


class Order(Base, TimestampMixin):
    """Projected order header. Built by folding events — not written to directly in prod."""

    __tablename__ = "pos_order"
    # A till never reuses an order number: it's what the kitchen calls out.
    __table_args__ = (
        UniqueConstraint("device_id", "order_number", name="uq_order_device_number"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)
    location_id: Mapped[str] = mapped_column(ForeignKey("location.id"), nullable=False, index=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("device.id"), nullable=False, index=True)
    customer_id: Mapped[str | None] = mapped_column(
        ForeignKey("customer.id"), nullable=True, index=True
    )
    order_number: Mapped[int] = mapped_column(Integer, nullable=False)  # from the device's range
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(40), nullable=False)  # pos|voice|qr|web|aggregator
    fulfillment: Mapped[str] = mapped_column(String(20), nullable=False)  # pickup|delivery|dine_in
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="open")  # open|sent|paid|voided

    lines: Mapped[list[OrderLine]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderLine(Base, TimestampMixin):
    """One line. A modifier selection is itself a line whose parent_line_id points at the
    item line it modifies. Name/price/tax/category are snapshots taken at sale time."""

    __tablename__ = "order_line"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)
    order_id: Mapped[str] = mapped_column(ForeignKey("pos_order.id"), nullable=False, index=True)
    parent_line_id: Mapped[str | None] = mapped_column(
        ForeignKey("order_line.id"), nullable=True, index=True
    )
    menu_item_id: Mapped[str | None] = mapped_column(ForeignKey("menu_item.id"), nullable=True)
    modifier_id: Mapped[str | None] = mapped_column(ForeignKey("modifier.id"), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    kitchen_name_snapshot: Mapped[str | None] = mapped_column(String(200), nullable=True)
    unit_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    tax_rate_snapshot: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, default=0)
    category_snapshot: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Dine-in: which seat the item is for and which course it fires with. Set on item
    # lines only; a modifier follows its item. Unset for counter and takeaway orders.
    seat_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    course_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    order: Mapped[Order] = relationship(back_populates="lines")
