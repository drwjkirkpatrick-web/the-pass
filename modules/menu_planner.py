"""The Pass — service planning (Prompt 08).

NOTE: this is where the menu meets reality. Given tonight's recipes and the
covers you expect, it produces the prep list, the shortfalls, what must be used
first, and substitution ideas.

WHY it stores the plan: the ordering phase (Phase 3) and the reminders (Phase 4)
both read the same plan, so everybody is working from one number.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from core.config import Config
from core.database import Database
from core.events import MENU_PLANNED, EventBus


@dataclass(frozen=True)
class Shortfall:
    ingredient_id: str
    name: str
    needed: float
    unit: str
    on_hand: float
    deficit: float
    reason: str  # missing | below_par | insufficient

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ServicePlan:
    service_date: str
    covers: Dict[str, float] = field(default_factory=dict)
    prep: List[Dict[str, Any]] = field(default_factory=list)
    shortfalls: List[Shortfall] = field(default_factory=list)
    use_first: List[Dict[str, Any]] = field(default_factory=list)
    substitutions: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "service_date": self.service_date,
            "covers": self.covers,
            "prep": self.prep,
            "shortfalls": [s.to_dict() for s in self.shortfalls],
            "use_first": self.use_first,
            "substitutions": self.substitutions,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ServicePlan":
        return cls(
            service_date=data.get("service_date", ""),
            covers=data.get("covers", {}),
            prep=data.get("prep", []),
            shortfalls=[Shortfall(**s) for s in data.get("shortfalls", [])],
            use_first=data.get("use_first", []),
            substitutions=data.get("substitutions", []),
        )

    def markdown(self) -> str:
        lines = [f"# Service plan — {self.service_date}", ""]
        lines.append(f"**Covers planned:** {int(sum(self.covers.values()))}")
        lines.append("")
        lines.append("## Prep")
        for item in self.prep:
            lines.append(f"- {item['name']}: {item['covers']:.0f} covers")
            for line in item.get("lines", []):
                lines.append(f"    - {line['ingredient_id']}: {line['qty']:.0f} {line['unit']}")
        lines.append("")
        lines.append("## Shortfalls (order these)")
        if not self.shortfalls:
            lines.append("- nothing short")
        for short in self.shortfalls:
            lines.append(
                f"- {short.name}: need {short.needed:.1f} {short.unit}, "
                f"have {short.on_hand:.1f}, short {short.deficit:.1f} ({short.reason})")
        lines.append("")
        lines.append("## Use first (going off)")
        for item in self.use_first:
            lines.append(f"- {item['name']} — {item.get('label', 'use soon')}")
        if self.substitutions:
            lines.append("")
            lines.append("## Substitution ideas")
            for sub in self.substitutions:
                lines.append(f"- instead of {sub['name']}: {', '.join(sub['suggestions'])}")
        return "\n".join(lines)


class MenuPlanner:
    def __init__(self, config: Config, db: Database, bus: Optional[EventBus] = None,
                 inventory: Any = None, recipe_book: Any = None) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.inventory = inventory
        self.recipe_book = recipe_book

    # -- planning ----------------------------------------------------------
    def plan_service(self, covers: Dict[str, float],
                     service_date: Optional[str] = None) -> ServicePlan:
        service_date = service_date or self.db.today()
        if not covers:
            plan = ServicePlan(service_date=service_date, covers={})
            self._store(plan)
            return plan

        requirements = self.recipe_book.ingredient_requirements(covers)

        prep: List[Dict[str, Any]] = []
        for recipe_id, count in covers.items():
            recipe = self.recipe_book.get(recipe_id)
            if recipe is None:
                continue
            prep.append({
                "recipe_id": recipe_id,
                "name": recipe.name,
                "covers": count,
                "lines": [{"ingredient_id": l["ingredient_id"], "qty": l["qty"],
                           "unit": l["unit"]}
                          for l in self.recipe_book.scale(recipe_id, count)],
            })

        shortfalls: List[Shortfall] = []
        use_first: List[Dict[str, Any]] = []
        for requirement in requirements:
            ingredient_id = requirement["ingredient_id"]
            needed = requirement["qty"]
            unit = requirement["unit"]
            ingredient = self.inventory.get(ingredient_id) if self.inventory else None
            if ingredient is None:
                shortfalls.append(Shortfall(
                    ingredient_id=ingredient_id, name=ingredient_id, needed=needed,
                    unit=unit, on_hand=0.0, deficit=needed, reason="missing"))
                continue
            # NOTE: recipe lines speak grams; the pantry speaks whatever unit the
            # delivery arrived in (often kg). Convert before comparing, or the
            # shortfall is arithmetic fiction.
            from modules.recipes import convert  # local import: avoid a cycle

            try:
                needed_in_unit = convert(needed, unit, ingredient.unit)
            except ValueError:
                needed_in_unit = needed
            on_hand = ingredient.on_hand
            deficit = round(needed_in_unit - on_hand, 4)
            if deficit > 0:
                reason = "below_par" if on_hand < ingredient.par_level else "insufficient"
                shortfalls.append(Shortfall(
                    ingredient_id=ingredient_id, name=ingredient.name,
                    needed=round(needed_in_unit, 4), unit=ingredient.unit,
                    on_hand=on_hand, deficit=deficit, reason=reason))
            freshness = self.inventory.freshness_score(ingredient)
            if freshness["label"] in ("use first", "use soon", "expired"):
                use_first.append({"ingredient_id": ingredient_id,
                                  "name": ingredient.name,
                                  "label": freshness["label"]})

        substitutions = self._substitution_ideas(shortfalls)
        plan = ServicePlan(service_date=service_date, covers=covers, prep=prep,
                           shortfalls=shortfalls, use_first=use_first,
                           substitutions=substitutions)
        self._store(plan)
        if self.bus is not None:
            self.bus.publish(MENU_PLANNED, {
                "service_date": service_date,
                "covers": covers,
                "shortfalls": [s.to_dict() for s in shortfalls],
            })
        return plan

    def get_plan(self, service_date: Optional[str] = None) -> Optional[ServicePlan]:
        service_date = service_date or self.db.today()
        row = self.db.query_one(
            "SELECT payload FROM service_plans WHERE service_date = ?", (service_date,))
        if row is None:
            return None
        return ServicePlan.from_dict(json.loads(row["payload"]))

    def latest_plan(self) -> Optional[ServicePlan]:
        """Today's plan if there is one, else the next upcoming, else the last.

        WHY: the chef asks "show me tonight" on the afternoon of a service whose
        plan was written for tomorrow's date, and a strict today-only lookup
        answers "no plan" — which is both wrong and alarming.
        """
        today = self.db.today()
        row = self.db.query_one(
            "SELECT service_date FROM service_plans WHERE service_date >= ?"
            " ORDER BY service_date LIMIT 1", (today,))
        if row is None:
            row = self.db.query_one(
                "SELECT service_date FROM service_plans WHERE service_date < ?"
                " ORDER BY service_date DESC LIMIT 1", (today,))
        return self.get_plan(row["service_date"]) if row else None

    def shortfalls(self, service_date: Optional[str] = None) -> List[Shortfall]:
        plan = self.get_plan(service_date)
        return plan.shortfalls if plan else []

    # -- internal ----------------------------------------------------------
    def _substitution_ideas(self, shortfalls: List[Shortfall]) -> List[Dict[str, Any]]:
        """What else could we cook with this? (global recipes, best effort)"""
        global_db = getattr(self.recipe_book, "global_db", None)
        if global_db is None or not global_db.available():
            return []
        ideas: List[Dict[str, Any]] = []
        for short in shortfalls[:5]:
            suggestions = [r.name for r in global_db.search(short.name)][:3]
            if suggestions:
                ideas.append({"ingredient_id": short.ingredient_id,
                              "name": short.name, "suggestions": suggestions})
        return ideas

    def _store(self, plan: ServicePlan) -> None:
        # re-planning the same date replaces the previous plan (idempotent)
        self.db.execute(
            "INSERT OR REPLACE INTO service_plans (service_date, payload, created_at)"
            " VALUES (?,?,?)",
            (plan.service_date, json.dumps(plan.to_dict()), self.db.now()),
        )
