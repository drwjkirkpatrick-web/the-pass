"""The Pass — supplier delivery delay history (Prompt 09).

NOTE: the kitchen knows who is late. This module writes it down, so ordering
can compensate automatically instead of hoping.

WHY p90 and not the average: the point of ordering early is to survive the bad
day, not the typical one. Phase 3 uses p90 to pick order dates.
"""
from __future__ import annotations

import csv
import math
import statistics
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence

from core.config import Config
from core.database import D_FMT, Database
from core.events import EventBus
from core.types import Supplier, filter_fields


@dataclass(frozen=True)
class DelayRecord:
    supplier_id: str
    ordered_date: str
    promised_date: str
    actual_date: str
    delay_days: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _as_date(value: str) -> date:
    return datetime.strptime(value, D_FMT).date()


class Deliveries:
    def __init__(self, config: Config, db: Database,
                 bus: Optional[EventBus] = None) -> None:
        self.config = config
        self.db = db
        self.bus = bus

    # -- suppliers ---------------------------------------------------------
    def add_supplier(self, name: str, order_cutoff: str = "08:00",
                     typical_delay_days: float = 1.0,
                     closed_days: Sequence[int] = (), notes: str = "",
                     id: Optional[str] = None) -> Supplier:
        supplier = Supplier(
            id=id or f"s-{uuid.uuid4().hex[:8]}", name=name,
            order_cutoff=order_cutoff, typical_delay_days=typical_delay_days,
            closed_days=list(closed_days), notes=notes,
        )
        self.db.execute(
            "INSERT OR REPLACE INTO suppliers (id, name, order_cutoff,"
            " typical_delay_days, closed_days, notes) VALUES (?,?,?,?,?,?)",
            (supplier.id, supplier.name, supplier.order_cutoff,
             supplier.typical_delay_days, ",".join(str(d) for d in supplier.closed_days),
             supplier.notes),
        )
        return supplier

    def get_supplier(self, supplier_id: str) -> Optional[Supplier]:
        row = self.db.query_one("SELECT * FROM suppliers WHERE id = ?", (supplier_id,))
        if row is None:
            return None
        data = {k: row[k] for k in row.keys()}
        raw = data.get("closed_days") or ""
        data["closed_days"] = [int(x) for x in str(raw).split(",") if str(x).strip().isdigit()]
        return Supplier(**filter_fields(Supplier, data))

    def suppliers(self) -> List[Supplier]:
        rows = self.db.query("SELECT id FROM suppliers ORDER BY name")
        return [self.get_supplier(r["id"]) for r in rows]

    # -- history -----------------------------------------------------------
    def record_arrival(self, supplier_id: str, ordered_date: str,
                       promised_date: str, actual_date: str) -> DelayRecord:
        delay = (_as_date(actual_date) - _as_date(promised_date)).days
        record = DelayRecord(supplier_id=supplier_id, ordered_date=ordered_date,
                             promised_date=promised_date, actual_date=actual_date,
                             delay_days=float(delay))
        self.db.execute(
            "INSERT INTO delivery_delays (supplier_id, ordered_date, promised_date,"
            " actual_date, recorded_at) VALUES (?,?,?,?,?)",
            (supplier_id, ordered_date, promised_date, actual_date, self.db.now()),
        )
        return record

    def delay_history(self, supplier_id: str) -> List[DelayRecord]:
        rows = self.db.query(
            "SELECT * FROM delivery_delays WHERE supplier_id = ?"
            " ORDER BY actual_date", (supplier_id,))
        out = []
        for row in rows:
            delay = (_as_date(row["actual_date"]) - _as_date(row["promised_date"])).days
            out.append(DelayRecord(supplier_id=row["supplier_id"],
                                   ordered_date=row["ordered_date"],
                                   promised_date=row["promised_date"],
                                   actual_date=row["actual_date"],
                                   delay_days=float(delay)))
        return out

    def median_delay(self, supplier_id: str) -> float:
        delays = [r.delay_days for r in self.delay_history(supplier_id)]
        if not delays:
            return self._default_delay(supplier_id)
        return float(statistics.median(delays))

    def p90_delay(self, supplier_id: str) -> float:
        """The delay we plan against: 9 deliveries out of 10 beat it."""
        delays = sorted(r.delay_days for r in self.delay_history(supplier_id))
        if not delays:
            return self._default_delay(supplier_id)
        index = min(len(delays) - 1, max(0, math.ceil(0.9 * len(delays)) - 1))
        return float(delays[index])

    def worst_recent_delay(self, supplier_id: str, window: int = 10) -> float:
        """The nastiest delay in the last ``window`` deliveries.

        WHY this exists next to p90: p90 tells you what 9 of 10 deliveries look
        like. This tells you what the tenth one cost. Both belong on the report
        card, because only the chef can decide how much slack to buy.
        """
        delays = [r.delay_days for r in self.delay_history(supplier_id)][-window:]
        if not delays:
            return self._default_delay(supplier_id)
        return float(max(delays))

    def planning_lead(self, supplier_id: str,
                      safety_buffer_days: float = 0.0) -> float:
        """Days of lead time to order against: p90 plus any slack you asked for."""
        return float(self.p90_delay(supplier_id) + safety_buffer_days)

    def on_time_rate(self, supplier_id: str) -> float:
        delays = [r.delay_days for r in self.delay_history(supplier_id)]
        if not delays:
            return 1.0
        return sum(1 for d in delays if d <= 0) / len(delays)

    def reliability_grade(self, supplier_id: str) -> Dict[str, Any]:
        """A-D grade with the reason spelled out (never a bare letter)."""
        history = self.delay_history(supplier_id)
        if not history:
            return {"grade": "?", "mean_delay": None, "on_time_rate": None,
                    "rationale": "no delivery history yet — using the default lead time"}
        mean = statistics.mean(r.delay_days for r in history)
        on_time = self.on_time_rate(supplier_id)
        if mean <= 0.5 and on_time >= 0.9:
            grade = "A"
        elif mean <= 1.0 and on_time >= 0.75:
            grade = "B"
        elif mean <= 2.0:
            grade = "C"
        else:
            grade = "D"
        return {"grade": grade, "mean_delay": round(mean, 2),
                "on_time_rate": round(on_time, 2), "deliveries": len(history),
                "rationale": f"mean delay {mean:.2f}d over {len(history)} deliveries, "
                             f"{on_time:.0%} on time"}

    def import_csv(self, path: str) -> int:
        """Chef's own log: supplier_id,ordered_date,promised_date,actual_date"""
        count = 0
        with open(path, "r", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if not row.get("supplier_id") or not row.get("actual_date"):
                    continue
                self.record_arrival(row["supplier_id"], row["ordered_date"],
                                    row["promised_date"], row["actual_date"])
                count += 1
        return count

    def supplier_report(self) -> List[Dict[str, Any]]:
        out = []
        for supplier in self.suppliers():
            grade = self.reliability_grade(supplier.id)
            out.append({
                "supplier_id": supplier.id,
                "name": supplier.name,
                "order_cutoff": supplier.order_cutoff,
                "median_delay": self.median_delay(supplier.id),
                "p90_delay": self.p90_delay(supplier.id),
                "worst_recent_delay": self.worst_recent_delay(supplier.id),
                **grade,
            })
        return out

    def _default_delay(self, supplier_id: str) -> float:
        supplier = self.get_supplier(supplier_id)
        return float(supplier.typical_delay_days) if supplier else 1.0
