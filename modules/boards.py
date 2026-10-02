"""The Pass — the chef, agent and server/host boards (Prompt 22).

NOTE: three boards, because three people look at different things:
  Cuisine   (chef)   — R&D through to 86'd
  Operations(agent)  — the agent's own queue, filled by the agent's own alarms
  Hospitality (FOH)  — sections, sidework, occasions, notes

WHY the agent board is the interesting one: everything the agent is waiting on a
human for (a pending approval, an unacknowledged temperature breach, an order
cutoff today) shows up there. It is the agent's to-do list made visible.

NOTE: no restaurant content is hardcoded. Recipes, sidework items and occasions
are the team's own data; this module supplies structure and automation.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from core.config import Config
from core.database import Database
from core.events import SERVICE_CLOSE, EventBus
from core.types import KanbanCard, Role
from modules.kanban import Kanban

CHEF_COLUMNS = ["R&D", "Testing", "On Menu", "86'd"]
AGENT_COLUMNS = ["Backlog", "Today", "Doing", "Done"]
FOH_COLUMNS = ["Sections", "Sidework", "VIP", "Notes"]

CHEF_BOARD = "Cuisine"
AGENT_BOARD = "Operations"
FOH_BOARD = "Hospitality"


class Boards:
    def __init__(self, config: Config, db: Database, bus: Optional[EventBus] = None,
                 kanban: Optional[Kanban] = None, review_queue: Any = None,
                 templog: Any = None, order_ahead: Any = None,
                 wash_counter: Any = None, recipe_book: Any = None,
                 planner: Any = None, inventory: Any = None) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.kanban = kanban or Kanban(config, db, bus)
        self.review_queue = review_queue
        self.templog = templog
        self.order_ahead = order_ahead
        self.wash_counter = wash_counter
        self.recipe_book = recipe_book
        self.planner = planner
        self.inventory = inventory
        self._build_boards()
        if bus is not None:
            bus.subscribe(SERVICE_CLOSE, self._on_service_close)

    # -- boards ------------------------------------------------------------
    def _build_boards(self) -> None:
        self.kanban.create_board(CHEF_BOARD, Role.CHEF.value, CHEF_COLUMNS,
                                 wip={"Testing": 3})
        self.kanban.create_board(AGENT_BOARD, Role.AGENT.value, AGENT_COLUMNS,
                                 wip={"Doing": 3})
        self.kanban.create_board(FOH_BOARD, Role.HOST.value, FOH_COLUMNS)

    def chef_board(self) -> Dict[str, Any]:
        return self.kanban.board_by_name(CHEF_BOARD)

    def agent_board(self) -> Dict[str, Any]:
        return self.kanban.board_by_name(AGENT_BOARD)

    def foh_board(self) -> Dict[str, Any]:
        return self.kanban.board_by_name(FOH_BOARD)

    def board_for_role(self, role: str) -> Optional[Dict[str, Any]]:
        mapping = {Role.CHEF.value: CHEF_BOARD, Role.AGENT.value: AGENT_BOARD,
                   Role.HOST.value: FOH_BOARD, Role.SERVER.value: FOH_BOARD}
        name = mapping.get(role)
        return self.kanban.board_by_name(name) if name else None

    # -- seeding from real data -------------------------------------------
    def seed_recipes(self) -> int:
        """Every recipe the kitchen knows about gets a card on the chef board."""
        if self.recipe_book is None:
            return 0
        board = self.chef_board()
        added = 0
        for recipe in self.recipe_book.local_recipes():
            if self._ensure_card(board["id"], f"Recipe: {recipe.name}",
                                 f"{int(recipe.portions)} portions, station {recipe.station}",
                                 "R&D", Role.CHEF.value):
                added += 1
        return added

    def sync(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Pull live state onto the agent board (idempotent, dedup by title)."""
        board = self.agent_board()
        created: List[str] = []

        # 1. anything waiting on a human
        if self.review_queue is not None:
            for item in self.review_queue.pending():
                card = self._ensure_card(board["id"], f"Approve: {item.summary}",
                                         f"deadline {item.deadline}", "Today",
                                         Role.CHEF.value, due=item.deadline)
                if card:
                    created.append(card.title)

        # 2. temperature trouble
        if self.templog is not None:
            for status in self.templog.zone_status():
                if status.get("streak", 0) >= 2 or status.get("in_bounds") is False:
                    card = self._ensure_card(
                        board["id"], f"Temperature: {status['zone']}",
                        f"{status.get('current')}C, streak {status.get('streak')}",
                        "Today", Role.CHEF.value)
                    if card:
                        created.append(card.title)

        # 3. order windows closing today
        if self.order_ahead is not None and self.planner is not None:
            plan = self.planner.latest_plan()
            for rec in (self.order_ahead.due_today(plan.service_date,
                                                   shortfalls=plan.shortfalls)
                        if plan is not None else []):
                card = self._ensure_card(
                    board["id"], f"Order today: {rec.supplier_name}",
                    f"{rec.ingredient_name} {rec.qty:g} {rec.unit}; {rec.note}",
                    "Today", Role.CHEF.value, due=rec.order_by)
                if card:
                    created.append(card.title)

        # 4. the pit falling behind
        if self.wash_counter is not None and self.wash_counter.open_racks() >= self.config.wash_backlog_threshold:
            card = self._ensure_card(
                board["id"], "Pit is behind",
                f"{self.wash_counter.open_racks()} racks open", "Doing",
                Role.CHEF.value)
            if card:
                created.append(card.title)

        return {"created": created, "count": len(created)}

    # -- close-down --------------------------------------------------------
    def close_checklist(self, when: Optional[datetime] = None) -> List[KanbanCard]:
        """Turn the team's own closing list into cards (never hardcoded)."""
        board = self.foh_board()
        rows = self.db.query(
            "SELECT item FROM checklists WHERE audience = 'foh' AND active = 1"
            " ORDER BY position, id")
        cards = []
        for row in rows:
            card = self._ensure_card(board["id"], f"Close: {row['item']}", "",
                                     "Sidework", Role.SERVER.value)
            if card:
                cards.append(card)
        return cards

    def _on_service_close(self, event_type: str, payload: Any) -> None:
        self.close_checklist()

    def tick(self, when: Optional[datetime] = None) -> Dict[str, Any]:
        return self.sync(when)

    # -- internal ----------------------------------------------------------
    def _ensure_card(self, board_id: str, title: str, detail: str, column: str,
                     owner_role: str, due: str = "") -> Optional[KanbanCard]:
        """Create the card unless an open card with the same title exists."""
        existing = self.db.query_one(
            "SELECT * FROM kanban_cards WHERE board_id = ? AND title = ?"
            " AND column != 'Done'", (board_id, title))
        if existing:
            return None
        return self.kanban.add_card(board_id, title, detail, column, owner_role, due)
