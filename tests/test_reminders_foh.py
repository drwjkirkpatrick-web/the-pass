"""Prompt 15 tests — FOH reminders on the shared engine, plus the 86 warning."""
from datetime import datetime

import pytest

from core.events import INGREDIENTS_UPDATED
from modules.inventory import Inventory
from modules.reminders_boh import ReminderEngine
from modules.reminders_foh import FOHReminders

WHEN = datetime(2026, 10, 1, 17, 0, 0)


@pytest.fixture
def foh(config, db, bus, ):
    engine = ReminderEngine(config, db, bus)
    inventory = Inventory(config, db, bus)
    return FOHReminders(config, db, bus, engine=engine, inventory=inventory)


def test_briefing_schedules_before_service(foh):
    reminder = foh.briefing("Specials: halibut. 86: scallops.", when=WHEN)
    assert reminder.audience == "foh"
    assert reminder.fire_at == "2026-10-01T15:30:00"
    assert "halibut" in reminder.body


def test_eighty_six_warning_fires_from_a_below_par_event(foh, bus, db):
    # the inventory write itself publishes a below-par event; the explicit
    # publish afterwards must dedupe against it, not stack a second warning
    foh.inventory.add("scallops", id="i-scallop", category="seafood",
                      unit="each", par_level=20.0, on_hand=2.0)
    bus.publish(INGREDIENTS_UPDATED, {
        "action": "upsert",
        "ingredient": {"id": "i-scallop", "name": "scallops"},
        "below_par": True,
    })
    warnings = [r for r in foh.engine.pending("foh") if "86 warning" in r.title]
    assert len(warnings) == 1
    assert warnings[0].urgency == "urgent"
    assert "scallops" in warnings[0].body


def test_86_warning_is_deduped_and_ignores_healthy_ingredients(foh, bus):
    for _ in range(3):
        bus.publish(INGREDIENTS_UPDATED, {
            "action": "upsert",
            "ingredient": {"id": "i-herb", "name": "tarragon"},
            "below_par": True,
        })
    assert len([r for r in foh.engine.pending("foh") if "86 warning" in r.title]) == 1

    bus.publish(INGREDIENTS_UPDATED, {
        "action": "upsert", "ingredient": {"id": "i-x", "name": "plenty"},
        "below_par": False})
    assert len([r for r in foh.engine.pending("foh") if "plenty" in r.title]) == 0


def test_checklists_are_user_entered_never_hardcoded(foh, db):
    assert foh.checklist_items("foh") == []
    assert foh.open_checklist(when=WHEN) == []
    foh.add_checklist_item("foh", "Polish glassware")
    foh.add_checklist_item("foh", "Light candles")
    assert foh.checklist_items("foh") == ["Polish glassware", "Light candles"]
    reminders = foh.open_checklist(when=WHEN)
    assert len(reminders) == 2
    assert reminders[0].fire_at == "2026-10-01T15:40:00"  # 20 min before doors


def test_close_checklist_fires_at_service_end(foh):
    foh.add_checklist_item("foh", "Reset the room")
    reminders = foh.close_checklist(when=WHEN)
    assert reminders[0].fire_at == foh.config.service_end


def test_restock_and_vip_reminders(foh):
    restock = foh.restock("rosemary")
    assert "rosemary" in restock.title
    vip = foh.vip("12", "anniversary", at="2026-10-01T19:00:00")
    assert "table 12" in vip.title
    # privacy: the occasion reminder carries no guest identity
    assert "anniversary" in vip.body
    assert vip.audience == "foh"


def test_foh_and_boh_share_one_engine_without_crossing_audiences(foh, db):
    from modules.reminders_boh import BOHReminders

    boh = BOHReminders(foh.config, db, foh.bus, engine=foh.engine)
    foh.briefing("Front", when=WHEN)
    boh.prep_timer("braise", 60, when=WHEN)
    assert len(foh.engine.pending("foh")) == 1
    assert len(foh.engine.pending("boh")) == 1
    assert db.scalar("SELECT COUNT(*) FROM reminders") == 2  # no duplication


def test_tick_delegates_to_the_shared_engine(foh):
    foh.briefing("Front", at="2026-10-01T16:00:00")
    result = foh.tick(WHEN)
    assert result["fired"] == 1
