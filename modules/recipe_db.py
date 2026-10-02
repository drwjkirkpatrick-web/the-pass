"""The Pass — read-only bridge to the global recipe database (Prompt 06).

NOTE: the external recipe database is somebody else's schema. This module
probes it at runtime (tables + columns) and adapts whatever it finds into our
Recipe shape, instead of assuming column names.

WHY degradation: if the DB is missing or has an unexpected layout, the agent
logs and carries on with locally-entered recipes. A missing optional
integration must never stop the kitchen.
"""
from __future__ import annotations

import os
import re
import sqlite3
from typing import Any, Dict, List, Optional

from core.config import Config
from core.database import Database
from core.events import EventBus
from core.types import Recipe, RecipeLine

NAME_COLUMNS = ("name", "title", "recipe_name", "recipe", "dish", "dish_name")
ID_COLUMNS = ("id", "recipe_id", "uuid", "slug")
PORTION_COLUMNS = ("servings", "yield", "portions", "serves", "yield_text")
TAG_COLUMNS = ("tags", "category", "cuisine", "type", "course")
NOTE_COLUMNS = ("instructions", "steps", "notes", "description", "method")
CHILD_RECIPE_COLUMNS = ("recipe_id", "recipe", "dish_id", "parent_id")
CHILD_NAME_COLUMNS = ("ingredient", "ingredient_name", "name", "item", "component")
CHILD_QTY_COLUMNS = ("quantity", "qty", "amount", "measure")
CHILD_UNIT_COLUMNS = ("unit", "units", "uom", "measure_unit")


def _first(row_keys, candidates) -> Optional[str]:
    lowered = {k.lower(): k for k in row_keys}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    return None


def parse_quantity(value: Any) -> float:
    """'2 cups' -> 2.0 ; None -> 0.0 ; 'to taste' -> 0.0 (never raises)."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d+(?:\.\d+)?", str(value))
    return float(match.group(0)) if match else 0.0


def slugify(name: str) -> str:
    return "g-" + re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


class GlobalRecipeDB:
    """Adapter over an external recipe database. Read-only, always optional."""

    def __init__(self, config: Config, db: Optional[Database] = None,
                 bus: Optional[EventBus] = None) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.warnings: List[str] = []

    # -- availability ------------------------------------------------------
    def available(self) -> bool:
        path = self.config.global_recipes_db
        return bool(path) and os.path.exists(path)

    def _connect(self) -> sqlite3.Connection:
        # Read-only URI: the agent must never write to the shared recipe DB.
        conn = sqlite3.connect(f"file:{self.config.global_recipes_db}?mode=ro",
                               uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def _warn(self, message: str) -> None:
        self.warnings.append(message)

    # -- introspection -----------------------------------------------------
    def layout(self) -> Dict[str, List[str]]:
        """{table_name: [columns]} for the external DB (empty if unavailable)."""
        if not self.available():
            self._warn("global recipe DB not configured or missing")
            return {}
        with self._connect() as conn:
            tables = [r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            out: Dict[str, List[str]] = {}
            for table in tables:
                cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
                out[table] = cols
            return out

    def recipe_table(self) -> Optional[str]:
        """The table that looks like it holds recipes."""
        for table, columns in self.layout().items():
            if table.startswith("sqlite_"):
                continue
            if _first(columns, NAME_COLUMNS):
                return table
        return None

    def child_table(self, recipe_table: Optional[str] = None) -> Optional[str]:
        """The table that looks like it holds recipe -> ingredient lines."""
        recipe_table = recipe_table or self.recipe_table()
        for table, columns in self.layout().items():
            if table in (recipe_table, "sqlite_sequence"):
                continue
            if _first(columns, CHILD_RECIPE_COLUMNS) and _first(columns, CHILD_NAME_COLUMNS):
                return table
        return None

    # -- normalisation -----------------------------------------------------
    def normalize_recipe(self, row: Any, columns: List[str],
                         lines: Optional[List[RecipeLine]] = None) -> Optional[Recipe]:
        """Map one external row onto our Recipe. Returns None if unusable."""
        keys = list(row.keys())
        name_col = _first(keys, NAME_COLUMNS)
        name = row[name_col] if name_col else None
        if not name:
            return None  # quarantine: unusable row, skip rather than crash
        id_col = _first(keys, ID_COLUMNS)
        raw_id = row[id_col] if id_col else slugify(str(name))
        portion_col = _first(keys, PORTION_COLUMNS)
        tag_col = _first(keys, TAG_COLUMNS)
        note_col = _first(keys, NOTE_COLUMNS)
        tags = []
        if tag_col and row[tag_col]:
            tags = [t.strip() for t in str(row[tag_col]).split(",") if t.strip()]
        return Recipe(
            id=f"g:{raw_id}",
            name=str(name),
            portions=parse_quantity(row[portion_col]) or 1.0 if portion_col else 1.0,
            station="line",
            technique_tags=tags,
            plating_notes=str(row[note_col])[:500] if note_col and row[note_col] else "",
            menu_price=0.0,
            lines=lines or [],
            source="global",
        )

    def _lines_for(self, recipe_table: str, child: Optional[str],
                   recipe_id: Any) -> List[RecipeLine]:
        if not child or recipe_id is None:
            return []
        with self._connect() as conn:
            columns = [r["name"] for r in conn.execute(f"PRAGMA table_info({child})")]
            link = _first(columns, CHILD_RECIPE_COLUMNS)
            name_col = _first(columns, CHILD_NAME_COLUMNS)
            qty_col = _first(columns, CHILD_QTY_COLUMNS)
            unit_col = _first(columns, CHILD_UNIT_COLUMNS)
            if not (link and name_col):
                return []
            rows = conn.execute(f"SELECT * FROM {child} WHERE {link} = ?",
                                (recipe_id,)).fetchall()
        lines: List[RecipeLine] = []
        for row in rows:
            ingredient = row[name_col]
            if not ingredient:
                continue
            lines.append(RecipeLine(
                ingredient_id=slugify(str(ingredient)),
                qty_per_portion=parse_quantity(row[qty_col]) if qty_col else 0.0,
                unit=str(row[unit_col]).lower() if unit_col and row[unit_col] else "g",
                prep_note=str(ingredient),
            ))
        return lines

    # -- public API --------------------------------------------------------
    def search(self, name: str, limit: int = 20) -> List[Recipe]:
        if not self.available():
            self._warn("global recipe search skipped: DB unavailable")
            return []
        table = self.recipe_table()
        if not table:
            return []
        columns = self.layout()[table]
        name_col = _first(columns, NAME_COLUMNS)
        child = self.child_table(table)
        out: List[Recipe] = []
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE lower({name_col}) LIKE ? LIMIT ?",
                (f"%{name.lower()}%", limit)).fetchall()
        for row in rows:
            id_col = _first(list(row.keys()), ID_COLUMNS)
            recipe = self.normalize_recipe(
                row, columns, self._lines_for(table, child, row[id_col] if id_col else None))
            if recipe is not None:
                out.append(recipe)
        return out

    def get(self, recipe_id: str) -> Optional[Recipe]:
        if not self.available():
            self._warn("global recipe lookup skipped: DB unavailable")
            return None
        table = self.recipe_table()
        if not table:
            return None
        columns = self.layout()[table]
        id_col = _first(columns, ID_COLUMNS)
        if not id_col:
            return None
        child = self.child_table(table)
        with self._connect() as conn:
            row = conn.execute(f"SELECT * FROM {table} WHERE {id_col} = ?",
                               (recipe_id,)).fetchone()
        if row is None:
            return None
        return self.normalize_recipe(row, columns, self._lines_for(table, child, recipe_id))

    def all_recipes(self, limit: int = 500) -> List[Recipe]:
        if not self.available():
            self._warn("global recipe listing skipped: DB unavailable")
            return []
        table = self.recipe_table()
        if not table:
            return []
        columns = self.layout()[table]
        child = self.child_table(table)
        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM {table} LIMIT ?", (limit,)).fetchall()
        out: List[Recipe] = []
        for row in rows:
            id_col = _first(list(row.keys()), ID_COLUMNS)
            recipe = self.normalize_recipe(
                row, columns, self._lines_for(table, child, row[id_col] if id_col else None))
            if recipe is not None:
                out.append(recipe)
        return out
