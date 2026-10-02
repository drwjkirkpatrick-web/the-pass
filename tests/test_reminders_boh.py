"""Prompt 14 tests — reminder engine: due, ack, recurrence, re-fire, dedupe."""
from datetime import datetime

import pytest

from core.events import REMINDER_DUE
from modules.reminders_boh import BOHReminders, ReminderEngine

WHEN = datetime(2026, 10, 1, 17, 0, 0)


@pytest.fixture
def engine(config, db, bus):
    return ReminderEngine(config, db, bus)


@pytest.fixture
def boh(config, db, bus, engine):
    return BOHReminders(config, db, bus, engine=engine)


def test_due_fires_once_and_publishes(engine, bus):
    seen = []
    bus.subscribe(REMINDER_DUE, lambda t, p: seen.append(p))
    reminder = engine.schedule("boh", "routine", "Line check",
                               fire_at="2026-10-01T16:00:00")
    fired = engine.due(WHEN)
    assert [r.id for r in fired] == [reminder.id]
    assert engine.get(reminder.id).status == "fired"
    assert seen and seen[0]["title"] == "Line check"
    # second tick does not fire it again
    assert engine.due(WHEN) == []


def test_future_reminder_does_not_fire(engine):
    engine.schedule("boh", "routine", "Later", fire_at="2026-10-01T23:00:00")
    assert engine.due(WHEN) == []


def test_ack_closes_a_fired_reminder(engine):
    reminder = engine.schedule("boh", "urgent", "Braise timer",
                               fire_at="2026-10-01T16:00:00")
    engine.due(WHEN)
    acked = engine.ack(reminder.id, by="chef")
    assert acked.status == "acked"
    assert acked.ack_by == "chef"
    assert engine.refire(WHEN) == []  # acknowledged, so nothing repeats


def test_unacked_urgent_repeats_after_the_cooldown(engine, bus):
    seen = []
    bus.subscribe(REMINDER_DUE, lambda t, p: seen.append(p))
    engine.schedule("boh", "urgent", "Temperature breach",
                    fire_at="2026-10-01T16:00:00")
    engine.due(WHEN)
    # cooldown is 5 minutes: immediately after firing, nothing repeats
    assert engine.refire(WHEN) == []
    repeated = engine.refire(datetime(2026, 10, 1, 17, 6, 0))
    assert len(repeated) == 1
    assert seen[-1]["repeat"] is True


def test_routine_reminders_never_repeat(engine):
    engine.schedule("boh", "routine", "Mise", fire_at="2026-10-01T16:00:00")
    engine.due(WHEN)
    assert engine.refire(datetime(2026, 10, 1, 18, 0, 0)) == []


def test_daily_recurrence_schedules_the_next_day(engine):
    engine.schedule("boh", "routine", "Family meal", fire_at="2026-10-01T16:00:00",
                    recurrence="daily")
    engine.due(WHEN)
    # the fired one still awaits a human; the recurrence adds tomorrow's
    scheduled = [r for r in engine.pending() if r.status == "scheduled"]
    assert len(scheduled) == 1
    assert scheduled[0].fire_at == "2026-10-02T16:00:00"


def test_pre_service_recurrence_uses_service_start_minus_lead(engine, config):
    engine.schedule("boh", "routine", "Mise", fire_at="2026-10-01T15:00:00",
                    recurrence="pre_service")
    engine.due(WHEN)
    # service starts 16:00, lead 45 min -> 15:15 next day
    scheduled = [r for r in engine.pending() if r.status == "scheduled"]
    assert scheduled[0].fire_at == "2026-10-02T15:15:00"


def test_audience_filtering(engine):
    engine.schedule("foh", "routine", "Front only", fire_at="2026-10-01T16:00:00")
    engine.schedule("boh", "routine", "Back only", fire_at="2026-10-01T16:00:00")
    assert [r.title for r in engine.pending("boh")] == ["Back only"]
    assert [r.title for r in engine.pending("foh")] == ["Front only"]


def test_dedupe_key_prevents_duplicates(engine):
    first = engine.schedule("boh", "routine", "Cutoff", fire_at="2026-10-01T16:00:00",
                            dedupe_key="cutoff-x-2026-10-01")
    second = engine.schedule("boh", "routine", "Cutoff again",
                             fire_at="2026-10-01T17:00:00",
                             dedupe_key="cutoff-x-2026-10-01")
    assert first.id == second.id
    assert len(engine.pending()) == 1


def test_prep_timer_fires_at_now_plus_minutes(boh):
    reminder = boh.prep_timer("braise", 240, when=WHEN)
    assert reminder.fire_at == "2026-10-01T21:00:00"
    assert reminder.urgency == "urgent"
    assert reminder.audience == "boh"


def test_mise_check_lands_before_service(boh, config):
    reminder = boh.mise_check(when=WHEN)
    assert reminder.fire_at == "2026-10-01T15:15:00"
    assert reminder.recurrence == "pre_service"


def test_ordering_deadline_is_lead_minutes_before_cutoff(boh):
    reminder = boh.ordering_deadline("Green Ridge", "07:00", when=WHEN)
    assert reminder.fire_at == "2026-10-01T05:00:00"
    assert "Green Ridge" in reminder.title


def test_tick_reports_counts(boh):
    boh.prep_timer("stock", 60, when=WHEN)
    result = boh.tick(datetime(2026, 10, 1, 18, 30, 0))
    assert result["fired"] == 1
    # fired but unacknowledged still counts as outstanding work
    assert result["pending"] == 1
    boh.engine.ack(boh.engine.pending()[0].id, by="chef")
    assert boh.tick(datetime(2026, 10, 1, 18, 31, 0))["pending"] == 0


def test_delivery_and_close_reminders(boh):
    assert "Delivery expected" in boh.delivery_expected("Dockside",
                                                        "2026-10-02T08:00:00").title
    assert boh.close_checklist().title == "BOH close-down"
