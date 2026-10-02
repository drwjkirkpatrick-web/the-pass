"""The Pass — photograph every dish on the pass (Prompt 18).

NOTE: every plated dish gets its photo, and the photo is chained to the plate
event from the dish counter (16), so photo -> plate -> dish -> service is one
unbroken line. That chain is what makes the archive useful months later.

WHY the analyzer is a sibling protocol, not the ingredient analyzer: the
ingredient analyzer answers "what is this and how fresh"; this one answers
"is this plate built the way the recipe says". Different question, different
output shape — same pattern (deterministic mock now, real model behind the
same contract later).
"""
from __future__ import annotations

import json
import os
import re
import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from core.config import Config
from core.database import Database
from core.events import EventBus

METRIC_TOKENS = ("sym", "por", "garn")
DEFAULT_METRICS = {"symmetry": 0.85, "portion": 0.85, "garnish": 0.85}


class PlateAnalyzer(ABC):
    @abstractmethod
    def score(self, photo_path: str) -> Dict[str, float]:
        raise NotImplementedError


class MockPlateAnalyzer(PlateAnalyzer):
    """Deterministic: reads metric tokens out of the filename.

    ``plate_sym0.8_por0.9_garn0.7.jpg``
      -> {"symmetry": 0.8, "portion": 0.9, "garnish": 0.7}
    No tokens (a real photo filename) -> sensible defaults.
    """

    TOKEN_RE = re.compile(r"(sym|por|garn)([01](?:\.\d+)?)")

    def score(self, photo_path: str) -> Dict[str, float]:
        stem = os.path.splitext(os.path.basename(photo_path))[0]
        found = {name: float(value) for name, value in self.TOKEN_RE.findall(stem)}
        return {
            "symmetry": found.get("sym", DEFAULT_METRICS["symmetry"]),
            "portion": found.get("por", DEFAULT_METRICS["portion"]),
            "garnish": found.get("garn", DEFAULT_METRICS["garnish"]),
        }


class RealPlateAnalyzer(PlateAnalyzer):
    """Extension point for a real plating model (local or hosted)."""

    def score(self, photo_path: str) -> Dict[str, float]:
        raise NotImplementedError(
            "Wire a real plating model here; see docs/ARCHITECTURE.md")


class PassPhoto:
    def __init__(self, config: Config, db: Database, bus: Optional[EventBus] = None,
                 analyzer: Optional[PlateAnalyzer] = None,
                 dish_counter: Any = None) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.analyzer = analyzer or MockPlateAnalyzer()
        self.dish_counter = dish_counter

    # -- capture -----------------------------------------------------------
    def capture(self, dish_id: str, station: str = "pass",
                photo_path: Optional[str] = None,
                service_date: Optional[str] = None) -> Dict[str, Any]:
        """Photograph a plate. Links to the most recent plated event."""
        service_date = service_date or self.db.today()
        path = photo_path or self._placeholder_path(dish_id, service_date)
        metrics = self.analyzer.score(path)
        event_id = None
        if self.dish_counter is not None:
            event = self.dish_counter.last_plated_event(dish_id)
            event_id = event.id if event else None
        photo_id = f"ph-{uuid.uuid4().hex[:8]}"
        self.db.execute(
            "INSERT INTO dish_photos (id, dish_id, path, ts, service_date, event_id,"
            " metrics, pending_review) VALUES (?,?,?,?,?,?,?,1)",
            (photo_id, dish_id, path, self.db.now(), service_date, event_id,
             json.dumps(metrics)))
        return {"photo_id": photo_id, "dish_id": dish_id, "path": path,
                "metrics": metrics, "event_id": event_id,
                "service_date": service_date, "station": station}

    def _placeholder_path(self, dish_id: str, service_date: str) -> str:
        """Without a camera we still record the slot, so the chain is complete."""
        target_dir = os.path.join(self.config.photo_dir, "pass", service_date)
        os.makedirs(target_dir, exist_ok=True)
        seq = int(self.db.scalar(
            "SELECT COUNT(*) FROM dish_photos WHERE dish_id = ? AND service_date = ?",
            (dish_id, service_date))) + 1
        # NOTE: dish ids are untrusted (they can come from a menu file or a chat
        # message). Slug them for the filename so a stray "/" or ".." can never
        # steer the write outside the photo store. The database keeps the id
        # exactly as given.
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", str(dish_id))[:60]
        safe_id = re.sub(r"\.{2,}", "-", safe_id).strip("-. ") or "dish"
        path = os.path.join(target_dir, f"{service_date}_{safe_id}_{seq:02d}.jpg")
        if not os.path.exists(path):
            with open(path, "wb") as fh:
                fh.write(b"placeholder-plate-photo")
        return path

    # -- reads -------------------------------------------------------------
    def _to_dict(self, row) -> Dict[str, Any]:
        data = dict(row)
        data["metrics"] = json.loads(data.get("metrics") or "{}")
        data["pending_review"] = bool(data.get("pending_review"))
        return data

    def get(self, photo_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.query_one("SELECT * FROM dish_photos WHERE id = ?", (photo_id,))
        return self._to_dict(row) if row else None

    def gallery(self, service_date: Optional[str] = None,
                dish_id: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM dish_photos WHERE 1=1"
        params: List[Any] = []
        if service_date:
            sql += " AND service_date = ?"
            params.append(service_date)
        if dish_id:
            sql += " AND dish_id = ?"
            params.append(dish_id)
        sql += " ORDER BY ts DESC"
        return [self._to_dict(r) for r in self.db.query(sql, tuple(params))]

    def unreviewed(self, limit: int = 10) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM dish_photos WHERE pending_review = 1 ORDER BY ts ASC"
            " LIMIT ?", (limit,))
        return [self._to_dict(r) for r in rows]

    def mark_reviewed(self, photo_id: str) -> None:
        self.db.execute("UPDATE dish_photos SET pending_review = 0 WHERE id = ?",
                        (photo_id,))

    def compare(self, dish_id: str, date_a: str, date_b: str) -> Dict[str, Any]:
        """Two plates of the same dish, side by side (paths for the app)."""
        def first_on(day: str) -> Optional[Dict[str, Any]]:
            row = self.db.query_one(
                "SELECT * FROM dish_photos WHERE dish_id = ? AND service_date = ?"
                " ORDER BY ts ASC LIMIT 1", (dish_id, day))
            return self._to_dict(row) if row else None

        return {"dish_id": dish_id, "a": first_on(date_a), "b": first_on(date_b)}

    def storage_usage(self) -> Dict[str, Any]:
        rows = self.db.query("SELECT path FROM dish_photos")
        total = 0
        missing = 0
        for row in rows:
            if os.path.exists(row["path"]):
                total += os.path.getsize(row["path"])
            else:
                missing += 1
        return {"photos": len(rows), "bytes": total, "missing_files": missing}
