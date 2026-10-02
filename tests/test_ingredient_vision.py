"""Prompt 04 tests — photo intake, mock analyzer, human approval gate."""
import os

import pytest

from core.types import Ingredient
from modules.ingredient_vision import (
    IngredientCurator,
    IngredientObservation,
    MockVisionAnalyzer,
    RealVisionAnalyzer,
    VisionAnalyzer,
    guess_category,
)


@pytest.fixture
def photos(tmp_path):
    """Named files are the mock analyzer's input, so names are the fixture."""
    names = ["heirloom_tomato_4kg_crisp.jpg", "beef_short_rib_9kg.jpg",
             "tomato_2kg_lo.jpg"]
    paths = []
    for name in names:
        path = tmp_path / name
        path.write_bytes(b"fake-jpeg")
        paths.append(str(path))
    return paths


def test_mock_analyzer_parses_name_qty_unit_and_note(photos):
    obs = MockVisionAnalyzer().analyze(photos[0])[0]
    assert obs.name == "heirloom tomato"
    assert obs.qty == 4.0
    assert obs.unit == "kg"
    assert obs.quality_note == "crisp"
    assert obs.confidence > 0.6


def test_mock_analyzer_low_confidence_token(photos):
    obs = MockVisionAnalyzer().analyze(photos[2])[0]
    assert obs.confidence < 0.6
    assert obs.name == "tomato"


def test_real_analyzer_is_an_explicit_extension_point():
    with pytest.raises(NotImplementedError):
        RealVisionAnalyzer().analyze("/tmp/whatever.jpg")
    assert issubclass(MockVisionAnalyzer, VisionAnalyzer)


def test_guess_category():
    assert guess_category("heirloom tomato") == "produce"
    assert guess_category("beef short rib") == "protein"
    assert guess_category("mystery powder") == "general"


def test_ingest_creates_a_draft_and_never_touches_live_inventory(config, db, photos):
    curator = IngredientCurator(config, db)
    result = curator.ingest(photos)
    assert result["status"] == "proposed"
    assert len(result["lines"]) == 3
    # the whole point: nothing is live until a human approves
    assert db.scalar("SELECT COUNT(*) FROM ingredients") == 0
    assert db.scalar("SELECT COUNT(*) FROM ingredient_drafts") == 1


def test_low_confidence_lines_are_flagged_for_review(config, db, photos):
    result = IngredientCurator(config, db).ingest(photos)
    flagged = [l for l in result["lines"] if l["flagged"]]
    assert len(flagged) == 1
    assert flagged[0]["name"] == "tomato"
    assert result["flagged"] == 1


def test_approve_promotes_draft_to_inventory_and_merges_duplicates(config, db, photos):
    curator = IngredientCurator(config, db)
    draft = curator.ingest(photos)
    written = curator.approve(draft["draft_id"])

    names = sorted(i.name for i in written)
    # "tomato" and "heirloom tomato" are different names -> two rows
    assert names == ["beef short rib", "heirloom tomato", "tomato"]
    tomato = [i for i in written if i.name == "heirloom tomato"][0]
    assert tomato.on_hand == 4.0
    assert tomato.category == "produce"
    assert tomato.freshness_date == db.today()
    assert db.scalar("SELECT COUNT(*) FROM ingredients") == 3
    assert curator.drafts() == []  # draft no longer pending


def test_approve_applies_human_edits_and_sums_duplicates(config, db, photos):
    curator = IngredientCurator(config, db)
    draft = curator.ingest(photos)
    # Chef says: the low-confidence "tomato" is the same heirloom tomato, and
    # there is a par level of 6 kg.
    low = [l for l in draft["lines"] if l["flagged"]][0]
    high = [l for l in draft["lines"] if l["name"] == "heirloom tomato"][0]
    written = curator.approve(draft["draft_id"], edits={
        low["id"]: {"name": "heirloom tomato"},
        high["id"]: {"par_level": 6.0},
    })
    assert len(written) == 2  # heirloom tomato (merged) + beef short rib
    tomato = [i for i in written if i.name == "heirloom tomato"][0]
    assert tomato.on_hand == 6.0     # 4 kg + 2 kg
    assert tomato.par_level == 6.0


def test_reject_keeps_inventory_untouched(config, db, photos):
    curator = IngredientCurator(config, db)
    draft = curator.ingest(photos)
    curator.reject(draft["draft_id"], reason="box was for the other kitchen")
    assert curator.drafts() == []
    assert curator.drafts(status="rejected")
    assert db.scalar("SELECT COUNT(*) FROM ingredients") == 0


def test_approve_uses_inventory_collaborator_when_wired(config, db, bus, photos):
    from modules.inventory import Inventory

    inventory = Inventory(config, db, bus)
    curator = IngredientCurator(config, db, bus, inventory=inventory)
    draft = curator.ingest(photos)
    curator.approve(draft["draft_id"])
    # Inventory owns shelf-life rules, so categories must now carry a shelf life
    tomato = inventory.by_name("heirloom tomato")
    assert tomato.shelf_life_days > 0
    assert inventory.summary()["ingredients"] == 3


def test_store_photo_copies_into_photo_store(config, db, photos):
    curator = IngredientCurator(config, db)
    stored = curator.store_photo(photos[0])
    assert os.path.exists(stored)
    assert stored.startswith(config.photo_dir)
    assert os.path.basename(stored).endswith("heirloom_tomato_4kg_crisp.jpg")


def test_ingest_can_store_photos(config, db, photos):
    result = IngredientCurator(config, db).ingest(photos[:1], store=True)
    assert result["lines"][0]["photo_path"].startswith(config.photo_dir)
    assert os.path.exists(result["lines"][0]["photo_path"])
