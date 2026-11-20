"""Uber Direct adapter — the commercial seam (deliberately a stub in the open core).

Uber Direct is Uber's white-label, on-demand courier API: a venue dispatches Uber couriers
for its *own* delivery orders (its website, phone, POS) without listing on the Uber Eats
marketplace. Wiring it up needs a merchant's own partner credentials — a customer id, an
OAuth client id/secret for the ``eats.deliveries`` scope, and a signed-webhook secret — so
the working adapter lives in the commercial module, not in this AGPL core.

This file marks the seam and records exactly what the commercial adapter implements, so the
open core stays provider-neutral and nothing hardcodes Uber's endpoints or payloads:

* ``request_delivery`` -> ``POST /customers/{id}/deliveries`` with the pickup (venue) and
  dropoff (customer) parties, using ``delivery_id`` as the ``external_id`` /
  ``Idempotency-Key`` so a retry never dispatches a second courier. Map Uber's delivery
  ``status`` onto :mod:`void_pos.delivery.states`.
* ``get_status`` -> ``GET /customers/{id}/deliveries/{delivery_id}`` for polling, and its
  signed webhook (``delivery.status`` events) drives the same status-update service the mock
  uses — the courier callback path is identical whichever provider is live.
* ``cancel`` -> ``POST /customers/{id}/deliveries/{delivery_id}/cancel`` while the job is
  still cancelable.

Keeping the real adapter out of the core is the same split as payments: ``CashProvider`` in
the open core, the Stripe Connect adapter in the commercial layer.
"""

from __future__ import annotations

from .provider import DeliveryError, DeliveryRequest, DeliveryResult

_COMMERCIAL = (
    "the Uber Direct courier adapter is a commercial module; it needs Uber partner "
    "credentials and is not part of the open core. Use the 'mock' provider for self-host "
    "and tests."
)


class UberDirectCourier:
    """Placeholder that satisfies the :class:`~void_pos.delivery.provider.DeliveryProvider`
    shape but carries no Uber integration. Every call raises, pointing at the commercial
    module — the open core never reaches a real courier platform."""

    name = "uber_direct"

    def request_delivery(self, request: DeliveryRequest) -> DeliveryResult:
        raise DeliveryError(_COMMERCIAL, code="commercial_provider")

    def get_status(self, external_ref: str) -> DeliveryResult:
        raise DeliveryError(_COMMERCIAL, code="commercial_provider")

    def cancel(self, external_ref: str) -> DeliveryResult:
        raise DeliveryError(_COMMERCIAL, code="commercial_provider")
