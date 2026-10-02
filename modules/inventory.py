"""The Pass — ingredient inventory, freshness and 86 tracking (Prompt 05).

NOTE: this is the single source of truth for "what do we have right now".
Ordering, menu planning, and the FOH 86 warning all read from here.

WHY the shelf-life table is small and editable: it is structure (a default per
category), not a food encyclopedia. Unknown categories get a conservative
default rather than an error.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from core.config import Config
from core.database import D_FMT, Database
from core.events import INGREDIENTS_UPDATED, EventBus
from core.types import Ingredient, Recipe, filter_fields

# Days a category typically stays good after delivery. Structure, not gospel.
SHELF_LIFE_DAYS: Dict[str, int] = {
    "produce": 5, "protein": 3, "seafood": 2, "dairy": 10, "dry": 180,
    "frozen": 90, "bakery": 3, "general": 7,
}
DEFAULT_SHELF_LIFE = SHELF_LIFE_DAYS["general"]


class Inventory:
    def __init__(self, config: Config, db: Database,
                 bus: Optional[EventBus] = None) -> None:
        self.config = config
        self.db = db
        self.bus = bus

    # -- mapping -----------------------------------------------------------
    def _to_ingredient(self, row) -> Optional[Ingredient]:
        if row is None:
            return None
        data = {k: row[k] for k in row.keys()}
        raw_photos = data.get("photo_ids") or ""
        data["photo_ids"] = [p for p in str(raw_photos).split(",") if p]
        return Ingredient(**filter_fields(Ingredient, data))

    # -- writes ------------------------------------------------------------
    def upsert(self, ingredient: Ingredient) -> Ingredient:
        shelf = ingredient.shelf_life_days or SHELF_LIFE_DAYS.get(
            ingredient.category, DEFAULT_SHELF_LIFE
        )
        stored = replace(ingredient, shelf_life_days=shelf)
        exists = self.db.query_one("SELECT id FROM ingredients WHERE id = ?",
                                   (stored.id,))
        if exists is None:
            self.db.execute(
                "INSERT INTO ingredients (id, name, category, unit, par_level,"
                " on_hand, unit_cost, shelf_life_days, freshness_date, photo_ids,"
                " supplier_id, status, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (stored.id, stored.name, stored.category, stored.unit,
                 stored.par_level, stored.on_hand, stored.unit_cost,
                 stored.shelf_life_days, stored.freshness_date,
                 ",".join(stored.photo_ids), stored.supplier_id, stored.status,
                 self.db.now(), self.db.now()),
            )
        else:
            self.db.execute(
                "UPDATE ingredients SET name=?, category=?, unit=?, par_level=?,"
                " on_hand=?, unit_cost=?, shelf_life_days=?, freshness_date=?,"
                " photo_ids=?, supplier_id=?, status=?, updated_at=? WHERE id=?",
                (stored.name, stored.category, stored.unit, stored.par_level,
                 stored.on_hand, stored.unit_cost, stored.shelf_life_days,
                 stored.freshness_date, ",".join(stored.photo_ids),
                 stored.supplier_id, stored.status, self.db.now(), stored.id),
            )
        self._announce(stored, "upsert")
        return stored

    def add(self, name: str, category: str = "general", unit: str = "each",
            par_level: float = 0.0, on_hand: float = 0.0, unit_cost: float = 0.0,
            freshness_date: Optional[str] = None, id: Optional[str] = None) -> Ingredient:
        ingredient = Ingredient(
            id=id or f"i-{abs(hash((name, self.db.now()))) % 10 ** 8}",
            name=name.strip().lower(), category=category, unit=unit,
            par_level=par_level, on_hand=on_hand, unit_cost=unit_cost,
            freshness_date=freshness_date or self.db.today(),
        )
        return self.upsert(ingredient)

    def adjust_qty(self, ingredient_id: str, delta: float) -> Optional[Ingredient]:
        current = self.get(ingredient_id)
        if current is None:
            return None
        return self.upsert(replace(current, on_hand=round(current.on_hand + delta, 4)))

    def set_par(self, ingredient_id: str, par_level: float) -> Optional[Ingredient]:
        current = self.get(ingredient_id)
        if current is None:
            return None
        return self.upsert(replace(current, par_level=par_level))

    def set_supplier(self, ingredient_id: str,
                     supplier_id: str) -> Optional[Ingredient]:
        """Remember who this ingredient is bought from (Phase 3 groups by it)."""
        current = self.get(ingredient_id)
        if current is None:
            return None
        return self.upsert(replace(current, supplier_id=supplier_id))

    def flag_eighty_six(self, ingredient_id: str, source: str = "chef") -> Optional[Ingredient]:
        """Take an ingredient off the menu. Logged — the journal reads this."""
        current = self.get(ingredient_id)
        if current is None:
            return None
        updated = self.upsert(replace(current, status="eighty_six"))
        self.db.execute(
            "INSERT INTO eighty_six_log (ingredient_id, ts, service_date, source)"
            " VALUES (?,?,?,?)",
            (ingredient_id, self.db.now(), self.db.today(), source),
        )
        return updated

    def clear_eighty_six(self, ingredient_id: str) -> Optional[Ingredient]:
        current = self.get(ingredient_id)
        if current is None:
            return None
        return self.upsert(replace(current, status="active"))

    # -- reads -------------------------------------------------------------
    def get(self, ingredient_id: str) -> Optional[Ingredient]:
        return self._to_ingredient(
            self.db.query_one("SELECT * FROM ingredients WHERE id = ?", (ingredient_id,))
        )

    def by_name(self, name: str) -> Optional[Ingredient]:
        needle = (name or "").strip().lower()
        row = self.db.query_one(
            "SELECT * FROM ingredients WHERE lower(name) = ? LIMIT 1", (needle,)
        )
        if row is None:
            row = self.db.query_one(
                "SELECT * FROM ingredients WHERE lower(name) LIKE ? LIMIT 1",
                (f"%{needle}%",),
            )
        return self._to_ingredient(row)

    def all(self) -> List[Ingredient]:
        rows = self.db.query("SELECT * FROM ingredients ORDER BY name")
        return [self._to_ingredient(r) for r in rows]

    def is_eighty_six(self, ingredient_id: str) -> bool:
        row = self.db.query_one("SELECT status FROM ingredients WHERE id = ?",
                                (ingredient_id,))
        return bool(row and row["status"] == "eighty_six")

    def list_available(self) -> List[Ingredient]:
        """On hand, not 86'd, not expired."""
        return [i for i in self.all()
                if i.on_hand > 0 and i.status != "eighty_six"
                and not self.is_expired(i)]

    def below_par(self) -> List[Ingredient]:
        return [i for i in self.all() if i.on_hand < i.par_level]

    def expiring_within(self, days: int = 2) -> List[Ingredient]:
        out = []
        for ingredient in self.all():
            left = self.days_left(ingredient)
            if left is not None and 0 < left <= days:
                out.append(ingredient)
        return out

    # -- freshness ---------------------------------------------------------
    def days_left(self, ingredient: Ingredient) -> Optional[int]:
        if not ingredient.freshness_date:
            return None
        try:
            delivered = datetime.strptime(ingredient.freshness_date, D_FMT).date()
        except ValueError:
            return None
        shelf = ingredient.shelf_life_days or SHELF_LIFE_DAYS.get(
            ingredient.category, DEFAULT_SHELF_LIFE
        )
        return (delivered + timedelta(days=shelf) - date.today()).days

    def is_expired(self, ingredient: Ingredient) -> bool:
        left = self.days_left(ingredient)
        return left is not None and left <= 0

    def freshness_score(self, ingredient: Ingredient) -> Dict[str, Any]:
        """0-100 plus a label the kitchen actually reads."""
        left = self.days_left(ingredient)
        shelf = ingredient.shelf_life_days or SHELF_LIFE_DAYS.get(
            ingredient.category, DEFAULT_SHELF_LIFE
        )
        if left is None or shelf <= 0:
            return {"score": 50, "label": "unknown", "days_left": left}
        if left <= 0:
            return {"score": 0, "label": "expired", "days_left": left}
        score = int(max(0, min(100, round(100.0 * left / shelf))))
        if score >= 70:
            label = "fresh"
        elif score >= 40:
            label = "use soon"
        else:
            label = "use first"
        return {"score": score, "label": label, "days_left": left}

    # -- cross-module helpers ---------------------------------------------
    def suggest_uses(self, ingredient_id: str,
                     recipes: Optional[List[Recipe]] = None) -> List[str]:
        """Which recipes use this ingredient (local recipes if not supplied)."""
        if recipes is None:
            from modules.recipes import RecipeBook  # local import: avoid cycle

            recipes = RecipeBook(self.config, self.db, self.bus).all_recipes()
        return [r.name for r in recipes
                if any(line.ingredient_id == ingredient_id for line in r.lines)]

    def summary(self) -> Dict[str, Any]:
        return {
            "ingredients": self.db.scalar("SELECT COUNT(*) FROM ingredients"),
            "available": len(self.list_available()),
            "below_par": len(self.below_par()),
            "eighty_six": self.db.scalar(
                "SELECT COUNT(*) FROM ingredients WHERE status = 'eighty_six'"),
            "expiring_2d": len(self.expiring_within(2)),
            "eighty_six_events_today": self.db.scalar(
                "SELECT COUNT(*) FROM eighty_six_log WHERE service_date = ?",
                (self.db.today(),)),
        }

    # -- internal ----------------------------------------------------------
    def _announce(self, ingredient: Ingredient, action: str) -> None:
        if self.bus is None:
            return
        self.bus.publish(INGREDIENTS_UPDATED, {
            "action": action,
            "ingredient": ingredient.to_dict(),
            "below_par": ingredient.on_hand < ingredient.par_level,
        })
