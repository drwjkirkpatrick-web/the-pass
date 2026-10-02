"""The Pass — temperature logging and cold chain (Prompt 13).

NOTE: the readings are an append-only log. That is not fussiness — it is the
compliance artifact. A log you can edit is not evidence.

WHY escalation at two consecutive breaches: one odd reading can be a door left
open. Two in a row is a problem, and the kitchen needs to hear about it.
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.config import Config
from core.database import TS_FMT, Database
from core.events import TEMP_EXCURSION, EventBus
from core.types import TempReading, TempZone, filter_fields


class SensorSource(ABC):
    """Anything that can read a zone temperature."""

    @abstractmethod
    def read(self, zone: TempZone) -> float:
        raise NotImplementedError


class MockSensorSource(SensorSource):
    """Deterministic readings for tests and for running without hardware.

    Cycles through a fixed sequence per zone so a breach can be scripted.
    """

    def __init__(self, values: Optional[List[float]] = None) -> None:
        self.values = values if values is not None else [3.0, 3.2, 2.8, 4.5]
        self._index = 0

    def read(self, zone: TempZone) -> float:
        value = self.values[self._index % len(self.values)]
        self._index += 1
        return value


class TempLog:
    def __init__(self, config: Config, db: Database, bus: Optional[EventBus] = None,
                 sensor: Optional[SensorSource] = None) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.sensor = sensor or MockSensorSource()

    # -- zones -------------------------------------------------------------
    def add_zone(self, name: str, station: str, min_c: float, max_c: float,
                 id: Optional[str] = None) -> TempZone:
        zone = TempZone(id=id or f"z-{uuid.uuid4().hex[:6]}", name=name,
                        station=station, min_c=min_c, max_c=max_c)
        self.db.execute(
            "INSERT OR REPLACE INTO temp_zones (id, name, station, min_c, max_c)"
            " VALUES (?,?,?,?,?)",
            (zone.id, zone.name, zone.station, zone.min_c, zone.max_c))
        return zone

    def default_zones(self) -> List[TempZone]:
        """A starting set from config thresholds — the chef edits these."""
        return [
            self.add_zone("Walk-in", "kitchen", 0.0, self.config.walkin_max_c,
                          id="z-walkin"),
            self.add_zone("Hot hold (pass)", "pass", self.config.hot_hold_min_c,
                          100.0, id="z-hot-hold"),
        ]

    def get_zone(self, zone_id: str) -> Optional[TempZone]:
        row = self.db.query_one("SELECT * FROM temp_zones WHERE id = ?", (zone_id,))
        return TempZone(**filter_fields(TempZone, {k: row[k] for k in row.keys()})) if row else None

    def zones(self) -> List[TempZone]:
        rows = self.db.query("SELECT * FROM temp_zones ORDER BY name")
        return [TempZone(**filter_fields(TempZone, {k: r[k] for k in r.keys()}))
                for r in rows]

    # -- logging -----------------------------------------------------------
    def log(self, zone_id: str, celsius: Optional[float] = None,
            sensor_id: str = "manual") -> Dict[str, Any]:
        zone = self.get_zone(zone_id)
        if zone is None:
            raise KeyError(f"no such zone: {zone_id}")
        if celsius is None:
            celsius = self.sensor.read(zone)
        in_bounds = 1 if (zone.min_c <= celsius <= zone.max_c) else 0
        self.db.execute(
            "INSERT INTO temp_readings (zone_id, station, sensor_id, celsius, ts,"
            " in_bounds) VALUES (?,?,?,?,?,?)",
            (zone_id, zone.station, sensor_id, float(celsius), self.db.now(),
             in_bounds))
        reading_id = self.db.scalar("SELECT MAX(id) FROM temp_readings")

        breach_streak = self.consecutive_breaches(zone_id)
        escalated = False
        if in_bounds == 0:
            escalated = breach_streak >= 2
            if self.bus is not None:
                self.bus.publish(TEMP_EXCURSION, {
                    "zone_id": zone.id, "zone_name": zone.name,
                    "station": zone.station, "celsius": float(celsius),
                    "min_c": zone.min_c, "max_c": zone.max_c,
                    "streak": breach_streak, "escalated": escalated,
                    "reading_id": reading_id,
                })
            if escalated:
                self._raise_urgent_reminder(zone, float(celsius))
        return {"reading_id": reading_id, "zone_id": zone.id, "celsius": float(celsius),
                "in_bounds": bool(in_bounds), "streak": breach_streak,
                "escalated": escalated}

    def check(self, zone_id: str, celsius: float) -> bool:
        zone = self.get_zone(zone_id)
        if zone is None:
            return False
        return zone.min_c <= celsius <= zone.max_c

    # -- reads -------------------------------------------------------------
    def consecutive_breaches(self, zone_id: str) -> int:
        """How many out-of-bounds readings in a row, counting back from now."""
        rows = self.db.query(
            "SELECT in_bounds FROM temp_readings WHERE zone_id = ? ORDER BY id DESC"
            " LIMIT 20", (zone_id,))
        streak = 0
        for row in rows:
            if row["in_bounds"] == 0:
                streak += 1
            else:
                break
        return streak

    def readings(self, zone_id: Optional[str] = None, limit: int = 200) -> List[TempReading]:
        if zone_id:
            rows = self.db.query(
                "SELECT * FROM temp_readings WHERE zone_id = ? ORDER BY id DESC"
                " LIMIT ?", (zone_id, limit))
        else:
            rows = self.db.query(
                "SELECT * FROM temp_readings ORDER BY id DESC LIMIT ?", (limit,))
        return [TempReading(**filter_fields(TempReading, {k: r[k] for k in r.keys()}))
                for r in rows]

    def excursion_report(self, since: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM temp_readings WHERE in_bounds = 0"
        params: tuple = ()
        if since:
            sql += " AND ts >= ?"
            params = (since,)
        sql += " ORDER BY id"
        out = []
        for row in self.db.query(sql, params):
            zone = self.get_zone(row["zone_id"])
            out.append({"reading_id": row["id"], "zone": zone.name if zone else row["zone_id"],
                        "celsius": row["celsius"], "ts": row["ts"],
                        "bounds": f"{zone.min_c:g}..{zone.max_c:g}" if zone else ""})
        return out

    def zone_status(self) -> List[Dict[str, Any]]:
        out = []
        for zone in self.zones():
            row = self.db.query_one(
                "SELECT * FROM temp_readings WHERE zone_id = ? ORDER BY id DESC LIMIT 1",
                (zone.id,))
            if row is None:
                out.append({"zone": zone.name, "current": None, "in_bounds": None,
                            "streak": 0})
                continue
            out.append({"zone": zone.name, "current": row["celsius"],
                        "in_bounds": bool(row["in_bounds"]),
                        "streak": self.consecutive_breaches(zone.id),
                        "ts": row["ts"]})
        return out

    # -- sanitizer (HACCP companion) ---------------------------------------
    def sanitizer_log(self, shift: str, concentration: str, temp_c: float,
                      checked_by: str = "") -> None:
        self.db.execute(
            "INSERT INTO sanitizer_log (shift, concentration, temp_c, checked_by, ts)"
            " VALUES (?,?,?,?,?)",
            (shift, concentration, temp_c, checked_by, self.db.now()))

    # -- clock -------------------------------------------------------------
    def tick(self, when: Optional[datetime] = None) -> Dict[str, Any]:
        """Read every zone (hardware or mock) and log it."""
        logged = [self.log(zone.id) for zone in self.zones()]
        return {"readings": len(logged),
                "breaches": sum(1 for l in logged if not l["in_bounds"]),
                "escalated": [l["zone_id"] for l in logged if l["escalated"]]}

    # -- internal ----------------------------------------------------------
    def _raise_urgent_reminder(self, zone: TempZone, celsius: float) -> None:
        """Two breaches in a row becomes an urgent BOH reminder (deduped)."""
        dedupe = f"temp-{zone.id}-{self.db.today()}"
        existing = self.db.query_one(
            "SELECT id FROM reminders WHERE dedupe_key = ?", (dedupe,))
        if existing:
            return
        self.db.execute(
            "INSERT INTO reminders (id, audience, urgency, title, body, fire_at,"
            " recurrence, status, dedupe_key, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"rem-{uuid.uuid4().hex[:8]}", "boh", "urgent",
             f"Temperature breach: {zone.name}",
             f"{celsius:g}C is outside {zone.min_c:g}..{zone.max_c:g}C, twice in a row. "
             "Move the product and check the unit.", self.db.now(), "none",
             "scheduled", dedupe, self.db.now()))
