"""Prompt 27 tests — the command surface and its exit-code contract."""
import json

import pytest

from cli import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE, main
from core.config import Config
from core.types import Recipe, RecipeLine
from main import build_agent


@pytest.fixture
def kitchen(tmp_path):
    db_path = str(tmp_path / "pass.db")
    config = Config.default().with_overrides(db_path=db_path,
                                             photo_dir=str(tmp_path / "photos"))
    agent = build_agent(config)
    inventory = agent.get("inventory")
    inventory.add("beef", id="i-beef", category="protein", unit="g", on_hand=1000.0,
                  par_level=5000.0, unit_cost=0.05)
    agent.get("recipes").save(Recipe(id="r-1", name="Short Rib", portions=4,
                                     menu_price=38.0,
                                     lines=[RecipeLine("i-beef", 250.0, "g")]))
    return {"db": db_path, "photos": str(tmp_path / "photos"), "agent": agent}


def run_cli(kitchen, *args, capsys=None):
    return main(["--db", kitchen["db"], *args])


def test_no_command_prints_help_and_returns_usage(kitchen, capsys):
    assert main(["--db", kitchen["db"]]) == EXIT_USAGE
    assert "restaurant agent" in capsys.readouterr().out


def test_unknown_option_returns_usage(kitchen):
    with pytest.raises(SystemExit) as exc:
        main(["--db", kitchen["db"], "plan", "--nonsense"])
    assert exc.value.code == EXIT_USAGE


def test_status_reports_state_and_modules(kitchen, capsys):
    assert run_cli(kitchen, "status") == EXIT_OK
    output = capsys.readouterr().out
    assert "michelin" in output
    assert "service_state" in output


def test_status_json_is_parseable(kitchen, capsys):
    assert run_cli(kitchen, "status", "--json") == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["modules"]
    assert "inventory" in payload


def test_tick_runs(kitchen, capsys):
    assert run_cli(kitchen, "tick") == EXIT_OK
    capsys.readouterr()


def test_plan_renders_markdown_and_json(kitchen, capsys):
    assert run_cli(kitchen, "plan", "--covers", '{"r-1": 40}',
                   "--date", "2026-10-02") == EXIT_OK
    text = capsys.readouterr().out
    assert "Service plan — 2026-10-02" in text
    assert "Shortfalls" in text

    assert run_cli(kitchen, "plan", "--covers", '{"r-1": 40}',
                   "--date", "2026-10-02", "--json") == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["service_date"] == "2026-10-02"


def test_plan_without_covers_is_a_usage_error(kitchen):
    with pytest.raises(SystemExit) as exc:
        run_cli(kitchen, "plan")
    assert exc.value.code == EXIT_USAGE


def test_temp_init_log_and_excursion_exit_code(kitchen, capsys):
    assert run_cli(kitchen, "temp", "--init-zones") == EXIT_OK
    assert "Walk-in" in capsys.readouterr().out

    assert run_cli(kitchen, "temp", "--zone", "z-walkin", "--c", "3.0") == EXIT_OK
    capsys.readouterr()

    # out of bounds: a human has to act, so the exit code says so
    assert run_cli(kitchen, "temp", "--zone", "z-walkin", "--c", "9.0") == EXIT_BLOCKED
    capsys.readouterr()  # drain the first reading's output
    run_cli(kitchen, "temp", "--zone", "z-walkin", "--c", "9.5", "--json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["escalated"] is True

    assert run_cli(kitchen, "temp", "--report") == EXIT_OK
    capsys.readouterr()


def test_ingest_blocks_for_human_approval(kitchen, capsys, tmp_path):
    photo = tmp_path / "heirloom_tomato_4kg_crisp.jpg"
    photo.write_bytes(b"fake")
    assert run_cli(kitchen, "ingest", str(photo), "--json") == EXIT_BLOCKED
    payload = json.loads(capsys.readouterr().out)
    assert payload["lines"][0]["name"] == "heirloom tomato"
    assert payload["draft_id"]


def test_orders_curate_then_pending_then_approve(kitchen, capsys):
    assert run_cli(kitchen, "orders", "--curate", "--date", "2026-10-02",
                   "--json") == EXIT_BLOCKED
    payload = json.loads(capsys.readouterr().out)
    assert payload["queued_for_review"]

    # exit 2: something is waiting on a human, and the exit code says so
    assert run_cli(kitchen, "orders", "--pending", "--json") == EXIT_BLOCKED
    pending = json.loads(capsys.readouterr().out)["pending_reviews"]
    assert pending

    review_id = pending[0]["id"]
    assert run_cli(kitchen, "orders", "--approve", review_id) == EXIT_OK
    capsys.readouterr()
    approved = kitchen["agent"].get("ordering").orders(status="approved")
    assert len(approved) == 1

    assert run_cli(kitchen, "orders", "--export", approved[0].id) == EXIT_OK
    assert "PURCHASE ORDER" in capsys.readouterr().out


def test_orders_json_shape_is_stable_when_nothing_is_pending(kitchen, capsys):
    assert run_cli(kitchen, "orders", "--curate", "--json") == EXIT_BLOCKED
    capsys.readouterr()
    pending = json.loads(run_cli(kitchen, "orders", "--pending", "--json")
                         and capsys.readouterr().out)["pending_reviews"]
    for item in pending:
        run_cli(kitchen, "orders", "--approve", item["id"])
        capsys.readouterr()

    assert run_cli(kitchen, "orders", "--json") == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    # both keys, always — even when there is nothing waiting
    assert set(payload) == {"pending_reviews", "drafts"}
    assert payload["pending_reviews"] == []
    assert payload["drafts"] == []


def test_review_blocks_while_plates_are_waiting(kitchen, capsys):
    kitchen["agent"].get("pass_photo").capture("r-1")
    assert run_cli(kitchen, "review", "--pending") == EXIT_BLOCKED
    capsys.readouterr()
    assert run_cli(kitchen, "review", "--json") == EXIT_BLOCKED
    payload = json.loads(capsys.readouterr().out)
    assert payload["next"]["dish_id"] == "r-1"


def test_journal_and_note(kitchen, capsys):
    assert run_cli(kitchen, "journal") == EXIT_OK
    assert "Service journal" in capsys.readouterr().out
    assert run_cli(kitchen, "journal", "--note", "service ran clean") == EXIT_OK
    assert "service ran clean" in capsys.readouterr().out


def test_boards_for_each_role(kitchen, capsys):
    for role in ("chef", "agent", "server"):
        assert run_cli(kitchen, "boards", "--role", role, "--json") == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["columns"]
    with pytest.raises(SystemExit) as exc:
        run_cli(kitchen, "boards", "--role", "nobody")
    assert exc.value.code == EXIT_USAGE


def test_michelin_renders_and_serialises(kitchen, capsys):
    assert run_cli(kitchen, "michelin") == EXIT_OK
    assert "five criteria" in capsys.readouterr().out
    assert run_cli(kitchen, "michelin", "--json") == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["criteria"]) == 5
