"""Prompt 13 tests — cold chain, breach streaks, escalation, append-only log."""
import pytest

from core.events import TEMP_EXCURSION
from modules.templog import MockSensorSource, SensorSource, TempLog


@pytest.fixture
def logbook(config, db, bus):
    logger = TempLog(config, db, bus, sensor=MockSensorSource([3.0, 9.0]))
    logger.default_zones()
    return logger


def test_zones_come_from_config_defaults(config, db, bus):
    logger = TempLog(config, db, bus)
    zones = logger.default_zones()
    names = [z.name for z in zones]
    assert names == ["Walk-in", "Hot hold (pass)"]
    walkin = logger.get_zone("z-walkin")
    assert walkin.max_c == config.walkin_max_c
    assert len(logger.zones()) == 2


def test_in_bounds_reading_is_silent(logbook, bus):
    seen = []
    bus.subscribe(TEMP_EXCURSION, lambda t, p: seen.append(p))
    result = logbook.log("z-walkin", celsius=3.0)
    assert result["in_bounds"] is True
    assert result["escalated"] is False
    assert seen == []


def test_first_breach_is_logged_and_announced_but_not_escalated(logbook, bus):
    seen = []
    bus.subscribe(TEMP_EXCURSION, lambda t, p: seen.append(p))
    result = logbook.log("z-walkin", celsius=9.0)
    assert result["in_bounds"] is False
    assert result["streak"] == 1
    assert result["escalated"] is False
    assert seen and seen[0]["celsius"] == 9.0
    assert seen[0]["escalated"] is False


def test_second_consecutive_breach_escalates_to_an_urgent_reminder(logbook, db, bus):
    seen = []
    bus.subscribe(TEMP_EXCURSION, lambda t, p: seen.append(p))
    logbook.log("z-walkin", celsius=9.0)
    result = logbook.log("z-walkin", celsius=9.5)
    assert result["streak"] == 2
    assert result["escalated"] is True
    assert seen[-1]["escalated"] is True

    reminder = db.query_one("SELECT * FROM reminders WHERE urgency = 'urgent'")
    assert reminder["audience"] == "boh"
    assert "Walk-in" in reminder["title"]
    assert "twice in a row" in reminder["body"]

    # deduped: a third breach does not stack up more reminders
    logbook.log("z-walkin", celsius=9.9)
    assert db.scalar("SELECT COUNT(*) FROM reminders") == 1


def test_recovery_resets_the_breach_streak(logbook):
    logbook.log("z-walkin", celsius=9.0)
    logbook.log("z-walkin", celsius=9.0)
    assert logbook.consecutive_breaches("z-walkin") == 2
    logbook.log("z-walkin", celsius=2.0)
    assert logbook.consecutive_breaches("z-walkin") == 0


def test_hot_hold_lower_bound_is_respected(logbook):
    # hot hold must be >= 60C: a cold reading is a breach
    result = logbook.log("z-hot-hold", celsius=40.0)
    assert result["in_bounds"] is False
    assert logbook.log("z-hot-hold", celsius=65.0)["in_bounds"] is True


def test_excursion_report_lists_only_breaches(logbook):
    logbook.log("z-walkin", celsius=3.0)
    logbook.log("z-walkin", celsius=9.0)
    report = logbook.excursion_report()
    assert len(report) == 1
    assert report[0]["zone"] == "Walk-in"
    assert report[0]["celsius"] == 9.0
    assert report[0]["bounds"] == "0..4"


def test_zone_status_shows_the_latest_reading(logbook):
    logbook.log("z-walkin", celsius=3.0)
    logbook.log("z-walkin", celsius=3.5)
    status = [s for s in logbook.zone_status() if s["zone"] == "Walk-in"][0]
    assert status["current"] == 3.5
    assert status["in_bounds"] is True
    assert status["streak"] == 0


def test_log_is_append_only(logbook, db):
    logbook.log("z-walkin", celsius=3.0)
    logbook.log("z-walkin", celsius=9.0)
    rows = db.query("SELECT * FROM temp_readings ORDER BY id")
    assert len(rows) == 2
    assert rows[0]["celsius"] == 3.0  # earlier reading untouched
    assert not hasattr(logbook, "delete_reading")
    assert not hasattr(logbook, "update_reading")


def test_mock_sensor_is_deterministic_and_cycles(config, db, bus):
    sensor = MockSensorSource([3.0, 9.0])
    logger = TempLog(config, db, bus, sensor=sensor)
    logger.add_zone("Walk-in", "kitchen", 0.0, 4.0, id="z-walkin")
    assert logger.log("z-walkin")["celsius"] == 3.0
    assert logger.log("z-walkin")["celsius"] == 9.0
    assert logger.log("z-walkin")["celsius"] == 3.0
    assert isinstance(sensor, SensorSource)


def test_sanitizer_log_writes_haccp_row(logbook, db):
    logbook.sanitizer_log("pm", concentration="200 ppm", temp_c=24.0,
                          checked_by="chef")
    row = db.query_one("SELECT * FROM sanitizer_log")
    assert row["concentration"] == "200 ppm"
    assert row["checked_by"] == "chef"


def test_tick_reads_every_zone(logbook):
    result = logbook.tick()
    assert result["readings"] == 2
    assert result["breaches"] >= 1


def test_unknown_zone_raises(logbook):
    with pytest.raises(KeyError):
        logbook.log("z-nowhere", celsius=1.0)
