"""Incoming delivery-aggregator orders (webhook) and menu sync-out.

The order webhook is authenticated by a shared secret the request carries in the
``X-Void-Aggregator-Secret`` header (``VOIDPOS_AGGREGATOR_WEBHOOK_SECRET``); unset means
the webhook is off (503). A real partner adapter verifies that platform's own signed
webhooks instead — this is the open-core seam, and the only provider the open core ships is
``mock``. Menu sync-out is admin-only, like the rest of venue setup.
"""

from __future__ import annotations

import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header
from sqlalchemy.orm import Session

from ..aggregators import (
    IngestOutcome,
    RejectOutcome,
    build_provider,
    ingest_order,
    reject_order,
    sync_menu_out,
)
from ..config import Settings, get_settings
from ..errors import Forbidden, ServiceUnavailable
from ..printing import Printer, Rasterizer
from .auth import AdminDep
from .deps import get_printer, get_rasterizer, get_session
from .schemas import AggregatorMenuSyncOut, AggregatorOrderOut

router = APIRouter(prefix="/aggregators", tags=["aggregators"])

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
PrinterDep = Annotated[Printer | None, Depends(get_printer)]
RasterizerDep = Annotated[Rasterizer | None, Depends(get_rasterizer)]


def require_aggregator_secret(
    settings: SettingsDep,
    x_void_aggregator_secret: Annotated[str | None, Header()] = None,
) -> None:
    """Gate the webhook on the shared secret, in constant time. Off when unset."""
    secret = settings.aggregator_webhook_secret
    if secret is None:
        raise ServiceUnavailable(
            "the aggregator webhook is off; set VOIDPOS_AGGREGATOR_WEBHOOK_SECRET",
            code="aggregator_disabled",
        )
    given = (x_void_aggregator_secret or "").encode("utf-8")
    if not hmac.compare_digest(given, secret.get_secret_value().encode("utf-8")):
        raise Forbidden(
            "the aggregator webhook secret is missing or wrong", code="invalid_webhook_secret"
        )


WebhookDep = Annotated[None, Depends(require_aggregator_secret)]


def _ingested(provider: str, outcome: IngestOutcome) -> AggregatorOrderOut:
    return AggregatorOrderOut(
        provider=provider,
        external_order_id=outcome.tie.external_order_id,
        display_id=outcome.tie.display_id,
        status=outcome.tie.status,
        order_id=outcome.order.id,
        order_number=outcome.order.order_number,
        order_state=outcome.order.state,
        replayed=outcome.replayed,
    )


def _rejected(provider: str, outcome: RejectOutcome) -> AggregatorOrderOut:
    return AggregatorOrderOut(
        provider=provider,
        external_order_id=outcome.tie.external_order_id,
        display_id=outcome.tie.display_id,
        status=outcome.tie.status,
        order_id=None,
        order_number=None,
        order_state=None,
        replayed=outcome.replayed,
    )


@router.post("/{provider}/locations/{location_id}/orders", response_model=AggregatorOrderOut)
def ingest_order_webhook(
    provider: str,
    location_id: str,
    session: SessionDep,
    printer: PrinterDep,
    rasterizer: RasterizerDep,
    _: WebhookDep,
    raw: Annotated[dict[str, Any], Body()],
    prep_minutes: int | None = None,
) -> AggregatorOrderOut:
    """Inject a partner order as a paid, kitchen-bound aggregator-channel order, and confirm
    it with the partner. Idempotent on the partner's order id."""
    adapter = build_provider(provider)
    outcome = ingest_order(
        session, adapter, location_id, raw, prep_minutes=prep_minutes,
        printer=printer, rasterizer=rasterizer,
    )
    session.commit()
    return _ingested(adapter.name, outcome)


@router.post(
    "/{provider}/locations/{location_id}/orders/reject", response_model=AggregatorOrderOut
)
def reject_order_webhook(
    provider: str,
    location_id: str,
    session: SessionDep,
    _: WebhookDep,
    raw: Annotated[dict[str, Any], Body()],
    reason: str = "the venue can't make this order",
) -> AggregatorOrderOut:
    """Decline a partner order before it enters the sales pipeline (the platform refunds the
    customer). Idempotent on the partner's order id."""
    adapter = build_provider(provider)
    outcome = reject_order(session, adapter, location_id, raw, reason=reason)
    session.commit()
    return _rejected(adapter.name, outcome)


@router.post(
    "/{provider}/locations/{location_id}/menu", response_model=AggregatorMenuSyncOut
)
def sync_menu_route(
    provider: str, location_id: str, session: SessionDep, _: AdminDep
) -> AggregatorMenuSyncOut:
    """Publish the venue's live menu to the platform. Admin-only, like venue setup."""
    adapter = build_provider(provider)
    result = sync_menu_out(session, adapter, location_id)
    session.commit()
    return AggregatorMenuSyncOut(
        provider=result.provider,
        status=result.status,
        item_count=result.item_count,
        external_ref=result.external_ref,
    )
