"""Prompt 20 tests — two-lane comms, acks, digests, transcripts, dedupe."""
from datetime import datetime

import pytest

from core.events import MESSAGE_URGENT
from core.types import Channel, Role
from modules.comms import Comms

WHEN = datetime(2026, 10, 1, 19, 40, 0)


@pytest.fixture
def line(config, db, bus):
    return Comms(config, db, bus)


def test_urgent_message_publishes_and_requires_ack(line, bus, db):
    seen = []
    bus.subscribe(MESSAGE_URGENT, lambda t, p: seen.append(p))
    message = line.send(Role.SERVER.value, Channel.URGENT.value,
                        "Table 4 shellfish allergy", now=WHEN)
    assert seen and seen[0]["id"] == message.id
    assert line.unacked_urgent() and line.unacked_urgent()[0].id == message.id
    acked = line.ack(message.id, by="chef")
    assert acked.acked is True
    assert acked.ack_by == "chef"
    assert line.unacked_urgent() == []


def test_non_urgent_does_not_interrupt(line, bus):
    seen = []
    bus.subscribe(MESSAGE_URGENT, lambda t, p: seen.append(p))
    line.send(Role.SERVER.value, Channel.NON_URGENT.value, "We need more rosemary",
              now=WHEN)
    assert seen == []  # nobody gets interrupted for rosemary


def test_non_urgent_is_batched_into_a_digest(line):
    line.send(Role.SERVER.value, Channel.NON_URGENT.value, "Low on polished forks",
              now=WHEN)
    line.send(Role.CHEF.value, Channel.NON_URGENT.value, "Rack of lamb sold out",
              now=WHEN)
    digest = line.digest(minutes=10, now=WHEN)
    assert [m.body for m in digest] == ["Low on polished forks", "Rack of lamb sold out"]


def test_digest_window_excludes_old_chatter(line):
    line.send(Role.SERVER.value, Channel.NON_URGENT.value, "ancient history",
              now=datetime(2026, 10, 1, 17, 0, 0))
    assert line.digest(minutes=10, now=WHEN) == []


def test_feed_shows_the_other_sides_urgents(line):
    line.send(Role.SERVER.value, Channel.URGENT.value, "Table 9 is in a rush", now=WHEN)
    server_feed = line.feed(Role.SERVER.value)
    chef_feed = line.feed(Role.CHEF.value)
    assert server_feed["urgent"] == []          # you do not need your own shout back
    assert len(chef_feed["urgent"]) == 1
    assert chef_feed["urgent"][0].body == "Table 9 is in a rush"


def test_duplicate_shout_within_the_window_is_one_message(line, db):
    first = line.send(Role.SERVER.value, Channel.URGENT.value, "86 the risotto",
                      now=WHEN)
    second = line.send(Role.SERVER.value, Channel.URGENT.value, "86 the risotto",
                       now=WHEN)
    assert first.id == second.id
    assert db.scalar("SELECT COUNT(*) FROM pass_messages") == 1
    # outside the window it is a genuinely new message
    third = line.send(Role.SERVER.value, Channel.URGENT.value, "86 the risotto",
                      now=datetime(2026, 10, 1, 19, 41, 0))
    assert third.id != first.id


def test_unacked_urgent_repeats_after_the_cooldown(line, bus):
    seen = []
    bus.subscribe(MESSAGE_URGENT, lambda t, p: seen.append(p))
    line.send(Role.SERVER.value, Channel.URGENT.value, "Fire table 12", now=WHEN)
    assert line.refire(WHEN) == []
    repeated = line.refire(datetime(2026, 10, 1, 19, 46, 0))
    assert len(repeated) == 1
    assert seen[-1]["repeat"] is True
    line.ack(repeated[0].id, by="chef")
    assert line.refire(datetime(2026, 10, 1, 20, 0, 0)) == []


def test_transcript_answers_what_did_they_ask_for(line):
    line.send(Role.SERVER.value, Channel.NON_URGENT.value, "more rosemary", now=WHEN)
    line.send(Role.CHEF.value, Channel.URGENT.value, "2 minutes on the duck", now=WHEN)
    everything = line.transcript()
    assert [m.body for m in everything] == ["more rosemary", "2 minutes on the duck"]
    assert len(line.transcript(since="2026-10-01T19:40:00")) == 2


def test_message_survives_a_failing_notifier(line, bus, db):
    def broken(event_type, payload):
        raise RuntimeError("telegram is down")

    bus.subscribe(MESSAGE_URGENT, broken)
    message = line.send(Role.SERVER.value, Channel.URGENT.value, "allergy at table 2",
                        now=WHEN)
    # write-ahead: the fact somebody said it is recorded even though delivery blew up
    assert db.query_one("SELECT body FROM pass_messages WHERE id = ?",
                        (message.id,))["body"] == "allergy at table 2"
    assert bus.errors


def test_templates_render(line):
    assert line.template("86", item="risotto", note="pan down") == "86 risotto — pan down"
    assert line.template("fire", count=2, dish="duck", table="12") == "Fire 2 x duck for table 12"
    with pytest.raises(KeyError):
        line.template("nonsense")


def test_summary_reports_ack_latency(line):
    message = line.send(Role.SERVER.value, Channel.URGENT.value, "rush on 4", now=WHEN)
    line.ack(message.id, by="chef")
    line.send(Role.SERVER.value, Channel.NON_URGENT.value, "note", now=WHEN)
    summary = line.summary()
    assert summary["urgent_total"] == 1
    assert summary["urgent_acked"] == 1
    assert summary["urgent_unacked"] == 0
    assert summary["non_urgent_total"] == 1
    assert summary["avg_ack_seconds"] is not None


def test_tick_reports_the_line_state(line):
    line.send(Role.SERVER.value, Channel.URGENT.value, "unacked thing", now=WHEN)
    result = line.tick(WHEN)
    assert result["unacked_urgent"] == 1
