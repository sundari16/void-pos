"""Send a campaign to a venue's opted-in customers over the CRM.

The audience is always filtered to customers who opted in *and* who have the channel's
contact detail (a phone for SMS, an email for email) — whatever is asked for, a customer
who didn't consent is never contacted. ``{name}`` in the body is filled per recipient.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..devices import get_location
from ..errors import InvalidRequest
from ..models import Customer
from .sender import CampaignMessage, Channel, LogSender, Sender, SendResult
from .sendgrid import SendGridSender
from .twilio import TwilioSender


@dataclass(frozen=True)
class CampaignResult:
    channel: Channel
    total: int  # recipients targeted
    sent: int
    failed: int
    skipped: int  # dry run, or no contact detail
    results: list[SendResult] = field(default_factory=list)


def select_recipients(
    session: Session, location_id: str, channel: Channel, *, limit: int | None = None
) -> list[Customer]:
    """The org's opted-in customers who can be reached on ``channel``, oldest first."""
    location = get_location(session, location_id)
    contact = Customer.phone if channel == "sms" else Customer.email
    stmt = (
        select(Customer)
        .where(
            Customer.org_id == location.org_id,
            Customer.marketing_opt_in.is_(True),
            contact.is_not(None),
            contact != "",
        )
        .order_by(Customer.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt).all())


def render(template: str, customer: Customer) -> str:
    """Fill ``{name}`` with the customer's first name (or 'there'). Unknown braces are left
    as-is so an accidental ``{`` in copy doesn't blow up the send."""
    first = (customer.name or "there").split(" ")[0]
    return template.replace("{name}", first)


def send_campaign(
    session: Session,
    *,
    location_id: str,
    channel: Channel,
    body: str,
    subject: str | None = None,
    sender: Sender,
    limit: int | None = None,
    dry_run: bool = False,
) -> CampaignResult:
    """Render and send ``body`` to every opted-in customer reachable on ``channel``. A dry
    run selects the audience and renders but sends nothing (every result is 'skipped')."""
    recipients = select_recipients(session, location_id, channel, limit=limit)
    results: list[SendResult] = []
    for customer in recipients:
        to = (customer.phone if channel == "sms" else customer.email) or ""
        message = CampaignMessage(
            channel=channel,
            to=to,
            body=render(body, customer),
            subject=subject,
            customer_id=customer.id,
            customer_name=customer.name,
        )
        if dry_run:
            results.append(
                SendResult(to=to, status="skipped", detail="dry run", customer_id=customer.id)
            )
        else:
            results.append(sender.send(message))
    return CampaignResult(
        channel=channel,
        total=len(recipients),
        sent=sum(1 for r in results if r.status == "sent"),
        failed=sum(1 for r in results if r.status == "failed"),
        skipped=sum(1 for r in results if r.status == "skipped"),
        results=results,
    )


def make_sender(settings: Settings, channel: Channel) -> Sender:
    """The configured sender for ``channel``: the no-op log sender by default, or the real
    adapter when the venue selected and configured one."""
    if channel == "sms":
        choice = settings.marketing_sms_sender
        if choice == "twilio":
            if not (
                settings.twilio_account_sid
                and settings.twilio_auth_token
                and settings.twilio_from_number
            ):
                raise InvalidRequest(
                    "set VOIDPOS_TWILIO_ACCOUNT_SID, _AUTH_TOKEN and _FROM_NUMBER to send SMS",
                    code="twilio_not_configured",
                )
            return TwilioSender(
                account_sid=settings.twilio_account_sid,
                auth_token=settings.twilio_auth_token.get_secret_value(),
                from_number=settings.twilio_from_number,
            )
    else:
        choice = settings.marketing_email_sender
        if choice == "sendgrid":
            if not (settings.sendgrid_api_key and settings.sendgrid_from_email):
                raise InvalidRequest(
                    "set VOIDPOS_SENDGRID_API_KEY and _FROM_EMAIL to send email",
                    code="sendgrid_not_configured",
                )
            return SendGridSender(
                api_key=settings.sendgrid_api_key.get_secret_value(),
                from_email=settings.sendgrid_from_email,
            )
    return LogSender(channel=channel)
