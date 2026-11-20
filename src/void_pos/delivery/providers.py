"""Open-core courier providers: a fully testable mock, plus the provider registry.

``MockCourier`` is to delivery what ``CashProvider`` is to payments — a real implementation
of the seam that never touches the network, so the whole dispatch/track/cancel flow is
exercised in tests and in a self-host demo. The commercial Uber Direct adapter is resolved
by name but lives outside this repo (see ``uber_direct``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import NotFound
from . import states
from .provider import DeliveryError, DeliveryProvider, DeliveryRequest, DeliveryResult

# Partner platforms whose adapters are commercial (need partner credentials + signed
# webhooks) and so are not shipped in the open core.
COMMERCIAL_PROVIDERS = frozenset({"uber_direct", "doordash_drive"})


@dataclass
class _Job:
    delivery_id: str
    external_ref: str
    status: str
    courier_name: str | None
    tracking_url: str | None
    fee_cents: int


@dataclass
class MockCourier:
    """An in-memory courier that behaves like the real seam without a network.

    ``request_delivery`` is idempotent on ``delivery_id`` (a retry returns the same job and
    the same ``external_ref``), just like a real platform's idempotency key. ``get_status``
    and ``cancel`` read and move the stored job; tests drive a job forward with ``advance``
    to stand in for the platform's status callbacks. Every call is recorded in ``calls`` so
    a test can assert the courier was actually asked.
    """

    name: str = "mock"
    fee_cents: int = 799  # a flat A$7.99 courier fee, in cents
    initial_status: str = states.REQUESTED
    courier_name: str | None = None
    tracking_base: str = "https://couriers.example/track"
    jobs: dict[str, _Job] = field(default_factory=dict)  # external_ref -> job
    calls: list[tuple[str, str]] = field(default_factory=list)  # (method, id)
    _by_delivery: dict[str, str] = field(default_factory=dict)  # delivery_id -> external_ref

    def request_delivery(self, request: DeliveryRequest) -> DeliveryResult:
        self.calls.append(("request_delivery", request.delivery_id))
        seen = self._by_delivery.get(request.delivery_id)
        if seen is not None:  # idempotent retry: the same courier, not a second one
            return self._result(self.jobs[seen])
        external_ref = f"mock_{request.delivery_id[-10:].lower()}"
        job = _Job(
            delivery_id=request.delivery_id,
            external_ref=external_ref,
            status=self.initial_status,
            courier_name=self.courier_name,
            tracking_url=f"{self.tracking_base}/{external_ref}",
            fee_cents=self.fee_cents,
        )
        self.jobs[external_ref] = job
        self._by_delivery[request.delivery_id] = external_ref
        return self._result(job)

    def get_status(self, external_ref: str) -> DeliveryResult:
        self.calls.append(("get_status", external_ref))
        return self._result(self._job(external_ref))

    def cancel(self, external_ref: str) -> DeliveryResult:
        # A real courier API is addressed by external_ref and is stateless across our
        # process instances — the service builds a fresh provider per request, like the
        # Stripe path. So cancelling a ref this instance never issued still succeeds (and
        # cancelling is idempotent), rather than erroring on an empty in-memory store.
        self.calls.append(("cancel", external_ref))
        job = self.jobs.get(external_ref)
        if job is None:
            return DeliveryResult(
                provider=self.name, status=states.CANCELED, external_ref=external_ref
            )
        if job.status in states.CANCELABLE:
            job.status = states.CANCELED
        return self._result(job)

    def advance(
        self,
        external_ref: str,
        status: str,
        *,
        courier_name: str | None = None,
        fee_cents: int | None = None,
    ) -> None:
        """Test / demo helper: move a job to ``status`` as a platform callback would."""
        job = self._job(external_ref)
        job.status = status
        if courier_name is not None:
            job.courier_name = courier_name
        if fee_cents is not None:
            job.fee_cents = fee_cents

    def _job(self, external_ref: str) -> _Job:
        job = self.jobs.get(external_ref)
        if job is None:
            raise DeliveryError(f"no mock delivery {external_ref!r}", code="delivery_not_found")
        return job

    def _result(self, job: _Job) -> DeliveryResult:
        return DeliveryResult(
            provider=self.name,
            status=job.status,
            external_ref=job.external_ref,
            fee_cents=job.fee_cents,
            currency="AUD",
            courier_name=job.courier_name,
            tracking_url=job.tracking_url,
        )


def build_provider(name: str) -> DeliveryProvider:
    """Resolve a courier provider by name. The open core ships only ``mock``; a commercial
    partner name resolves in the commercial build, not here."""
    if name == "mock":
        return MockCourier()
    if name in COMMERCIAL_PROVIDERS:
        raise NotFound(
            f"the {name!r} courier adapter is a commercial module, not part of the open core",
            code="commercial_provider",
        )
    raise NotFound(f"unknown delivery provider {name!r}", code="unknown_provider")
