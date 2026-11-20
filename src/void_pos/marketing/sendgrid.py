"""SendGrid email adapter — a config-driven ``Sender``, mirroring the Stripe HTTP client.

The venue supplies its own SendGrid API key and a verified sender address. The key stays
on the server. Tests inject a mock ``httpx.Client`` and never reach SendGrid.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from .sender import CampaignMessage, Channel, SendResult

API = "https://api.sendgrid.com/v3"
log = logging.getLogger(__name__)


@dataclass
class SendGridSender:
    api_key: str
    from_email: str
    channel: Channel = "email"
    client: httpx.Client | None = None

    def _http(self) -> httpx.Client:
        return self.client or httpx.Client(base_url=API, timeout=20)

    def send(self, message: CampaignMessage) -> SendResult:
        payload = {
            "personalizations": [{"to": [{"email": message.to}]}],
            "from": {"email": self.from_email},
            "subject": message.subject or "A message from us",
            "content": [{"type": "text/plain", "value": message.body}],
        }
        try:
            response = self._http().post(
                "/mail/send",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        except httpx.HTTPError as exc:
            log.warning("SendGrid unreachable for %s: %s", message.to, exc)
            return SendResult(
                to=message.to, status="failed", detail="couldn't reach SendGrid",
                customer_id=message.customer_id,
            )
        if response.status_code >= 400:
            return SendResult(
                to=message.to, status="failed", detail="SendGrid refused the message",
                customer_id=message.customer_id,
            )
        # SendGrid returns 202 with an empty body and the id in a header.
        ref = response.headers.get("X-Message-Id")
        return SendResult(
            to=message.to, status="sent", external_ref=ref, customer_id=message.customer_id
        )
