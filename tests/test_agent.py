"""Prompt 03 tests — agent tick loop and service state machine."""
from datetime import datetime

import pytest

from core.agent import PassAgent
from core.config import Config
from core.events import SERVICE_CLOSE, SERVICE_OPEN
from core.types import ServiceState


class RecordingModule:
    def __init__(self):
        self.calls = []

    def tick(self, when):
        self.calls.append(when)
        return {"ok": True}


class BrokenModule:
    def tick(self, when):
        raise RuntimeError("sensor unplugged")


def make_agent(db, bus, start="16:00", end="22:00"):
    cfg = Config.default().with_overrides(
        db_path=":memory:", service_start=start, service_end=end
    )
    return PassAgent(config=cfg, db=db, bus=bus)


def test_tick_with_no_modules_is_a_noop(db, bus):
    agent = make_agent(db, bus)
    assert agent.tick(datetime(2026, 10, 1, 15, 0, 0)) == {}


def test_registered_module_receives_the_tick(db, bus):
    agent = make_agent(db, bus)
    mod = agent.register("recorder", RecordingModule())
    when = datetime(2026, 10, 1, 18, 0, 0)
    results = agent.tick(when)
    assert results["recorder"] == {"ok": True}
    assert mod.calls == [when]


def test_broken_module_is_contained(db, bus):
    agent = make_agent(db, bus)
    agent.register("broken", BrokenModule())
    good = agent.register("recorder", RecordingModule())
    results = agent.tick(datetime(2026, 10, 1, 18, 0, 0))
    assert "error" in results["broken"]
    assert results["recorder"] == {"ok": True}
    assert len(good.calls) == 1


def test_state_transitions(db, bus):
    agent = make_agent(db, bus, start="16:00", end="22:00")
    assert agent.state(datetime(2026, 10, 1, 9, 0)) is ServiceState.PRE_SERVICE
    assert agent.state(datetime(2026, 10, 1, 16, 0)) is ServiceState.SERVICE
    assert agent.state(datetime(2026, 10, 1, 21, 59)) is ServiceState.SERVICE
    assert agent.state(datetime(2026, 10, 1, 22, 0)) is ServiceState.POST_SERVICE


def test_service_open_announced_once_per_day(db, bus):
    agent = make_agent(db, bus)
    opened = []
    bus.subscribe(SERVICE_OPEN, lambda t, p: opened.append(p))
    agent.tick(datetime(2026, 10, 1, 17, 0))
    agent.tick(datetime(2026, 10, 1, 17, 30))
    agent.tick(datetime(2026, 10, 1, 18, 0))
    assert len(opened) == 1
    assert opened[0]["date"] == "2026-10-01"


def test_service_close_announced_after_service(db, bus):
    agent = make_agent(db, bus)
    closed = []
    bus.subscribe(SERVICE_CLOSE, lambda t, p: closed.append(p))
    agent.tick(datetime(2026, 10, 1, 23, 30))
    agent.tick(datetime(2026, 10, 1, 23, 45))
    assert len(closed) == 1


def test_get_returns_registered_module_or_none(db, bus):
    agent = make_agent(db, bus)
    mod = agent.register("inventory", RecordingModule())
    assert agent.get("inventory") is mod
    assert agent.get("missing") is None
