"""Prompt 05 tests — inventory, par levels, freshness and 86 tracking."""
from datetime import date, timedelta

import pytest

from core.types import Ingredient, Recipe, RecipeLine
from modules.inventory import DEFAULT_SHELF_LIFE, SHELF_LIFE_DAYS, Inventory


@pytest.fixture
def inv(config, db, bus):
    return Inventory(config, db, bus)


def test_add_and_get_round_trip(inv):
    created = inv.add("heirloom tomato", category="produce", unit="kg",
                      par_level=6.0, on_hand=4.0)
    fetched = inv.get(created.id)
    assert fetched.name == "heirloom tomato"
    assert fetched.category == "produce"
    assert fetched.shelf_life_days == SHELF_LIFE_DAYS["produce"]


def test_upsert_updates_existing_row_instead_of_duplicating(inv):
    created = inv.add("butter", category="dairy", on_hand=2.0)
    inv.upsert(Ingredient(id=created.id, name="butter", category="dairy",
                          on_hand=5.0))
    assert inv.summary()["ingredients"] == 1
    assert inv.get(created.id).on_hand == 5.0


def test_unknown_category_gets_conservative_default(inv):
    created = inv.add("mystery powder", category="unobtainium", on_hand=1.0)
    assert created.shelf_life_days == DEFAULT_SHELF_LIFE


def test_below_par_detection(inv):
    inv.add("beef short rib", category="protein", par_level=10.0, on_hand=4.0)
    inv.add("carrots", category="produce", par_level=2.0, on_hand=5.0)
    short = [i.name for i in inv.below_par()]
    assert short == ["beef short rib"]


def test_adjust_qty_and_set_par(inv):
    item = inv.add("cream", category="dairy", on_hand=1.0, par_level=3.0)
    inv.adjust_qty(item.id, 2.5)
    assert inv.get(item.id).on_hand == 3.5
    inv.set_par(item.id, 5.0)
    assert inv.get(item.id).par_level == 5.0
    assert inv.below_par()[0].name == "cream"


def test_freshness_score_labels(inv):
    fresh = inv.add("just delivered", category="produce", on_hand=1.0,
                    freshness_date=date.today().strftime("%Y-%m-%d"))
    score = inv.freshness_score(fresh)
    assert score["label"] == "fresh"
    assert score["score"] >= 70

    old = inv.add("old greens", category="produce", on_hand=1.0,
                  freshness_date=(date.today() - timedelta(days=4)).strftime("%Y-%m-%d"))
    assert inv.freshness_score(old)["label"] in ("use first", "use soon")

    expired = inv.add("rotten", category="seafood", on_hand=1.0,
                      freshness_date=(date.today() - timedelta(days=9)).strftime("%Y-%m-%d"))
    assert inv.freshness_score(expired)["label"] == "expired"
    assert inv.is_expired(expired) is True


def test_expiring_within_window_and_available_excludes_expired(inv):
    inv.add("day 1", category="produce", on_hand=2.0,
            freshness_date=(date.today() - timedelta(days=4)).strftime("%Y-%m-%d"))
    inv.add("day 4", category="produce", on_hand=2.0,
            freshness_date=date.today().strftime("%Y-%m-%d"))
    expiring = [i.name for i in inv.expiring_within(2)]
    assert expiring == ["day 1"]
    available = [i.name for i in inv.list_available()]
    assert "day 1" in available and "day 4" in available

    inv.add("gone", category="seafood", on_hand=3.0,
            freshness_date=(date.today() - timedelta(days=30)).strftime("%Y-%m-%d"))
    assert "gone" not in [i.name for i in inv.list_available()]


def test_eighty_six_flag_is_logged_and_hides_from_available(inv, db):
    item = inv.add("scallops", category="seafood", on_hand=20.0)
    inv.flag_eighty_six(item.id, source="chef")
    assert inv.is_eighty_six(item.id) is True
    assert item.name not in [i.name for i in inv.list_available()]
    assert db.scalar("SELECT COUNT(*) FROM eighty_six_log") == 1
    assert inv.summary()["eighty_six"] == 1
    inv.clear_eighty_six(item.id)
    assert inv.is_eighty_six(item.id) is False


def test_events_published_on_change(config, db, bus):
    from core.events import INGREDIENTS_UPDATED

    seen = []
    bus.subscribe(INGREDIENTS_UPDATED, lambda t, p: seen.append(p))
    inv = Inventory(config, db, bus)
    inv.add("onions", category="produce", par_level=5.0, on_hand=1.0)
    assert seen and seen[-1]["action"] == "upsert"
    assert seen[-1]["below_par"] is True


def test_suggest_uses_joins_recipes(inv, config, db):
    item = inv.add("beef short rib", category="protein", on_hand=9.0)
    recipes = [
        Recipe(id="r-1", name="Braised Short Rib",
               lines=[RecipeLine(item.id, 250.0, "g")]),
        Recipe(id="r-2", name="Carrot Soup",
               lines=[RecipeLine("i-carrot", 200.0, "g")]),
    ]
    assert inv.suggest_uses(item.id, recipes) == ["Braised Short Rib"]


def test_summary_on_empty_database_does_not_crash(inv):
    summary = inv.summary()
    assert summary["ingredients"] == 0
    assert summary["below_par"] == 0
    assert summary["eighty_six_events_today"] == 0
