from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
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
    offline terminals never collide.

    Pairing: an admin issues a short-lived, one-time code; the till redeems it for a
    long-lived bearer token. Only SHA-256 hashes of the code and the token are stored.
    """

    __tablename__ = "device"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_device_token_hash"),
        UniqueConstraint("pairing_code_hash", name="uq_device_pairing_code_hash"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)
    location_id: Mapped[str] = mapped_column(ForeignKey("location.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    order_number_start: Mapped[int] = mapped_column(Integer, nullable=False)
    order_number_end: Mapped[int] = mapped_column(Integer, nullable=False)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pairing_code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pairing_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
