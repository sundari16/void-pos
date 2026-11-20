"""The delivery-job state machine.

A courier job runs forward through these statuses; the terminal ones never change again.
Statuses arrive out of our control (the courier platform reports them), so the only rules
are: you can move forward (and a provider may skip intermediate steps — a courier can be
assigned and collect before we hear the "accepted" callback), you can cancel a job the
courier hasn't collected yet, and a job can fail at any point before it is delivered.
"""

from __future__ import annotations

REQUESTED = "requested"  # we asked the platform for a courier
ACCEPTED = "accepted"  # a courier was assigned
PICKED_UP = "picked_up"  # the courier has the order from the venue
DELIVERED = "delivered"  # dropped off with the customer
CANCELED = "canceled"  # called off before pickup
FAILED = "failed"  # the courier could not complete it (e.g. returned to venue)

DELIVERY_STATES = frozenset(
    {REQUESTED, ACCEPTED, PICKED_UP, DELIVERED, CANCELED, FAILED}
)
TERMINAL = frozenset({DELIVERED, CANCELED, FAILED})
ACTIVE = frozenset({REQUESTED, ACCEPTED, PICKED_UP})
# A job we can still call off: once the courier has the food, it is too late to cancel.
CANCELABLE = frozenset({REQUESTED, ACCEPTED})

_ALLOWED: dict[str, frozenset[str]] = {
    REQUESTED: frozenset({ACCEPTED, PICKED_UP, DELIVERED, CANCELED, FAILED}),
    ACCEPTED: frozenset({PICKED_UP, DELIVERED, CANCELED, FAILED}),
    PICKED_UP: frozenset({DELIVERED, FAILED}),
    DELIVERED: frozenset(),
    CANCELED: frozenset(),
    FAILED: frozenset(),
}


def can_transition(current: str, target: str) -> bool:
    """True if a delivery may move from ``current`` to ``target`` (a repeat of the current
    status is handled as an idempotent no-op by the service, not here)."""
    return target in _ALLOWED.get(current, frozenset())
