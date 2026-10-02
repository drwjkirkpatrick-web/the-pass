"""Prompt 29 — one full simulated day, asserted as a narrative."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures"))

from service_sim import DAY, DaySim  # noqa: E402


@pytest.fixture(scope="module")
def day(tmp_path_factory):
    started = time.time()
    tmp = tmp_path_factory.mktemp("service")
    sim = DaySim(tmp)
    report = sim.run_full_day(tmp)
    report["_seconds"] = round(time.time() - started, 2)
    report["_agent"] = sim.agent
    return report


def test_the_whole_day_runs_in_one_process_and_quickly(day):
    assert day["_seconds"] < 60, f"the day took {day['_seconds']}s"


def test_morning_delivery_became_an_approved_ingredient_list(day):
    names = day["ingredients"]
    assert "beef short rib" in names
    assert "heirloom tomato" in names


def test_order_dates_respect_the_supplier_p90(day):
    """Independently computed: arrival = service - 1 day; order by arrival - p90."""
    from datetime import date, timedelta

    from service_sim import DAY

    assert day["p90_delay"] == 3.0
    assert day["median_delay"] == 1.5

    arrival = date.fromisoformat(DAY) - timedelta(days=1)
    expected_order_by = (arrival - timedelta(days=3)).isoformat()
    rationales = [line for order in day["order_dates"] for line in order["lines"]]
    assert rationales
    assert any(f"order by {expected_order_by}" in r for r in rationales)
    assert any(f"arrives {arrival.isoformat()}" in r for r in rationales)


def test_orders_were_drafted_then_approved_by_a_human(day):
    assert day["draft_orders"]
    assert day["queued_for_review"]
    assert day["approved_orders"] == day["draft_orders"]
    agent = day["_agent"]
    for order_id in day["approved_orders"]:
        assert agent.get("ordering").get_order(order_id).status.value == "approved"


def test_the_excursion_escalated_only_after_two_readings(day):
    assert day["temp_escalated"] is True
    agent = day["_agent"]
    urgent = agent.db.query_one("SELECT * FROM reminders WHERE urgency = 'urgent'"
                                " AND title LIKE 'Temperature%'")
    assert urgent is not None
    assert "twice in a row" in urgent["body"]


def test_the_line_was_used_and_acknowledged(day):
    agent = day["_agent"]
    comms = agent.get("comms")
    assert comms.get(day["urgent_id"]).acked is True
    assert comms.summary()["urgent_unacked"] == 0
    assert comms.summary()["non_urgent_total"] == 1


def test_the_pass_counted_plates_and_the_pit_counted_racks(day):
    assert day["covers_served"] == 7
    assert day["wash_racks"] == 5


def test_journal_has_every_section_filled(day):
    sections = day["journal_sections"]
    assert sections["covers"] == 7
    assert sections["dishes"] == 2
    assert sections["reviews"] == 6
    assert sections["excursions"] == 2
    assert sections["pit_racks"] == 5
    assert sections["urgent_calls"] == 1


def test_the_journal_names_the_weakest_plates(day):
    assert day["low_plates"]
    assert day["low_plates"][0]["dish_id"] == "r-rib"
    assert day["low_plates"][0]["score"] == 3.0


def test_drift_flags_the_dish_that_slipped_mid_service(day):
    assert day["drift"]["metrics"]["score_change"] < 0
    assert any("Review scores down" in f for f in day["drift"]["findings"])
    assert "Drift: r-rib" in day["drift_cards"]


def test_the_agent_board_shows_what_needs_a_human(day):
    titles = [card["title"] for card in day["agent_board"]]
    assert any(title.startswith("Temperature:") for title in titles)
    assert any(title.startswith("Drift:") for title in titles)


def test_the_michelin_dashboard_reports_five_grounded_criteria(day):
    assert day["michelin_cards"] == 5
    agent = day["_agent"]
    note = agent.get("michelin").star_readiness_note()
    assert "/100" in note
    assert "not a verdict" in note
    assert day["michelin_focus"] in (
        "quality of ingredients", "mastery of technique",
        "personality of the cuisine", "value", "consistency")


def test_the_cli_still_works_against_the_finished_day(day, capsys):  # noqa: D401
    from cli import EXIT_OK, main
    agent = day["_agent"]
    config = agent.config
    assert main(["--db", config.db_path, "status", "--json"]) == EXIT_OK
    import json
    payload = json.loads(capsys.readouterr().out)
    assert payload["modules"]
    assert main(["--db", config.db_path, "journal", "--date", DAY]) == EXIT_OK
    assert "Service journal" in capsys.readouterr().out
