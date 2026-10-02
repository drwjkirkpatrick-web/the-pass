"""The Pass — FOH reminders (Prompt 15).

NOTE: same engine as BOH (Prompt 14) — this module owns only the front of
house's vocabulary and one clever hook: the 86 warning.

WHY the 86 warning matters: the worst service moment is finding out at the
table that a dish is gone. This fires before the kitchen has to pull it.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from core.config import Config
from core.database import TS_FMT, Database
from core.events import INGREDIENTS_UPDATED, EventBus
from core.types import Reminder
from modules.reminders_boh import ReminderEngine


class FOHReminders:
    def __init__(self, config: Config, db: Database,
                 bus: Optional[EventBus] = None,
                 engine: Optional[ReminderEngine] = None,
                 inventory: Any = None) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.engine = engine or ReminderEngine(config, db, bus)
        self.inventory = inventory
        if bus is not None:
            bus.subscribe(INGREDIENTS_UPDATED, self._on_ingredients_updated)

    # -- scheduled reminders ----------------------------------------------
    def briefing(self, headline: str, at: Optional[str] = None,
                 when: Optional[datetime] = None) -> Reminder:
        when = when or datetime.now()
        start = datetime.combine(when.date(),
                                 datetime.strptime(self.config.service_start,
                                                   "%H:%M").time())
        fire_at = at or (start - timedelta(minutes=30)).strftime(TS_FMT)
        return self.engine.schedule("foh", "routine", "Pre-service briefing",
                                    headline, fire_at=fire_at,
                                    dedupe_key=f"briefing-{when.date().isoformat()}")

    def open_checklist(self, when: Optional[datetime] = None) -> List[Reminder]:
        return self._checklist_reminders("open", when)

    def close_checklist(self, when: Optional[datetime] = None) -> List[Reminder]:
        return self._checklist_reminders("close", when)

    def restock(self, item: str, at: Optional[str] = None) -> Reminder:
        return self.engine.schedule("foh", "routine", f"Restock: {item}",
                                    "Running low up front.",
                                    fire_at=at or self.db.now(),
                                    dedupe_key=f"restock-{item}-{self.db.today()}")

    def vip(self, table: str, occasion: str, at: Optional[str] = None) -> Reminder:
        """Occasion reminder.

        PRIVACY: carries the table and the occasion, never the guest's name —
        the floor does not need a database of people to be hospitable.
        """
        return self.engine.schedule(
            "foh", "urgent", f"Occasion: table {table}",
            f"{occasion}. Brief the server and the kitchen.",
            fire_at=at or self.db.now(),
            dedupe_key=f"vip-{table}-{self.db.today()}")

    # -- 86 warning --------------------------------------------------------
    def eighty_six_warning(self, ingredient: Any) -> Reminder:
        name = getattr(ingredient, "name", str(ingredient))
        return self.engine.schedule(
            "foh", "urgent", f"86 warning: {name}",
            f"{name} is at or below par — expect to pull it soon. "
            "Warn the floor before the next seating.",
            fire_at=self.db.now(),
            dedupe_key=f"86-{getattr(ingredient, 'id', name)}-{self.db.today()}")

    def _on_ingredients_updated(self, event_type: str, payload: Any) -> None:
        """When something drops under par, warn the floor once."""
        if not isinstance(payload, dict):
            return
        if payload.get("action") == "draft_approved":
            return
        ingredient = payload.get("ingredient") or {}
        if not payload.get("below_par"):
            return
        name = ingredient.get("name")
        if not name:
            return

        class _Stub:
            pass

        stub = _Stub()
        stub.name = name
        stub.id = ingredient.get("id", name)
        self.eighty_six_warning(stub)

    # -- checklist content (user data, never hardcoded) --------------------
    def add_checklist_item(self, audience: str, item: str,
                           position: int = 0) -> None:
        self.db.execute(
            "INSERT INTO checklists (audience, item, position, active)"
            " VALUES (?,?,?,1)", (audience, item, position))

    def checklist_items(self, audience: str = "foh") -> List[str]:
        rows = self.db.query(
            "SELECT item FROM checklists WHERE audience = ? AND active = 1"
            " ORDER BY position, id", (audience,))
        return [r["item"] for r in rows]

    def _checklist_reminders(self, phase: str,
                             when: Optional[datetime] = None) -> List[Reminder]:
        when = when or datetime.now()
        if phase == "open":
            start = datetime.combine(when.date(),
                                     datetime.strptime(self.config.service_start,
                                                       "%H:%M").time())
            fire_at = (start - timedelta(minutes=20)).strftime(TS_FMT)
        else:
            fire_at = self.config.service_end
        out = []
        for item in self.checklist_items("foh"):
            out.append(self.engine.schedule(
                "foh", "routine", f"FOH {phase}: {item}", "",
                fire_at=fire_at,
                dedupe_key=f"foh-{phase}-{item}-{when.date().isoformat()}"))
        return out

    def tick(self, when: Optional[datetime] = None) -> Dict[str, Any]:
        return self.engine.tick(when)
