from __future__ import annotations

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ..ids import new_ulid
from .base import Base, TimestampMixin


class Customer(Base, TimestampMixin):
    """The CRM record. Full order history is the data asset competitors cannot backfill."""

    __tablename__ = "customer"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)
    org_id: Mapped[str] = mapped_column(ForeignKey("org.id"), nullable=False, index=True)
    location_id: Mapped[str | None] = mapped_column(
        ForeignKey("location.id"), nullable=True, index=True
    )
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
