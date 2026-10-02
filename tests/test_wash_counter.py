"""Prompt 17 tests — racks, throughput, and the backlog warning."""
import pytest

from core.events import WASH_BACKLOG, WASH_CYCLE_DONE
from modules.dish_counter import DishCounter
from modules.wash_counter import WashCounter


@pytest.fixture
def pit(config, db, bus):
    dishes = DishCounter(config, db, bus)
    return WashCounter(config, db, bus, dish_counter=dishes), dishes


def test_rack_in_and_out_pairing(pit, db):
    wash, dishes = pit
    rack = wash.rack_in()
    assert wash.open_racks() == 1
    result = wash.rack_out(rack)
    assert result["closed"] is True
    assert wash.open_racks() == 0
    assert wash.racks_done() == 1


def test_rack_out_closes_the_oldest_open_rack(pit):
    wash, dishes = pit
    first = wash.rack_in()
    second = wash.rack_in()
    assert wash.rack_out()["rack_id"] == first
    assert wash.open_racks() == 1
    assert wash.rack_out()["rack_id"] == second
    assert wash.rack_out()["closed"] is False  # nothing left open


def test_cycle_done_event_is_published(pit, bus):
    wash, dishes = pit
    seen = []
    bus.subscribe(WASH_CYCLE_DONE, lambda t, p: seen.append(p))
    rack = wash.rack_in()
    wash.rack_out(rack)
    assert seen and seen[0]["rack_id"] == rack


def test_throughput_and_peak_hour(pit, db):
    wash, dishes = pit
    for _ in range(3):
        db.execute("INSERT INTO wash_racks (rack_in_ts, rack_out_ts, service_date)"
                   " VALUES ('2026-10-01T19:00:00','2026-10-01T19:20:00','2026-10-01')")
    db.execute("INSERT INTO wash_racks (rack_in_ts, rack_out_ts, service_date)"
               " VALUES ('2026-10-01T20:00:00','2026-10-01T20:20:00','2026-10-01')")
    assert wash.racks_done("2026-10-01") == 4
    assert wash.throughput_per_hour("2026-10-01") == 2.0
    assert wash.peak_hour("2026-10-01") == {"hour": "19", "racks": 3}
    assert wash.daily_total("2026-10-01") == 4


def test_no_backlog_alert_below_threshold(pit):
    wash, dishes = pit
    wash.rack_in()
    assert wash.backlog_alert() is None


def test_backlog_alert_fires_and_writes_an_urgent_reminder(pit, bus, db, config):
    wash, dishes = pit
    for _ in range(config.wash_backlog_threshold + 1):
        wash.rack_in()
    for _ in range(6):
        dishes.plated("r-1")

    seen = []
    bus.subscribe(WASH_BACKLOG, lambda t, p: seen.append(p))
    alert = wash.backlog_alert()

    assert alert is not None
    assert alert["open_racks"] == config.wash_backlog_threshold + 1
    assert seen and seen[0]["open_racks"] == alert["open_racks"]
    reminder = db.query_one("SELECT * FROM reminders WHERE urgency = 'urgent'")
    assert reminder["audience"] == "boh"
    assert "Pit is falling behind" in reminder["title"]


def test_backlog_alert_is_deduped_per_service(pit, db, config):
    wash, dishes = pit
    for _ in range(config.wash_backlog_threshold + 1):
        wash.rack_in()
    wash.backlog_alert()
    wash.backlog_alert()
    assert db.scalar("SELECT COUNT(*) FROM reminders") == 1


def test_sanitizer_check_writes_a_row(pit, db):
    wash, dishes = pit
    wash.sanitizer_check("pm", "200 ppm", 24.0, checked_by="dishwasher")
    row = db.query_one("SELECT * FROM sanitizer_log")
    assert row["shift"] == "pm"
    assert row["checked_by"] == "dishwasher"


def test_tick_and_summary(pit, config):
    wash, dishes = pit
    for _ in range(config.wash_backlog_threshold + 2):
        wash.rack_in()
    result = wash.tick()
    assert result["open_racks"] == config.wash_backlog_threshold + 2
    assert result["backlog_alert"] is True
    summary = wash.summary()
    assert summary["open_racks"] == config.wash_backlog_threshold + 2
