"""The voice-stack seam: the boundary where the external telephony/LLM agent plugs in.

A customer phones the venue. The *voice stack* (LiveKit-based, in the separate
``livekit-impl`` / ``cold-caller`` repos) answers the call, runs speech-to-text and an
LLM to work out what the caller wants, and reads the order back for confirmation. None of
that lives here — this module defines only the **contract** between that stack and Void
POS:

* what the stack hands us — a :class:`VoiceOrderDraft` (items and modifiers by *name* or
  menu id, plus pickup/delivery and the caller's phone);
* what we hand back — a :class:`VoiceOrderConfirmation` (the resolved, priced order for the
  stack to read back before it finalises);
* the :class:`VoiceAgent` protocol the stack implements.

The POS side (``voice.service`` / ``voice.resolve``) validates a draft against the live
menu, prices it, and places it on the ``voice`` channel. Tests supply a fake
:class:`VoiceAgent`; the shipped :class:`ScriptedVoiceAgent` replays a fixed draft so the
CLI (and a demo) can drive the whole flow with no telephony at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

VoiceFulfillment = Literal["pickup", "delivery"]


# --- what the voice stack hands us (speech already turned into structure) -------------


@dataclass(frozen=True)
class InboundCall:
    """One inbound phone call, routed to a venue. ``from_number`` is the caller id; the
    real stack also knows a provider call id, kept here only for logs/tracing."""

    location_id: str
    from_number: str
    call_id: str | None = None


@dataclass(frozen=True)
class DraftModifier:
    """A modifier the caller asked for, named the way they said it ("extra hot") or, when
    the stack already matched it to the catalogue, by ``modifier_id``."""

    name: str | None = None
    modifier_id: str | None = None
    quantity: int = 1


@dataclass(frozen=True)
class DraftItem:
    """One item the caller asked for, by spoken ``name`` or resolved ``menu_item_id``."""

    name: str | None = None
    menu_item_id: str | None = None
    quantity: int = 1
    modifiers: list[DraftModifier] = field(default_factory=list)


@dataclass(frozen=True)
class VoiceOrderDraft:
    """A whole order as the voice stack understood it — still unverified against the menu.

    ``order_id`` is an optional caller-stable id (a ULID the stack mints once for the
    call): pass it and a retry of the same order replays instead of placing twice.
    """

    items: list[DraftItem]
    customer_phone: str
    fulfillment: VoiceFulfillment = "pickup"
    customer_name: str | None = None
    note: str | None = None
    delivery_address: str | None = None
    order_id: str | None = None


# --- what we hand back for the read-back ----------------------------------------------


@dataclass(frozen=True)
class ConfirmedModifier:
    name: str
    quantity: int
    price_delta_cents: int


@dataclass(frozen=True)
class ConfirmedItem:
    name: str
    quantity: int
    unit_price_cents: int
    line_total_cents: int  # the item and its modifiers, times quantity
    modifiers: list[ConfirmedModifier] = field(default_factory=list)


@dataclass(frozen=True)
class VoiceOrderConfirmation:
    """The resolved, priced order for the stack to read back before finalising.

    Everything here is drawn from the live menu (names, prices and GST are the venue's own,
    not the caller's words), so when the caller says "yes" the order placed is exactly this.
    """

    order_id: str
    customer_phone: str
    fulfillment: VoiceFulfillment
    items: list[ConfirmedItem]
    subtotal_cents: int
    total_cents: int
    gst_cents: int
    customer_name: str | None = None
    note: str | None = None
    delivery_address: str | None = None
    currency: str = "AUD"

    @property
    def read_back(self) -> str:
        """A plain-language summary the agent can speak back, e.g.
        ``"2 Masala Dosa (extra hot), 1 Mango Lassi — $41.50 total for pickup."``"""
        parts = []
        for item in self.items:
            text = f"{item.quantity} {item.name}"
            if item.modifiers:
                text += " (" + ", ".join(m.name for m in item.modifiers) + ")"
            parts.append(text)
        where = (
            f"delivery to {self.delivery_address}"
            if self.fulfillment == "delivery" and self.delivery_address
            else self.fulfillment
        )
        return f"{', '.join(parts)} — {_money(self.total_cents, self.currency)} total for {where}."


def _money(cents: int, currency: str = "AUD") -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) // 100}.{abs(cents) % 100:02d} {currency}"


# --- the seam -------------------------------------------------------------------------


@runtime_checkable
class VoiceAgent(Protocol):
    """The external voice stack, as the POS sees it. ``livekit-impl`` / ``cold-caller``
    implement this; tests and the CLI supply stand-ins.

    The POS never calls telephony or an LLM directly — it only ``take_order`` (listen, get
    a structured draft) and ``confirm`` (read the priced order back, get a yes/no)."""

    def take_order(self, call: InboundCall) -> VoiceOrderDraft:
        """Listen to the caller and return what they asked for, as structure."""
        ...

    def confirm(self, call: InboundCall, confirmation: VoiceOrderConfirmation) -> bool:
        """Read ``confirmation`` back to the caller; return whether they confirmed it."""
        ...


class ScriptedVoiceAgent:
    """A non-telephony stand-in that replays a fixed draft and a fixed yes/no. Used by the
    ``void-pos voice-order`` CLI to drive the flow end to end, and handy in tests. It does
    no speech and no LLM — it is the seam filled in with a canned script, not the voice
    stack."""

    def __init__(self, draft: VoiceOrderDraft, *, accept: bool = True) -> None:
        self._draft = draft
        self._accept = accept

    def take_order(self, call: InboundCall) -> VoiceOrderDraft:
        return self._draft

    def confirm(self, call: InboundCall, confirmation: VoiceOrderConfirmation) -> bool:
        return self._accept
