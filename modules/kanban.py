"""The Pass — one board machine, three boards later (Prompt 21).

NOTE: the engine knows nothing about restaurants. It knows boards have columns,
cards move between them, some columns are capacity-limited, and every move is
recorded.

WHY WIP limits: a "Doing" column with fourteen cards is a to-do list pretending
to be a plan. The limit is the feature.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.config import Config
from core.database import TS_FMT, Database
from core.events import EventBus
from core.types import KanbanCard


class Kanban:
    def __init__(self, config: Config, db: Database,
                 bus: Optional[EventBus] = None) -> None:
        self.config = config
        self.db = db
        self.bus = bus

    # -- boards ------------------------------------------------------------
    def create_board(self, name: str, role: str, columns: List[str],
                     wip: Optional[Dict[str, int]] = None,
                     board_id: Optional[str] = None) -> Dict[str, Any]:
        """Idempotent by name: calling twice returns the same board."""
        existing = self.board_by_name(name)
        if existing:
            return existing
        board = {"id": board_id or f"b-{uuid.uuid4().hex[:6]}", "name": name,
                 "role": role, "columns": list(columns),
                 "wip": dict(wip or {})}
        self.db.execute(
            "INSERT INTO kanban_boards (id, name, role, columns, wip)"
            " VALUES (?,?,?,?,?)",
            (board["id"], board["name"], board["role"], json.dumps(board["columns"]),
             json.dumps(board["wip"])))
        return board

    def _to_board(self, row) -> Dict[str, Any]:
        return {"id": row["id"], "name": row["name"], "role": row["role"],
                "columns": json.loads(row["columns"] or "[]"),
                "wip": json.loads(row["wip"] or "{}")}

    def boards(self) -> List[Dict[str, Any]]:
        return [self._to_board(r) for r in
                self.db.query("SELECT * FROM kanban_boards ORDER BY name")]

    def get_board(self, board_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.query_one("SELECT * FROM kanban_boards WHERE id = ?", (board_id,))
        return self._to_board(row) if row else None

    def board_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        row = self.db.query_one("SELECT * FROM kanban_boards WHERE name = ?", (name,))
        return self._to_board(row) if row else None

    # -- cards -------------------------------------------------------------
    def add_card(self, board_id: str, title: str, detail: str = "",
                 column: Optional[str] = None, owner_role: str = "",
                 due: str = "", card_id: Optional[str] = None) -> KanbanCard:
        board = self.get_board(board_id)
        if board is None:
            raise KeyError(f"no such board: {board_id}")
        column = column or board["columns"][0]
        if column not in board["columns"]:
            raise ValueError(f"{column!r} is not a column on {board['name']}")
        card = KanbanCard(id=card_id or f"c-{uuid.uuid4().hex[:8]}", board_id=board_id,
                          title=title, detail=detail, column=column,
                          owner_role=owner_role, due=due)
        self.db.execute(
            "INSERT INTO kanban_cards (id, board_id, title, detail, column,"
            " owner_role, due, blocked, created_at) VALUES (?,?,?,?,?,?,?,0,?)",
            (card.id, card.board_id, card.title, card.detail, card.column,
             card.owner_role, card.due, self.db.now()))
        return card

    def move(self, card_id: str, to_column: str, actor: str = "chef") -> KanbanCard:
        card = self.get(card_id)
        if card is None:
            raise KeyError(f"no such card: {card_id}")
        board = self.get_board(card.board_id)
        if to_column not in board["columns"]:
            raise ValueError(f"{to_column!r} is not a column on {board['name']}")
        limit = board["wip"].get(to_column)
        if limit is not None and card.column != to_column:
            in_column = len([c for c in self.cards(board["id"], to_column)])
            if in_column >= int(limit):
                raise ValueError(
                    f"{to_column} is at its WIP limit of {limit}; finish something first")
        self.db.execute("UPDATE kanban_cards SET column = ? WHERE id = ?",
                        (to_column, card_id))
        self.db.execute(
            "INSERT INTO kanban_moves (card_id, from_col, to_col, moved_at)"
            " VALUES (?,?,?,?)", (card_id, card.column, to_column, self.db.now()))
        return self.get(card_id)

    def set_blocked(self, card_id: str, blocked: bool = True) -> KanbanCard:
        self.db.execute("UPDATE kanban_cards SET blocked = ? WHERE id = ?",
                        (1 if blocked else 0, card_id))
        return self.get(card_id)

    # -- reads -------------------------------------------------------------
    def _to_card(self, row) -> KanbanCard:
        return KanbanCard(id=row["id"], board_id=row["board_id"], title=row["title"],
                          detail=row["detail"], column=row["column"],
                          owner_role=row["owner_role"], due=row["due"],
                          blocked=bool(row["blocked"]))

    def get(self, card_id: str) -> Optional[KanbanCard]:
        row = self.db.query_one("SELECT * FROM kanban_cards WHERE id = ?", (card_id,))
        return self._to_card(row) if row else None

    def cards(self, board_id: str, column: Optional[str] = None) -> List[KanbanCard]:
        if column:
            rows = self.db.query(
                "SELECT * FROM kanban_cards WHERE board_id = ? AND column = ?"
                " ORDER BY created_at", (board_id, column))
        else:
            rows = self.db.query(
                "SELECT * FROM kanban_cards WHERE board_id = ? ORDER BY column, created_at",
                (board_id,))
        return [self._to_card(r) for r in rows]

    def by_owner(self, role: str) -> List[KanbanCard]:
        rows = self.db.query(
            "SELECT * FROM kanban_cards WHERE owner_role = ? ORDER BY created_at", (role,))
        return [self._to_card(r) for r in rows]

    def blocked(self) -> List[KanbanCard]:
        rows = self.db.query(
            "SELECT * FROM kanban_cards WHERE blocked = 1 ORDER BY created_at")
        return [self._to_card(r) for r in rows]

    def overdue(self, now: Optional[str] = None) -> List[KanbanCard]:
        now = now or self.db.now()
        rows = self.db.query(
            "SELECT * FROM kanban_cards WHERE due != '' AND due < ? ORDER BY due",
            (now,))
        return [self._to_card(r) for r in rows]

    def history(self, card_id: str) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM kanban_moves WHERE card_id = ? ORDER BY id", (card_id,))
        return [dict(r) for r in rows]

    def summary(self) -> Dict[str, Any]:
        out = {}
        for board in self.boards():
            out[board["name"]] = {column: len(self.cards(board["id"], column))
                                  for column in board["columns"]}
        return out
