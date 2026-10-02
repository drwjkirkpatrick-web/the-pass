"""Prompt 24 tests — variance, trend, drift findings, agent-board cards."""
import json

import pytest

from modules.boards import Boards
from modules.consistency import Consistency, DriftReport, mean, slope, variance
from modules.kanban import Kanban
from modules.templog import TempLog

DAY_A, DAY_B, DAY_C = "2026-09-29", "2026-09-30", "2026-10-01"


def seed_reviews(db, dish_id, scores_by_day):
    for index, (day, score) in enumerate(scores_by_day):
        db.execute(
            "INSERT INTO dish_reviews (id, photo_id, dish_id, presentation, portion,"
            " color, execution, note, reviewer, ts, service_date)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"rv-{dish_id}-{index}", f"ph-{index}", dish_id, score, score, score,
             score, "", "chef", f"{day}T19:00:00", day))


def seed_timing(db, dish_id, pairs):
    """pairs: [(service_date, fired_ts, plated_ts)]"""
    for day, fired, plated in pairs:
        db.execute("INSERT INTO dish_events (dish_id, action, ts, service_date)"
                   " VALUES (?,?,?,?)", (dish_id, "fired", fired, day))
        db.execute("INSERT INTO dish_events (dish_id, action, ts, service_date)"
                   " VALUES (?,?,?,?)", (dish_id, "plated", plated, day))


def seed_photo_metric(db, dish_id, day, portion):
    db.execute("INSERT INTO dish_photos (id, dish_id, path, ts, service_date,"
               " metrics, pending_review) VALUES (?,?,?,?,?,?,0)",
               (f"ph-{day}", dish_id, f"/tmp/{day}.jpg", f"{day}T19:05:00", day,
                json.dumps({"symmetry": 0.9, "portion": portion, "garnish": 0.9})))


@pytest.fixture
def engine(config, db, bus):
    return Consistency(config, db, bus)


def test_statistics_helpers():
    assert mean([1.0, 2.0, 3.0]) == 2.0
    assert mean([]) == 0.0
    assert variance([1.0, 2.0, 3.0, 4.0]) == 1.25
    assert variance([5.0]) == 0.0
    assert slope([1.0, 2.0, 3.0]) == 1.0
    assert round(slope([5.0, 4.0, 3.0]), 6) == -1.0
    assert slope([4.0]) == 0.0


def test_review_series_is_per_service(engine, db):
    seed_reviews(db, "r-1", [(DAY_A, 5), (DAY_A, 5), (DAY_B, 4), (DAY_C, 3)])
    assert engine.review_series("r-1") == [5.0, 4.0, 3.0]


def test_timing_series_measures_fire_to_plate(engine, db):
    seed_timing(db, "r-1", [(DAY_A, f"{DAY_A}T19:00:00", f"{DAY_A}T19:05:00"),
                            (DAY_B, f"{DAY_B}T19:00:00", f"{DAY_B}T19:10:00")])
    assert engine.timing_series("r-1") == [300.0, 600.0]


def test_drift_detects_a_declining_dish(engine, db):
    seed_reviews(db, "r-1", [(DAY_A, 5), (DAY_B, 4), (DAY_C, 3)])
    report = engine.drift_report("r-1")
    assert isinstance(report, DriftReport)
    assert report.metrics["score_change"] == -2.0
    assert report.metrics["score_trend"] < 0
    assert report.drift_score > 0
    assert any("Review scores down" in f for f in report.findings)


def test_steady_dish_reports_no_drift(engine, db):
    seed_reviews(db, "r-1", [(DAY_A, 5), (DAY_B, 5), (DAY_C, 5)])
    report = engine.drift_report("r-1")
    assert report.drift_score == 0.0
    assert report.findings == ["No drift detected in the recorded history"]


def test_plating_drift_is_flagged_from_photo_metrics(engine, db):
    seed_reviews(db, "r-1", [(DAY_A, 5), (DAY_C, 5)])
    seed_photo_metric(db, "r-1", DAY_A, 0.95)
    seed_photo_metric(db, "r-1", DAY_C, 0.70)
    report = engine.drift_report("r-1")
    assert report.metrics["plating_first"] == 0.95
    assert report.metrics["plating_last"] == 0.70
    assert any("portions lighter" in f for f in report.findings)


def test_uneven_timing_is_flagged(engine, db):
    seed_reviews(db, "r-1", [(DAY_A, 5), (DAY_B, 5)])
    seed_timing(db, "r-1", [(DAY_A, f"{DAY_A}T19:00:00", f"{DAY_A}T19:01:00"),
                            (DAY_B, f"{DAY_B}T19:00:00", f"{DAY_B}T19:30:00")])
    report = engine.drift_report("r-1")
    assert any("timing is uneven" in f for f in report.findings)


def test_temperature_excursions_on_the_dish_station_are_counted(config, db, bus):
    temp = TempLog(config, db, bus)
    temp.add_zone("Grill fridge", "grill", 0.0, 4.0, id="z-grill")
    db.execute("INSERT INTO recipes (id, name, station, created_at)"
               " VALUES ('r-1','Grilled Thing','grill',?)", (db.now(),))
    temp.log("z-grill", celsius=9.0)
    temp.log("z-grill", celsius=9.5)
    engine = Consistency(config, db, bus, templog=temp)
    assert engine.excursion_count("r-1") == 2
    report = engine.drift_report("r-1")
    assert any("temperature excursion" in f for f in report.findings)


def test_dish_with_no_history_says_so(engine):
    report = engine.drift_report("r-unknown")
    assert report.metrics["services"] == 0
    assert report.drift_score == 0.0
    assert report.findings == ["No drift detected in the recorded history"]


def test_worst_drifts_ranks_by_score(engine, db):
    seed_reviews(db, "r-bad", [(DAY_A, 5), (DAY_B, 3), (DAY_C, 1)])
    seed_reviews(db, "r-good", [(DAY_A, 5), (DAY_B, 5), (DAY_C, 5)])
    ranked = engine.worst_drifts()
    assert ranked[0].dish_id == "r-bad"


def test_tick_raises_an_agent_board_card(config, db, bus):
    kanban = Kanban(config, db, bus)
    boards = Boards(config, db, bus, kanban=kanban)
    engine = Consistency(config, db, bus, kanban=kanban, boards=boards)
    seed_reviews(db, "r-bad", [(DAY_A, 5), (DAY_B, 3), (DAY_C, 1)])

    result = engine.tick()
    assert "Drift: r-bad" in result["flagged"]
    cards = kanban.cards(boards.agent_board()["id"], "Backlog")
    assert cards[0].title == "Drift: r-bad"
    assert "Review scores down" in cards[0].detail
    # and it does not stack duplicates on every tick
    assert engine.tick()["count"] == 0
