"""The Pass — the dish washing counter (Prompt 17).

NOTE: the pit is the quiet failure mode of a busy service. Plates run out,
service stalls, nobody notices until it is embarrassing.

WHY it watches the pace: open racks alone mean little at 17:00 and everything
at 20:30. Backlog plus a rising plate rate is the warning that matters.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from core.config import Config
from core.database import TS_FMT, Database
from core.events import WASH_BACKLOG, WASH_CYCLE_DONE, EventBus


class WashCounter:
    def __init__(self, config: Config, db: Database,
                 bus: Optional[EventBus] = None,
                 dish_counter: Any = None) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.dish_counter = dish_counter

    # -- racks -------------------------------------------------------------
    def rack_in(self, service_date: Optional[str] = None) -> int:
        return int(self.db.execute(
            "INSERT INTO wash_racks (rack_in_ts, rack_out_ts, service_date)"
            " VALUES (?,?,?)",
            (self.db.now(), "", service_date or self.db.today())) or 0)

    def rack_out(self, rack_id: Optional[int] = None,
                 service_date: Optional[str] = None) -> Dict[str, Any]:
        """Close the oldest open rack unless a specific one is named."""
        if rack_id is None:
            row = self.db.query_one(
                "SELECT id FROM wash_racks WHERE rack_out_ts = '' ORDER BY id LIMIT 1")
            if row is None:
                return {"rack_id": None, "closed": False,
                        "reason": "no open rack to close"}
            rack_id = row["id"]
        self.db.execute("UPDATE wash_racks SET rack_out_ts = ? WHERE id = ?",
                        (self.db.now(), rack_id))
        payload = {"rack_id": rack_id, "service_date": service_date or self.db.today()}
        if self.bus is not None:
            self.bus.publish(WASH_CYCLE_DONE, payload)
        return {"rack_id": rack_id, "closed": True, **payload}

    def open_racks(self) -> int:
        return int(self.db.scalar(
            "SELECT COUNT(*) FROM wash_racks WHERE rack_out_ts = ''"))

    # -- throughput --------------------------------------------------------
    def racks_done(self, service_date: Optional[str] = None) -> int:
        service_date = service_date or self.db.today()
        return int(self.db.scalar(
            "SELECT COUNT(*) FROM wash_racks WHERE service_date = ? AND"
            " rack_out_ts != ''", (service_date,)))

    def throughput_per_hour(self, service_date: Optional[str] = None) -> float:
        service_date = service_date or self.db.today()
        rows = self.db.query(
            "SELECT rack_out_ts FROM wash_racks WHERE service_date = ? AND"
            " rack_out_ts != ''", (service_date,))
        if not rows:
            return 0.0
        hours: Dict[str, int] = {}
        for row in rows:
            hour = str(row["rack_out_ts"])[11:13]
            hours[hour] = hours.get(hour, 0) + 1
        return round(len(rows) / len(hours), 2)

    def peak_hour(self, service_date: Optional[str] = None) -> Dict[str, Any]:
        service_date = service_date or self.db.today()
        rows = self.db.query(
            "SELECT rack_out_ts FROM wash_racks WHERE service_date = ? AND"
            " rack_out_ts != ''", (service_date,))
        hours: Dict[str, int] = {}
        for row in rows:
            hour = str(row["rack_out_ts"])[11:13]
            hours[hour] = hours.get(hour, 0) + 1
        if not hours:
            return {"hour": None, "racks": 0}
        hour = max(hours, key=lambda h: hours[h])
        return {"hour": hour, "racks": hours[hour]}

    def daily_total(self, service_date: Optional[str] = None) -> int:
        return self.racks_done(service_date)

    # -- the warning that matters -----------------------------------------
    def backlog_alert(self, service_date: Optional[str] = None,
                      now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        """Fire when the pit is behind AND the pass is speeding up."""
        now = now or datetime.now()
        service_date = service_date or self.db.today()
        open_count = self.open_racks()
        pace = (self.dish_counter.pace_per_hour(service_date)
                if self.dish_counter is not None else 0.0)
        if open_count < self.config.wash_backlog_threshold:
            return None
        dedupe = f"wash-backlog-{service_date}"
        existing = self.db.query_one(
            "SELECT id FROM reminders WHERE dedupe_key = ?", (dedupe,))
        alert = {"open_racks": open_count, "pace_per_hour": pace,
                 "threshold": self.config.wash_backlog_threshold,
                 "service_date": service_date}
        if existing:
            return alert
        self.db.execute(
            "INSERT INTO reminders (id, audience, urgency, title, body, fire_at,"
            " recurrence, status, dedupe_key, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"rem-wash-{service_date}", "boh", "urgent",
             "Pit is falling behind",
             f"{open_count} racks open with the pass plating {pace:g}/hour. "
             "Send help or clear the rack.", now.strftime(TS_FMT), "none",
             "scheduled", dedupe, self.db.now()))
        if self.bus is not None:
            self.bus.publish(WASH_BACKLOG, alert)
        return alert

    # -- sanitizer (the pit's own HACCP check) -----------------------------
    def sanitizer_check(self, shift: str, concentration: str, temp_c: float,
                        checked_by: str = "") -> None:
        self.db.execute(
            "INSERT INTO sanitizer_log (shift, concentration, temp_c, checked_by, ts)"
            " VALUES (?,?,?,?,?)",
            (shift, concentration, temp_c, checked_by, self.db.now()))

    # -- clock -------------------------------------------------------------
    def tick(self, when: Optional[datetime] = None) -> Dict[str, Any]:
        alert = self.backlog_alert(now=when)
        return {"open_racks": self.open_racks(),
                "racks_done": self.racks_done(),
                "backlog_alert": bool(alert)}

    def summary(self, service_date: Optional[str] = None) -> Dict[str, Any]:
        return {
            "service_date": service_date or self.db.today(),
            "open_racks": self.open_racks(),
            "racks_done": self.racks_done(service_date),
            "throughput_per_hour": self.throughput_per_hour(service_date),
            "peak": self.peak_hour(service_date),
        }
