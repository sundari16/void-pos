from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from ..clock import as_aware
from ..events.payloads import CURRENCY_PATTERN, MAX_LINE_CENTS, OrderPlaced
from ..ids import Ulid
from ..models import Device, Location

# --- till commands ----------------------------------------------------------------


class CommandIn(BaseModel):
    """Every write a till makes carries ids it minted, so a retry replays the original
    result instead of duplicating it. ``seq`` counts up per till."""

    event_id: Ulid
    seq: int = Field(ge=1, le=2_147_483_647)
    occurred_at: AwareDatetime | None = None


class PlaceOrderRequest(CommandIn, OrderPlaced):
    business_date: date | None = None  # default: today in the location's timezone


class OrderResponse(BaseModel):
    id: str
    order_number: int
    state: str
    business_date: date
    total_cents: int
    replayed: bool = False


class KitchenRequest(CommandIn):
    pass


class KitchenResponse(BaseModel):
    order_id: str
    state: str
    printed: bool  # false when no kitchen printer is configured, or on a replay
    ticket_bytes: int
    replayed: bool = False


class PayRequest(CommandIn):
    payment_id: Ulid
    method: Literal["cash", "external"] = "cash"
    external_ref: str | None = Field(default=None, min_length=1, max_length=200)
    amount_cents: int | None = Field(default=None, ge=0)  # default: the order total
    currency: str = Field(default="AUD", pattern=CURRENCY_PATTERN)

    @model_validator(mode="after")
    def _external_needs_ref(self) -> PayRequest:
        if self.method == "external" and not self.external_ref:
            raise ValueError("external_ref is required for method 'external'")
        return self


class PaymentResponse(BaseModel):
    order_id: str
    payment_id: str
    provider: str
    amount_cents: int
    currency: str
    status: str
    order_state: str
    replayed: bool = False


# --- sync --------------------------------------------------------------------------


class EventIn(BaseModel):
    event_id: Ulid
    device_id: Ulid
    seq: int = Field(ge=1, le=2_147_483_647)
    location_id: Ulid
    business_date: date
    occurred_at: AwareDatetime
    type: str = Field(min_length=1, max_length=80)
    payload: dict[str, Any] = Field(default_factory=dict)


class PushRequest(BaseModel):
    events: list[EventIn] = Field(min_length=1, max_length=500)


class Ack(BaseModel):
    event_id: str
    status: str  # applied | duplicate | conflict | ignored | rejected
    detail: str | None = None
    code: str | None = None


class PushResponse(BaseModel):
    acks: list[Ack]


class PullResponse(BaseModel):
    events: list[dict[str, Any]]
    next_cursor: str | None = None
    has_more: bool


# --- menu --------------------------------------------------------------------------

Name = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class MenuItemIn(BaseModel):
    name: Name
    base_price_cents: int = Field(ge=0, le=MAX_LINE_CENTS)
    kitchen_name: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=120)
    tax_rate: float = Field(default=0, ge=0, le=1)
    is_available: bool = True


_REQUIRED_ON_ITEM = ("name", "base_price_cents", "tax_rate", "is_available")


class MenuItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    base_price_cents: int | None = Field(default=None, ge=0, le=MAX_LINE_CENTS)
    kitchen_name: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=120)
    tax_rate: float | None = Field(default=None, ge=0, le=1)
    is_available: bool | None = None

    @model_validator(mode="after")
    def _no_null_for_required(self) -> MenuItemUpdate:
        cleared = [
            f for f in _REQUIRED_ON_ITEM if f in self.model_fields_set and getattr(self, f) is None
        ]
        if cleared:
            raise ValueError(f"{', '.join(cleared)} can't be cleared")
        return self


class MenuItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    location_id: str
    name: str
    kitchen_name: str | None
    category: str | None
    base_price_cents: int
    tax_rate: float
    is_available: bool


class ModifierGroupIn(BaseModel):
    name: Name
    min_select: int = Field(default=0, ge=0, le=100)
    max_select: int | None = Field(default=None, ge=0, le=100)


class ModifierGroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    location_id: str
    name: str
    min_select: int
    max_select: int | None


class ModifierIn(BaseModel):
    name: Name
    price_delta_cents: int = Field(default=0, ge=-MAX_LINE_CENTS, le=MAX_LINE_CENTS)
    kitchen_name: str | None = Field(default=None, max_length=200)


class ModifierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    group_id: str
    name: str
    kitchen_name: str | None
    price_delta_cents: int


# --- venue setup and pairing -------------------------------------------------------


class LocationIn(BaseModel):
    org_name: Name
    name: Name
    timezone: str = Field(default="Australia/Brisbane", min_length=1, max_length=64)


class LocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    org_id: str
    name: str
    timezone: str


class DeviceIn(BaseModel):
    name: Name
    order_number_start: int = Field(ge=1, le=2_147_483_647)
    order_number_end: int = Field(ge=1, le=2_147_483_647)

    @model_validator(mode="after")
    def _range(self) -> DeviceIn:
        if self.order_number_end < self.order_number_start:
            raise ValueError("order_number_end must be at or after order_number_start")
        return self


class DeviceOut(BaseModel):
    id: str
    location_id: str
    name: str
    order_number_start: int
    order_number_end: int
    last_seq: int
    paired: bool
    paired_at: datetime | None
    revoked_at: datetime | None

    @classmethod
    def of(cls, device: Device) -> DeviceOut:
        return cls(
            id=device.id,
            location_id=device.location_id,
            name=device.name,
            order_number_start=device.order_number_start,
            order_number_end=device.order_number_end,
            last_seq=device.last_seq,
            paired=device.token_hash is not None,
            paired_at=as_aware(device.paired_at) if device.paired_at else None,
            revoked_at=as_aware(device.revoked_at) if device.revoked_at else None,
        )


class PairingCodeOut(BaseModel):
    pairing_code: str
    expires_at: datetime


class DeviceCreated(PairingCodeOut):
    device: DeviceOut


class PairRequest(BaseModel):
    pairing_code: str = Field(min_length=4, max_length=20)


class DeviceSession(BaseModel):
    """What a till needs to run: who it is, where it is, and its numbering."""

    device: DeviceOut
    location: LocationOut

    @classmethod
    def of(cls, device: Device, location: Location) -> DeviceSession:
        return cls(device=DeviceOut.of(device), location=LocationOut.model_validate(location))


class PairResponse(DeviceSession):
    token: str  # shown once; the till keeps it, the server only stores its hash
