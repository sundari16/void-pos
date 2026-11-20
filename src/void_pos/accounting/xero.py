"""A thin Xero API client for the daily-takings sync — only the calls this needs.

Mockable like ``SquareCatalog`` and ``StripeHttp``: pass an ``httpx.Client`` wired to a
``MockTransport`` in tests. It talks to the venue's own Xero app over OAuth2 (authorization
code, confidential client): the client id/secret authenticate token calls; API calls carry
the short-lived access token and the org's tenant id. Secrets are only sent to Xero, never
logged or returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from ..errors import InvalidRequest, ServiceUnavailable, VoidPosError

AUTHORIZE_URL = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL = "https://identity.xero.com/connect/token"
CONNECTIONS_URL = "https://api.xero.com/connections"
API = "https://api.xero.com/api.xro/2.0"


class XeroError(VoidPosError):
    """Xero accepted the request but refused it (a bad account code, a locked invoice)."""

    status_code = 502
    code = "xero_error"


@dataclass(frozen=True)
class XeroAuth:
    access_token: str
    refresh_token: str
    expires_in: int = 0
    scope: str = ""


def authorize_url(*, client_id: str, redirect_uri: str, scopes: str, state: str) -> str:
    """Where the venue signs in and consents; the browser is sent back to ``redirect_uri``
    with a ``code`` and the ``state`` echoed."""
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scopes,
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


class XeroClient:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        http: httpx.Client | None = None,
        access_token: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        self._http = http or httpx.Client(timeout=30)
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self.access_token = access_token
        self.tenant_id = tenant_id

    # --- OAuth2 -----------------------------------------------------------------------
    def exchange_code(self, code: str) -> XeroAuth:
        return self._token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._redirect_uri,
            }
        )

    def refresh(self, refresh_token: str) -> XeroAuth:
        auth = self._token({"grant_type": "refresh_token", "refresh_token": refresh_token})
        self.access_token = auth.access_token
        return auth

    def _token(self, data: dict[str, str]) -> XeroAuth:
        try:
            response = self._http.post(
                TOKEN_URL, data=data, auth=(self._client_id, self._client_secret)
            )
        except httpx.HTTPError as exc:
            raise ServiceUnavailable("can't reach Xero", code="xero_unavailable") from exc
        body: dict[str, Any] = _json(response)
        if response.status_code in (400, 401):
            error = body.get("error", "the authorisation was refused")
            raise InvalidRequest(
                f"Xero refused the sign-in ({error}); reconnect with void-pos xero-connect",
                code="xero_unauthorized",
            )
        if response.status_code >= 400:
            raise XeroError(f"Xero token request failed ({response.status_code})")
        return XeroAuth(
            access_token=body["access_token"],
            refresh_token=body["refresh_token"],
            expires_in=int(body.get("expires_in", 0)),
            scope=str(body.get("scope", "")),
        )

    def connections(self) -> list[dict[str, Any]]:
        """The Xero organisations this access token can reach (tenantId, tenantName). Called
        right after sign-in, before a tenant is chosen, so it needs no tenant header."""
        body = self._api("GET", CONNECTIONS_URL, tenant=False)
        return body if isinstance(body, list) else []

    # --- Accounting API ---------------------------------------------------------------
    def get_or_create_contact(self, name: str) -> str:
        where = urlencode({"where": f'Name=="{name}"'})
        found = self._api("GET", f"{API}/Contacts?{where}")
        contacts = found.get("Contacts") if isinstance(found, dict) else None
        if contacts:
            return str(contacts[0]["ContactID"])
        created = self._api("POST", f"{API}/Contacts", {"Contacts": [{"Name": name}]})
        return str(created["Contacts"][0]["ContactID"])

    def find_invoice(self, reference: str) -> dict[str, Any] | None:
        """The existing daily-takings invoice for a reference, or None."""
        where = urlencode({"where": f'InvoiceNumber=="{reference}"'})
        body = self._api("GET", f"{API}/Invoices?{where}")
        invoices = body.get("Invoices") if isinstance(body, dict) else None
        return invoices[0] if invoices else None

    def upsert_invoice(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create the invoice, or update it when ``payload`` carries an ``InvoiceID``."""
        body = self._api("POST", f"{API}/Invoices", {"Invoices": [payload]})
        return body["Invoices"][0]

    def delete_payment(self, payment_id: str) -> None:
        self._api(
            "POST",
            f"{API}/Payments/{payment_id}",
            {"Payments": [{"PaymentID": payment_id, "Status": "DELETED"}]},
        )

    def create_payment(
        self, *, invoice_id: str, account_code: str, amount_cents: int, date: str, reference: str
    ) -> dict[str, Any]:
        payload = {
            "Invoice": {"InvoiceID": invoice_id},
            "Account": {"Code": account_code},
            "Amount": str(amount_cents / 100),
            "Date": date,
            "Reference": reference,
        }
        body = self._api("PUT", f"{API}/Payments", {"Payments": [payload]})
        return body["Payments"][0]

    # --- plumbing ---------------------------------------------------------------------
    def _api(
        self, method: str, url: str, json: dict[str, Any] | None = None, *, tenant: bool = True
    ) -> Any:
        if not self.access_token:
            raise XeroError("the Xero client has no access token; refresh first")
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }
        if tenant:
            if not self.tenant_id:
                raise XeroError("the Xero client has no tenant id")
            headers["Xero-Tenant-Id"] = self.tenant_id
        try:
            response = self._http.request(method, url, json=json, headers=headers)
        except httpx.HTTPError as exc:
            raise ServiceUnavailable("can't reach Xero", code="xero_unavailable") from exc
        if response.status_code == 401:
            raise InvalidRequest(
                "Xero refused the access token; reconnect with void-pos xero-connect",
                code="xero_unauthorized",
            )
        if response.status_code >= 500:
            raise ServiceUnavailable("Xero is having trouble; try again", code="xero_unavailable")
        body = _json(response)
        if response.status_code >= 400:
            raise XeroError(f"Xero: {_message(body)}")
        return body


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {}


def _message(body: Any) -> str:
    if not isinstance(body, dict):
        return "request failed"
    elements = body.get("Elements") or []
    if elements:
        errors = (elements[0] or {}).get("ValidationErrors") or []
        if errors:
            return str(errors[0].get("Message", "validation failed"))
    return str(body.get("Message") or body.get("Detail") or "request failed")
