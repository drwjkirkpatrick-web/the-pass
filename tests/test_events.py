"""Prompt 03 tests — event bus isolation and delivery."""
from core.events import ALL_EVENTS, MESSAGE_URGENT, EventBus


def test_publish_delivers_to_multiple_subscribers(bus):
    seen = []
    bus.subscribe(MESSAGE_URGENT, lambda t, p: seen.append(("a", p)))
    bus.subscribe(MESSAGE_URGENT, lambda t, p: seen.append(("b", p)))
    delivered = bus.publish(MESSAGE_URGENT, {"body": "86 the risotto"})
    assert delivered == 2
    assert [s[0] for s in seen] == ["a", "b"]


def test_publish_with_no_listeners_is_harmless(bus):
    assert bus.publish("nothing.listens", {}) == 0
    assert bus.errors == []


def test_a_raising_handler_does_not_stop_the_others(bus):
    order = []

    def broken(event_type, payload):
        order.append("broken")
        raise RuntimeError("kitchen printer offline")

    bus.subscribe(MESSAGE_URGENT, broken)
    bus.subscribe(MESSAGE_URGENT, lambda t, p: order.append("healthy"))

    delivered = bus.publish(MESSAGE_URGENT, {})

    assert order == ["broken", "healthy"]
    assert delivered == 1  # only the healthy one counted
    assert len(bus.errors) == 1
    assert "kitchen printer offline" in bus.errors[0][1]


def test_listener_counts_and_subscribed_types(bus):
    bus.subscribe(MESSAGE_URGENT, lambda t, p: None)
    bus.subscribe("temp.excursion", lambda t, p: None)
    assert bus.listeners(MESSAGE_URGENT) == 1
    assert bus.listeners("never.subscribed") == 0
    assert bus.subscribed_types() == ["message.urgent", "temp.excursion"]


def test_clear_errors(bus):
    bus.subscribe(MESSAGE_URGENT, lambda t, p: 1 / 0)
    bus.publish(MESSAGE_URGENT, {})
    assert bus.errors
    bus.clear_errors()
    assert bus.errors == []


def test_all_events_are_declared_once_and_unique():
    assert len(ALL_EVENTS) == len(set(ALL_EVENTS))
    assert "service.open" in ALL_EVENTS and "service.close" in ALL_EVENTS
