"""Prompt 06 tests — adapting an unknown external recipe database."""
import sqlite3

import pytest

from modules.recipe_db import GlobalRecipeDB, parse_quantity, slugify


def make_global_db(path, layout):
    conn = sqlite3.connect(path)
    if layout == "a":
        conn.executescript("""
            CREATE TABLE recipes (id INTEGER PRIMARY KEY, name TEXT, servings INT,
                                  tags TEXT, instructions TEXT);
            CREATE TABLE recipe_ingredients (recipe_id INT, ingredient TEXT,
                                             quantity TEXT, unit TEXT);
            INSERT INTO recipes VALUES (1,'Braised Short Rib',4,'braise,french',
                                        'Sear, then braise 4 hours.');
            INSERT INTO recipes VALUES (2,'Carrot Veloute',2,'soup','Blend.');
            INSERT INTO recipe_ingredients VALUES (1,'beef short rib','900','g');
            INSERT INTO recipe_ingredients VALUES (1,'mirepoix','200','g');
            INSERT INTO recipe_ingredients VALUES (2,'carrot','500','g');
        """)
    else:
        # different layout: "dishes" table, no child table, yield as free text
        conn.executescript("""
            CREATE TABLE dishes (dish_id TEXT PRIMARY KEY, title TEXT,
                                 cuisine TEXT, yield_text TEXT, steps TEXT);
            INSERT INTO dishes VALUES ('d1','Scallop Crudo','japanese',
                                       '2 portions','Slice thin.');
            INSERT INTO dishes VALUES ('d2',NULL,'french','4','broken row');
        """)
    conn.commit()
    conn.close()


@pytest.fixture
def global_a(tmp_path):
    path = tmp_path / "recipes_a.db"
    make_global_db(str(path), "a")
    return str(path)


@pytest.fixture
def global_b(tmp_path):
    path = tmp_path / "recipes_b.db"
    make_global_db(str(path), "b")
    return str(path)


def test_unavailable_db_degrades_without_raising(config):
    adapter = GlobalRecipeDB(config.with_overrides(global_recipes_db=""))
    assert adapter.available() is False
    assert adapter.search("anything") == []
    assert adapter.all_recipes() == []
    assert adapter.get("1") is None
    assert adapter.warnings  # it says why


def test_missing_file_degrades(config, tmp_path):
    adapter = GlobalRecipeDB(
        config.with_overrides(global_recipes_db=str(tmp_path / "nope.db")))
    assert adapter.available() is False
    assert adapter.layout() == {}


def test_layout_introspection_finds_recipe_and_child_tables(config, global_a):
    adapter = GlobalRecipeDB(config.with_overrides(global_recipes_db=global_a))
    assert adapter.available() is True
    layout = adapter.layout()
    assert "recipes" in layout and "recipe_ingredients" in layout
    assert adapter.recipe_table() == "recipes"
    assert adapter.child_table() == "recipe_ingredients"


def test_normalize_layout_a_with_child_ingredients(config, global_a):
    adapter = GlobalRecipeDB(config.with_overrides(global_recipes_db=global_a))
    recipes = adapter.all_recipes()
    assert len(recipes) == 2
    short_rib = [r for r in recipes if "Short Rib" in r.name][0]
    assert short_rib.source == "global"
    assert short_rib.portions == 4.0
    assert short_rib.technique_tags == ["braise", "french"]
    assert len(short_rib.lines) == 2
    assert short_rib.lines[0].qty_per_portion == 900.0
    assert short_rib.lines[0].unit == "g"


def test_normalize_layout_b_alternate_column_names(config, global_b):
    adapter = GlobalRecipeDB(config.with_overrides(global_recipes_db=global_b))
    recipes = adapter.all_recipes()
    # the NULL-title row is quarantined, not fatal
    assert len(recipes) == 1
    crudo = recipes[0]
    assert crudo.name == "Scallop Crudo"
    assert crudo.portions == 2.0          # parsed out of "2 portions"
    assert crudo.technique_tags == ["japanese"]
    assert crudo.lines == []              # no child table in this layout


def test_search_is_case_insensitive_substring(config, global_a):
    adapter = GlobalRecipeDB(config.with_overrides(global_recipes_db=global_a))
    assert [r.name for r in adapter.search("short")] == ["Braised Short Rib"]
    assert adapter.search("nothing here") == []


def test_get_by_id(config, global_a):
    adapter = GlobalRecipeDB(config.with_overrides(global_recipes_db=global_a))
    assert adapter.get("2").name == "Carrot Veloute"


def test_global_db_is_never_written(config, global_a):
    """Read-only URI: any write attempt must fail."""
    adapter = GlobalRecipeDB(config.with_overrides(global_recipes_db=global_a))
    with pytest.raises(sqlite3.OperationalError):
        adapter._connect().execute("INSERT INTO recipes (name) VALUES ('hacked')")


def test_parse_quantity_and_slugify():
    assert parse_quantity("2 cups") == 2.0
    assert parse_quantity(None) == 0.0
    assert parse_quantity("to taste") == 0.0
    assert parse_quantity(3) == 3.0
    assert slugify("Beef Short Rib!") == "g-beef-short-rib"
