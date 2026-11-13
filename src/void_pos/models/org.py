from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..ids import new_ulid
from .base import Base, TimestampMixin


class Org(Base, TimestampMixin):
    __tablename__ = "org"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)


class Location(Base, TimestampMixin):
    __tablename__ = "location"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)
    org_id: Mapped[str] = mapped_column(ForeignKey("org.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Australia/Brisbane")


class Device(Base, TimestampMixin):
    """A POS device. Mints ULIDs offline and owns a private order-number range so two
    offline terminals never collide."""

    __tablename__ = "device"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)
    location_id: Mapped[str] = mapped_column(ForeignKey("location.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    order_number_start: Mapped[int] = mapped_column(Integer, nullable=False)
    order_number_end: Mapped[int] = mapped_column(Integer, nullable=False)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
