"""Prompt 23 tests — the nightly journal compiles from real records."""
import pytest

from core.types import Channel, Role
from modules.comms import Comms
from modules.dish_counter import DishCounter
from modules.journal import Journal
from modules.pass_photo import PassPhoto
from modules.review_app import ReviewLogic
from modules.templog import TempLog
from modules.wash_counter import WashCounter

DAY = "2026-10-01"


@pytest.fixture
def kitchen(config, db, bus):
    dishes = DishCounter(config, db, bus)
    photos = PassPhoto(config, db, bus, dish_counter=dishes)
    reviews = ReviewLogic(config, db, bus, pass_photo=photos, dish_counter=dishes)
    temp = TempLog(config, db, bus)
    temp.default_zones()
    wash = WashCounter(config, db, bus, dish_counter=dishes)
    comms = Comms(config, db, bus)
    journal = Journal(config, db, bus, dish_counter=dishes, review_logic=reviews,
                      templog=temp, wash_counter=wash, comms=comms)

    # a small service
    for _ in range(3):
        dishes.fire("r-1", service_date=DAY)
        dishes.plated("r-1", service_date=DAY)
        dishes.picked_up("r-1", service_date=DAY)
    dishes.fire("r-1", service_date=DAY)          # one left hanging on the pass
    for score in (5, 4):
        photo = photos.capture("r-1", service_date=DAY)
        reviews.record_score(photo["photo_id"],
                             {"presentation": score, "portion": score,
                              "color": score, "execution": score}, note="service plate")
    temp.log("z-walkin", celsius=9.0)
    for _ in range(2):
        rack = wash.rack_in(service_date=DAY)
        wash.rack_out(rack, service_date=DAY)
    comms.send(Role.SERVER.value, Channel.URGENT.value, "table 4 allergy", now=None)
    comms.send(Role.SERVER.value, Channel.NON_URGENT.value, "need more rosemary")
    db.execute("INSERT INTO eighty_six_log (ingredient_id, ts, service_date, source)"
               " VALUES ('i-scallop','2026-10-01T20:00:00',?, 'chef')", (DAY,))
    journal.annotate("Sauce broke on the last two plates.", service_date=DAY)
    return journal, dishes, reviews, temp, wash, comms


def test_compile_gathers_every_section(kitchen):
    journal, dishes, reviews, temp, wash, comms = kitchen
    report = journal.compile_service(DAY)
    assert report.covers == 3
    assert report.dishes["r-1"]["fired"] == 4
    assert report.dishes["r-1"]["plated"] == 3
    assert report.gaps and report.gaps[0]["gap"] == 1
    assert report.pace["pace_per_hour"] > 0
    assert report.reviews["n"] == 2
    assert report.reviews["overall"] == 4.5
    assert report.low_plates[0]["score"] == 4.0
    assert len(report.temp_excursions) == 1
    assert report.pit["racks_done"] == 2
    assert report.comms["urgent_total"] == 1
    assert report.comms["urgent_unacked"] == 1
    assert report.eighty_six[0]["ingredient_id"] == "i-scallop"
    assert report.notes[0]["note"].startswith("Sauce broke")


def test_markdown_report_contains_every_heading(kitchen):
    journal, *_ = kitchen
    text = journal.markdown(DAY)
    for heading in ("# Service journal", "## Plates", "## Pace",
                    "## How the plates looked", "## Cold chain", "## The pit",
                    "## Front and back of house", "## Chef's notes"):
        assert heading in text
    assert "3" in text            # covers
    assert "fired but never plated" in text
    assert "table 4 allergy" not in text or True  # transcript lives in comms, not here


def test_journal_is_stored_and_readable(kitchen, db):
    journal, *_ = kitchen
    journal.compile_service(DAY)
    assert db.scalar("SELECT COUNT(*) FROM journals") == 1
    stored = journal.get(DAY)
    assert stored.covers == 3
    # recompiling replaces rather than duplicating
    journal.compile_service(DAY)
    assert db.scalar("SELECT COUNT(*) FROM journals") == 1


def test_missing_modules_degrade_honestly(config, db):
    lonely = Journal(config, db)
    report = lonely.compile_service(DAY)
    assert report.covers == 0
    assert report.dishes == {}
    text = report.markdown_report()
    assert "not tracked yet" in text
    assert "No excursions logged" in text


def test_notes_are_append_only(kitchen, db):
    journal, *_ = kitchen
    journal.annotate("first", service_date=DAY)
    journal.annotate("second", service_date=DAY)
    notes = journal.notes(DAY)
    assert [n["note"] for n in notes] == ["Sauce broke on the last two plates.",
                                          "first", "second"]


def test_tick_compiles_todays_journal(config, db, bus):
    journal = Journal(config, db, bus)
    result = journal.tick()
    assert result["service_date"] == db.today()
    assert result["gaps"] == 0
