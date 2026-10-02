"""The Pass — recipes, portion scaling and requirement aggregation (Prompt 07).

NOTE: this is the module the chef orders against. Scale a recipe to the covers
you expect, add up the ingredients across the whole menu, and you have the
shopping math that Phase 3 turns into purchase orders.

WHY unit conversion lives here: quantities arrive in g/kg/oz/lb/each and the
kitchen thinks in portions. One conversion table, one place to be right.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Union

from core.config import Config
from core.database import Database
from core.events import EventBus
from core.types import Recipe, RecipeLine, filter_fields

# unit -> (dimension, factor to the base unit of that dimension)
UNITS: Dict[str, tuple] = {
    "mg": ("mass", 0.001), "g": ("mass", 1.0), "kg": ("mass", 1000.0),
    "oz": ("mass", 28.3495), "lb": ("mass", 453.592),
    "ml": ("volume", 1.0), "cl": ("volume", 10.0), "l": ("volume", 1000.0),
    "each": ("count", 1.0), "ea": ("count", 1.0), "unit": ("count", 1.0),
}


def convert(qty: float, from_unit: str, to_unit: str) -> float:
    """Convert between units of the same dimension; raise on nonsense."""
    src = UNITS.get((from_unit or "").lower())
    dst = UNITS.get((to_unit or "").lower())
    if src is None:
        raise ValueError(f"unknown unit: {from_unit}")
    if dst is None:
        raise ValueError(f"unknown unit: {to_unit}")
    if src[0] != dst[0]:
        raise ValueError(f"cannot convert {from_unit} -> {to_unit}")
    return qty * src[1] / dst[1]


class RecipeBook:
    def __init__(self, config: Config, db: Database,
                 bus: Optional[EventBus] = None, global_db: Any = None) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.global_db = global_db  # optional GlobalRecipeDB

    # -- persistence -------------------------------------------------------
    def save(self, recipe: Recipe) -> Recipe:
        self.db.execute(
            "INSERT OR REPLACE INTO recipes (id, name, portions, station,"
            " technique_tags, plating_notes, menu_price, source, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,COALESCE((SELECT created_at FROM recipes"
            " WHERE id=?), ?))",
            (recipe.id, recipe.name, recipe.portions, recipe.station,
             ",".join(recipe.technique_tags), recipe.plating_notes,
             recipe.menu_price, recipe.source, recipe.id, self.db.now()),
        )
        self.db.execute("DELETE FROM recipe_lines WHERE recipe_id = ?", (recipe.id,))
        for line in recipe.lines:
            self.db.execute(
                "INSERT OR REPLACE INTO recipe_lines (recipe_id, ingredient_id,"
                " qty_per_portion, unit, prep_note) VALUES (?,?,?,?,?)",
                (recipe.id, line.ingredient_id, line.qty_per_portion, line.unit,
                 line.prep_note),
            )
        return recipe

    def get(self, recipe_id: str) -> Optional[Recipe]:
        if recipe_id.startswith("g:") and self.global_db is not None:
            return self.global_db.get(recipe_id[2:])
        row = self.db.query_one("SELECT * FROM recipes WHERE id = ?", (recipe_id,))
        if row is None:
            return None
        return self._hydrate(row)

    def delete(self, recipe_id: str) -> None:
        self.db.execute("DELETE FROM recipe_lines WHERE recipe_id = ?", (recipe_id,))
        self.db.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))

    def local_recipes(self) -> List[Recipe]:
        rows = self.db.query("SELECT * FROM recipes ORDER BY name")
        return [self._hydrate(r) for r in rows]

    def all_recipes(self) -> List[Recipe]:
        """Local first, then global (deduplicated by name)."""
        local = self.local_recipes()
        seen = {r.name.lower() for r in local}
        out = list(local)
        if self.global_db is not None and self.global_db.available():
            for recipe in self.global_db.all_recipes():
                if recipe.name.lower() not in seen:
                    out.append(recipe)
                    seen.add(recipe.name.lower())
        return out

    def search(self, name: str) -> List[Recipe]:
        needle = (name or "").lower()
        hits = [r for r in self.local_recipes() if needle in r.name.lower()]
        if self.global_db is not None and self.global_db.available():
            local_names = {r.name.lower() for r in hits}
            for recipe in self.global_db.search(name):
                if recipe.name.lower() not in local_names:
                    hits.append(recipe)
        return hits

    # -- math --------------------------------------------------------------
    def scale(self, recipe: Union[Recipe, str], portions: float) -> List[Dict[str, Any]]:
        """Total quantities needed to produce ``portions`` plates."""
        resolved = self._resolve(recipe)
        if resolved is None:
            return []
        return [{"ingredient_id": line.ingredient_id,
                 "qty": round(line.qty_per_portion * portions, 4),
                 "unit": line.unit,
                 "prep_note": line.prep_note}
                for line in resolved.lines]

    def portion_size(self, recipe: Union[Recipe, str]) -> Dict[str, Any]:
        """Per-plate size: grams of solid/volume mass, plus counted items."""
        resolved = self._resolve(recipe)
        if resolved is None:
            return {"grams": 0.0, "count_items": 0, "lines": 0}
        grams = 0.0
        counted = 0
        for line in resolved.lines:
            spec = UNITS.get((line.unit or "").lower())
            if spec is None:
                continue
            if spec[0] == "count":
                counted += int(line.qty_per_portion)
            else:
                grams += convert(line.qty_per_portion, line.unit, "g")
        return {"grams": round(grams, 2), "count_items": counted,
                "lines": len(resolved.lines)}

    def portion_cost(self, recipe: Union[Recipe, str], inventory: Any) -> Dict[str, Any]:
        """Cost per portion from live ingredient prices (missing prices skipped)."""
        resolved = self._resolve(recipe)
        if resolved is None or inventory is None:
            return {"total": 0.0, "per_portion": 0.0, "missing_prices": []}
        total = 0.0
        missing: List[str] = []
        for line in resolved.lines:
            ingredient = inventory.get(line.ingredient_id)
            if ingredient is None or not ingredient.unit_cost:
                missing.append(line.ingredient_id)
                continue
            try:
                qty = convert(line.qty_per_portion, line.unit, ingredient.unit)
            except ValueError:
                missing.append(line.ingredient_id)
                continue
            total += qty * ingredient.unit_cost
        portions = resolved.portions or 1.0
        # NOTE: recipe lines hold per-portion quantities, so the running sum IS
        # the cost of one plate; the recipe total is that times its yield.
        per_portion = total
        return {"total": round(per_portion * portions, 2),
                "per_portion": round(per_portion, 2),
                "missing_prices": missing}

    def ingredient_requirements(self, plan: Dict[str, float]) -> List[Dict[str, Any]]:
        """{recipe_id: covers} -> aggregated ingredient totals for the menu.

        Same ingredient across recipes accumulates; incompatible units are kept
        as separate buckets rather than silently mis-added.
        """
        buckets: Dict[tuple, Dict[str, Any]] = {}
        for recipe_id, covers in plan.items():
            resolved = self._resolve(recipe_id)
            if resolved is None:
                continue
            for line in resolved.lines:
                key = (line.ingredient_id, (line.unit or "").lower())
                bucket = buckets.setdefault(key, {
                    "ingredient_id": line.ingredient_id,
                    "unit": line.unit,
                    "qty": 0.0,
                    "by_recipe": {},
                })
                qty = line.qty_per_portion * covers
                bucket["qty"] += qty
                bucket["by_recipe"][recipe_id] = (
                    bucket["by_recipe"].get(recipe_id, 0.0) + qty)
        out = []
        for bucket in buckets.values():
            bucket["qty"] = round(bucket["qty"], 4)
            bucket["by_recipe"] = {k: round(v, 4) for k, v in bucket["by_recipe"].items()}
            out.append(bucket)
        return sorted(out, key=lambda b: -b["qty"])

    # -- internal ----------------------------------------------------------
    def _resolve(self, recipe: Union[Recipe, str]) -> Optional[Recipe]:
        return recipe if isinstance(recipe, Recipe) else self.get(recipe)

    def _hydrate(self, row) -> Recipe:
        data = {k: row[k] for k in row.keys()}
        data["technique_tags"] = [t for t in (data.get("technique_tags") or "").split(",") if t]
        lines = self.db.query(
            "SELECT * FROM recipe_lines WHERE recipe_id = ?", (row["id"],))
        recipe = Recipe(**filter_fields(Recipe, data))
        return Recipe(**{**recipe.to_dict(), "lines": [
            RecipeLine(ingredient_id=l["ingredient_id"],
                       qty_per_portion=l["qty_per_portion"],
                       unit=l["unit"], prep_note=l["prep_note"]) for l in lines]})
