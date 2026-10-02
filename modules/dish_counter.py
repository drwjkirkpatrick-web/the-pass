"""The Pass — the dish counter (Prompt 16).

NOTE: three beats per plate — fired, plated, picked up. The gap between fired
and plated is where a pass bottleneck shows up; the gap between plated and
picked up is where food dies under a heat lamp.

WHY append-only: counts are evidence. You cannot reconcile a service against a
number somebody can edit.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from core.config import Config
from core.database import Database
from core.events import DISH_PLATED, EventBus
from core.types import DishAction, DishEvent, filter_fields


class DishCounter:
    def __init__(self, config: Config, db: Database,
                 bus: Optional[EventBus] = None) -> None:
        self.config = config
        self.db = db
        self.bus = bus

    # -- events ------------------------------------------------------------
    def _record(self, dish_id: str, action: DishAction, service_date: Optional[str],
                photo_id: str = "") -> DishEvent:
        service_date = service_date or self.db.today()
        ts = self.db.now()
        row_id = self.db.execute(
            "INSERT INTO dish_events (dish_id, action, ts, service_date, photo_id)"
            " VALUES (?,?,?,?,?)",
            (dish_id, action.value, ts, service_date, photo_id))
        return DishEvent(id=row_id or 0, dish_id=dish_id, action=action.value, ts=ts,
                         service_date=service_date, photo_id=photo_id)

    def fire(self, dish_id: str, service_date: Optional[str] = None) -> DishEvent:
        return self._record(dish_id, DishAction.FIRED, service_date)

    def plated(self, dish_id: str, service_date: Optional[str] = None,
               photo_id: str = "") -> DishEvent:
        event = self._record(dish_id, DishAction.PLATED, service_date, photo_id)
        if self.bus is not None:
            self.bus.publish(DISH_PLATED, {"dish_id": dish_id, "ts": event.ts,
                                           "service_date": event.service_date,
                                           "event_id": event.id})
        return event

    def picked_up(self, dish_id: str,
                  service_date: Optional[str] = None) -> DishEvent:
        return self._record(dish_id, DishAction.PICKED_UP, service_date)

    # -- aggregates --------------------------------------------------------
    def per_service(self, service_date: Optional[str] = None) -> Dict[str, Dict[str, int]]:
        service_date = service_date or self.db.today()
        rows = self.db.query(
            "SELECT dish_id, action, COUNT(*) AS n FROM dish_events"
            " WHERE service_date = ? GROUP BY dish_id, action", (service_date,))
        out: Dict[str, Dict[str, int]] = {}
        for row in rows:
            bucket = out.setdefault(row["dish_id"],
                                    {"fired": 0, "plated": 0, "picked_up": 0})
            bucket[row["action"]] = row["n"]
        for dish_id, counts in out.items():
            counts["gap"] = counts["fired"] - counts["plated"]
        return out

    def covers_total(self, service_date: Optional[str] = None) -> int:
        service_date = service_date or self.db.today()
        return int(self.db.scalar(
            "SELECT COUNT(*) FROM dish_events WHERE action = 'picked_up'"
            " AND service_date = ?", (service_date,)))

    def per_hour_histogram(self, service_date: Optional[str] = None) -> Dict[str, int]:
        service_date = service_date or self.db.today()
        rows = self.db.query(
            "SELECT ts FROM dish_events WHERE action = 'plated' AND service_date = ?",
            (service_date,))
        histogram: Dict[str, int] = {}
        for row in rows:
            hour = str(row["ts"])[11:13]
            histogram[hour] = histogram.get(hour, 0) + 1
        return dict(sorted(histogram.items()))

    def pace_per_hour(self, service_date: Optional[str] = None) -> float:
        """Plates per hour over the hours we actually plated in."""
        histogram = self.per_hour_histogram(service_date)
        if not histogram:
            return 0.0
        return round(sum(histogram.values()) / len(histogram), 2)

    def gaps(self, service_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fired but never plated — a pass bottleneck, not a mystery."""
        return [{"dish_id": dish_id, **counts}
                for dish_id, counts in self.per_service(service_date).items()
                if counts.get("gap", 0) > 0]

    def last_plated_event(self, dish_id: str) -> Optional[DishEvent]:
        row = self.db.query_one(
            "SELECT * FROM dish_events WHERE dish_id = ? AND action = 'plated'"
            " ORDER BY id DESC LIMIT 1", (dish_id,))
        return (DishEvent(**filter_fields(DishEvent, {k: row[k] for k in row.keys()}))
                if row else None)

    def recent_plated(self, dish_id: Optional[str] = None,
                      limit: int = 5) -> List[DishEvent]:
        if dish_id:
            rows = self.db.query(
                "SELECT * FROM dish_events WHERE action = 'plated' AND dish_id = ?"
                " ORDER BY id DESC LIMIT ?", (dish_id, limit))
        else:
            rows = self.db.query(
                "SELECT * FROM dish_events WHERE action = 'plated'"
                " ORDER BY id DESC LIMIT ?", (limit,))
        return [DishEvent(**filter_fields(DishEvent, {k: r[k] for k in r.keys()}))
                for r in rows]

    def service_summary(self, service_date: Optional[str] = None) -> Dict[str, Any]:
        per = self.per_service(service_date)
        return {
            "service_date": service_date or self.db.today(),
            "dishes": len(per),
            "fired": sum(c["fired"] for c in per.values()),
            "plated": sum(c["plated"] for c in per.values()),
            "covers": self.covers_total(service_date),
            "gaps": len(self.gaps(service_date)),
            "pace_per_hour": self.pace_per_hour(service_date),
        }

    def tick(self, when: Optional[datetime] = None) -> Dict[str, Any]:
        return self.service_summary()
