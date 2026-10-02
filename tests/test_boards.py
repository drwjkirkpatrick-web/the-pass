"""Prompt 22 tests — three role boards fed by live module state."""
from datetime import datetime

import pytest

from core.types import Recipe, RecipeLine
from modules.boards import (AGENT_BOARD, CHEF_BOARD, FOH_BOARD, Boards)
from modules.dish_counter import DishCounter
from modules.inventory import Inventory
from modules.menu_planner import MenuPlanner
from modules.recipes import RecipeBook
from modules.review_queue import ReviewQueue
from modules.templog import TempLog
from modules.wash_counter import WashCounter

WHEN = datetime(2026, 10, 1, 19, 0, 0)


@pytest.fixture
def kitchen(config, db, bus):
    inventory = Inventory(config, db, bus)
    book = RecipeBook(config, db, bus)
    book.save(Recipe(id="r-1", name="Braised Short Rib", portions=4,
                     lines=[RecipeLine("i-beef", 250.0, "g")]))
    planner = MenuPlanner(config, db, bus, inventory=inventory, recipe_book=book)
    review_queue = ReviewQueue(config, db, bus)
    templog = TempLog(config, db, bus)
    templog.default_zones()
    dishes = DishCounter(config, db, bus)
    wash = WashCounter(config, db, bus, dish_counter=dishes)
    boards = Boards(config, db, bus, review_queue=review_queue, templog=templog,
                    wash_counter=wash, recipe_book=book, planner=planner,
                    inventory=inventory)
    return {"boards": boards, "review": review_queue, "temp": templog,
            "wash": wash, "book": book, "planner": planner, "inventory": inventory}


def test_three_boards_exist_with_role_columns(kitchen):
    boards = kitchen["boards"]
    assert boards.chef_board()["columns"] == ["R&D", "Testing", "On Menu", "86'd"]
    assert boards.agent_board()["columns"] == ["Backlog", "Today", "Doing", "Done"]
    assert boards.foh_board()["columns"] == ["Sections", "Sidework", "VIP", "Notes"]
    assert boards.chef_board()["role"] == "chef"
    assert boards.board_for_role("server")["name"] == FOH_BOARD


def test_seeding_recipes_is_idempotent(kitchen):
    boards = kitchen["boards"]
    assert boards.seed_recipes() == 1
    assert boards.seed_recipes() == 0  # already there
    titles = [c.title for c in boards.kanban.cards(boards.chef_board()["id"])]
    assert titles == ["Recipe: Braised Short Rib"]


def test_pending_reviews_land_on_the_agent_board(kitchen):
    boards = kitchen["boards"]
    kitchen["review"].submit("order", "o-1", "Approve purchase order for s-1 (2 lines)")
    result = boards.sync()
    assert "Approve: Approve purchase order for s-1 (2 lines)" in result["created"]
    cards = boards.kanban.cards(boards.agent_board()["id"], "Today")
    assert len(cards) == 1
    assert cards[0].owner_role == "chef"
    # syncing again does not duplicate the card
    assert boards.sync()["count"] == 0


def test_temperature_trouble_lands_on_the_agent_board(kitchen):
    boards = kitchen["boards"]
    kitchen["temp"].log("z-walkin", celsius=9.0)
    kitchen["temp"].log("z-walkin", celsius=9.5)  # two in a row
    result = boards.sync()
    assert any("Temperature: Walk-in" in title for title in result["created"])
    assert any(c.title.startswith("Temperature:")
               for c in boards.kanban.cards(boards.agent_board()["id"], "Today"))


def test_wash_backlog_lands_on_the_agent_board(kitchen, config):
    boards = kitchen["boards"]
    for _ in range(config.wash_backlog_threshold):
        kitchen["wash"].rack_in()
    result = boards.sync()
    assert "Pit is behind" in result["created"]


def test_close_checklist_comes_from_user_entered_items(kitchen, bus):
    boards = kitchen["boards"]
    db = boards.db
    db.execute("INSERT INTO checklists (audience, item, position, active)"
               " VALUES ('foh','Reset the room',1,1)")
    db.execute("INSERT INTO checklists (audience, item, position, active)"
               " VALUES ('foh','Count the till',2,1)")
    cards = boards.close_checklist()
    assert [c.title for c in cards] == ["Close: Reset the room", "Close: Count the till"]
    assert all(c.column == "Sidework" for c in cards)
    assert all(c.owner_role == "server" for c in cards)


def test_service_close_event_generates_the_checklist(kitchen, bus):
    boards = kitchen["boards"]
    boards.db.execute("INSERT INTO checklists (audience, item, position, active)"
                      " VALUES ('foh','Wipe the pass',1,1)")
    from core.events import SERVICE_CLOSE
    bus.publish(SERVICE_CLOSE, {"date": "2026-10-01"})
    titles = [c.title for c in boards.kanban.cards(boards.foh_board()["id"])]
    assert titles == ["Close: Wipe the pass"]


def test_agent_board_can_be_worked_by_the_agent(kitchen):
    boards = kitchen["boards"]
    kitchen["review"].submit("order", "o-9", "Approve purchase order for s-2 (1 lines)")
    boards.sync()
    card = boards.kanban.cards(boards.agent_board()["id"], "Today")[0]
    boards.kanban.move(card.id, "Doing", actor="agent")
    boards.kanban.move(card.id, "Done", actor="agent")
    # a Done card can be re-raised if the situation comes back
    assert boards.sync()["count"] == 1


def test_tick_is_the_sync_entry_point(kitchen):
    boards = kitchen["boards"]
    kitchen["review"].submit("order", "o-3", "Approve purchase order for s-3 (1 lines)")
    assert boards.tick(WHEN)["count"] == 1
