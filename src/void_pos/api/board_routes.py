"""The public feature request board.

Any paired till or the venue admin can submit a request, see the board (votes, status and
dates), and upvote. Only the admin can move a request's status along. The voter is the
caller's own identity — a till's device id, or ``admin`` — taken from the token, never the
body, so one till upvoting counts once however many times it presses.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from ..board import list_requests, request_view, set_status, submit_request, upvote
from .auth import AdminDep, Principal, PrincipalDep
from .deps import get_session
from .schemas import RequestOut, RequestStatusIn, RequestSubmitIn

router = APIRouter(prefix="/board", tags=["board"])

SessionDep = Annotated[Session, Depends(get_session)]


def _voter(principal: Principal) -> str:
    """Who is voting: a till by its device id, or the venue admin as ``admin``."""
    return principal.device.id if principal.device is not None else "admin"


@router.post("/requests", response_model=RequestOut, status_code=status.HTTP_201_CREATED)
def submit(body: RequestSubmitIn, session: SessionDep, principal: PrincipalDep) -> RequestOut:
    request = submit_request(session, title=body.title, body=body.body, author=body.author)
    session.commit()
    return RequestOut.of(request_view(session, request, _voter(principal)))


@router.get("/requests", response_model=list[RequestOut])
def board(
    session: SessionDep,
    principal: PrincipalDep,
    sort: Literal["top", "new"] = "top",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[RequestOut]:
    """The whole board: most-voted first by default, newest first with ``sort=new``."""
    views = list_requests(session, voter=_voter(principal), sort=sort, limit=limit)
    return [RequestOut.of(view) for view in views]


@router.post("/requests/{request_id}/upvote", response_model=RequestOut)
def upvote_request(request_id: str, session: SessionDep, principal: PrincipalDep) -> RequestOut:
    """Upvote a request. Idempotent: pressing it again changes nothing."""
    view = upvote(session, request_id, _voter(principal))
    session.commit()
    return RequestOut.of(view)


@router.post("/requests/{request_id}/status", response_model=RequestOut)
def change_status(
    request_id: str, body: RequestStatusIn, session: SessionDep, principal: AdminDep
) -> RequestOut:
    """Move a request to submitted / under_review / planned / shipped. Admin only."""
    request = set_status(session, request_id, body.status)
    session.commit()
    return RequestOut.of(request_view(session, request, _voter(principal)))
