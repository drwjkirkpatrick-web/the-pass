"""Prompt 02 tests — schema, persistence, and row mapping."""
from core.database import EXPECTED_TABLES, Database
from core.types import Ingredient, Reminder


def test_migrate_creates_every_expected_table(db):
    tables = set(db.tables())
    missing = [t for t in EXPECTED_TABLES if t not in tables]
    assert missing == []


def test_migrate_is_idempotent(db):
    db.migrate()
    db.migrate()
    assert "ingredients" in db.tables()


def test_ingredient_insert_and_read_round_trip(db):
    db.execute(
        "INSERT INTO ingredients (id, name, category, unit, par_level, on_hand,"
        " freshness_date, created_at) VALUES (?,?,?,?,?,?,?,?)",
        ("i-1", "Heirloom Tomato", "produce", "kg", 4.0, 2.5, "2026-10-05", db.now()),
    )
    row = db.query_one("SELECT * FROM ingredients WHERE id = ?", ("i-1",))
    ing = Database.row_to(Ingredient, row)
    assert ing.name == "Heirloom Tomato"
    assert ing.par_level == 4.0
    assert ing.on_hand == 2.5


def test_reminder_and_dish_event_tables_accept_rows(db):
    db.execute(
        "INSERT INTO reminders (id, audience, urgency, title, fire_at, created_at)"
        " VALUES (?,?,?,?,?,?)",
        ("rem-1", "boh", "urgent", "Braise timer", "2026-10-01T14:00:00", db.now()),
    )
    rem = Database.row_to(
        Reminder, db.query_one("SELECT * FROM reminders WHERE id = ?", ("rem-1",))
    )
    assert rem.title == "Braise timer"
    assert rem.status == "scheduled"

    db.execute(
        "INSERT INTO dish_events (dish_id, action, ts, service_date) VALUES (?,?,?,?)",
        ("r-1", "plated", db.now(), db.today()),
    )
    count = db.scalar("SELECT COUNT(*) FROM dish_events")
    assert count == 1


def test_scalar_normalises_none_from_empty_sum(db):
    # SQLite returns None for SUM over an empty set — the guard must return 0.
    assert db.query_one("SELECT SUM(on_hand) AS s FROM ingredients")["s"] is None
    assert db.scalar("SELECT SUM(on_hand) AS s FROM ingredients") == 0


def test_row_to_returns_none_for_missing_row(db):
    assert Database.row_to(Ingredient, None) is None


def test_time_helpers_have_expected_format():
    now = Database.now()
    assert len(now) == 19 and now[4] == "-" and now[10] == "T"
    assert len(Database.today()) == 10
    assert Database.fmt(Database.parse(now)) == now


def test_close_then_reconnect_works(tmp_path):
    path = str(tmp_path / "pass.db")
    d = Database(path).migrate()
    d.execute("INSERT INTO ingredients (id, name) VALUES ('i-1','Butter')")
    d.close()
    d2 = Database(path).migrate()
    assert d2.scalar("SELECT COUNT(*) FROM ingredients") == 1
    d2.close()
