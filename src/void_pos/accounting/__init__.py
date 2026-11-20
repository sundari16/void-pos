"""Push a day's takings to the venue's own Xero organisation.

Open core, like the Square menu import: it uses the *venue's own* Xero connection, not a
Void platform account. It reads the existing closeout/reporting projections and posts one
"Daily takings" sales invoice per (location, business date) to Xero — idempotently, keyed
by a stable reference — and allocates the money taken to bank/clearing accounts by payment
provider. It never changes the event model.
"""

from __future__ import annotations

from .mapping import AccountMap
from .service import (
    XeroNotConnected,
    account_map_for,
    build_authorize_url,
    build_takings,
    complete_connection,
    load_connection,
    new_state,
    push_daily_takings,
    set_account_map,
)
from .summary import DailyTakings, build_daily_takings
from .xero import XeroAuth, XeroClient, XeroError

__all__ = [
    "AccountMap",
    "DailyTakings",
    "XeroAuth",
    "XeroClient",
    "XeroError",
    "XeroNotConnected",
    "account_map_for",
    "build_authorize_url",
    "build_daily_takings",
    "build_takings",
    "complete_connection",
    "load_connection",
    "new_state",
    "push_daily_takings",
    "set_account_map",
]
