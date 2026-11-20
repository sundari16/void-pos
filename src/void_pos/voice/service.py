"""Voice order intake: confirm (read-back), place, and the end-to-end call flow.

Three entry points, in the order a call uses them:

* :func:`confirm_voice_order` — resolve and price a draft against the live menu and hand
  back the summary, **writing nothing**. This is the spec's confirm-back step: the stack
  reads it to the caller before anything is placed.
* :func:`place_voice_order` — resolve again, capture the caller as a CRM customer, and
  place the order on the ``voice`` channel through the one shared ``place_order`` path.
* :func:`run_voice_call` — drive the whole flow against a :class:`VoiceAgent`: take the
  order, confirm it, and place it only if the caller says yes. This is where the external
  voice stack plugs in; tests pass a fake agent.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..commands import Command
from ..devices import get_location
from ..events import OrderPlaced
from ..ids import new_ulid
from ..models import Customer, Location, Order
from ..orders import place_order
from .agent import InboundCall, VoiceAgent, VoiceOrderConfirmation, VoiceOrderDraft
from .device import next_voice_number, voice_device
from .resolve import resolve_draft


@dataclass(frozen=True)
class VoicePlacement:
    order: Order
    confirmation: VoiceOrderConfirmation
    replayed: bool  # True when this draft's order_id was already placed (a retry)


@dataclass(frozen=True)
class VoiceCallResult:
    placed: bool
    confirmation: VoiceOrderConfirmation
    order: Order | None = None
    replayed: bool = False


def confirm_voice_order(
    session: Session, location_id: str, draft: VoiceOrderDraft
) -> VoiceOrderConfirmation:
    """Resolve and price ``draft`` against the live menu and return the read-back summary.
    Nothing is written — raises if an item or modifier can't be resolved or isn't allowed,
    so a summary that comes back is one the order can actually be placed from."""
    location = get_location(session, location_id)
    order_id = draft.order_id or new_ulid()
    return resolve_draft(session, location, draft, order_id=order_id).confirmation


def place_voice_order(
    session: Session, location_id: str, draft: VoiceOrderDraft
) -> VoicePlacement:
    """Place ``draft`` on the ``voice`` channel: resolve it, capture the caller as a
    customer, and run it through the shared order pipeline. Idempotent on ``draft.order_id``
    — re-placing the same order replays to the first result instead of placing twice."""
    location = get_location(session, location_id)
    order_id = draft.order_id or new_ulid()
    resolved = resolve_draft(session, location, draft, order_id=order_id)
    customer = _resolve_customer(session, location, draft)

    device = voice_device(session, location_id, for_update=True)
    placed = OrderPlaced(
        order_id=order_id,
        order_number=next_voice_number(session, device),
        channel="voice",
        fulfillment=draft.fulfillment,
        customer_id=customer.id if customer is not None else None,
        lines=resolved.lines,
    )
    # One voice order is one event; keying the event on the order id gives a retry of the
    # same call a natural idempotency key.
    command = Command(event_id=order_id, seq=device.last_seq + 1)
    outcome = place_order(session, device, command, placed)
    return VoicePlacement(
        order=outcome.order, confirmation=resolved.confirmation, replayed=outcome.replayed
    )


def run_voice_call(session: Session, agent: VoiceAgent, call: InboundCall) -> VoiceCallResult:
    """The full inbound flow for one call. ``agent`` is the external voice stack: it turns
    speech into a draft (``take_order``) and reads the priced order back for a yes/no
    (``confirm``). The order is placed only on a yes."""
    draft = agent.take_order(call)
    confirmation = confirm_voice_order(session, call.location_id, draft)
    if not agent.confirm(call, confirmation):
        return VoiceCallResult(placed=False, confirmation=confirmation)
    placement = place_voice_order(session, call.location_id, draft)
    return VoiceCallResult(
        placed=True,
        confirmation=placement.confirmation,
        order=placement.order,
        replayed=placement.replayed,
    )


def _resolve_customer(
    session: Session, location: Location, draft: VoiceOrderDraft
) -> Customer | None:
    """Match the caller to a CRM customer by phone within the org, creating one if new, so
    a phone order is linked to someone the venue can call back."""
    phone = (draft.customer_phone or "").strip()
    if not phone:
        return None
    customer = session.scalars(
        select(Customer).where(Customer.org_id == location.org_id, Customer.phone == phone)
    ).first()
    if customer is None:
        customer = Customer(
            org_id=location.org_id,
            location_id=location.id,
            phone=phone,
            name=draft.customer_name,
        )
        session.add(customer)
        session.flush()
    elif draft.customer_name and not customer.name:
        customer.name = draft.customer_name
        session.flush()
    return customer
