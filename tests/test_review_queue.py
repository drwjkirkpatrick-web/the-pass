"""Prompt 12 tests — the human gate: approve, edit, reject, escalate, audit."""
import pytest

from core.events import ORDER_APPROVED, REVIEW_PENDING
from core.types import OrderStatus
from modules.deliveries import Deliveries
from modules.inventory import Inventory
from modules.menu_planner import MenuPlanner
from modules.order_ahead import OrderAhead
from modules.ordering import Ordering
from modules.recipes import RecipeBook
from modules.review_queue import ReviewQueue


@pytest.fixture
def gate(config, db, bus):
    deliveries = Deliveries(config, db, bus)
    inventory = Inventory(config, db, bus)
    book = RecipeBook(config, db, bus)
    planner = MenuPlanner(config, db, bus, inventory=inventory, recipe_book=book)
    ahead = OrderAhead(config, db, bus, deliveries=deliveries, inventory=inventory)
    ordering = Ordering(config, db, bus, inventory=inventory, recipe_book=book,
                        planner=planner, order_ahead=ahead, deliveries=deliveries)
    queue = ReviewQueue(config, db, bus, ordering=ordering)

    supplier = deliveries.add_supplier("Green Ridge", typical_delay_days=1.0)
    inventory.add("beef", id="i-beef", category="protein", unit="g",
                  on_hand=1000.0, par_level=5000.0, unit_cost=0.05)
    inventory.set_supplier("i-beef", supplier.id)
    order = ordering.curate_orders("2026-10-02")[0]
    return ordering, queue, order


def test_submit_then_pending(gate, bus):
    ordering, queue, order = gate
    seen = []
    bus.subscribe(REVIEW_PENDING, lambda t, p: seen.append(p))
    item = queue.submit_order(order)
    assert item.status == "pending"
    assert item.item_type == "order"
    assert [i.id for i in queue.pending()] == [item.id]
    assert seen and seen[0]["id"] == item.id


def test_approve_is_the_only_way_an_order_becomes_approved(gate):
    ordering, queue, order = gate
    item = queue.submit_order(order)
    with pytest.raises(ValueError):
        ordering.set_status(order.id, OrderStatus.APPROVED)  # guard rail
    queue.approve(item.id, actor="chef")
    assert ordering.get_order(order.id).status is OrderStatus.APPROVED


def test_approve_publishes_order_approved_event(gate, bus):
    ordering, queue, order = gate
    seen = []
    bus.subscribe(ORDER_APPROVED, lambda t, p: seen.append(p))
    item = queue.submit_order(order)
    queue.approve(item.id, actor="chef")
    assert seen == [{"order_id": order.id, "approved_by": "chef"}]


def test_approve_applies_chef_edits_atomically(gate):
    ordering, queue, order = gate
    item = queue.submit_order(order)
    queue.approve(item.id, edits={"i-beef": {"qty": 1234.0}})
    updated = ordering.get_order(order.id)
    line = [l for l in updated.lines if l.ingredient_id == "i-beef"][0]
    assert line.qty == 1234.0
    assert "[edited by chef]" in line.rationale
    assert updated.status is OrderStatus.APPROVED


def test_approve_can_remove_a_line(gate):
    ordering, queue, order = gate
    item = queue.submit_order(order)
    queue.approve(item.id, edits={"i-beef": {"remove": True}})
    assert ordering.get_order(order.id).lines == []


def test_reject_requires_a_reason_and_leaves_the_order_draft(gate):
    ordering, queue, order = gate
    item = queue.submit_order(order)
    with pytest.raises(ValueError):
        queue.reject(item.id, reason="   ")
    queue.reject(item.id, reason="we still have beef")
    assert queue.get(item.id).status == "rejected"
    assert ordering.get_order(order.id).status is OrderStatus.DRAFT
    assert queue.pending() == []


def test_double_decisions_are_refused(gate):
    ordering, queue, order = gate
    item = queue.submit_order(order)
    queue.approve(item.id)
    with pytest.raises(ValueError):
        queue.approve(item.id)


def test_audit_trail_records_every_transition(gate):
    ordering, queue, order = gate
    item = queue.submit_order(order)
    queue.approve(item.id, edits={"i-beef": {"qty": 10.0}}, actor="chef")
    entries = queue.log(item.id)
    assert len(entries) == 1
    assert entries[0]["decision"] == "approved"
    assert entries[0]["actor"] == "chef"
    assert '"qty": 10.0' in entries[0]["reason"]

    other = queue.submit_order(order)
    queue.reject(other.id, reason="too much", actor="chef")
    assert queue.log(other.id)[0]["reason"] == "too much"


def test_overdue_items_escalate_on_tick(gate, bus):
    ordering, queue, order = gate
    from core.events import REMINDER_DUE

    seen = []
    bus.subscribe(REMINDER_DUE, lambda t, p: seen.append(p))
    item = queue.submit("order", order.id, "Approve order",
                        deadline="2020-01-01T00:00:00")
    result = queue.tick()
    assert result["escalated"] == [item.id]
    assert queue.get(item.id).status == "escalated"
    assert seen and "Waiting on you" in seen[0]["title"]


def test_submit_sets_a_default_deadline_from_config(gate):
    ordering, queue, order = gate
    item = queue.submit_order(order)
    assert item.deadline
    assert item.deadline > item.created_at


def test_unknown_review_id_raises(gate):
    ordering, queue, order = gate
    with pytest.raises(KeyError):
        queue.approve("rv-nope")
    assert queue.get("rv-nope") is None
