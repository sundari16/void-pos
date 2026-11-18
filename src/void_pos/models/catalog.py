from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..ids import new_ulid
from .base import Base, TimestampMixin
from .event import JsonType


class MenuItem(Base, TimestampMixin):
    __tablename__ = "menu_item"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)
    location_id: Mapped[str] = mapped_column(ForeignKey("location.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kitchen_name: Mapped[str | None] = mapped_column(String(200), nullable=True)  # any script
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    base_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, default=0)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    available_from: Mapped[time | None] = mapped_column(Time, nullable=True)
    available_to: Mapped[time | None] = mapped_column(Time, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ModifierGroup(Base, TimestampMixin):
    """e.g. "Spice level" with min/max selectable. Nesting (a modifier owning a child
    group) is deferred — the model starts at depth 1."""

    __tablename__ = "modifier_group"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)
    location_id: Mapped[str] = mapped_column(ForeignKey("location.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    min_select: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_select: Mapped[int | None] = mapped_column(Integer, nullable=True)
    available_from: Mapped[time | None] = mapped_column(Time, nullable=True)
    available_to: Mapped[time | None] = mapped_column(Time, nullable=True)


class Modifier(Base, TimestampMixin):
    __tablename__ = "modifier"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)
    group_id: Mapped[str] = mapped_column(ForeignKey("modifier_group.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kitchen_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    price_delta_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class MenuItemModifierGroup(Base):
    """Which modifier groups apply to a menu item."""

    __tablename__ = "menu_item_modifier_group"

    menu_item_id: Mapped[str] = mapped_column(ForeignKey("menu_item.id"), primary_key=True)
    modifier_group_id: Mapped[str] = mapped_column(
        ForeignKey("modifier_group.id"), primary_key=True
    )


class MenuChange(Base, TimestampMixin):
    """One admin edit to the menu, numbered per location, with the values before and
    after. A revert is a change too, pointing at the change it undid (once only)."""

    __tablename__ = "menu_change"
    __table_args__ = (
        UniqueConstraint("location_id", "number", name="uq_menu_change_location_number"),
        UniqueConstraint("reverts_id", name="uq_menu_change_reverts"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)
    location_id: Mapped[str] = mapped_column(ForeignKey("location.id"), nullable=False, index=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    entity: Mapped[str] = mapped_column(String(40), nullable=False)  # menu_item|modifier_group|modifier
    entity_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # create|update|delete|attach|detach
    before: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    after: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    reverts_id: Mapped[str | None] = mapped_column(ForeignKey("menu_change.id"), nullable=True)
    actor: Mapped[str] = mapped_column(String(80), nullable=False, default="admin")
