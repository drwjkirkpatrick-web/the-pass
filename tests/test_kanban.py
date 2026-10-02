"""Prompt 21 tests — the generic board engine."""
import pytest

from modules.kanban import Kanban


@pytest.fixture
def boards(config, db, bus):
    engine = Kanban(config, db, bus)
    engine.create_board("Cuisine", "chef", ["R&D", "Testing", "On Menu", "86'd"],
                        wip={"Testing": 1})
    return engine


def test_create_board_is_idempotent_by_name(boards, db):
    first = boards.board_by_name("Cuisine")
    second = boards.create_board("Cuisine", "chef", ["whatever"])
    assert first["id"] == second["id"]
    assert db.scalar("SELECT COUNT(*) FROM kanban_boards") == 1
    assert second["columns"] == ["R&D", "Testing", "On Menu", "86'd"]


def test_add_card_defaults_to_the_first_column(boards):
    board = boards.board_by_name("Cuisine")
    card = boards.add_card(board["id"], "Try a koji cure")
    assert card.column == "R&D"
    assert card.blocked is False
    assert boards.get(card.id).title == "Try a koji cure"


def test_add_card_rejects_an_unknown_column(boards):
    board = boards.board_by_name("Cuisine")
    with pytest.raises(ValueError):
        boards.add_card(board["id"], "x", column="Nowhere")


def test_add_card_rejects_an_unknown_board(boards):
    with pytest.raises(KeyError):
        boards.add_card("b-nope", "x")


def test_move_records_history(boards):
    board = boards.board_by_name("Cuisine")
    card = boards.add_card(board["id"], "Koji cure")
    moved = boards.move(card.id, "Testing", actor="chef")
    assert moved.column == "Testing"
    history = boards.history(card.id)
    assert len(history) == 1
    assert history[0]["from_col"] == "R&D"
    assert history[0]["to_col"] == "Testing"


def test_wip_limit_is_enforced(boards):
    board = boards.board_by_name("Cuisine")
    first = boards.add_card(board["id"], "one")
    boards.move(first.id, "Testing")
    second = boards.add_card(board["id"], "two")
    with pytest.raises(ValueError) as err:
        boards.move(second.id, "Testing")
    assert "WIP limit" in str(err.value)
    # finishing the first frees the slot
    boards.move(first.id, "On Menu")
    assert boards.move(second.id, "Testing").column == "Testing"


def test_moving_within_the_same_column_is_allowed_at_the_limit(boards):
    board = boards.board_by_name("Cuisine")
    card = boards.add_card(board["id"], "one", column="Testing")
    assert boards.move(card.id, "Testing").column == "Testing"


def test_move_to_unknown_column_raises(boards):
    board = boards.board_by_name("Cuisine")
    card = boards.add_card(board["id"], "one")
    with pytest.raises(ValueError):
        boards.move(card.id, "Nonsense")


def test_owner_blocked_and_overdue_filters(boards):
    board = boards.board_by_name("Cuisine")
    mine = boards.add_card(board["id"], "mine", owner_role="chef")
    yours = boards.add_card(board["id"], "yours", owner_role="agent",
                            due="2020-01-01T00:00:00")
    boards.set_blocked(yours.id, True)
    assert [c.id for c in boards.by_owner("chef")] == [mine.id]
    assert [c.id for c in boards.blocked()] == [yours.id]
    assert [c.id for c in boards.overdue()] == [yours.id]


def test_boards_are_isolated(boards):
    cuisine = boards.board_by_name("Cuisine")
    pastry = boards.create_board("Pastry", "chef", ["Ideas"])
    boards.add_card(cuisine["id"], "savoury thing")
    boards.add_card(pastry["id"], "sweet thing")
    assert [c.title for c in boards.cards(cuisine["id"])] == ["savoury thing"]
    assert [c.title for c in boards.cards(pastry["id"])] == ["sweet thing"]


def test_summary_counts_by_column(boards):
    board = boards.board_by_name("Cuisine")
    boards.add_card(board["id"], "a")
    boards.add_card(board["id"], "b", column="On Menu")
    assert boards.summary()["Cuisine"]["R&D"] == 1
    assert boards.summary()["Cuisine"]["On Menu"] == 1
