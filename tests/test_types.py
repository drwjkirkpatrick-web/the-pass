"""Prompt 01 tests — core types round-trips and helpers."""
from dataclasses import FrozenInstanceError

import pytest

from core.types import (
    Audience,
    Channel,
    DishAction,
    DishReview,
    DishEvent,
    Ingredient,
    KanbanCard,
    Order,
    OrderLine,
    OrderStatus,
    PassMessage,
    Recipe,
    RecipeLine,
    Reminder,
    Role,
    ServiceState,
    Supplier,
    TempReading,
    TempZone,
    Urgency,
    filter_fields,
)


def test_filter_fields_drops_unknown_keys():
    data = {"id": "i-1", "name": "Butter", "line_total": 9.99, "bogus": None}
    kept = filter_fields(Ingredient, data)
    assert kept == {"id": "i-1", "name": "Butter"}


def test_ingredient_round_trip():
    ing = Ingredient(id="i-1", name="Heirloom Tomato", category="produce",
                     unit="kg", par_level=4.0, on_hand=2.5,
                     freshness_date="2026-10-05")
    assert Ingredient.from_dict(ing.to_dict()) == ing


def test_recipe_round_trip_with_nested_lines():
    r = Recipe(id="r-1", name="Braised Short Rib", portions=4, menu_price=38.0,
               technique_tags=["braise"],
               lines=[RecipeLine("i-beef", 250.0, "g", "sear first"),
                      RecipeLine("i-mirepoix", 60.0, "g")])
    r2 = Recipe.from_dict(r.to_dict())
    assert r2 == r
    assert r2.lines[0].prep_note == "sear first"


def test_order_round_trip_with_nested_lines_and_status():
    o = Order(id="o-1", supplier_id="s-1", status=OrderStatus.DRAFT,
              lines=[OrderLine("i-beef", "Beef Short Rib", 9000.0, "g", 5.0,
                               "short for 80 covers")])
    d = o.to_dict()
    assert d["status"] == "draft"  # enum serialized as value
    o2 = Order.from_dict(d)
    assert o2 == o
    assert o2.status is OrderStatus.DRAFT
    assert o2.lines[0].rationale.startswith("short for")


def test_reminder_round_trip():
    rem = Reminder(id="rem-1", audience=Audience.BOH.value,
                   urgency=Urgency.URGENT.value, title="Braise timer",
                   body="4h braise done", fire_at="2026-10-01T14:00:00")
    assert Reminder.from_dict(rem.to_dict()) == rem


def test_pass_message_round_trip():
    msg = PassMessage(id="m-1", channel=Channel.URGENT.value,
                      sender_role=Role.SERVER.value, body="Table 4 allergy!")
    assert PassMessage.from_dict(msg.to_dict()) == msg


def test_dish_event_and_review_round_trip():
    ev = DishEvent(id=1, dish_id="r-1", action=DishAction.PLATED.value,
                   ts="2026-10-01T18:04:11", service_date="2026-10-01")
    assert DishEvent.from_dict(ev.to_dict()) == ev
    rv = DishReview(id="rev-1", dish_id="r-1", photo_id="ph-1", presentation=5,
                    portion=4, color=5, execution=5, note="Perfect crust",
                    service_date="2026-10-01")
    assert DishReview.from_dict(rv.to_dict()) == rv


def test_kanban_card_round_trip():
    card = KanbanCard(id="c-1", board_id="b-ops", title="Approve order",
                      column="Today", owner_role=Role.AGENT.value,
                      due="2026-10-01")
    assert KanbanCard.from_dict(card.to_dict()) == card


def test_supplier_and_temp_round_trip():
    sup = Supplier(id="s-1", name="Green Ridge Farms", closed_days=[0, 6])
    assert Supplier.from_dict(sup.to_dict()) == sup
    zone = TempZone(id="z-1", name="Walk-in", station="kitchen", min_c=0.0,
                    max_c=4.0)
    assert TempZone.from_dict(zone.to_dict()) == zone
    reading = TempReading(id=7, zone_id="z-1", station="kitchen", sensor_id="t1",
                          celsius=3.2, ts="2026-10-01T15:00:00")
    assert TempReading.from_dict(reading.to_dict()) == reading


def test_enums_have_expected_values():
    assert Role.AGENT.value == "agent"
    assert Urgency.URGENT.value == "urgent"
    assert Audience.FOH.value == "foh"
    assert OrderStatus.DRAFT.value == "draft"
    assert DishAction.FIRED.value == "fired"
    assert Channel.URGENT.value == "urgent"
    assert ServiceState.SERVICE.value == "service"


def test_dataclasses_are_frozen():
    ing = Ingredient(id="i-1", name="Butter")
    with pytest.raises(FrozenInstanceError):
        ing.name = "Ghee"
