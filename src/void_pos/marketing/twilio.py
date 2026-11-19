"""Twilio SMS adapter — a config-driven ``Sender``, mirroring the Stripe HTTP client.

The venue supplies its own Twilio account SID, auth token and sender number. Credentials
stay on the server. Tests inject a mock ``httpx.Client`` and never reach Twilio.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from .sender import CampaignMessage, Channel, SendResult

API = "https://api.twilio.com/2010-04-01"
log = logging.getLogger(__name__)


@dataclass
class TwilioSender:
    account_sid: str
    auth_token: str
    from_number: str
    channel: Channel = "sms"
    client: httpx.Client | None = None

    def _http(self) -> httpx.Client:
        return self.client or httpx.Client(base_url=API, timeout=20)

    def send(self, message: CampaignMessage) -> SendResult:
        data = {"To": message.to, "From": self.from_number, "Body": message.body}
        try:
            response = self._http().post(
                f"/Accounts/{self.account_sid}/Messages.json",
                data=data,
                auth=(self.account_sid, self.auth_token),
            )
        except httpx.HTTPError as exc:
            log.warning("Twilio unreachable for %s: %s", message.to, exc)
            return SendResult(
                to=message.to, status="failed", detail="couldn't reach Twilio",
                customer_id=message.customer_id,
            )
        if response.status_code >= 400:
            body = _error(response)
            return SendResult(
                to=message.to, status="failed", detail=body, customer_id=message.customer_id
            )
        sid = response.json().get("sid")
        return SendResult(
            to=message.to, status="sent", external_ref=sid, customer_id=message.customer_id
        )


def _error(response: httpx.Response) -> str:
    try:
        return str(response.json().get("message") or "Twilio refused the message")
    except ValueError:
        return "Twilio refused the message"
