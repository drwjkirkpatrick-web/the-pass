"""The Pass — food ordering list curation (Prompt 11).

NOTE: this turns plan shortfalls + below-par staples into *draft* purchase
orders, one per supplier, each line carrying the reason it is there.

WHY the rationale: a chef should never have to trust a black box. Every line
says how much, why, and when it lands.

WHY drafts only: nothing here can send an order. Prompt 12 is the human gate.
"""
from __future__ import annotations

import math
import uuid
from typing import Any, Dict, List, Optional, Sequence

from core.config import Config
from core.database import Database
from core.events import DRAFT_ORDER_READY, EventBus
from core.types import Order, OrderLine, OrderStatus

UNASSIGNED = "unassigned"


class Ordering:
    def __init__(self, config: Config, db: Database, bus: Optional[EventBus] = None,
                 inventory: Any = None, recipe_book: Any = None,
                 planner: Any = None, order_ahead: Any = None,
                 deliveries: Any = None) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.inventory = inventory
        self.recipe_book = recipe_book
        self.planner = planner
        self.order_ahead = order_ahead
        self.deliveries = deliveries

    # -- curation ----------------------------------------------------------
    def curate_orders(self, service_date: Optional[str] = None,
                      covers: Optional[Dict[str, float]] = None,
                      pack_sizes: Optional[Dict[str, float]] = None) -> List[Order]:
        service_date = service_date or self.db.today()
        pack_sizes = pack_sizes or {}

        plan = self.planner.get_plan(service_date) if self.planner else None
        if plan is None and self.planner is not None and covers:
            plan = self.planner.plan_service(covers, service_date)

        # 1. what the menu needs and we do not have
        wanted: Dict[str, Dict[str, Any]] = {}
        for short in (plan.shortfalls if plan else []):
            entry = wanted.setdefault(short.ingredient_id, {
                "qty": 0.0, "unit": short.unit, "reasons": []})
            entry["qty"] = max(entry["qty"], float(short.deficit))
            entry["reasons"].append(f"short for service {service_date} ({short.reason})")

        # 2. staples that have fallen under their par level
        for ingredient in (self.inventory.below_par() if self.inventory else []):
            top_up = max(0.0, ingredient.par_level - ingredient.on_hand)
            entry = wanted.setdefault(ingredient.id, {
                "qty": 0.0, "unit": ingredient.unit, "reasons": []})
            entry["qty"] = max(entry["qty"], round(top_up, 4))
            entry["reasons"].append(
                f"below par ({ingredient.on_hand:g}/{ingredient.par_level:g} {ingredient.unit})")

        if not wanted:
            return []

        # 3. group by supplier, round to pack sizes
        grouped: Dict[str, List[OrderLine]] = {}
        for ingredient_id, entry in wanted.items():
            ingredient = self.inventory.get(ingredient_id) if self.inventory else None
            supplier_id = (getattr(ingredient, "supplier_id", "") or UNASSIGNED)
            pack = float(pack_sizes.get(ingredient_id, 1.0) or 1.0)
            qty = entry["qty"]
            if pack > 1:
                qty = math.ceil(qty / pack) * pack
            name = ingredient.name if ingredient else ingredient_id
            order_by = ""
            if self.order_ahead is not None and supplier_id != UNASSIGNED:
                order_by = self.order_ahead.order_by(supplier_id, service_date)
            rationale = ("; ".join(entry["reasons"]) +
                         (f"; order by {order_by}" if order_by else "") +
                         f"; needed {qty:g} {entry['unit']}")
            grouped.setdefault(supplier_id, []).append(OrderLine(
                ingredient_id=ingredient_id, ingredient_name=name,
                qty=round(qty, 4), unit=entry["unit"], pack_size=pack,
                rationale=rationale))

        orders: List[Order] = []
        for supplier_id, lines in grouped.items():
            order = Order(
                id=f"o-{uuid.uuid4().hex[:8]}",
                supplier_id=supplier_id,
                status=OrderStatus.DRAFT,
                created_by="agent",
                created_at=self.db.now(),
                service_date=service_date,
                lines=lines,
            )
            self._persist(order)
            orders.append(order)
            if self.bus is not None:
                self.bus.publish(DRAFT_ORDER_READY, {
                    "order_id": order.id,
                    "supplier_id": supplier_id,
                    "lines": len(lines),
                    "service_date": service_date,
                })
        return orders

    # -- reads -------------------------------------------------------------
    def get_order(self, order_id: str) -> Optional[Order]:
        row = self.db.query_one("SELECT * FROM orders WHERE id = ?", (order_id,))
        if row is None:
            return None
        lines = [OrderLine(ingredient_id=l["ingredient_id"],
                           ingredient_name=l["ingredient_name"], qty=l["qty"],
                           unit=l["unit"], pack_size=l["pack_size"],
                           rationale=l["rationale"])
                 for l in self.db.query(
                     "SELECT * FROM order_lines WHERE order_id = ? ORDER BY id",
                     (order_id,))]
        return Order(id=row["id"], supplier_id=row["supplier_id"],
                     status=OrderStatus(row["status"]), created_by=row["created_by"],
                     created_at=row["created_at"], service_date=row["service_date"],
                     lines=lines)

    def orders(self, status: Optional[str] = None) -> List[Order]:
        if status:
            rows = self.db.query(
                "SELECT id FROM orders WHERE status = ? ORDER BY created_at", (status,))
        else:
            rows = self.db.query("SELECT id FROM orders ORDER BY created_at")
        return [self.get_order(r["id"]) for r in rows]

    def set_status(self, order_id: str, status: OrderStatus) -> None:
        """Status changes, with one guard: APPROVED belongs to the human gate.

        WHY: the whole point of the review queue is that no code path can turn
        a draft into an approved order. Anything that could would be a bug that
        ships food nobody signed for.
        """
        if status is OrderStatus.APPROVED:
            raise ValueError(
                "orders are approved only through ReviewQueue.approve()")
        self.db.execute("UPDATE orders SET status = ? WHERE id = ?",
                        (status.value, order_id))

    # -- export ------------------------------------------------------------
    def export_order(self, order: Order) -> str:
        supplier = (self.deliveries.get_supplier(order.supplier_id)
                    if self.deliveries else None)
        header = f"PURCHASE ORDER {order.id}"
        lines = [header, "=" * len(header)]
        lines.append(f"Supplier: {supplier.name if supplier else order.supplier_id}")
        if supplier:
            lines.append(f"Order cutoff: {supplier.order_cutoff}")
        lines.append(f"For service: {order.service_date}")
        lines.append(f"Status: {order.status.value}")
        lines.append("")
        for line in order.lines:
            lines.append(f"  {line.qty:>10.2f} {line.unit:<5} {line.ingredient_name}")
            lines.append(f"             ({line.rationale})")
        lines.append("")
        lines.append(f"Prepared by the-pass agent at {order.created_at}. "
                     "Human approval required before sending.")
        return "\n".join(lines)

    # -- internal ----------------------------------------------------------
    def _persist(self, order: Order) -> None:
        self.db.execute(
            "INSERT INTO orders (id, supplier_id, status, created_by, created_at,"
            " service_date, rationale) VALUES (?,?,?,?,?,?,?)",
            (order.id, order.supplier_id, order.status.value, order.created_by,
             order.created_at, order.service_date, "curated from service plan"))
        for line in order.lines:
            self.db.execute(
                "INSERT INTO order_lines (order_id, ingredient_id, ingredient_name,"
                " qty, unit, pack_size, rationale) VALUES (?,?,?,?,?,?,?)",
                (order.id, line.ingredient_id, line.ingredient_name, line.qty,
                 line.unit, line.pack_size, line.rationale))
