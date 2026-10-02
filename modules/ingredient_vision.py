"""The Pass — ingredient photo intake and curation (Prompt 04).

NOTE: the agent looks at photos of what came in (delivery boxes, walk-in
shelves) and *proposes* an ingredient list. It never writes to live inventory
by itself — a human approves the draft first.

WHY the VisionAnalyzer interface: real computer vision is a separate problem.
This module ships a deterministic mock so the whole program is testable today,
and a real model drops in behind the same one-method contract later.
"""
from __future__ import annotations

import os
import re
import shutil
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from core.config import Config
from core.database import Database
from core.events import INGREDIENTS_UPDATED, EventBus
from core.types import Ingredient, filter_fields

# Words that describe quality rather than the ingredient itself.
QUALITY_WORDS = {"crisp", "wilted", "fresh", "bruised", "frozen", "soft",
                 "dry", "prime", "spotty", "excellent", "poor"}

# Rough category guesses — the chef corrects these during approval.
CATEGORY_HINTS = {
    "tomato": "produce", "lettuce": "produce", "carrot": "produce",
    "onion": "produce", "herb": "produce", "lemon": "produce",
    "beef": "protein", "pork": "protein", "chicken": "protein",
    "lamb": "protein", "duck": "protein", "fish": "seafood",
    "salmon": "seafood", "scallop": "seafood", "shrimp": "seafood",
    "butter": "dairy", "cream": "dairy", "milk": "dairy", "cheese": "dairy",
    "flour": "dry", "rice": "dry", "salt": "dry", "sugar": "dry",
    "bread": "bakery", "stock": "dry",
}


def guess_category(name: str) -> str:
    lowered = (name or "").lower()
    for hint, category in CATEGORY_HINTS.items():
        if hint in lowered:
            return category
    return "general"


@dataclass(frozen=True)
class IngredientObservation:
    """One thing the analyzer thinks it saw in one photo."""
    name: str
    confidence: float
    qty: float = 0.0
    unit: str = "each"
    quality_note: str = ""


class VisionAnalyzer(ABC):
    """The one-method contract every vision backend implements."""

    @abstractmethod
    def analyze(self, photo_path: str) -> List[IngredientObservation]:
        raise NotImplementedError


class MockVisionAnalyzer(VisionAnalyzer):
    """Deterministic analyzer: reads the filename, not the pixels.

    Filename grammar: ``heirloom_tomato_4kg_crisp.jpg``
      -> name "heirloom tomato", qty 4, unit kg, quality note "crisp"

    A ``lo``/``low``/``blurry`` token forces low confidence so the human-review
    path can be exercised on purpose (e.g. ``tomato_2kg_lo.jpg``).
    """

    QTY_RE = re.compile(r"(\d+(?:\.\d+)?)(kg|g|l|ml|lb|oz|each|ea|cs)")

    def analyze(self, photo_path: str) -> List[IngredientObservation]:
        stem = os.path.splitext(os.path.basename(photo_path))[0]
        tokens = [t for t in re.split(r"[_\s]+", stem) if t]
        qty, unit, note, confidence = 0.0, "each", "", 0.9
        name_tokens: List[str] = []
        for token in tokens:
            match = self.QTY_RE.fullmatch(token.lower())
            if match:
                qty = float(match.group(1))
                unit = "each" if match.group(2) in ("ea", "each", "cs") else match.group(2)
                continue
            if token.lower() in ("lo", "low", "blurry"):
                confidence = 0.35
                continue
            name_tokens.append(token)
        if name_tokens and name_tokens[-1].lower() in QUALITY_WORDS:
            note = name_tokens.pop().lower()
        name = " ".join(name_tokens).lower() or "unknown"
        return [IngredientObservation(name=name, confidence=confidence, qty=qty,
                                      unit=unit, quality_note=note)]


class RealVisionAnalyzer(VisionAnalyzer):
    """Extension point — drop a real model in here (local or hosted)."""

    def analyze(self, photo_path: str) -> List[IngredientObservation]:
        raise NotImplementedError(
            "Wire a real vision backend here; see docs/ARCHITECTURE.md"
        )


class IngredientCurator:
    """Photos in -> a *draft* ingredient list, awaiting human approval."""

    def __init__(self, config: Config, db: Database, bus: Optional[EventBus] = None,
                 analyzer: Optional[VisionAnalyzer] = None,
                 inventory: Any = None) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.analyzer = analyzer or (
            MockVisionAnalyzer() if config.mock_vision else RealVisionAnalyzer()
        )
        # Optional collaborator: when wired, approval goes through Inventory
        # (which owns shelf-life rules). Standalone, we write rows directly.
        self.inventory = inventory

    # -- photo storage -----------------------------------------------------
    def store_photo(self, photo_path: str) -> str:
        """Copy a photo into the agent's photo store; return the stored path."""
        target_dir = os.path.join(self.config.photo_dir, "ingredients")
        os.makedirs(target_dir, exist_ok=True)
        stamp = self.db.now().replace(":", "").replace("-", "")
        name = f"{stamp}_{os.path.basename(photo_path)}"
        target = os.path.join(target_dir, name)
        shutil.copyfile(photo_path, target)
        return target

    # -- draft lifecycle ---------------------------------------------------
    def ingest(self, photo_paths: Sequence[str], store: bool = False) -> Dict[str, Any]:
        draft_id = f"draft-{uuid.uuid4().hex[:8]}"
        self.db.execute(
            "INSERT INTO ingredient_drafts (id, status, created_at) VALUES (?,?,?)",
            (draft_id, "proposed", self.db.now()),
        )
        lines: List[Dict[str, Any]] = []
        for path in photo_paths:
            stored = self.store_photo(path) if store else path
            for obs in self.analyzer.analyze(path):
                flagged = 1 if obs.confidence < self.config.conf_threshold else 0
                line_id = f"dl-{uuid.uuid4().hex[:8]}"
                self.db.execute(
                    "INSERT INTO draft_lines (id, draft_id, name, category, unit,"
                    " qty, confidence, quality_note, photo_path, flagged)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (line_id, draft_id, obs.name, guess_category(obs.name),
                     obs.unit, obs.qty, obs.confidence, obs.quality_note, stored,
                     flagged),
                )
                lines.append({
                    "id": line_id, "name": obs.name, "qty": obs.qty,
                    "unit": obs.unit, "confidence": obs.confidence,
                    "quality_note": obs.quality_note, "photo_path": stored,
                    "flagged": bool(flagged),
                })
        return {
            "draft_id": draft_id,
            "status": "proposed",
            "lines": lines,
            "flagged": sum(1 for line in lines if line["flagged"]),
        }

    def drafts(self, status: str = "proposed") -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM ingredient_drafts WHERE status = ? ORDER BY created_at",
            (status,),
        )
        return [dict(r) for r in rows]

    def draft_lines(self, draft_id: str) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM draft_lines WHERE draft_id = ? ORDER BY name", (draft_id,)
        )
        return [dict(r) for r in rows]

    def approve(self, draft_id: str,
                edits: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Ingredient]:
        """THE HUMAN GATE. Promote a draft into live inventory.

        ``edits`` maps draft-line id -> corrected fields
        (name / qty / unit / category / par_level / unit_cost).

        Duplicate names merge: two lines of "heirloom tomato" become one
        ingredient whose quantity is the sum.
        """
        edits = edits or {}
        merged: Dict[str, Dict[str, Any]] = {}
        for line in self.draft_lines(draft_id):
            fix = edits.get(line["id"], {})
            name = (fix.get("name") or line["name"]).strip().lower()
            if not name:
                continue
            bucket = merged.setdefault(name, {
                "qty": 0.0, "unit": fix.get("unit") or line["unit"],
                "category": fix.get("category") or line["category"],
                "par_level": fix.get("par_level", 0.0),
                "unit_cost": fix.get("unit_cost", 0.0),
                "photo_ids": [],
            })
            bucket["qty"] += float(fix.get("qty", line["qty"]) or 0.0)
            if line["photo_path"]:
                bucket["photo_ids"].append(line["photo_path"])

        written: List[Ingredient] = []
        for name, data in merged.items():
            ingredient = Ingredient(
                id=f"i-{uuid.uuid4().hex[:8]}",
                name=name,
                category=data["category"],
                unit=data["unit"],
                par_level=float(data["par_level"] or 0.0),
                on_hand=float(data["qty"] or 0.0),
                unit_cost=float(data["unit_cost"] or 0.0),
                freshness_date=self.db.today(),
                photo_ids=data["photo_ids"],
            )
            if self.inventory is not None:
                written.append(self.inventory.upsert(ingredient))
            else:
                self._write_ingredient(ingredient)
                written.append(ingredient)

        self.db.execute("UPDATE ingredient_drafts SET status = 'approved' WHERE id = ?",
                        (draft_id,))
        if self.bus is not None:
            self.bus.publish(INGREDIENTS_UPDATED, {
                "action": "draft_approved",
                "draft_id": draft_id,
                "ingredients": [i.name for i in written],
            })
        return written

    def reject(self, draft_id: str, reason: str = "") -> None:
        self.db.execute(
            "UPDATE ingredient_drafts SET status = 'rejected' WHERE id = ?", (draft_id,)
        )

    def _write_ingredient(self, ingredient: Ingredient) -> None:
        self.db.execute(
            "INSERT INTO ingredients (id, name, category, unit, par_level, on_hand,"
            " unit_cost, shelf_life_days, freshness_date, photo_ids, supplier_id,"
            " status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ingredient.id, ingredient.name, ingredient.category, ingredient.unit,
             ingredient.par_level, ingredient.on_hand, ingredient.unit_cost,
             ingredient.shelf_life_days, ingredient.freshness_date,
             ",".join(ingredient.photo_ids), ingredient.supplier_id,
             ingredient.status, self.db.now(), self.db.now()),
        )
