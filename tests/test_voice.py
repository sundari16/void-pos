"""Voice AI order intake: resolve a spoken order against the menu, confirm it, place it.

The external voice stack (telephony + LLM) is mocked throughout — a tiny ``FakeAgent`` for
the service flow, the shipped ``ScriptedVoiceAgent`` for the CLI. No network, no speech.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from support import TILL_TOKEN, add_till, api, auth, make_engine
from void_pos.cli import main
from void_pos.config import Settings, get_settings
from void_pos.devices import create_location
from void_pos.errors import Conflict, InvalidRequest
from void_pos.ids import new_ulid
from void_pos.menu import (
    attach_modifier_group,
    create_item,
    create_modifier,
    create_modifier_group,
    update_item,
)
from void_pos.models import Base, Customer, Order
from void_pos.voice import (
    VOICE_ORDER_NUMBER_START,
    DraftItem,
    DraftModifier,
    InboundCall,
    VoiceOrderConfirmation,
    VoiceOrderDraft,
    confirm_voice_order,
    place_voice_order,
    run_voice_call,
)

# --- a mockable stand-in for the external voice stack ---------------------------------


class FakeAgent:
    """A hand-rolled ``VoiceAgent`` — proves the seam is mockable without the real stack."""

    def __init__(self, draft: VoiceOrderDraft, *, accept: bool = True) -> None:
        self.draft = draft
        self.accept = accept
        self.read_back: VoiceOrderConfirmation | None = None

    def take_order(self, call: InboundCall) -> VoiceOrderDraft:
        return self.draft

    def confirm(self, call: InboundCall, confirmation: VoiceOrderConfirmation) -> bool:
        self.read_back = confirmation  # what the stack would read to the caller
        return self.accept


# --- a seeded venue with a menu the caller can order from -----------------------------


@dataclass(frozen=True)
class VoiceMenu:
    location_id: str
    dosa_id: str
    lassi_id: str
    mild_id: str
    hot_id: str
    chutney_id: str


def seed_menu(session: Session, location_id: str) -> VoiceMenu:
    dosa = create_item(
        session,
        location_id=location_id,
        name="Masala Dosa",
        base_price_cents=1850,
        category="Mains",
        tax_rate=0.10,
        kitchen_name="மசாலா தோசை",
    )
    lassi = create_item(
        session, location_id=location_id, name="Mango Lassi", base_price_cents=650, tax_rate=0.10
    )
    spice = create_modifier_group(
        session, location_id=location_id, name="Spice level", min_select=1, max_select=1
    )
    mild = create_modifier(session, group_id=spice.id, name="Mild")
    hot = create_modifier(session, group_id=spice.id, name="Extra hot")
    extras = create_modifier_group(session, location_id=location_id, name="Extras", min_select=0)
    chutney = create_modifier(
        session, group_id=extras.id, name="Extra chutney", price_delta_cents=150
    )
    attach_modifier_group(session, menu_item_id=dosa.id, modifier_group_id=spice.id)
    attach_modifier_group(session, menu_item_id=dosa.id, modifier_group_id=extras.id)
    session.commit()
    return VoiceMenu(location_id, dosa.id, lassi.id, mild.id, hot.id, chutney.id)


def seed_voice_venue(session: Session) -> VoiceMenu:
    """An org, a location, a paired till (so the pipeline is reachable), and the menu."""
    location = create_location(session, org_name="Madras Curry House", name="Brisbane CBD")
    add_till(session, location.id, name="Till 1", token=TILL_TOKEN, order_range=(1000, 1999))
    return seed_menu(session, location.id)


def dosa_and_lassi(**over: object) -> VoiceOrderDraft:
    """2 Masala Dosa (extra hot + extra chutney) and a Mango Lassi, by name."""
    draft = VoiceOrderDraft(
        items=[
            DraftItem(
                name="Masala Dosa",
                quantity=2,
                modifiers=[DraftModifier(name="Extra hot"), DraftModifier(name="Extra chutney")],
            ),
            DraftItem(name="Mango Lassi", quantity=1),
        ],
        customer_phone="+61400111222",
        customer_name="Sam",
        fulfillment="pickup",
    )
    return _replace(draft, **over)


def _replace(draft: VoiceOrderDraft, **over: object) -> VoiceOrderDraft:
    from dataclasses import replace

    return replace(draft, **over)  # type: ignore[arg-type]


def _count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


# --- resolving + placing (service layer) ----------------------------------------------


def test_place_voice_order_by_name_lands_on_the_voice_channel() -> None:
    with Session(make_engine()) as s:
        menu = seed_voice_venue(s)
        placement = place_voice_order(s, menu.location_id, dosa_and_lassi())
        s.commit()

        order = placement.order
        assert order.channel == "voice"
        assert order.state == "open"
        assert order.fulfillment == "pickup"
        # The voice device's reserved, high order-number band.
        assert order.order_number == VOICE_ORDER_NUMBER_START
        assert not placement.replayed
        # A caller the venue can ring back.
        customer = s.get(Customer, order.customer_id)
        assert customer is not None and customer.phone == "+61400111222"
        assert customer.name == "Sam"


def test_pricing_snapshots_the_menu_and_includes_gst() -> None:
    with Session(make_engine()) as s:
        menu = seed_voice_venue(s)
        confirmation = confirm_voice_order(s, menu.location_id, dosa_and_lassi())

    # 2 dosa @1850 + chutney 150 x2 + lassi 650 = 4650; GST is the 10% baked into the price.
    assert confirmation.subtotal_cents == 4650
    assert confirmation.total_cents == 4650
    assert confirmation.gst_cents == 423  # round_half_up(4650 * 0.10 / 1.10)
    assert confirmation.currency == "AUD"
    first = confirmation.items[0]
    assert first.name == "Masala Dosa" and first.quantity == 2
    assert first.line_total_cents == 1850 * 2 + 150 * 2
    assert [m.name for m in first.modifiers] == ["Extra hot", "Extra chutney"]
    assert "2 Masala Dosa" in confirmation.read_back
    assert "$46.50" in confirmation.read_back


def test_confirm_reads_back_without_placing_anything() -> None:
    with Session(make_engine()) as s:
        menu = seed_voice_venue(s)
        confirmation = confirm_voice_order(s, menu.location_id, dosa_and_lassi())
        s.commit()
        assert confirmation.total_cents == 4650
        assert _count(s, Order) == 0  # the read-back step writes nothing
        assert _count(s, Customer) == 0


def test_items_and_modifiers_resolve_by_menu_id_too() -> None:
    with Session(make_engine()) as s:
        menu = seed_voice_venue(s)
        draft = VoiceOrderDraft(
            items=[
                DraftItem(
                    menu_item_id=menu.dosa_id,
                    quantity=1,
                    modifiers=[DraftModifier(modifier_id=menu.mild_id)],
                )
            ],
            customer_phone="+61400111222",
        )
        placement = place_voice_order(s, menu.location_id, draft)
        s.commit()
        assert placement.order.channel == "voice"
        assert placement.confirmation.total_cents == 1850


def test_an_item_not_on_the_menu_is_rejected() -> None:
    with Session(make_engine()) as s:
        menu = seed_voice_venue(s)
        draft = VoiceOrderDraft(
            items=[DraftItem(name="Pepperoni Pizza", quantity=1)], customer_phone="+61400111222"
        )
        with pytest.raises(InvalidRequest) as exc:
            place_voice_order(s, menu.location_id, draft)
        assert exc.value.code == "item_not_found"
        s.rollback()
        assert _count(s, Order) == 0


def test_a_missing_required_modifier_is_rejected() -> None:
    with Session(make_engine()) as s:
        menu = seed_voice_venue(s)
        # The dosa needs a spice level; order it with none.
        draft = VoiceOrderDraft(
            items=[DraftItem(name="Masala Dosa", quantity=1)], customer_phone="+61400111222"
        )
        with pytest.raises(InvalidRequest) as exc:
            confirm_voice_order(s, menu.location_id, draft)
        assert exc.value.code == "modifier_selection"


def test_an_unavailable_item_is_rejected() -> None:
    with Session(make_engine()) as s:
        menu = seed_voice_venue(s)
        update_item(s, menu.dosa_id, {"is_available": False})
        s.commit()
        draft = VoiceOrderDraft(
            items=[
                DraftItem(name="Masala Dosa", quantity=1, modifiers=[DraftModifier(name="Mild")])
            ],
            customer_phone="+61400111222",
        )
        with pytest.raises(Conflict) as exc:
            confirm_voice_order(s, menu.location_id, draft)
        assert exc.value.code == "item_unavailable"


def test_placing_the_same_order_twice_replays_once() -> None:
    with Session(make_engine()) as s:
        menu = seed_voice_venue(s)
        draft = dosa_and_lassi(order_id=new_ulid())
        first = place_voice_order(s, menu.location_id, draft)
        s.commit()
        second = place_voice_order(s, menu.location_id, draft)
        s.commit()
        assert not first.replayed
        assert second.replayed
        assert first.order.id == second.order.id
        assert _count(s, Order) == 1
        assert _count(s, Customer) == 1


# --- the end-to-end call flow through the agent seam ----------------------------------


def test_run_voice_call_places_when_the_caller_confirms() -> None:
    with Session(make_engine()) as s:
        menu = seed_voice_venue(s)
        agent = FakeAgent(dosa_and_lassi())
        call = InboundCall(location_id=menu.location_id, from_number="+61400111222")
        result = run_voice_call(s, agent, call)
        s.commit()
        assert result.placed and result.order is not None
        assert result.order.channel == "voice"
        # The stack was handed the priced order to read back.
        assert agent.read_back is not None and agent.read_back.total_cents == 4650


def test_run_voice_call_places_nothing_when_the_caller_declines() -> None:
    with Session(make_engine()) as s:
        menu = seed_voice_venue(s)
        agent = FakeAgent(dosa_and_lassi(), accept=False)
        call = InboundCall(location_id=menu.location_id, from_number="+61400111222")
        result = run_voice_call(s, agent, call)
        s.commit()
        assert not result.placed and result.order is None
        assert agent.read_back is not None  # it still heard the read-back
        assert _count(s, Order) == 0
        assert _count(s, Customer) == 0


# --- the HTTP endpoints ---------------------------------------------------------------


def _request_body(**over: object) -> dict[str, object]:
    body: dict[str, object] = {
        "items": [
            {
                "name": "Masala Dosa",
                "quantity": 2,
                "modifiers": [{"name": "Extra hot"}, {"name": "Extra chutney"}],
            },
            {"name": "Mango Lassi"},
        ],
        "customer_phone": "+61400111222",
        "fulfillment": "pickup",
    }
    body.update(over)
    return body


def test_quote_endpoint_prices_without_placing() -> None:
    engine = make_engine()
    with Session(engine) as s:
        menu = seed_voice_venue(s)
    with api(engine) as client:
        resp = client.post(f"/voice/locations/{menu.location_id}/quote", json=_request_body())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_cents"] == 4650
        assert body["gst_cents"] == 423
        assert "2 Masala Dosa" in body["read_back"]
    with Session(engine) as s:
        assert _count(s, Order) == 0  # quoting places nothing


def test_place_endpoint_lands_in_the_pipeline() -> None:
    engine = make_engine()
    with Session(engine) as s:
        menu = seed_voice_venue(s)
    with api(engine) as client:
        resp = client.post(f"/voice/locations/{menu.location_id}/orders", json=_request_body())
        assert resp.status_code == 200, resp.text
        placed = resp.json()
        assert placed["channel"] == "voice"
        assert placed["state"] == "open"
        assert placed["order_number"] == VOICE_ORDER_NUMBER_START
        order_id = placed["order_id"]

        # A paired till at the venue can see it in the open-orders pipeline.
        got = client.get(f"/orders/{order_id}", headers=auth(TILL_TOKEN))
        assert got.status_code == 200, got.text
        assert got.json()["state"] == "open"


def test_voice_ordering_can_be_disabled() -> None:
    engine = make_engine()
    with Session(engine) as s:
        menu = seed_voice_venue(s)
    with api(engine) as client:
        client.app.dependency_overrides[get_settings] = lambda: Settings(
            _env_file=None, voice_ordering_enabled=False
        )  # type: ignore[call-arg]
        resp = client.post(f"/voice/locations/{menu.location_id}/orders", json=_request_body())
        assert resp.status_code == 404
        assert resp.json()["code"] == "voice_ordering_disabled"


# --- the CLI simulation ---------------------------------------------------------------


@pytest.fixture
def seeded_db(tmp_path: Path) -> tuple[str, str]:
    url = f"sqlite:///{tmp_path / 'venue.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        menu = seed_voice_venue(s)
    engine.dispose()
    return url, menu.location_id


def _run(url: str, *args: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(["--database-url", url, *args])
    out, err = capsys.readouterr()
    return code, out, err


def test_cli_voice_order_places_end_to_end(
    seeded_db: tuple[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    url, location_id = seeded_db
    code, out, _ = _run(
        url,
        "voice-order",
        "--location", location_id,
        "--phone", "+61400111222",
        "--name", "Sam",
        "--item", "Masala Dosa:2:Extra hot",
        "--item", "Mango Lassi",
        capsys=capsys,
    )
    assert code == 0, out
    assert "Read-back:" in out
    assert "Placed voice order" in out
    assert "voice channel" in out

    with Session(create_engine(url)) as s:
        order = s.scalars(select(Order)).one()
        assert order.channel == "voice"
        assert order.order_number == VOICE_ORDER_NUMBER_START


def test_cli_voice_order_declined_places_nothing(
    seeded_db: tuple[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    url, location_id = seeded_db
    code, out, _ = _run(
        url,
        "voice-order",
        "--location", location_id,
        "--phone", "+61400111222",
        "--item", "Masala Dosa:1:Mild",
        "--decline",
        capsys=capsys,
    )
    assert code == 0, out
    assert "declined" in out
    with Session(create_engine(url)) as s:
        assert _count(s, Order) == 0
