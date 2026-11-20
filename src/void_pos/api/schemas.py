from __future__ import annotations

from datetime import date, datetime, time
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from ..clock import as_aware
from ..events.payloads import (
    CURRENCY_PATTERN,
    MAX_LINE_CENTS,
    AdjustmentIn,
    OrderLineIn,
    OrderPlaced,
    check_line_tree,
)
from ..ids import Ulid
from ..menu.service import TillMenu
from ..models import Device, Location, MenuChange, OrderLine, Payment, Refund
from ..orders.views import OrderView

if TYPE_CHECKING:
    from ..board.service import RequestView

# --- till commands ----------------------------------------------------------------


Pin = Annotated[str, StringConstraints(pattern=r"^[0-9]{4,8}$")]


class CommandIn(BaseModel):
    """Every write a till makes carries ids it minted, so a retry replays the original
    result instead of duplicating it. ``seq`` counts up per till."""

    event_id: Ulid
    seq: int = Field(ge=1, le=2_147_483_647)
    occurred_at: AwareDatetime | None = None


class PlaceOrderRequest(CommandIn, OrderPlaced):
    business_date: date | None = None  # default: today in the location's timezone


class OrderLineOut(BaseModel):
    id: str
    parent_line_id: str | None
    menu_item_id: str | None
    modifier_id: str | None
    quantity: int
    name: str
    kitchen_name: str | None
    unit_price_cents: int
    tax_rate: float
    category: str | None
    seat_number: int | None
    course_number: int | None
    sent: bool  # the kitchen has it
    removed: bool  # taken off the order; left out of every total

    @classmethod
    def of(cls, line: OrderLine) -> OrderLineOut:
        return cls(
            id=line.id,
            parent_line_id=line.parent_line_id,
            menu_item_id=line.menu_item_id,
            modifier_id=line.modifier_id,
            quantity=line.quantity,
            name=line.name_snapshot,
            kitchen_name=line.kitchen_name_snapshot,
            unit_price_cents=line.unit_price_cents,
            tax_rate=float(line.tax_rate_snapshot),
            category=line.category_snapshot,
            seat_number=line.seat_number,
            course_number=line.course_number,
            sent=line.sent_at is not None,
            removed=line.removed_at is not None,
        )


class PaymentOut(BaseModel):
    payment_id: str
    provider: str
    external_ref: str | None
    amount_cents: int  # toward the order
    tip_cents: int
    currency: str
    status: str

    @classmethod
    def of(cls, payment: Payment) -> PaymentOut:
        return cls(
            payment_id=payment.id,
            provider=payment.provider,
            external_ref=payment.external_ref,
            amount_cents=payment.amount_cents,
            tip_cents=payment.tip_cents,
            currency=payment.currency,
            status=payment.status,
        )


class AdjustmentOut(BaseModel):
    id: str
    kind: str
    basis: str
    value: int  # basis points (1000 = 10%), or cents
    label: str | None
    amount_cents: int  # what it comes to now: negative for a discount


class RefundOut(BaseModel):
    refund_id: str
    payment_id: str
    amount_cents: int
    reason: str
    provider: str
    external_ref: str | None

    @classmethod
    def of(cls, refund: Refund) -> RefundOut:
        return cls(
            refund_id=refund.id,
            payment_id=refund.payment_id,
            amount_cents=refund.amount_cents,
            reason=refund.reason,
            provider=refund.provider,
            external_ref=refund.external_ref,
        )


class OrderSummary(BaseModel):
    """An order in a list: enough to find the table and see what's owing."""

    id: str
    order_number: int
    state: str
    fulfillment: str
    table_label: str | None
    business_date: date
    created_at: datetime
    subtotal_cents: int  # the items, before discounts and surcharges
    total_cents: int
    gst_cents: int
    balance_cents: int
    tips_cents: int
    refunded_cents: int
    unsent_lines: int  # lines the kitchen hasn't had yet

    @classmethod
    def of(cls, view: OrderView) -> OrderSummary:
        o = view.order
        return cls(
            id=o.id,
            order_number=o.order_number,
            state=o.state,
            fulfillment=o.fulfillment,
            table_label=o.table_label,
            business_date=o.business_date,
            created_at=as_aware(o.created_at),
            subtotal_cents=view.subtotal_cents,
            total_cents=view.total_cents,
            gst_cents=view.gst_cents,
            balance_cents=view.balance_cents,
            tips_cents=view.tips_cents,
            refunded_cents=view.refunded_cents,
            unsent_lines=view.unsent_lines,
        )


class OrderResponse(OrderSummary):
    """The whole order, as a till needs it to carry on with it."""

    lines: list[OrderLineOut]
    adjustments: list[AdjustmentOut]
    payments: list[PaymentOut]
    refunds: list[RefundOut]
    replayed: bool = False

    @classmethod
    def of_view(cls, view: OrderView, *, replayed: bool = False) -> OrderResponse:
        return cls(
            **OrderSummary.of(view).model_dump(),
            lines=[OrderLineOut.of(line) for line in view.lines],
            adjustments=[
                AdjustmentOut(
                    id=a.id,
                    kind=a.kind,
                    basis=a.basis,
                    value=a.value,
                    label=a.label,
                    amount_cents=view.adjustment_cents.get(a.id, 0),
                )
                for a in view.adjustments
            ],
            payments=[PaymentOut.of(p) for p in view.payments],
            refunds=[RefundOut.of(r) for r in view.refunds],
            replayed=replayed,
        )


class RemoveLinesRequest(CommandIn):
    """Take items (with their modifiers) off an open order. An item the kitchen has needs
    a manager's PIN once the venue has a manager."""

    line_ids: list[Ulid] = Field(min_length=1, max_length=500)
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    manager_pin: Pin | None = None


class AdjustRequest(CommandIn, AdjustmentIn):
    """A discount (needs a manager's PIN once the venue has one) or a surcharge. ``value``
    is basis points for a percentage (1000 = 10%) or cents for an amount."""

    manager_pin: Pin | None = None


class RefundRequest(CommandIn):
    refund_id: Ulid
    payment_id: Ulid
    amount_cents: int = Field(ge=1)
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    external_ref: str | None = Field(default=None, min_length=1, max_length=200)  # terminal ref
    manager_pin: Pin | None = None


class AddLinesRequest(CommandIn):
    """More items for a placed order: new item lines with their modifiers."""

    lines: list[OrderLineIn] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _line_tree(self) -> AddLinesRequest:
        check_line_tree(self.lines)
        return self


class KitchenRequest(CommandIn):
    pass


class KitchenResponse(BaseModel):
    order_id: str
    state: str
    printed: bool  # false when no kitchen printer is configured, or on a replay
    ticket_bytes: int
    lines_sent: int  # lines the kitchen hadn't had; 0 means the ticket was a reprint
    replayed: bool = False




class VoidRequest(CommandIn):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    manager_pin: Pin | None = None  # needed once the location has a manager


class VoidResponse(BaseModel):
    order_id: str
    state: str
    printed: bool  # a VOID ticket went to the kitchen (only if it had the order)
    replayed: bool = False


class StripeIntentRequest(BaseModel):
    """Start a card payment through Stripe: what will be charged (amount plus tip)."""

    payment_id: Ulid  # the till's id for the payment it will record
    amount_cents: int = Field(ge=1)
    tip_cents: int = Field(default=0, ge=0, le=MAX_LINE_CENTS)


class StripeIntentOut(BaseModel):
    payment_intent_id: str
    client_secret: str
    publishable_key: str


class StripeConfigOut(BaseModel):
    enabled: bool
    publishable_key: str | None


class PayRequest(CommandIn):
    payment_id: Ulid
    method: Literal["cash", "external", "stripe"] = "cash"
    stripe_payment_intent_id: str | None = Field(default=None, max_length=100)
    external_ref: str | None = Field(default=None, min_length=1, max_length=200)
    amount_cents: int | None = Field(default=None, ge=0)  # default: all that is owing
    tip_cents: int = Field(default=0, ge=0, le=MAX_LINE_CENTS)  # on top of the amount
    currency: str = Field(default="AUD", pattern=CURRENCY_PATTERN)

    @model_validator(mode="after")
    def _external_needs_ref(self) -> PayRequest:
        if self.method == "external" and not self.external_ref:
            raise ValueError("external_ref is required for method 'external'")
        if self.method == "stripe" and not self.stripe_payment_intent_id:
            raise ValueError("stripe_payment_intent_id is required for method 'stripe'")
        return self


class PaymentResponse(BaseModel):
    order_id: str
    payment_id: str
    provider: str
    amount_cents: int
    tip_cents: int
    currency: str
    status: str
    order_state: str
    balance_cents: int  # still owing; the order is paid when this reaches 0
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


def _wall_clock(value: time) -> time:
    if value.tzinfo is not None:
        raise ValueError("use the location's local time without a timezone, e.g. 11:30")
    return value


LocalTime = Annotated[time, AfterValidator(_wall_clock)]


def _no_null(model: BaseModel, required: tuple[str, ...]) -> None:
    cleared = [f for f in required if f in model.model_fields_set and getattr(model, f) is None]
    if cleared:
        raise ValueError(f"{', '.join(cleared)} can't be cleared")


class MenuItemIn(BaseModel):
    name: Name
    base_price_cents: int = Field(ge=0, le=MAX_LINE_CENTS)
    kitchen_name: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=120)
    tax_rate: float = Field(default=0, ge=0, le=1)
    is_available: bool = True
    available_from: LocalTime | None = None
    available_to: LocalTime | None = None


_REQUIRED_ON_ITEM = ("name", "base_price_cents", "tax_rate", "is_available")


class MenuItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    base_price_cents: int | None = Field(default=None, ge=0, le=MAX_LINE_CENTS)
    kitchen_name: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=120)
    tax_rate: float | None = Field(default=None, ge=0, le=1)
    is_available: bool | None = None
    available_from: LocalTime | None = None
    available_to: LocalTime | None = None

    @model_validator(mode="after")
    def _no_null_for_required(self) -> MenuItemUpdate:
        _no_null(self, _REQUIRED_ON_ITEM)
        return self


class AvailabilityIn(BaseModel):
    """Switch a dish off when it sells out (or back on). From a till this needs a
    manager's PIN once the location has a manager."""

    is_available: bool
    manager_pin: Pin | None = None


class MenuItemEdit(MenuItemUpdate):
    id: Ulid


class BulkMenuUpdate(BaseModel):
    """Edits to several items at once: all of them apply, or none do."""

    updates: list[MenuItemEdit] = Field(min_length=1, max_length=500)


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
    available_from: time | None
    available_to: time | None


class ModifierGroupIn(BaseModel):
    name: Name
    min_select: int = Field(default=0, ge=0, le=100)
    max_select: int | None = Field(default=None, ge=0, le=100)
    available_from: LocalTime | None = None
    available_to: LocalTime | None = None


class ModifierGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    min_select: int | None = Field(default=None, ge=0, le=100)
    max_select: int | None = Field(default=None, ge=0, le=100)
    available_from: LocalTime | None = None
    available_to: LocalTime | None = None

    @model_validator(mode="after")
    def _no_null_for_required(self) -> ModifierGroupUpdate:
        _no_null(self, ("name", "min_select"))
        return self


class ModifierGroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    location_id: str
    name: str
    min_select: int
    max_select: int | None
    available_from: time | None
    available_to: time | None


class ModifierIn(BaseModel):
    name: Name
    price_delta_cents: int = Field(default=0, ge=-MAX_LINE_CENTS, le=MAX_LINE_CENTS)
    kitchen_name: str | None = Field(default=None, max_length=200)


class ModifierUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    price_delta_cents: int | None = Field(default=None, ge=-MAX_LINE_CENTS, le=MAX_LINE_CENTS)
    kitchen_name: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _no_null_for_required(self) -> ModifierUpdate:
        _no_null(self, ("name", "price_delta_cents"))
        return self


class ModifierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    group_id: str
    name: str
    kitchen_name: str | None
    price_delta_cents: int


class TillMenuItem(MenuItemOut):
    modifier_group_ids: list[str]
    available_now: bool


class TillModifierGroup(ModifierGroupOut):
    modifiers: list[ModifierOut]
    available_now: bool


class TillMenuOut(BaseModel):
    """A location's whole menu for a till; ``available_now`` is as of ``local_time``."""

    location_id: str
    timezone: str
    local_time: time
    items: list[TillMenuItem]
    modifier_groups: list[TillModifierGroup]

    @classmethod
    def of(cls, menu: TillMenu) -> TillMenuOut:
        return cls(
            location_id=menu.location.id,
            timezone=menu.location.timezone,
            local_time=menu.local_time.replace(microsecond=0),
            items=[
                TillMenuItem(
                    **MenuItemOut.model_validate(entry.item).model_dump(),
                    modifier_group_ids=entry.modifier_group_ids,
                    available_now=entry.available_now,
                )
                for entry in menu.items
            ],
            modifier_groups=[
                TillModifierGroup(
                    **ModifierGroupOut.model_validate(entry.group).model_dump(),
                    modifiers=[ModifierOut.model_validate(m) for m in entry.modifiers],
                    available_now=entry.available_now,
                )
                for entry in menu.groups
            ],
        )


class MenuChangeOut(BaseModel):
    id: str
    number: int
    entity: str
    entity_id: str
    action: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    reverts_id: str | None
    actor: str
    changed_at: datetime

    @classmethod
    def of(cls, change: MenuChange) -> MenuChangeOut:
        return cls(
            id=change.id,
            number=change.number,
            entity=change.entity,
            entity_id=change.entity_id,
            action=change.action,
            before=change.before,
            after=change.after,
            reverts_id=change.reverts_id,
            actor=change.actor,
            changed_at=as_aware(change.created_at),
        )


# --- venue setup and pairing -------------------------------------------------------


class StaffIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    role: Literal["staff", "manager"] = "staff"
    pin: Pin


class PinIn(BaseModel):
    pin: Pin


class StaffOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    location_id: str
    name: str
    role: str
    active: bool


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
    business_name: str | None = None
    abn: str | None = None
    address: str | None = None
    phone: str | None = None
    receipt_footer: str | None = None
    holiday_surcharge_bp: int | None = None  # 1500 = 15%


class VenueDetailsIn(BaseModel):
    """What customer receipts show. A blank value clears it; an ABN makes receipts tax
    invoices."""

    business_name: str | None = Field(default=None, max_length=200)
    abn: str | None = Field(default=None, max_length=20)
    address: str | None = Field(default=None, max_length=300)
    phone: str | None = Field(default=None, max_length=40)
    receipt_footer: str | None = Field(default=None, max_length=300)
    holiday_surcharge_bp: int | None = Field(default=None, ge=0, le=10_000)  # 0 clears it


class ReceiptLineOut(BaseModel):
    quantity: int
    name: str
    amount_cents: int
    is_modifier: bool


class ReceiptPaymentOut(BaseModel):
    label: str
    amount_cents: int
    reference: str | None


class ReceiptOut(BaseModel):
    title: str
    venue_name: str
    address: str | None
    phone: str | None
    abn: str | None
    order_number: int
    table_label: str | None
    ordered_at: str
    printed_at: str
    lines: list[ReceiptLineOut]
    total_cents: int
    gst_cents: int
    payments: list[ReceiptPaymentOut]
    balance_cents: int
    subtotal_cents: int | None
    adjustments: list[ReceiptLineOut]
    refunds: list[ReceiptPaymentOut]
    voided: bool
    footer: str | None


class ReceiptPrinted(BaseModel):
    printed: bool
    ticket_bytes: int


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
    """What a till needs to run: who it is, where it is, and where its counters resume
    (``device.last_seq + 1`` for seq, ``next_order_number`` for order numbers)."""

    device: DeviceOut
    location: LocationOut
    next_order_number: int

    @classmethod
    def of(cls, device: Device, location: Location, *, next_order_number: int) -> DeviceSession:
        return cls(
            device=DeviceOut.of(device),
            location=LocationOut.model_validate(location),
            next_order_number=next_order_number,
        )


class PairResponse(DeviceSession):
    token: str  # shown once; the till keeps it, the server only stores its hash


# --- request board -----------------------------------------------------------------

RequestStatus = Literal["submitted", "under_review", "planned", "shipped"]


class RequestSubmitIn(BaseModel):
    """A new request for the board. The author is the venue or operator asking for it."""

    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)]
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    author: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class RequestStatusIn(BaseModel):
    status: RequestStatus


class RequestOut(BaseModel):
    id: str
    title: str
    body: str
    author: str
    status: RequestStatus
    votes: int  # COUNT of the upvote ledger, never a stored field
    voted: bool  # whether the caller (this till or the admin) has upvoted it
    created_at: datetime
    updated_at: datetime  # moves when the status changes

    @classmethod
    def of(cls, view: RequestView) -> RequestOut:
        request = view.request
        return cls(
            id=request.id,
            title=request.title,
            body=request.body,
            author=request.author,
            status=request.status,  # type: ignore[arg-type]
            votes=view.votes,
            voted=view.voted,
            created_at=as_aware(request.created_at),
            updated_at=as_aware(request.updated_at),
        )


# --- marketing campaigns -----------------------------------------------------------


class CampaignIn(BaseModel):
    """Send a message to the location's opted-in customers on one channel. ``{name}`` in
    the body is filled per recipient. A dry run selects the audience but sends nothing."""

    channel: Literal["sms", "email"]
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1600)]
    subject: str | None = Field(default=None, max_length=200)  # email only
    limit: int | None = Field(default=None, ge=1, le=100_000)
    dry_run: bool = False


class SendResultOut(BaseModel):
    to: str
    status: str  # sent | skipped | failed
    detail: str | None = None
    external_ref: str | None = None
    customer_id: str | None = None


class CampaignResultOut(BaseModel):
    channel: str
    total: int
    sent: int
    failed: int
    skipped: int
    results: list[SendResultOut]


# --- delivery aggregators ---------------------------------------------------------


class AggregatorOrderOut(BaseModel):
    """What the aggregator webhook reports back about one partner order."""

    provider: str
    external_order_id: str
    display_id: str | None
    status: str  # accepted | rejected
    order_id: str | None  # null for a rejected order (none entered the sales pipeline)
    order_number: int | None
    order_state: str | None
    replayed: bool  # the partner order had already been handled (idempotent)


class AggregatorMenuSyncOut(BaseModel):
    provider: str
    status: str  # published | queued | skipped
    item_count: int
    external_ref: str | None
