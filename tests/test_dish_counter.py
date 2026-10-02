"""Prompt 16 tests — fired/plated/picked-up counting and gap detection."""
import pytest

from core.events import DISH_PLATED
from modules.dish_counter import DishCounter


@pytest.fixture
def counter(config, db, bus):
    return DishCounter(config, db, bus)


def test_event_sequence_counts(counter):
    for _ in range(5):
        counter.fire("r-1")
    for _ in range(4):
        counter.plated("r-1")
    for _ in range(3):
        counter.picked_up("r-1")
    counts = counter.per_service()["r-1"]
    assert counts == {"fired": 5, "plated": 4, "picked_up": 3, "gap": 1}


def test_gap_detection_flags_a_pass_bottleneck(counter):
    for _ in range(3):
        counter.fire("r-1")
    counter.plated("r-1")
    gaps = counter.gaps()
    assert len(gaps) == 1
    assert gaps[0]["dish_id"] == "r-1"
    assert gaps[0]["gap"] == 2


def test_plated_publishes_an_event(counter, bus):
    seen = []
    bus.subscribe(DISH_PLATED, lambda t, p: seen.append(p))
    counter.plated("r-1")
    assert seen and seen[0]["dish_id"] == "r-1"
    assert seen[0]["event_id"]


def test_per_hour_histogram_buckets_by_hour(counter, db):
    db.execute("INSERT INTO dish_events (dish_id, action, ts, service_date)"
               " VALUES ('r-1','plated','2026-10-01T18:05:00','2026-10-01')")
    db.execute("INSERT INTO dish_events (dish_id, action, ts, service_date)"
               " VALUES ('r-1','plated','2026-10-01T18:45:00','2026-10-01')")
    db.execute("INSERT INTO dish_events (dish_id, action, ts, service_date)"
               " VALUES ('r-1','plated','2026-10-01T19:10:00','2026-10-01')")
    assert counter.per_hour_histogram("2026-10-01") == {"18": 2, "19": 1}
    assert counter.pace_per_hour("2026-10-01") == 1.5


def test_covers_total_counts_picked_up_only(counter):
    counter.plated("r-1")
    counter.picked_up("r-1")
    counter.picked_up("r-2")
    assert counter.covers_total() == 2


def test_service_summary(counter):
    for _ in range(4):
        counter.fire("r-1")
        counter.plated("r-1")
        counter.picked_up("r-1")
    counter.fire("r-1")  # one left hanging on the pass
    summary = counter.service_summary()
    assert summary["fired"] == 5
    assert summary["plated"] == 4
    assert summary["covers"] == 4
    assert summary["gaps"] == 1
    assert summary["dishes"] == 1


def test_services_are_kept_separate(counter):
    counter.fire("r-1", service_date="2026-10-01")
    counter.fire("r-1", service_date="2026-10-02")
    assert counter.per_service("2026-10-01")["r-1"]["fired"] == 1
    assert counter.per_service("2026-10-02")["r-1"]["fired"] == 1


def test_last_plated_event_and_recent_plated(counter):
    counter.plated("r-1", photo_id="ph-1")
    counter.plated("r-1", photo_id="ph-2")
    latest = counter.last_plated_event("r-1")
    assert latest.photo_id == "ph-2"
    assert len(counter.recent_plated("r-1")) == 2
    assert len(counter.recent_plated()) == 2
    assert counter.last_plated_event("never-fired") is None


def test_log_is_append_only(counter, db):
    counter.fire("r-1")
    counter.plated("r-1")
    assert db.scalar("SELECT COUNT(*) FROM dish_events") == 2
    assert not hasattr(counter, "delete_event")
    assert not hasattr(counter, "update_event")


def test_tick_returns_the_service_summary(counter):
    counter.fire("r-1")
    counter.plated("r-1")
    result = counter.tick()
    assert result["fired"] == 1 and result["plated"] == 1
