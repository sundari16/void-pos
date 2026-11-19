from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from support import ADMIN_TOKEN, TILL_TOKEN, Venue, add_till, api, auth, make_engine, seed_venue
from void_pos.board import list_requests, set_status, submit_request, upvote
from void_pos.errors import InvalidRequest, NotFound

ADMIN = auth(ADMIN_TOKEN)
TILL = auth(TILL_TOKEN)
Ctx = tuple[TestClient, Venue, Session]


@pytest.fixture
def ctx() -> Iterator[Ctx]:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
        with api(engine) as client:
            yield client, venue, s


def _submit(c: TestClient, title: str = "Split bills", headers: dict[str, str] = TILL) -> dict:
    r = c.post(
        "/board/requests",
        json={"title": title, "body": "Let diners split a bill across cards.", "author": "MCH"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


# --- API ---------------------------------------------------------------------------


def test_submit_starts_at_submitted_with_no_votes(ctx: Ctx) -> None:
    c, _, _ = ctx
    body = _submit(c)
    assert body["title"] == "Split bills"
    assert body["author"] == "MCH"
    assert body["status"] == "submitted"
    assert body["votes"] == 0 and body["voted"] is False
    assert body["created_at"] and body["updated_at"]


def test_board_needs_a_token(ctx: Ctx) -> None:
    c, _, _ = ctx
    assert c.get("/board/requests").status_code == 401
    assert c.post("/board/requests", json={"title": "x", "body": "y", "author": "z"}).status_code == 401


def test_title_is_validated(ctx: Ctx) -> None:
    c, _, _ = ctx
    r = c.post("/board/requests", json={"title": "no", "body": "b", "author": "a"}, headers=TILL)
    assert r.status_code == 422  # title shorter than 3 chars


def test_upvote_is_idempotent_per_voter(ctx: Ctx) -> None:
    c, _, _ = ctx
    req = _submit(c)
    first = c.post(f"/board/requests/{req['id']}/upvote", headers=TILL).json()
    assert first["votes"] == 1 and first["voted"] is True
    # The same till pressing again changes nothing.
    again = c.post(f"/board/requests/{req['id']}/upvote", headers=TILL).json()
    assert again["votes"] == 1 and again["voted"] is True


def test_admin_and_each_till_vote_independently(ctx: Ctx) -> None:
    c, venue, s = ctx
    req = _submit(c)
    second = add_till(s, venue.location_id, name="Till 2", token="till-2-board-token", order_range=(2000, 2999))
    s.commit()
    c.post(f"/board/requests/{req['id']}/upvote", headers=TILL)
    c.post(f"/board/requests/{req['id']}/upvote", headers=ADMIN)
    counted = c.post(f"/board/requests/{req['id']}/upvote", headers=auth("till-2-board-token")).json()
    assert counted["votes"] == 3
    # The admin sees its own vote reflected; a fresh till has not voted.
    assert second is not None
    as_admin = c.get("/board/requests", headers=ADMIN).json()[0]
    assert as_admin["votes"] == 3 and as_admin["voted"] is True


def test_board_sorts_top_and_new(ctx: Ctx) -> None:
    c, _, _ = ctx
    a = _submit(c, title="First idea")
    _submit(c, title="Second idea")
    c.post(f"/board/requests/{a['id']}/upvote", headers=TILL)

    top = c.get("/board/requests", headers=ADMIN).json()
    assert [r["title"] for r in top] == ["First idea", "Second idea"]  # most votes first
    newest = c.get("/board/requests", params={"sort": "new"}, headers=ADMIN).json()
    assert [r["title"] for r in newest] == ["Second idea", "First idea"]  # newest first
    assert all({"votes", "status", "created_at", "updated_at"} <= r.keys() for r in top)


def test_only_admin_moves_status(ctx: Ctx) -> None:
    c, _, _ = ctx
    req = _submit(c)
    denied = c.post(f"/board/requests/{req['id']}/status", json={"status": "planned"}, headers=TILL)
    assert (denied.status_code, denied.json()["code"]) == (403, "admin_only")

    ok = c.post(f"/board/requests/{req['id']}/status", json={"status": "planned"}, headers=ADMIN)
    assert ok.status_code == 200 and ok.json()["status"] == "planned"
    shipped = c.post(f"/board/requests/{req['id']}/status", json={"status": "shipped"}, headers=ADMIN)
    assert shipped.json()["status"] == "shipped"
    # Its updated date moved past its created date.
    row = shipped.json()
    assert row["updated_at"] >= row["created_at"]


def test_bad_status_is_rejected(ctx: Ctx) -> None:
    c, _, _ = ctx
    req = _submit(c)
    r = c.post(f"/board/requests/{req['id']}/status", json={"status": "done"}, headers=ADMIN)
    assert r.status_code == 422  # not one of the four allowed values


def test_missing_request_is_404(ctx: Ctx) -> None:
    c, _, _ = ctx
    assert c.post("/board/requests/NOPE/upvote", headers=TILL).status_code == 404
    assert c.post("/board/requests/NOPE/status", json={"status": "planned"}, headers=ADMIN).status_code == 404


# --- service -----------------------------------------------------------------------


def test_service_rules() -> None:
    with Session(make_engine()) as s:
        req = submit_request(s, title="  Offline receipts  ", body="Print when back.", author=" MCH ")
        assert (req.title, req.author, req.status) == ("Offline receipts", "MCH", "submitted")

        for bad in ("", "   "):
            with pytest.raises(InvalidRequest):
                submit_request(s, title=bad, body="b", author="a")

        # Idempotent per voter; counted per distinct voter.
        assert upvote(s, req.id, "admin").votes == 1
        assert upvote(s, req.id, "admin").votes == 1
        assert upvote(s, req.id, "till-xyz").votes == 2

        top = list_requests(s, voter="admin")
        assert top[0].votes == 2 and top[0].voted is True

        with pytest.raises(InvalidRequest) as bad_status:
            set_status(s, req.id, "done")
        assert bad_status.value.code == "invalid_status"
        assert set_status(s, req.id, "shipped").status == "shipped"

        with pytest.raises(NotFound):
            upvote(s, "missing", "admin")
