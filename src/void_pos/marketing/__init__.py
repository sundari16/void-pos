"""Marketing campaigns: a send seam plus a simple send-to-customers service over the CRM.

The open core ships the ``Sender`` protocol and a no-op ``LogSender``; Twilio (SMS) and
SendGrid (email) are config-driven adapters that implement the same protocol — exactly the
way the payments layer keeps the Stripe Connect adapter out of the open core.
"""

from __future__ import annotations

from .sender import CampaignMessage, Channel, LogSender, Sender, SendResult
from .sendgrid import SendGridSender
from .service import CampaignResult, make_sender, render, select_recipients, send_campaign
from .twilio import TwilioSender

__all__ = [
    "CampaignMessage",
    "CampaignResult",
    "Channel",
    "LogSender",
    "SendGridSender",
    "SendResult",
    "Sender",
    "TwilioSender",
    "make_sender",
    "render",
    "select_recipients",
    "send_campaign",
]
