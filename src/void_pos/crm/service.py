"""Customer upsert and order-history reads.

Matching within an org is by phone first, then email, so a returning customer who gives
the same number is recognised and their history accrues against one record. Nothing here
goes through the event log: a customer is reference data (like the menu), referenced by
``Order.customer_id`` and the loyalty ledger.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import InvalidRequest, NotFound
from ..models import Customer, Order


def normalize_phone(phone: str | None) -> str | None:
    """Drop spaces and the usual separators; keep a leading ``+`` and the digits."""
    if phone is None:
        return None
    cleaned = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
    return cleaned or None


def normalize_email(email: str | None) -> str | None:
    if email is None:
        return None
    cleaned = email.strip().lower()
    return cleaned or None


def get_customer(session: Session, customer_id: str) -> Customer:
    customer = session.get(Customer, customer_id)
    if customer is None:
        raise NotFound("customer not found")
    return customer


def find_customer(
    session: Session, *, org_id: str, phone: str | None, email: str | None
) -> Customer | None:
    """The org's customer matching this phone (preferred) or email, if any."""
    if phone:
        match = session.scalars(
            select(Customer).where(Customer.org_id == org_id, Customer.phone == phone)
        ).first()
        if match is not None:
            return match
    if email:
        return session.scalars(
            select(Customer).where(Customer.org_id == org_id, Customer.email == email)
        ).first()
    return None


def upsert_customer(
    session: Session,
    *,
    org_id: str,
    location_id: str,
    name: str | None,
    phone: str | None,
    email: str | None,
    marketing_opt_in: bool = False,
) -> Customer:
    """Find the org's customer (by phone then email) and update them, or create one. At
    least one of phone/email is required so the record is identifiable and reachable."""
    phone = normalize_phone(phone)
    email = normalize_email(email)
    if not phone and not email:
        raise InvalidRequest("give a phone number or an email address", code="customer_contact_required")
    customer = find_customer(session, org_id=org_id, phone=phone, email=email)
    if customer is None:
        customer = Customer(
            org_id=org_id,
            location_id=location_id,
            name=name,
            phone=phone,
            email=email,
            marketing_opt_in=marketing_opt_in,
        )
        session.add(customer)
        session.flush()
        return customer
    # Latest details win; never drop a contact we already hold just because this order
    # left it out.
    if name:
        customer.name = name
    if phone:
        customer.phone = phone
    if email:
        customer.email = email
    customer.marketing_opt_in = marketing_opt_in
    session.flush()
    return customer


def customer_orders(session: Session, customer_id: str, *, limit: int = 100) -> list[Order]:
    """The customer's orders, newest first."""
    get_customer(session, customer_id)
    return list(
        session.scalars(
            select(Order)
            .where(Order.customer_id == customer_id)
            .order_by(Order.id.desc())
            .limit(limit)
        ).all()
    )
