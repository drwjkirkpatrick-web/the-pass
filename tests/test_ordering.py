"""Prompt 11 tests — draft purchase orders with rationale, never sent."""
import pytest

from core.events import DRAFT_ORDER_READY
from core.types import OrderStatus, Recipe, RecipeLine
from modules.deliveries import Deliveries
from modules.inventory import Inventory
from modules.menu_planner import MenuPlanner
from modules.order_ahead import OrderAhead
from modules.ordering import UNASSIGNED, Ordering
from modules.recipes import RecipeBook


@pytest.fixture
def kitchen(config, db, bus):
    deliveries = Deliveries(config, db, bus)
    inventory = Inventory(config, db, bus)
    book = RecipeBook(config, db, bus)
    planner = MenuPlanner(config, db, bus, inventory=inventory, recipe_book=book)
    ahead = OrderAhead(config, db, bus, deliveries=deliveries, inventory=inventory)
    ordering = Ordering(config, db, bus, inventory=inventory, recipe_book=book,
                        planner=planner, order_ahead=ahead, deliveries=deliveries)

    green = deliveries.add_supplier("Green Ridge", order_cutoff="07:00",
                                    typical_delay_days=2.0)
    fish = deliveries.add_supplier("Dockside", typical_delay_days=1.0)

    inventory.add("beef", id="i-beef", category="protein", unit="g",
                  on_hand=8000.0, par_level=5000.0, unit_cost=0.05)
    inventory.set_supplier("i-beef", green.id)
    inventory.add("carrot", id="i-carrot", category="produce", unit="g",
                  on_hand=1000.0, par_level=20000.0, unit_cost=0.002)
    inventory.set_supplier("i-carrot", green.id)
    inventory.add("scallop", id="i-scallop", category="seafood", unit="g",
                  on_hand=0.0, par_level=2000.0, unit_cost=0.09)
    inventory.set_supplier("i-scallop", fish.id)

    book.save(Recipe(id="r-1", name="Braised Short Rib", portions=4, lines=[
        RecipeLine("i-beef", 250.0, "g"), RecipeLine("i-carrot", 50.0, "g"),
        RecipeLine("i-scallop", 30.0, "g")]))
    planner.plan_service({"r-1": 40}, service_date="2026-10-02")
    return inventory, ordering, green, fish


def test_curate_creates_one_draft_per_supplier(kitchen):
    inventory, ordering, green, fish = kitchen
    orders = ordering.curate_orders("2026-10-02")
    suppliers = sorted(o.supplier_id for o in orders)
    assert suppliers == sorted([green.id, fish.id])
    assert all(o.status is OrderStatus.DRAFT for o in orders)
    assert all(o.created_by == "agent" for o in orders)


def test_every_line_carries_a_rationale_and_no_line_is_duplicated(kitchen):
    inventory, ordering, green, fish = kitchen
    orders = ordering.curate_orders("2026-10-02")
    for order in orders:
        assert order.lines
        for line in order.lines:
            assert line.rationale
            assert "needed" in line.rationale
    # an ingredient appears in exactly one order, exactly once
    seen = [l.ingredient_id for o in orders for l in o.lines]
    assert len(seen) == len(set(seen))


def test_shortfall_and_par_topup_are_not_double_counted(kitchen):
    inventory, ordering, green, fish = kitchen
    orders = ordering.curate_orders("2026-10-02")
    carrots = [l for o in orders for l in o.lines if l.ingredient_id == "i-carrot"][0]
    # menu needs 40 x 50 = 2000 g, par top-up would be 19000 g -> menu need is
    # smaller, so the par top-up (the larger ask) wins exactly once
    assert carrots.qty == 19000.0
    assert "below par" in carrots.rationale


def test_pack_sizes_round_up(kitchen):
    inventory, ordering, green, fish = kitchen
    orders = ordering.curate_orders("2026-10-02", pack_sizes={"i-carrot": 5000.0})
    carrots = [l for o in orders for l in o.lines if l.ingredient_id == "i-carrot"][0]
    assert carrots.qty == 20000.0  # 19000 rounds up to 4 x 5000
    assert carrots.pack_size == 5000.0


def test_nothing_is_approved_or_sent_automatically(kitchen, db):
    inventory, ordering, green, fish = kitchen
    ordering.curate_orders("2026-10-02")
    statuses = {r["status"] for r in db.query("SELECT status FROM orders")}
    assert statuses == {"draft"}
    assert ordering.orders(status="approved") == []


def test_order_date_appears_in_the_rationale(kitchen):
    inventory, ordering, green, fish = kitchen
    orders = ordering.curate_orders("2026-10-02")
    beef = [l for o in orders for l in o.lines if l.ingredient_id == "i-beef"][0]
    # Green Ridge p90 = 2d, arrival 2026-10-01 -> order by 2026-09-29
    assert "order by 2026-09-29" in beef.rationale


def test_unassigned_ingredients_land_in_their_own_order(kitchen):
    inventory, ordering, green, fish = kitchen
    inventory.add("mystery herb", id="i-herb", category="produce", unit="g",
                  on_hand=0.0, par_level=100.0)
    orders = ordering.curate_orders("2026-10-02")
    unassigned = [o for o in orders if o.supplier_id == UNASSIGNED]
    assert len(unassigned) == 1
    assert unassigned[0].lines[0].ingredient_name == "mystery herb"


def test_export_produces_a_readable_purchase_order(kitchen):
    inventory, ordering, green, fish = kitchen
    order = [o for o in ordering.curate_orders("2026-10-02")
             if o.supplier_id == green.id][0]
    text = ordering.export_order(order)
    assert text.startswith("PURCHASE ORDER")
    assert "Green Ridge" in text
    assert "Order cutoff: 07:00" in text
    assert "Human approval required before sending." in text
    assert "Braised" in text or "beef" in text


def test_draft_order_ready_event_fires(kitchen, bus):
    inventory, ordering, green, fish = kitchen
    seen = []
    bus.subscribe(DRAFT_ORDER_READY, lambda t, p: seen.append(p))
    ordering.curate_orders("2026-10-02")
    assert len(seen) == 2
    assert all(p["lines"] > 0 for p in seen)


def test_nothing_to_order_returns_no_drafts(config, db, bus):
    inventory = Inventory(config, db, bus)
    ordering = Ordering(config, db, bus, inventory=inventory)
    assert ordering.curate_orders("2026-10-02") == []


def test_orders_can_be_listed_by_status(kitchen):
    inventory, ordering, green, fish = kitchen
    created = ordering.curate_orders("2026-10-02")
    drafts = ordering.orders(status="draft")
    assert len(drafts) == len(created)
    assert ordering.get_order(created[0].id).lines[0].qty > 0
