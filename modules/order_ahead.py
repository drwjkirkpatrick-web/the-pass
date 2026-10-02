"""The Pass — order-ahead scheduling (Prompt 10).

NOTE: the ask was "order days in advance so it lands the day before". This
module does the backward date math:

    service date  ->  arrival = service - 1 day  ->  order_by = arrival - p90 lead

WHY p90 and not the average: you order early to survive the supplier's bad day,
not their typical one. One slow delivery should not cost you a service.

WHY closed days are walked back: an order placed on a day the supplier does not
trade is an order that never arrives.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from core.config import Config
from core.database import D_FMT, Database
from core.events import EventBus


def _as_date(value: str):
    return datetime.strptime(value, D_FMT).date()


@dataclass(frozen=True)
class OrderRecommendation:
    supplier_id: str
    supplier_name: str
    ingredient_id: str
    ingredient_name: str
    qty: float
    unit: str
    service_date: str
    arrival_date: str
    order_by: str
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class OrderAhead:
    def __init__(self, config: Config, db: Database, bus: Optional[EventBus] = None,
                 deliveries: Any = None, inventory: Any = None) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.deliveries = deliveries
        self.inventory = inventory

    # -- date math ---------------------------------------------------------
    def arrival_date(self, service_date: str) -> str:
        """Goods must be on the shelf the day before service."""
        return (_as_date(service_date) - timedelta(days=1)).strftime(D_FMT)

    def order_by(self, supplier_id: str, service_date: str) -> str:
        arrival = _as_date(self.arrival_date(service_date))
        lead = (self.deliveries.planning_lead(
                    supplier_id, self.config.delivery_safety_buffer_days)
                if self.deliveries else 1.0)
        candidate = arrival - timedelta(days=int(math.ceil(lead)))
        supplier = self.deliveries.get_supplier(supplier_id) if self.deliveries else None
        closed = set(supplier.closed_days) if supplier else set()
        while candidate.weekday() in closed:
            candidate -= timedelta(days=1)
        return candidate.strftime(D_FMT)

    def required_by_date(self, service_date: str, ingredient_id: str = "") -> str:
        """When the goods must be in the building for this service."""
        return self.arrival_date(service_date)

    # -- schedule ----------------------------------------------------------
    def build_schedule(self, service_date: str,
                       shortfalls: Optional[Sequence[Any]] = None,
                       default_supplier_id: str = "") -> List[OrderRecommendation]:
        if shortfalls is None:
            return []
        default_supplier = default_supplier_id
        if not default_supplier and self.deliveries is not None:
            suppliers = self.deliveries.suppliers()
            default_supplier = suppliers[0].id if suppliers else ""
        arrival = self.arrival_date(service_date)
        out: List[OrderRecommendation] = []
        for short in shortfalls:
            ingredient = (self.inventory.get(short.ingredient_id)
                          if self.inventory else None)
            supplier_id = (getattr(ingredient, "supplier_id", "") or default_supplier)
            supplier = (self.deliveries.get_supplier(supplier_id)
                        if self.deliveries else None)
            order_by = (self.order_by(supplier_id, service_date)
                        if supplier_id else arrival)
            lead = (self.deliveries.p90_delay(supplier_id) if self.deliveries else 1.0)
            note_parts = [
                f"short {short.deficit:g} {short.unit}",
                f"arrive {arrival} for service {service_date}",
                f"supplier p90 {lead:g}d -> order by {order_by}",
            ]
            shelf = getattr(ingredient, "shelf_life_days", 0) or 0
            if shelf and shelf <= int(math.ceil(lead)):
                note_parts.append(
                    f"WARNING shelf life {shelf}d is shorter than the lead time "
                    f"({math.ceil(lead)}d) - ask for the freshest drop or a faster source")
            out.append(OrderRecommendation(
                supplier_id=supplier_id,
                supplier_name=supplier.name if supplier else "unassigned",
                ingredient_id=short.ingredient_id,
                ingredient_name=getattr(short, "name", short.ingredient_id),
                qty=float(short.deficit),
                unit=short.unit,
                service_date=service_date,
                arrival_date=arrival,
                order_by=order_by,
                note="; ".join(note_parts),
            ))
        return sorted(out, key=lambda r: (r.order_by, r.supplier_name))

    def due_today(self, service_date: str, today: Optional[str] = None,
                  shortfalls: Optional[Sequence[Any]] = None) -> List[OrderRecommendation]:
        """Recommendations whose order date is today or already past."""
        today = today or self.db.today()
        return [r for r in self.build_schedule(service_date, shortfalls)
                if r.order_by <= today]
