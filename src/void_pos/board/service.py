"""Feature request board rules.

The event log is the source of truth for the POS; the board is plainer reference data, so
it is stored directly like staff or the CRM. The one rule it keeps from the data model is
loyalty's: a count is a ``SUM`` of a ledger, never a stored field. Each upvote is a row,
unique per ``(request, voter)``, and the vote count is always ``COUNT`` of those rows — so
an upvote is idempotent and the number on the board can never drift.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..errors import InvalidRequest, NotFound
from ..models import FeatureRequest, FeatureRequestVote
from ..models.board import REQUEST_STATUSES

Sort = Literal["top", "new"]

MAX_TITLE = 200
MAX_BODY = 4000
MAX_AUTHOR = 120


@dataclass(frozen=True)
class RequestView:
    """A request with its live vote count, and whether the caller has upvoted it."""

    request: FeatureRequest
    votes: int
    voted: bool


def _clean(value: str, *, field: str, max_length: int) -> str:
    text = value.strip()
    if not text:
        raise InvalidRequest(f"a {field} is needed", code="invalid_request")
    if len(text) > max_length:
        raise InvalidRequest(f"the {field} is too long (max {max_length})", code="invalid_request")
    return text


def submit_request(session: Session, *, title: str, body: str, author: str) -> FeatureRequest:
    """Add a request to the board. It starts at ``submitted`` with no votes."""
    request = FeatureRequest(
        title=_clean(title, field="title", max_length=MAX_TITLE),
        body=_clean(body, field="description", max_length=MAX_BODY),
        author=_clean(author, field="name", max_length=MAX_AUTHOR),
        status="submitted",
    )
    session.add(request)
    session.flush()
    return request


def get_request(session: Session, request_id: str) -> FeatureRequest:
    request = session.get(FeatureRequest, request_id)
    if request is None:
        raise NotFound("request not found")
    return request


def _counts(session: Session, request_ids: Sequence[str]) -> dict[str, int]:
    if not request_ids:
        return {}
    rows = session.execute(
        select(FeatureRequestVote.request_id, func.count())
        .where(FeatureRequestVote.request_id.in_(request_ids))
        .group_by(FeatureRequestVote.request_id)
    ).all()
    return {request_id: count for request_id, count in rows}


def _voted(session: Session, request_ids: Sequence[str], voter: str) -> set[str]:
    if not request_ids:
        return set()
    rows = session.scalars(
        select(FeatureRequestVote.request_id).where(
            FeatureRequestVote.request_id.in_(request_ids),
            FeatureRequestVote.voter == voter,
        )
    ).all()
    return set(rows)


def request_view(session: Session, request: FeatureRequest, voter: str) -> RequestView:
    return RequestView(
        request=request,
        votes=_counts(session, [request.id]).get(request.id, 0),
        voted=request.id in _voted(session, [request.id], voter),
    )


def list_requests(
    session: Session, *, voter: str, sort: Sort = "top", limit: int = 100
) -> list[RequestView]:
    """The board. ``top`` is most-voted first, ``new`` is newest first; ties break on the
    newest request. ``voted`` reflects whether ``voter`` has upvoted each one."""
    requests = list(session.scalars(select(FeatureRequest)).all())
    counts = _counts(session, [r.id for r in requests])
    voted = _voted(session, [r.id for r in requests], voter)
    views = [
        RequestView(request=r, votes=counts.get(r.id, 0), voted=r.id in voted) for r in requests
    ]
    # The id is a ULID, so it orders by creation time and makes every sort deterministic.
    if sort == "new":
        views.sort(key=lambda v: v.request.id, reverse=True)
    else:
        views.sort(key=lambda v: (v.votes, v.request.id), reverse=True)
    return views[:limit]


def upvote(session: Session, request_id: str, voter: str) -> RequestView:
    """Record ``voter``'s upvote once. Calling it again is a no-op (the row already exists),
    so the caller can press the button as often as they like."""
    request = get_request(session, request_id)
    if voter not in _voted(session, [request_id], voter):
        savepoint = session.begin_nested()
        try:
            session.add(FeatureRequestVote(request_id=request_id, voter=voter))
            session.flush()
        except IntegrityError:
            savepoint.rollback()  # lost a race to a concurrent upvote; the vote is already in
    return request_view(session, request, voter)


def set_status(session: Session, request_id: str, status: str) -> FeatureRequest:
    """Move a request along its lifecycle. Admin only (enforced at the route)."""
    if status not in REQUEST_STATUSES:
        raise InvalidRequest(
            f"status must be one of {', '.join(REQUEST_STATUSES)}", code="invalid_status"
        )
    request = get_request(session, request_id)
    request.status = status
    session.flush()
    return request
