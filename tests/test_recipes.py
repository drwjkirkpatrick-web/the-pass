"""Prompt 07 tests — recipe book, scaling, costing and requirements."""
import pytest

from core.types import Recipe, RecipeLine
from modules.recipe_db import GlobalRecipeDB
from modules.recipes import RecipeBook, convert


@pytest.fixture
def book(config, db, bus):
    return RecipeBook(config, db, bus)


@pytest.fixture
def short_rib():
    return Recipe(id="r-1", name="Braised Short Rib", portions=4, station="meat",
                  technique_tags=["braise"], menu_price=38.0,
                  lines=[RecipeLine("i-beef", 250.0, "g", "sear first"),
                         RecipeLine("i-mirepoix", 60.0, "g")])


def test_save_and_get_round_trip(book, short_rib):
    book.save(short_rib)
    loaded = book.get("r-1")
    assert loaded.name == "Braised Short Rib"
    assert loaded.portions == 4
    assert loaded.technique_tags == ["braise"]
    assert [l.ingredient_id for l in loaded.lines] == ["i-beef", "i-mirepoix"]
    assert loaded.lines[0].prep_note == "sear first"


def test_save_replaces_lines_instead_of_duplicating(book, short_rib):
    book.save(short_rib)
    book.save(Recipe(id="r-1", name="Braised Short Rib", portions=4,
                     lines=[RecipeLine("i-beef", 200.0, "g")]))
    loaded = book.get("r-1")
    assert len(loaded.lines) == 1
    assert loaded.lines[0].qty_per_portion == 200.0


def test_convert_units_both_directions():
    assert convert(1.0, "kg", "g") == 1000.0
    assert convert(1000.0, "g", "kg") == 1.0
    assert round(convert(1.0, "lb", "g"), 2) == 453.59
    assert convert(2.0, "each", "ea") == 2.0


def test_convert_rejects_incompatible_or_unknown_units():
    with pytest.raises(ValueError):
        convert(1.0, "g", "ml")
    with pytest.raises(ValueError):
        convert(1.0, "furlong", "g")


def test_scale_to_portions(book, short_rib):
    scaled = book.scale(short_rib, 10)
    by_id = {s["ingredient_id"]: s["qty"] for s in scaled}
    assert by_id["i-beef"] == 2500.0
    assert by_id["i-mirepoix"] == 600.0


def test_scale_by_recipe_id(book, short_rib):
    book.save(short_rib)
    assert book.scale("r-1", 2)[0]["qty"] == 500.0
    assert book.scale("missing", 2) == []


def test_portion_size(book):
    recipe = Recipe(id="r-2", name="Plate", portions=1,
                    lines=[RecipeLine("i-a", 0.25, "kg"), RecipeLine("i-b", 50.0, "g"),
                           RecipeLine("i-c", 2.0, "each")])
    size = book.portion_size(recipe)
    assert size["grams"] == 300.0
    assert size["count_items"] == 2


def test_portion_cost_with_live_prices(book, short_rib, config, db, bus):
    from modules.inventory import Inventory

    inv = Inventory(config, db, bus)
    inv.add("beef", id="i-beef", unit="g", unit_cost=0.05, on_hand=9000.0)
    inv.add("mirepoix", id="i-mirepoix", unit="g", unit_cost=0.01, on_hand=2000.0)
    cost = book.portion_cost(short_rib, inv)
    # per portion: 250*0.05 + 60*0.01 = 12.5 + 0.6 = 13.1 ; x4 = 52.4
    assert cost["total"] == 52.4
    assert cost["per_portion"] == 13.1
    assert cost["missing_prices"] == []


def test_portion_cost_reports_missing_prices_instead_of_crashing(book, short_rib,
                                                                 config, db, bus):
    from modules.inventory import Inventory

    inv = Inventory(config, db, bus)
    inv.add("beef", id="i-beef", unit="g", unit_cost=0.05, on_hand=1000.0)
    cost = book.portion_cost(short_rib, inv)
    assert cost["missing_prices"] == ["i-mirepoix"]
    assert cost["total"] == 50.0


def test_ingredient_requirements_aggregate_across_recipes(book):
    book.save(Recipe(id="r-1", name="A", lines=[RecipeLine("i-beef", 250.0, "g")]))
    book.save(Recipe(id="r-2", name="B", lines=[RecipeLine("i-beef", 100.0, "g"),
                                                RecipeLine("i-carrot", 50.0, "g")]))
    reqs = book.ingredient_requirements({"r-1": 40, "r-2": 20})
    by_id = {r["ingredient_id"]: r for r in reqs}
    assert by_id["i-beef"]["qty"] == 40 * 250.0 + 20 * 100.0
    assert by_id["i-beef"]["by_recipe"] == {"r-1": 10000.0, "r-2": 2000.0}
    assert by_id["i-carrot"]["qty"] == 1000.0
    # sorted by quantity, biggest first
    assert reqs[0]["ingredient_id"] == "i-beef"


def test_incompatible_units_are_kept_apart(book):
    book.save(Recipe(id="r-1", name="A", lines=[RecipeLine("i-x", 1.0, "kg")]))
    book.save(Recipe(id="r-2", name="B", lines=[RecipeLine("i-x", 2.0, "each")]))
    reqs = book.ingredient_requirements({"r-1": 1, "r-2": 1})
    assert len(reqs) == 2  # never silently added together


def test_search_merges_global_recipes(config, db, bus, tmp_path, short_rib):
    import sqlite3

    global_path = tmp_path / "global.db"
    conn = sqlite3.connect(str(global_path))
    conn.executescript("""
        CREATE TABLE recipes (id INTEGER PRIMARY KEY, name TEXT, servings INT);
        INSERT INTO recipes VALUES (1,'Braised Short Rib',4);
        INSERT INTO recipes VALUES (2,'Scallop Crudo',2);
    """)
    conn.commit()
    conn.close()

    book = RecipeBook(config.with_overrides(global_recipes_db=str(global_path)),
                      db, bus, global_db=None)
    book.global_db = GlobalRecipeDB(book.config, db, bus)
    book.save(short_rib)

    hits = book.search("braised")
    assert len(hits) == 1  # local copy wins, no duplicate
    assert book.search("scallop")[0].source == "global"
    assert len(book.all_recipes()) == 2
    assert book.get("g:2").name == "Scallop Crudo"


def test_delete_removes_recipe_and_lines(book, short_rib):
    book.save(short_rib)
    book.delete("r-1")
    assert book.get("r-1") is None
    assert book.db.scalar("SELECT COUNT(*) FROM recipe_lines") == 0
