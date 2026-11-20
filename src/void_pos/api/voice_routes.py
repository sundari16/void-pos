"""Voice AI order-intake endpoints.

The external voice stack (``livekit-impl`` / ``cold-caller``) posts a confirmed phone order
here. Two steps mirror the call:

* ``POST /voice/locations/{id}/quote`` — resolve and price the order against the live menu
  and return the read-back summary, **placing nothing**. The stack reads it to the caller.
* ``POST /voice/locations/{id}/orders`` — place the order on the ``voice`` channel.

Like the online storefront, these are channel-intake routes gated by a feature flag
(``voice_ordering_enabled``) rather than a till token: the voice stack is a trusted
server-side integration, and in a deployment it reaches this API over the venue's private
network / behind a gateway. Everything is validated against the live menu regardless.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..errors import NotFound
from ..voice import confirm_voice_order, place_voice_order
from .deps import get_session
from .voice_schemas import VoiceConfirmationOut, VoiceOrderRequest, VoiceOrderResponse

router = APIRouter(prefix="/voice", tags=["voice"])

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _require_enabled(settings: Settings) -> None:
    if not settings.voice_ordering_enabled:
        raise NotFound("voice ordering is off at this venue", code="voice_ordering_disabled")


@router.post("/locations/{location_id}/quote", response_model=VoiceConfirmationOut)
def quote_voice_order(
    location_id: str, body: VoiceOrderRequest, session: SessionDep, settings: SettingsDep
) -> VoiceConfirmationOut:
    """Resolve and price the order for the read-back. Nothing is placed."""
    _require_enabled(settings)
    confirmation = confirm_voice_order(session, location_id, body.to_draft())
    return VoiceConfirmationOut.of(confirmation)


@router.post("/locations/{location_id}/orders", response_model=VoiceOrderResponse)
def place_voice_order_route(
    location_id: str, body: VoiceOrderRequest, session: SessionDep, settings: SettingsDep
) -> VoiceOrderResponse:
    """Place the confirmed order on the ``voice`` channel in the pipeline."""
    _require_enabled(settings)
    placement = place_voice_order(session, location_id, body.to_draft())
    session.commit()
    return VoiceOrderResponse(
        order_id=placement.order.id,
        order_number=placement.order.order_number,
        channel=placement.order.channel,
        state=placement.order.state,
        replayed=placement.replayed,
        confirmation=VoiceConfirmationOut.of(placement.confirmation),
    )
