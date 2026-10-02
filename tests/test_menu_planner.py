"""Prompt 08 tests — service planning, shortfalls, use-first, substitutions."""
import sqlite3

import pytest

from core.types import Recipe, RecipeLine
from modules.inventory import Inventory
from modules.menu_planner import MenuPlanner, ServicePlan, Shortfall
from modules.recipe_db import GlobalRecipeDB
from modules.recipes import RecipeBook


@pytest.fixture
def kitchen(config, db, bus):
    inv = Inventory(config, db, bus)
    book = RecipeBook(config, db, bus)
    book.save(Recipe(id="r-1", name="Braised Short Rib", portions=4, lines=[
        RecipeLine("i-beef", 250.0, "g"), RecipeLine("i-carrot", 50.0, "g")]))
    book.save(Recipe(id="r-2", name="Carrot Veloute", portions=2, lines=[
        RecipeLine("i-carrot", 150.0, "g")]))
    return inv, book, MenuPlanner(config, db, bus, inventory=inv, recipe_book=book)


def test_plan_with_enough_stock_has_no_shortfalls(kitchen):
    inv, book, planner = kitchen
    inv.add("beef", id="i-beef", unit="g", par_level=5000, on_hand=20000.0)
    inv.add("carrot", id="i-carrot", unit="g", par_level=2000, on_hand=20000.0)
    plan = planner.plan_service({"r-1": 40, "r-2": 20})
    assert plan.shortfalls == []
    assert int(sum(plan.covers.values())) == 60
    assert len(plan.prep) == 2
    assert planner.get_plan().service_date == plan.service_date


def test_shortfall_math_and_reason(kitchen):
    inv, book, planner = kitchen
    inv.add("beef", id="i-beef", unit="g", par_level=5000, on_hand=8000.0)
    inv.add("carrot", id="i-carrot", unit="g", par_level=10000, on_hand=4000.0)
    plan = planner.plan_service({"r-1": 40, "r-2": 20})
    by_id = {s.ingredient_id: s for s in plan.shortfalls}

    # beef: 40 x 250 = 10000 needed, 8000 on hand -> short 2000, above par
    assert by_id["i-beef"].deficit == 2000.0
    assert by_id["i-beef"].reason == "insufficient"
    # carrot: 40 x 50 + 20 x 150 = 5000 needed, 4000 on hand, under par 10000
    assert by_id["i-carrot"].needed == 5000.0
    assert by_id["i-carrot"].deficit == 1000.0
    assert by_id["i-carrot"].reason == "below_par"


def test_ingredient_never_seen_before_is_a_missing_shortfall(kitchen):
    inv, book, planner = kitchen
    inv.add("beef", id="i-beef", unit="g", par_level=100, on_hand=50000.0)
    plan = planner.plan_service({"r-1": 10})
    missing = [s for s in plan.shortfalls if s.reason == "missing"]
    assert [s.ingredient_id for s in missing] == ["i-carrot"]
    assert missing[0].on_hand == 0.0


def test_use_first_flags_ingredients_going_off(kitchen):
    from datetime import date, timedelta

    inv, book, planner = kitchen
    # protein shelf life is 3 days, so a 3-day-old delivery is off tonight
    inv.add("beef", id="i-beef", category="protein", unit="g", on_hand=50000.0,
            freshness_date=(date.today() - timedelta(days=3)).strftime("%Y-%m-%d"))
    inv.add("carrot", id="i-carrot", unit="g", on_hand=50000.0)
    plan = planner.plan_service({"r-1": 10})
    names = [u["name"] for u in plan.use_first]
    assert names == ["beef"]
    assert plan.use_first[0]["label"] == "expired"


def test_substitution_ideas_come_from_the_global_db(config, db, bus, tmp_path):
    global_path = tmp_path / "global.db"
    conn = sqlite3.connect(str(global_path))
    conn.executescript("""
        CREATE TABLE recipes (id INTEGER PRIMARY KEY, name TEXT, servings INT);
        INSERT INTO recipes VALUES (1,'Carrot Soup',4);
        INSERT INTO recipes VALUES (2,'Carrot Cake',8);
    """)
    conn.commit()
    conn.close()

    cfg = config.with_overrides(global_recipes_db=str(global_path))
    inv = Inventory(cfg, db, bus)
    global_db = GlobalRecipeDB(cfg, db, bus)
    book = RecipeBook(cfg, db, bus, global_db=global_db)
    book.save(Recipe(id="r-1", name="Needs Carrots",
                     lines=[RecipeLine("i-carrot", 150.0, "g")]))
    planner = MenuPlanner(cfg, db, bus, inventory=inv, recipe_book=book)
    # the ingredient must exist (with its real name) for the shortfall to carry
    # a searchable name into the global recipe database
    inv.add("carrot", id="i-carrot", unit="g", on_hand=0.0)

    plan = planner.plan_service({"r-1": 10})
    assert plan.substitutions
    assert plan.substitutions[0]["name"] == "carrot"  # the ingredient's name
    assert "Carrot Soup" in plan.substitutions[0]["suggestions"]


def test_replanning_the_same_date_replaces_the_plan(kitchen, db):
    inv, book, planner = kitchen
    inv.add("beef", id="i-beef", unit="g", on_hand=50000.0)
    inv.add("carrot", id="i-carrot", unit="g", on_hand=50000.0)
    planner.plan_service({"r-1": 10}, service_date="2026-10-01")
    planner.plan_service({"r-1": 25}, service_date="2026-10-01")
    assert db.scalar("SELECT COUNT(*) FROM service_plans") == 1
    assert planner.get_plan("2026-10-01").covers == {"r-1": 25}


def test_empty_covers_is_a_noop_plan(kitchen):
    inv, book, planner = kitchen
    plan = planner.plan_service({})
    assert plan.shortfalls == []
    assert plan.prep == []


def test_event_published_with_shortfall_payload(kitchen, bus):
    from core.events import MENU_PLANNED

    seen = []
    bus.subscribe(MENU_PLANNED, lambda t, p: seen.append(p))
    inv, book, planner = kitchen
    planner.plan_service({"r-1": 10})
    assert seen and seen[0]["shortfalls"]
    assert seen[0]["shortfalls"][0]["ingredient_id"] == "i-beef"


def test_markdown_report_covers_every_section(kitchen):
    inv, book, planner = kitchen
    inv.add("beef", id="i-beef", unit="g", par_level=99999, on_hand=100.0)
    plan = planner.plan_service({"r-1": 10}, service_date="2026-10-01")
    report = plan.markdown()
    assert "Service plan — 2026-10-01" in report
    assert "## Prep" in report
    assert "## Shortfalls (order these)" in report
    assert "## Use first (going off)" in report
    assert "beef" in report


def test_plan_serialisation_round_trip(kitchen):
    inv, book, planner = kitchen
    inv.add("beef", id="i-beef", unit="g", on_hand=1.0)
    plan = planner.plan_service({"r-1": 10}, service_date="2026-10-02")
    restored = ServicePlan.from_dict(plan.to_dict())
    assert restored.shortfalls == plan.shortfalls
    assert isinstance(restored.shortfalls[0], Shortfall)
