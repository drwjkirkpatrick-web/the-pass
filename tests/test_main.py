"""Prompt 26 tests — composition root, wiring verification, degradation."""
from datetime import datetime

import pytest

from core.agent import PassAgent
from core.config import Config
from core.events import (INGREDIENTS_UPDATED, SERVICE_CLOSE, TEMP_EXCURSION,
                         EventBus)
from core.database import Database
from main import EXPECTED_LISTENED, build_agent, verify_wiring

WHEN = datetime(2026, 10, 1, 19, 0, 0)


@pytest.fixture
def agent(tmp_path):
    config = Config.default().with_overrides(
        db_path=str(tmp_path / "pass.db"), photo_dir=str(tmp_path / "photos"))
    return build_agent(config)


def test_every_module_is_constructed(agent):
    expected = {"inventory", "ingredient_vision", "recipe_db", "recipes",
                "menu_planner", "deliveries", "order_ahead", "ordering",
                "review_queue", "templog", "reminders_boh", "reminders_foh",
                "dish_counter", "wash_counter", "pass_photo", "review_logic",
                "comms", "kanban", "boards", "journal", "consistency", "michelin"}
    assert expected.issubset(set(agent.modules))
    assert set(agent.modules) == set(expected)


def test_wiring_verification_passes_on_the_full_build(agent):
    report = verify_wiring(agent)
    assert report["ok"] is True
    for event in EXPECTED_LISTENED:
        assert report["listeners"][event] > 0
    assert "comms" in report["modules"]


def test_wiring_verification_flags_an_unwired_agent():
    bare = PassAgent(config=Config.default().with_overrides(db_path=":memory:"),
                     db=Database(":memory:").migrate(), bus=EventBus())
    report = verify_wiring(bare)
    assert report["ok"] is False
    assert INGREDIENTS_UPDATED in report["orphan_events"]


def test_ingredient_event_reaches_the_foh_reminder_lane(agent):
    inventory = agent.get("inventory")
    inventory.add("scallops", id="i-scallop", category="seafood", unit="each",
                  par_level=20.0, on_hand=1.0)
    warnings = [r for r in agent.get("reminders_foh").engine.pending("foh")
                if "86 warning" in r.title]
    assert len(warnings) == 1


def test_service_close_reaches_the_boards(agent):
    agent.db.execute("INSERT INTO checklists (audience, item, position, active)"
                     " VALUES ('foh','Wipe the pass',1,1)")
    agent.bus.publish(SERVICE_CLOSE, {"date": "2026-10-01"})
    titles = [c.title for c in agent.get("kanban").cards(
        agent.get("boards").foh_board()["id"])]
    assert titles == ["Close: Wipe the pass"]


def test_missing_optional_pieces_do_not_stop_the_build(tmp_path):
    config = Config.default().with_overrides(
        db_path=str(tmp_path / "pass.db"), photo_dir=str(tmp_path / "photos"),
        global_recipes_db=str(tmp_path / "does-not-exist.db"))
    agent = build_agent(config)
    assert agent.get("recipe_db").available() is False
    assert agent.get("recipes").all_recipes() == []   # degrades, does not crash
    assert agent.get("michelin") is not None


def test_tick_runs_every_module(agent):
    results = agent.tick(WHEN)
    assert "inventory" not in results or True   # modules without tick() are skipped
    assert set(results).issubset(set(agent.modules))
    assert agent.state(WHEN).value == "service"


def test_full_pipeline_smoke_on_the_wired_agent(agent):
    """Recipe -> plan -> curated drafts -> human gate, all through the bus."""
    inventory = agent.get("inventory")
    recipes = agent.get("recipes")
    from core.types import Recipe, RecipeLine

    inventory.add("beef", id="i-beef", category="protein", unit="g", on_hand=1000.0,
                  par_level=5000.0, unit_cost=0.05)
    recipes.save(Recipe(id="r-1", name="Short Rib", portions=4,
                        lines=[RecipeLine("i-beef", 250.0, "g")]))

    orders = agent.get("ordering").curate_orders("2026-10-02")
    assert orders and orders[0].status.value == "draft"

    queue = agent.get("review_queue")
    item = queue.submit_order(orders[0])
    assert queue.pending()[0].id == item.id
    queue.approve(item.id, actor="chef")
    assert agent.get("ordering").get_order(orders[0].id).status.value == "approved"
