"""Prompt 25 tests — five criteria, each grounded in the restaurant's records."""
import pytest

from core.types import Recipe, RecipeLine
from modules.boards import Boards
from modules.consistency import Consistency
from modules.deliveries import Deliveries
from modules.inventory import Inventory
from modules.kanban import Kanban
from modules.michelin import CRITERIA, Michelin
from modules.recipes import RecipeBook
from modules.review_app import ReviewLogic

DAY_A, DAY_B = "2026-09-30", "2026-10-01"


@pytest.fixture
def wired(config, db, bus):
    inventory = Inventory(config, db, bus)
    deliveries = Deliveries(config, db, bus)
    book = RecipeBook(config, db, bus)
    kanban = Kanban(config, db, bus)
    boards = Boards(config, db, bus, kanban=kanban, recipe_book=book)
    consistency = Consistency(config, db, bus)
    review_logic = ReviewLogic(config, db, bus)
    dashboard = Michelin(config, db, bus, inventory=inventory, deliveries=deliveries,
                         recipe_book=book, boards=boards, kanban=kanban,
                         consistency=consistency, review_logic=review_logic)
    return (dashboard, inventory, deliveries, book, kanban, boards, consistency,
            review_logic)


def criterion(dashboard, name):
    return [c for c in dashboard.criteria_status() if c["criterion"] == name][0]


def test_all_five_criteria_are_present(wired):
    dashboard, *_ = wired
    assert [c["criterion"] for c in dashboard.criteria_status()] == list(CRITERIA)


def test_empty_state_is_honest_and_does_not_crash(config, db):
    dashboard = Michelin(config, db)
    for card in dashboard.criteria_status():
        assert card["signal"] is None
        assert card["evidence_summary"]
    focus = dashboard.focus()
    assert focus["criterion"] is None
    note = dashboard.star_readiness_note()
    assert "not enough recorded history" in note.lower()
    assert "not a verdict" in note
    assert dashboard.dashboard_html().count("class='criterion'") == 5


def test_ingredient_quality_follows_freshness_and_supplier_grades(wired):
    dashboard, inventory, deliveries, *_ = wired
    assert criterion(dashboard, "quality of ingredients")["signal"] is None

    inventory.add("fresh fish", category="seafood", unit="kg", on_hand=5.0)
    supplier = deliveries.add_supplier("Dockside", typical_delay_days=0.0)
    for day in range(1, 6):
        deliveries.record_arrival(supplier.id, f"2026-09-{day:02d}",
                                  f"2026-09-{day:02d}", f"2026-09-{day:02d}")
    card = criterion(dashboard, "quality of ingredients")
    assert card["signal"] is not None
    assert any("freshness" in e for e in card["evidence"])
    assert any("supplier grades" in e for e in card["evidence"])

    # a sloppy supplier drags the signal down
    sloppy = deliveries.add_supplier("Sloppy Supply")
    for day in range(1, 6):
        deliveries.record_arrival(sloppy.id, f"2026-09-{day:02d}",
                                  f"2026-09-{day:02d}", f"2026-09-{day + 4:02d}")
    assert criterion(dashboard, "quality of ingredients")["signal"] < card["signal"]


def test_technique_follows_execution_scores(wired, db):
    dashboard, inventory, deliveries, book, kanban, boards, consistency, reviews = wired
    db.execute("INSERT INTO dish_reviews (id, photo_id, dish_id, presentation, portion,"
               " color, execution, note, reviewer, ts, service_date)"
               " VALUES ('rv-1','ph-1','r-1',5,5,5,5,'','chef',?,?)",
               (f"{DAY_A}T19:00:00", DAY_A))
    strong = criterion(dashboard, "mastery of technique")
    assert any("execution scored 5.0/5" in e for e in strong["evidence"])
    assert strong["signal"] == 100.0

    db.execute("INSERT INTO dish_reviews (id, photo_id, dish_id, presentation, portion,"
               " color, execution, note, reviewer, ts, service_date)"
               " VALUES ('rv-2','ph-2','r-1',2,2,2,2,'','chef',?,?)",
               (f"{DAY_B}T19:00:00", DAY_B))
    weaker = criterion(dashboard, "mastery of technique")
    assert any("execution scored 3.5/5" in e for e in weaker["evidence"])
    assert weaker["signal"] < strong["signal"]


def test_personality_follows_r_and_d_activity(wired):
    dashboard, inventory, deliveries, book, kanban, boards, consistency, reviews = wired
    book.save(Recipe(id="r-1", name="New Idea", portions=4,
                     lines=[RecipeLine("i-x", 100.0, "g")]))
    boards.seed_recipes()
    card = criterion(dashboard, "personality of the cuisine")
    assert any("recipe" in e for e in card["evidence"])
    assert any("R&D or Testing" in e for e in card["evidence"])
    assert card["signal"] is not None


def test_value_follows_margins(wired):
    dashboard, inventory, deliveries, book, kanban, boards, consistency, reviews = wired
    book.save(Recipe(id="r-1", name="Thin Margin", portions=1, menu_price=20.0,
                     lines=[RecipeLine("i-beef", 250.0, "g")]))
    inventory.add("beef", id="i-beef", unit="g", unit_cost=0.06, on_hand=5000.0)
    card = criterion(dashboard, "value")
    assert any("margin" in e for e in card["evidence"])
    assert card["signal"] is not None


def test_value_says_so_when_no_prices_are_entered(wired):
    dashboard, inventory, deliveries, book, kanban, boards, consistency, reviews = wired
    book.save(Recipe(id="r-1", name="Unpriced", portions=1,
                     lines=[RecipeLine("i-x", 100.0, "g")]))
    card = criterion(dashboard, "value")
    assert card["signal"] is None
    assert "no menu prices entered" in card["evidence_summary"]


def test_consistency_follows_drift(wired, db):
    dashboard, inventory, deliveries, book, kanban, boards, consistency, reviews = wired
    for i, score in enumerate((5, 4, 3)):
        db.execute(
            "INSERT INTO dish_reviews (id, photo_id, dish_id, presentation, portion,"
            " color, execution, note, reviewer, ts, service_date)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"rv-{i}", f"ph-{i}", "r-1", score, score, score, score, "", "chef",
             f"2026-09-2{i}T19:00:00", f"2026-09-2{i}"))
    card = criterion(dashboard, "consistency")
    assert card["signal"] is not None
    assert card["signal"] < 100.0
    assert any("drift" in e for e in card["evidence"])


def test_focus_picks_the_weakest_criterion(wired, db):
    dashboard, inventory, deliveries, book, kanban, boards, consistency, reviews = wired
    # strong technique (execution 5) and weak consistency (declining scores)
    for i, score in enumerate((5, 5, 5, 1)):
        db.execute(
            "INSERT INTO dish_reviews (id, photo_id, dish_id, presentation, portion,"
            " color, execution, note, reviewer, ts, service_date)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"rv-{i}", f"ph-{i}", "r-1", score, score, score, 5, "", "chef",
             f"2026-09-2{i}T19:00:00", f"2026-09-2{i}"))
    focus = dashboard.focus()
    assert focus["criterion"] is not None
    assert focus["focus_recommendation"]


def test_readiness_note_cites_evidence_and_never_promises(wired):
    dashboard, inventory, deliveries, book, kanban, boards, consistency, reviews = wired
    inventory.add("fresh fish", category="seafood", unit="kg", on_hand=5.0)
    note = dashboard.star_readiness_note()
    assert "quality of ingredients" in note
    assert "/100" in note
    assert "not a verdict" in note
    assert "star" not in note.lower().replace("stars are", "")  # no promises


def test_dashboard_html_renders_five_cards_with_escaped_evidence(wired):
    dashboard, inventory, deliveries, book, kanban, boards, consistency, reviews = wired
    # supplier names reach the evidence text, so they must be escaped on the page
    supplier = deliveries.add_supplier("<script>alert(1)</script>",
                                       typical_delay_days=0.0)
    for day in range(1, 5):
        deliveries.record_arrival(supplier.id, f"2026-09-{day:02d}",
                                  f"2026-09-{day:02d}", f"2026-09-{day + 3:02d}")
    page = dashboard.dashboard_html()
    assert page.count("class='criterion'") == 5
    assert "&lt;script&gt;" in page
    assert "<script>alert" not in page
    assert "Focus:" in page


def test_tick_reports_the_focus(wired):
    dashboard, *_ = wired
    result = dashboard.tick()
    assert "criteria_scored" in result
