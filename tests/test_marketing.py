from __future__ import annotations

import httpx
import pytest
from sqlalchemy.orm import Session

from support import ADMIN_TOKEN, api, auth, make_engine, seed_venue
from void_pos.config import Settings
from void_pos.marketing import (
    CampaignMessage,
    LogSender,
    SendGridSender,
    TwilioSender,
    make_sender,
    render,
    select_recipients,
    send_campaign,
)
from void_pos.marketing.sender import SendResult
from void_pos.models import Customer


def _customer(session: Session, org_id: str, **kw: object) -> Customer:
    customer = Customer(org_id=org_id, **kw)  # type: ignore[arg-type]
    session.add(customer)
    session.flush()
    return customer


def _seed_audience(session: Session, org_id: str) -> None:
    _customer(session, org_id, name="Ada", phone="+61400000001", marketing_opt_in=True)
    _customer(session, org_id, name="Bo", email="bo@x.io", marketing_opt_in=True)
    _customer(session, org_id, name="No consent", phone="+61400000003", marketing_opt_in=False)
    _customer(session, org_id, name="No phone", email="cy@x.io", marketing_opt_in=True)


def test_log_sender_reports_sent_without_calling_out() -> None:
    result = LogSender(channel="sms").send(
        CampaignMessage(channel="sms", to="+61400000001", body="Hi")
    )
    assert result.status == "sent"


def test_render_fills_the_name() -> None:
    cust = Customer(org_id="o", name="Ada Lovelace")
    assert render("Hi {name}, 10% off today", cust) == "Hi Ada, 10% off today"
    assert render("Hello {name}", Customer(org_id="o")) == "Hello there"


def test_only_opted_in_customers_with_the_channel_contact_are_targeted() -> None:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
        _seed_audience(s, venue.org_id)
        s.commit()
        sms = select_recipients(s, venue.location_id, "sms")
        email = select_recipients(s, venue.location_id, "email")
        assert {c.name for c in sms} == {"Ada"}  # Bo has no phone; No-consent opted out
        assert {c.name for c in email} == {"Bo", "No phone"}


def test_send_campaign_counts_and_dry_run(caplog: pytest.LogCaptureFixture) -> None:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
        _seed_audience(s, venue.org_id)
        s.commit()
        dry = send_campaign(
            s, location_id=venue.location_id, channel="sms", body="Hi {name}",
            sender=LogSender("sms"), dry_run=True,
        )
        assert (dry.total, dry.sent, dry.skipped) == (1, 0, 1)
        live = send_campaign(
            s, location_id=venue.location_id, channel="email", body="Hi {name}",
            subject="Specials", sender=LogSender("email"),
        )
        assert (live.total, live.sent, live.failed) == (2, 2, 0)


class _StubSender:
    channel = "sms"

    def send(self, message: CampaignMessage) -> SendResult:
        return SendResult(to=message.to, status="failed", detail="boom")


def test_a_failed_send_is_counted_not_raised() -> None:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
        _customer(s, venue.org_id, name="Ada", phone="+61400000001", marketing_opt_in=True)
        s.commit()
        result = send_campaign(
            s, location_id=venue.location_id, channel="sms", body="Hi", sender=_StubSender(),
        )
        assert (result.sent, result.failed) == (0, 1)


def test_make_sender_defaults_to_log_and_needs_config_for_adapters() -> None:
    log_default = make_sender(Settings(_env_file=None), "sms")  # type: ignore[call-arg]
    assert isinstance(log_default, LogSender)
    configured = Settings(  # type: ignore[call-arg]
        _env_file=None,
        marketing_sms_sender="twilio",
        twilio_account_sid="AC1",
        twilio_auth_token="tok",
        twilio_from_number="+61400000000",
    )
    assert isinstance(make_sender(configured, "sms"), TwilioSender)
    missing = Settings(_env_file=None, marketing_sms_sender="twilio")  # type: ignore[call-arg]
    with pytest.raises(Exception, match="TWILIO"):
        make_sender(missing, "sms")


def test_twilio_adapter_posts_the_message() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"sid": "SM123"})

    client = httpx.Client(
        base_url="https://api.twilio.com/2010-04-01", transport=httpx.MockTransport(handler)
    )
    sender = TwilioSender("AC1", "tok", "+61400000000", client=client)
    result = sender.send(CampaignMessage(channel="sms", to="+61400000009", body="Hi"))
    assert result.status == "sent" and result.external_ref == "SM123"
    assert b"To=%2B61400000009" in seen[0].content and b"Body=Hi" in seen[0].content

    def fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "unverified number"})

    bad = TwilioSender(
        "AC1", "tok", "+61400000000",
        client=httpx.Client(base_url="https://x", transport=httpx.MockTransport(fail)),
    )
    out = bad.send(CampaignMessage(channel="sms", to="+1", body="Hi"))
    assert out.status == "failed" and out.detail == "unverified number"


def test_sendgrid_adapter_posts_the_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer SG.key"
        return httpx.Response(202, headers={"X-Message-Id": "msg-1"})

    sender = SendGridSender(
        "SG.key", "hi@venue.test",
        client=httpx.Client(base_url="https://api.sendgrid.com/v3", transport=httpx.MockTransport(handler)),
    )
    result = sender.send(
        CampaignMessage(channel="email", to="a@x.io", subject="Hi", body="Specials")
    )
    assert result.status == "sent" and result.external_ref == "msg-1"


def test_admin_campaign_endpoint_sends_to_opted_in_customers() -> None:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
        _seed_audience(s, venue.org_id)
        s.commit()
        with api(engine) as client:
            r = client.post(
                f"/admin/locations/{venue.location_id}/campaigns",
                json={"channel": "sms", "body": "Hi {name}, 10% off"},
                headers=auth(ADMIN_TOKEN),
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert (body["total"], body["sent"], body["channel"]) == (1, 1, "sms")
            assert body["results"][0]["status"] == "sent"
