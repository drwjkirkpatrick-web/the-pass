"""The Pass — SQLite persistence (Prompt 02).

NOTE: one schema, created by migrate(). Every module in the agent shares these
tables, so the whole program's data model is visible in one file.

WHY lazy connect: tests use ":memory:" and must be able to build a Database
without touching the filesystem. Creating connections at import time would
break that (and would break Flask reloads too).
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from typing import Any, List, Optional

from core.types import filter_fields

TS_FMT = "%Y-%m-%dT%H:%M:%S"
D_FMT = "%Y-%m-%d"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ingredients (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT DEFAULT 'general',
  unit TEXT DEFAULT 'each', par_level REAL DEFAULT 0, on_hand REAL DEFAULT 0,
  unit_cost REAL DEFAULT 0, shelf_life_days INTEGER DEFAULT 0,
  freshness_date TEXT DEFAULT '', photo_ids TEXT DEFAULT '',
  status TEXT DEFAULT 'active', created_at TEXT DEFAULT '', updated_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS ingredient_drafts (
  id TEXT PRIMARY KEY, status TEXT DEFAULT 'proposed', created_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS draft_lines (
  id TEXT PRIMARY KEY, draft_id TEXT, name TEXT, category TEXT DEFAULT 'general',
  unit TEXT DEFAULT 'each', qty REAL DEFAULT 0, confidence REAL DEFAULT 0,
  quality_note TEXT DEFAULT '', photo_path TEXT DEFAULT '', flagged INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS recipes (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, portions REAL DEFAULT 1,
  station TEXT DEFAULT 'line', technique_tags TEXT DEFAULT '',
  plating_notes TEXT DEFAULT '', menu_price REAL DEFAULT 0,
  source TEXT DEFAULT 'local', created_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS recipe_lines (
  recipe_id TEXT, ingredient_id TEXT, qty_per_portion REAL DEFAULT 0,
  unit TEXT DEFAULT 'g', prep_note TEXT DEFAULT '',
  PRIMARY KEY (recipe_id, ingredient_id)
);
CREATE TABLE IF NOT EXISTS suppliers (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, order_cutoff TEXT DEFAULT '08:00',
  typical_delay_days REAL DEFAULT 1, closed_days TEXT DEFAULT '[]',
  notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS delivery_delays (
  id INTEGER PRIMARY KEY AUTOINCREMENT, supplier_id TEXT, ordered_date TEXT,
  promised_date TEXT, actual_date TEXT, recorded_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY, supplier_id TEXT, status TEXT DEFAULT 'draft',
  created_by TEXT DEFAULT 'chef', created_at TEXT DEFAULT '',
  service_date TEXT DEFAULT '', rationale TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS order_lines (
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, ingredient_id TEXT,
  ingredient_name TEXT, qty REAL DEFAULT 0, unit TEXT DEFAULT 'g',
  pack_size REAL DEFAULT 1, rationale TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS temp_zones (
  id TEXT PRIMARY KEY, name TEXT, station TEXT, min_c REAL, max_c REAL
);
CREATE TABLE IF NOT EXISTS temp_readings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, zone_id TEXT, station TEXT,
  sensor_id TEXT, celsius REAL, ts TEXT, in_bounds INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS reminders (
  id TEXT PRIMARY KEY, audience TEXT, urgency TEXT DEFAULT 'routine',
  title TEXT, body TEXT DEFAULT '', fire_at TEXT DEFAULT '',
  recurrence TEXT DEFAULT 'none', status TEXT DEFAULT 'scheduled',
  ack_by TEXT DEFAULT '', ack_at TEXT DEFAULT '', last_notify_at TEXT DEFAULT '',
  dedupe_key TEXT DEFAULT '', created_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS pass_messages (
  id TEXT PRIMARY KEY, channel TEXT, sender_role TEXT, body TEXT,
  ts TEXT DEFAULT '', acked INTEGER DEFAULT 0, ack_by TEXT DEFAULT '',
  ack_at TEXT DEFAULT '', last_notify_at TEXT DEFAULT '', dedupe_hash TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS dish_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, dish_id TEXT, action TEXT, ts TEXT,
  service_date TEXT DEFAULT '', photo_id TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS dish_photos (
  id TEXT PRIMARY KEY, dish_id TEXT, path TEXT, ts TEXT, service_date TEXT,
  event_id INTEGER, metrics TEXT DEFAULT '{}', pending_review INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS dish_reviews (
  id TEXT PRIMARY KEY, photo_id TEXT, dish_id TEXT, presentation INTEGER,
  portion INTEGER, color INTEGER, execution INTEGER, note TEXT DEFAULT '',
  reviewer TEXT DEFAULT '', ts TEXT DEFAULT '', service_date TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS kanban_boards (
  id TEXT PRIMARY KEY, name TEXT, role TEXT, columns TEXT DEFAULT '[]',
  wip TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS kanban_cards (
  id TEXT PRIMARY KEY, board_id TEXT, title TEXT, detail TEXT DEFAULT '',
  column TEXT, owner_role TEXT DEFAULT '', due TEXT DEFAULT '',
  blocked INTEGER DEFAULT 0, created_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS kanban_moves (
  id INTEGER PRIMARY KEY AUTOINCREMENT, card_id TEXT, from_col TEXT,
  to_col TEXT, moved_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS review_items (
  id TEXT PRIMARY KEY, item_type TEXT, item_id TEXT, summary TEXT,
  payload TEXT DEFAULT '{}', status TEXT DEFAULT 'pending',
  deadline TEXT DEFAULT '', created_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS review_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, review_id TEXT, decision TEXT,
  actor TEXT DEFAULT '', reason TEXT DEFAULT '', ts TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS wash_racks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, rack_in_ts TEXT, rack_out_ts TEXT,
  service_date TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sanitizer_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, shift TEXT, concentration TEXT,
  temp_c REAL, checked_by TEXT DEFAULT '', ts TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS service_plans (
  service_date TEXT PRIMARY KEY, payload TEXT, created_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS journals (
  service_date TEXT PRIMARY KEY, payload TEXT, created_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS journal_notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, service_date TEXT, note TEXT,
  author TEXT DEFAULT '', ts TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS eighty_six_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ingredient_id TEXT, ts TEXT,
  service_date TEXT DEFAULT '', source TEXT DEFAULT 'chef'
);
CREATE TABLE IF NOT EXISTS checklists (
  id INTEGER PRIMARY KEY AUTOINCREMENT, audience TEXT, item TEXT,
  position INTEGER DEFAULT 0, active INTEGER DEFAULT 1
);
"""

EXPECTED_TABLES = (
    "ingredients", "ingredient_drafts", "draft_lines", "recipes", "recipe_lines",
    "suppliers", "delivery_delays", "orders", "order_lines", "temp_zones",
    "temp_readings", "reminders", "pass_messages", "dish_events", "dish_photos",
    "dish_reviews", "kanban_boards", "kanban_cards", "kanban_moves",
    "review_items", "review_log", "wash_racks", "sanitizer_log",
    "service_plans", "journals", "journal_notes", "eighty_six_log", "checklists",
)


class Database:
    """Thin SQLite wrapper: lazy connection, DDL-as-constant, row->dataclass."""

    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
        return self._conn

    def migrate(self) -> "Database":
        """Create every table if absent. Idempotent by design (IF NOT EXISTS)."""
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()
        return self

    def execute(self, sql: str, params: tuple = ()) -> Optional[int]:
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur.lastrowid

    def executemany(self, sql: str, rows) -> None:
        self.conn.executemany(sql, rows)
        self.conn.commit()

    def query(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        return list(self.conn.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchone()

    def tables(self) -> List[str]:
        rows = self.query("SELECT name FROM sqlite_master WHERE type='table'")
        return sorted(r["name"] for r in rows)

    @staticmethod
    def row_to(cls, row: Optional[sqlite3.Row]):
        """Map a flat row onto a dataclass, ignoring unknown columns."""
        if row is None:
            return None
        return cls(**filter_fields(cls, {k: row[k] for k in row.keys()}))

    def scalar(self, sql: str, params: tuple = (), default: Any = 0) -> Any:
        """SUM()/COUNT() helper.

        WHY: SQLite returns None for SUM over an empty set. Callers that add up
        quantities would crash on a fresh install; this normalises None -> 0.
        """
        row = self.query_one(sql, params)
        if row is None:
            return default
        value = row[0]
        return default if value is None else value

    # -- time helpers (local wall-clock; the Jetson lives in the restaurant) --
    @staticmethod
    def now() -> str:
        return datetime.now().strftime(TS_FMT)

    @staticmethod
    def today() -> str:
        return date.today().strftime(D_FMT)

    @staticmethod
    def parse(ts: str) -> datetime:
        return datetime.strptime(ts, TS_FMT)

    @staticmethod
    def fmt(dt: datetime) -> str:
        return dt.strftime(TS_FMT)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
