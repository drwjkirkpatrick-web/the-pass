"""Prompt 28 tests — intent routing and the never-approve invariant."""
import pytest

from core.config import Config
from core.types import Channel, Recipe, RecipeLine, Role
from main import build_agent
from hermes_bridge import HELP_TEXT, HermesBridge


@pytest.fixture
def bridge(tmp_path):
    config = Config.default().with_overrides(
        db_path=str(tmp_path / "pass.db"), photo_dir=str(tmp_path / "photos"))
    agent = build_agent(config)
    inventory = agent.get("inventory")
    inventory.add("scallops", id="i-scallop", category="seafood", unit="each",
                  par_level=20.0, on_hand=4.0)
    inventory.add("beef", id="i-beef", category="protein", unit="g", on_hand=9000.0,
                  par_level=5000.0)
    agent.get("recipes").save(Recipe(id="r-1", name="Short Rib", portions=4,
                                     lines=[RecipeLine("i-beef", 250.0, "g")]))
    agent.get("templog").default_zones()
    return HermesBridge(agent)


def test_help_and_unknown_intents(bridge):
    assert bridge.handle({"text": "help"})["intent"] == "help"
    assert bridge.handle({"text": "what can you do?"})["intent"] == "help"
    unknown = bridge.handle({"text": "book me a flight to Lyon"})
    assert unknown["intent"] == "unknown"
    assert "I did not catch that" in unknown["text"]


def test_eighty_six_flow_end_state(bridge):
    result = bridge.handle({"text": "86 the scallops"})
    assert result["intent"] == "eighty_six"
    assert "86'd scallops" in result["text"]   # article stripped, ingredient found

    inventory = bridge.agent.get("inventory")
    assert inventory.is_eighty_six("i-scallop") is True
    assert bridge.agent.db.scalar("SELECT COUNT(*) FROM eighty_six_log") == 1

    warnings = [r for r in bridge.agent.get("reminders_foh").engine.pending("foh")
                if "86 warning" in r.title]
    assert len(warnings) == 1

    messages = [m for m in bridge.agent.get("comms").transcript()
                if m.channel == Channel.URGENT.value]
    assert messages and "86 scallops" in messages[0].body

    # idempotent: saying it twice does not double-log
    assert "already 86'd" in bridge.handle({"text": "86 scallops"})["text"]


def test_eighty_six_unknown_ingredient_is_honest(bridge):
    result = bridge.handle({"text": "86 the unicorn"})
    assert "do not have 'unicorn'" in result["text"]
    assert bridge.handle({"text": "86 a unicorn"})["intent"] == "eighty_six"
    assert bridge.agent.db.scalar("SELECT COUNT(*) FROM eighty_six_log") == 0


def test_pit_orders_tonight_reminders_temps_journal_michelin_route(bridge):
    assert bridge.handle({"text": "how's the pit?"})["intent"] == "pit"
    assert bridge.handle({"text": "order status"})["intent"] == "orders"
    assert bridge.handle({"text": "show me tonight"})["intent"] == "tonight"
    assert bridge.handle({"text": "any reminders?"})["intent"] == "reminders"
    assert bridge.handle({"text": "temps"})["intent"] == "temps"
    assert bridge.handle({"text": "journal"})["intent"] == "journal"
    assert bridge.handle({"text": "michelin"})["intent"] == "michelin"


def test_tonight_reports_the_plan(bridge):
    bridge.agent.get("menu_planner").plan_service({"r-1": 40}, "2026-10-02")
    reply = bridge.handle({"text": "show me tonight"})["text"]
    assert "40 covers" in reply
    assert "2026-10-02" in reply
    assert "beef" in reply.lower()          # the shortfall, named


def test_approve_phrasing_never_approves(bridge):
    ordering = bridge.agent.get("ordering")
    queue = bridge.agent.get("review_queue")
    inventory = bridge.agent.get("inventory")
    inventory.set_par("i-beef", 50000.0)
    drafts = ordering.curate_orders("2026-10-02")
    assert drafts
    item = queue.submit_order(drafts[0])

    reply = bridge.handle({"text": f"approve {item.id}"})["text"]
    assert "cannot approve anything from chat" in reply
    assert item.id in reply
    # the order is still a draft and the review still pending
    assert queue.get(item.id).status == "pending"
    assert ordering.get_order(item.id and drafts[0].id).status.value == "draft"


def test_ack_relay_acknowledges_an_urgent_call(bridge):
    comms = bridge.agent.get("comms")
    message = comms.send(Role.SERVER.value, Channel.URGENT.value, "allergy at table 2")
    reply = bridge.handle({"text": f"ack {message.id}"})["text"]
    assert "Acknowledged" in reply
    assert comms.get(message.id).acked is True
    assert "already acknowledged" in bridge.handle({"text": f"ack {message.id}"})["text"]
    assert "No message with id" in bridge.handle({"text": "ack m-nope"})["text"]


def test_due_reminders_push_shape(bridge):
    boh = bridge.agent.get("reminders_boh")
    boh.prep_timer("braise", 0)
    boh.engine.due()
    pushes = bridge.due_reminders_push()
    assert pushes
    assert all(p["urgency"] == "urgent" for p in pushes)
    assert any("Prep timer" in p["text"] for p in pushes)


def test_urgent_messages_push_shape(bridge):
    comms = bridge.agent.get("comms")
    comms.send(Role.SERVER.value, Channel.URGENT.value, "table 9 in a rush")
    pushes = bridge.urgent_messages_push()
    assert pushes and "URGENT from server" in pushes[0]["text"]
    comms.ack(pushes[0]["id"], by="chef")
    assert bridge.urgent_messages_push() == []
