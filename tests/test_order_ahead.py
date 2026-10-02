"""Prompt 10 tests — backward date math for day-before arrival."""
import pytest

from modules.deliveries import Deliveries
from modules.inventory import Inventory
from modules.menu_planner import Shortfall
from modules.order_ahead import OrderAhead


@pytest.fixture
def kit(config, db, bus):
    deliveries = Deliveries(config, db, bus)
    inventory = Inventory(config, db, bus)
    return deliveries, inventory, OrderAhead(config, db, bus, deliveries=deliveries,
                                             inventory=inventory)


def short(ingredient_id="i-beef", name="beef", qty=2.0, unit="kg"):
    return Shortfall(ingredient_id=ingredient_id, name=name, needed=10.0, unit=unit,
                     on_hand=8.0, deficit=qty, reason="below_par")


def test_arrival_is_the_day_before_service(kit):
    deliveries, inventory, ahead = kit
    assert ahead.arrival_date("2026-10-02") == "2026-10-01"
    assert ahead.required_by_date("2026-10-02") == "2026-10-01"


def test_order_by_subtracts_the_p90_lead_time(kit):
    deliveries, inventory, ahead = kit
    supplier = deliveries.add_supplier("Green Ridge", typical_delay_days=2.0)
    # no history -> default 2 days; service Fri 2026-10-02, arrival Thu 10-01
    # -> order by Tue 2026-09-29
    assert ahead.order_by(supplier.id, "2026-10-02") == "2026-09-29"


def test_closed_days_are_walked_back(kit):
    deliveries, inventory, ahead = kit
    # candidate lands on Monday 2026-09-28; supplier is closed Mondays
    supplier = deliveries.add_supplier("Monday Closed", typical_delay_days=3.0,
                                       closed_days=[0])
    assert ahead.order_by(supplier.id, "2026-10-02") == "2026-09-27"  # Sunday


def test_p90_beats_median_for_the_order_date(kit):
    deliveries, inventory, ahead = kit
    supplier = deliveries.add_supplier("Sloppy Supply", typical_delay_days=0.0)
    # mixed history: median 1.5, p90 3 -> the order date follows the p90
    for i, delay in enumerate([0, 0, 1, 1, 1, 2, 2, 3, 3, 5]):
        promised = 1 + i
        deliveries.record_arrival(supplier.id, f"2026-09-{promised:02d}",
                                  f"2026-09-{promised:02d}",
                                  f"2026-09-{promised + delay:02d}")
    assert deliveries.median_delay(supplier.id) == 1.5
    assert deliveries.p90_delay(supplier.id) == 3.0
    # arrival 2026-10-01 minus 3 days = 2026-09-28
    assert ahead.order_by(supplier.id, "2026-10-02") == "2026-09-28"


def test_safety_buffer_pushes_the_order_date_earlier(kit):
    deliveries, inventory, ahead = kit
    supplier = deliveries.add_supplier("Green Ridge", typical_delay_days=2.0)
    assert ahead.order_by(supplier.id, "2026-10-02") == "2026-09-29"
    ahead.config = ahead.config.with_overrides(delivery_safety_buffer_days=2.0)
    assert ahead.order_by(supplier.id, "2026-10-02") == "2026-09-27"


def test_schedule_groups_by_supplier_and_carries_the_math(kit):
    deliveries, inventory, ahead = kit
    green = deliveries.add_supplier("Green Ridge", typical_delay_days=2.0)
    inventory.add("beef", id="i-beef", unit="kg", on_hand=8.0, par_level=10.0)
    inventory.set_supplier("i-beef", green.id)

    schedule = ahead.build_schedule("2026-10-02", [short()])
    assert len(schedule) == 1
    rec = schedule[0]
    assert rec.supplier_id == green.id
    assert rec.supplier_name == "Green Ridge"
    assert rec.arrival_date == "2026-10-01"
    assert rec.order_by == "2026-09-29"
    assert rec.qty == 2.0
    assert "short 2 kg" in rec.note
    assert "p90 2d" in rec.note


def test_short_shelf_life_produces_a_warning_note(kit):
    deliveries, inventory, ahead = kit
    supplier = deliveries.add_supplier("Far Away", typical_delay_days=3.0)
    inventory.add("scallops", id="i-scallop", category="seafood", unit="kg",
                  on_hand=0.0, par_level=2.0)
    inventory.set_supplier("i-scallop", supplier.id)
    schedule = ahead.build_schedule("2026-10-02",
                                    [short("i-scallop", "scallops", 2.0, "kg")])
    assert "WARNING shelf life 2d" in schedule[0].note


def test_ingredient_without_a_supplier_falls_back_to_the_default(kit):
    deliveries, inventory, ahead = kit
    deliveries.add_supplier("Default House", typical_delay_days=1.0)
    schedule = ahead.build_schedule("2026-10-02", [short("i-unknown", "mystery")])
    assert schedule[0].supplier_name == "Default House"
    assert schedule[0].order_by == "2026-09-30"


def test_empty_shortfalls_give_an_empty_schedule(kit):
    deliveries, inventory, ahead = kit
    deliveries.add_supplier("Any", typical_delay_days=1.0)
    assert ahead.build_schedule("2026-10-02", []) == []
    assert ahead.build_schedule("2026-10-02", None) == []


def test_due_today_filters_past_order_dates(kit):
    deliveries, inventory, ahead = kit
    supplier = deliveries.add_supplier("Green Ridge", typical_delay_days=2.0)
    inventory.add("beef", id="i-beef", unit="kg", on_hand=0.0, par_level=10.0)
    inventory.set_supplier("i-beef", supplier.id)
    schedule = [short()]
    assert ahead.due_today("2026-10-02", today="2026-09-29", shortfalls=schedule)
    assert ahead.due_today("2026-10-02", today="2026-09-28", shortfalls=schedule) == []


def test_recommendation_serialises(kit):
    deliveries, inventory, ahead = kit
    supplier = deliveries.add_supplier("Green Ridge", typical_delay_days=1.0)
    inventory.add("beef", id="i-beef", unit="kg", on_hand=0.0, par_level=5.0)
    inventory.set_supplier("i-beef", supplier.id)
    rec = ahead.build_schedule("2026-10-02", [short()])[0]
    data = rec.to_dict()
    assert data["order_by"] == "2026-09-30"
    assert set(data) >= {"supplier_id", "ingredient_id", "qty", "unit", "note"}
