"""The campaign-send seam: a ``Sender`` protocol plus the open-core log sender.

This mirrors the payments provider seam. The open core ships a no-op ``LogSender`` that
records what it would send; real Twilio (SMS) and SendGrid (email) adapters implement the
same protocol and are selected by config (see ``service.make_sender``). Nothing here holds
provider credentials beyond what the venue puts in its own ``.env``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Protocol

log = logging.getLogger(__name__)

Channel = Literal["sms", "email"]


@dataclass(frozen=True)
class CampaignMessage:
    """One rendered message to one recipient."""

    channel: Channel
    to: str  # phone number (sms) or email address (email)
    body: str
    subject: str | None = None  # email only
    customer_id: str | None = None
    customer_name: str | None = None


@dataclass(frozen=True)
class SendResult:
    to: str
    status: Literal["sent", "skipped", "failed"]
    detail: str | None = None
    external_ref: str | None = None  # the provider's message id, when it gives one
    customer_id: str | None = None


class Sender(Protocol):
    """A campaign backend. The log sender is open core; Twilio/SendGrid are config-driven
    adapters implementing this same protocol."""

    channel: Channel

    def send(self, message: CampaignMessage) -> SendResult: ...


@dataclass
class LogSender:
    """The open-core default: record the message and report it sent, without contacting any
    external service. Lets a venue exercise the whole campaign path with no accounts."""

    channel: Channel = "sms"

    def send(self, message: CampaignMessage) -> SendResult:
        log.info(
            "campaign (%s) to %s: %s%s",
            message.channel,
            message.to,
            f"[{message.subject}] " if message.subject else "",
            message.body,
        )
        return SendResult(
            to=message.to, status="sent", detail="logged (no-op sender)",
            customer_id=message.customer_id,
        )
