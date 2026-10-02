"""The Pass — the human review gate (Prompt 12).

NOTE: this is the universal "the agent drafted it, a person decides" queue.
Orders are its first customer; curated ingredient lists, drift findings and
Michelin conclusions register here too.

WHY deadlines: a draft that nobody looks at is worse than no draft. Overdue
items escalate through the same reminder machinery the kitchen already uses.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from core.config import Config
from core.database import TS_FMT, Database
from core.events import (DRAFT_ORDER_READY, ORDER_APPROVED, REMINDER_DUE,
                         REVIEW_PENDING, EventBus)
from core.types import OrderStatus


@dataclass(frozen=True)
class ReviewItem:
    id: str
    item_type: str
    item_id: str
    summary: str
    payload: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending | approved | rejected | escalated
    deadline: str = ""
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ReviewQueue:
    def __init__(self, config: Config, db: Database, bus: Optional[EventBus] = None,
                 ordering: Any = None) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.ordering = ordering

    # -- submission --------------------------------------------------------
    def submit(self, item_type: str, item_id: str, summary: str,
               payload: Optional[Dict[str, Any]] = None,
               deadline: Optional[str] = None) -> ReviewItem:
        deadline = deadline or (datetime.now() + timedelta(
            minutes=self.config.review_lead_min)).strftime(TS_FMT)
        item = ReviewItem(id=f"rv-{uuid.uuid4().hex[:8]}", item_type=item_type,
                          item_id=item_id, summary=summary,
                          payload=payload or {}, deadline=deadline,
                          created_at=self.db.now())
        self.db.execute(
            "INSERT INTO review_items (id, item_type, item_id, summary, payload,"
            " status, deadline, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (item.id, item.item_type, item.item_id, item.summary,
             json.dumps(item.payload), item.status, item.deadline, item.created_at))
        if self.bus is not None:
            self.bus.publish(REVIEW_PENDING, item.to_dict())
        return item

    def submit_order(self, order: Any) -> ReviewItem:
        """Convenience: queue a curated draft order for chef approval."""
        lines = len(getattr(order, "lines", []) or [])
        return self.submit("order", order.id,
                           f"Approve purchase order for {order.supplier_id} "
                           f"({lines} lines)",
                           {"supplier_id": order.supplier_id,
                            "service_date": order.service_date, "lines": lines})

    # -- reads -------------------------------------------------------------
    def _to_item(self, row) -> ReviewItem:
        return ReviewItem(id=row["id"], item_type=row["item_type"],
                          item_id=row["item_id"], summary=row["summary"],
                          payload=json.loads(row["payload"] or "{}"),
                          status=row["status"], deadline=row["deadline"],
                          created_at=row["created_at"])

    def get(self, review_id: str) -> Optional[ReviewItem]:
        row = self.db.query_one("SELECT * FROM review_items WHERE id = ?",
                                (review_id,))
        return self._to_item(row) if row else None

    def pending(self) -> List[ReviewItem]:
        rows = self.db.query(
            "SELECT * FROM review_items WHERE status = 'pending' ORDER BY deadline")
        return [self._to_item(r) for r in rows]

    def overdue(self, now: Optional[str] = None) -> List[ReviewItem]:
        now = now or self.db.now()
        return [item for item in self.pending() if item.deadline and item.deadline <= now]

    def log(self, review_id: str) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM review_log WHERE review_id = ? ORDER BY id", (review_id,))
        return [dict(r) for r in rows]

    # -- decisions ---------------------------------------------------------
    def approve(self, review_id: str, edits: Optional[Dict[str, Any]] = None,
                actor: str = "chef") -> ReviewItem:
        """THE HUMAN GATE. Applies any edits, then marks the item approved."""
        item = self.get(review_id)
        if item is None:
            raise KeyError(f"no such review item: {review_id}")
        if item.status != "pending":
            raise ValueError(f"review item already {item.status}")

        edits = edits or {}
        if item.item_type == "order":
            self._apply_order_edits(item.item_id, edits)
            self._set_order_status(item.item_id, OrderStatus.APPROVED)
            if self.bus is not None:
                self.bus.publish(ORDER_APPROVED, {"order_id": item.item_id,
                                                  "approved_by": actor})

        self.db.execute("UPDATE review_items SET status = 'approved' WHERE id = ?",
                        (review_id,))
        self._audit(review_id, "approved", actor,
                    json.dumps(edits) if edits else "")
        return self.get(review_id)

    def reject(self, review_id: str, reason: str, actor: str = "chef") -> ReviewItem:
        """A rejection must say why — a silent no teaches the agent nothing."""
        if not reason or not reason.strip():
            raise ValueError("a rejection requires a reason")
        item = self.get(review_id)
        if item is None:
            raise KeyError(f"no such review item: {review_id}")
        if item.item_type == "order":
            self._set_order_status(item.item_id, OrderStatus.DRAFT)
        self.db.execute("UPDATE review_items SET status = 'rejected' WHERE id = ?",
                        (review_id,))
        self._audit(review_id, "rejected", actor, reason.strip())
        return self.get(review_id)

    def escalate(self, review_id: str, actor: str = "agent") -> ReviewItem:
        item = self.get(review_id)
        if item is None:
            raise KeyError(f"no such review item: {review_id}")
        self.db.execute("UPDATE review_items SET status = 'escalated' WHERE id = ?",
                        (review_id,))
        self._audit(review_id, "escalated", actor, "deadline passed")
        if self.bus is not None:
            self.bus.publish(REMINDER_DUE, {"audience": "both", "urgency": "urgent",
                                            "title": f"Waiting on you: {item.summary}",
                                            "review_id": review_id})
        return self.get(review_id)

    # -- clock -------------------------------------------------------------
    def tick(self, when: Optional[datetime] = None) -> Dict[str, Any]:
        """Escalate anything overdue; report what is still waiting."""
        when = when or datetime.now()
        overdue = self.overdue(now=when.strftime(TS_FMT))
        escalated = []
        for item in overdue:
            if item.status == "pending":
                self.escalate(item.id)
                escalated.append(item.id)
        return {"pending": len(self.pending()), "escalated": escalated}

    # -- internal ----------------------------------------------------------
    def _apply_order_edits(self, order_id: str, edits: Dict[str, Any]) -> None:
        """Chef edits keyed by ingredient_id: {"qty": n} or {"remove": True}."""
        for ingredient_id, change in (edits or {}).items():
            if not isinstance(change, dict):
                continue
            if change.get("remove"):
                self.db.execute(
                    "DELETE FROM order_lines WHERE order_id = ? AND ingredient_id = ?",
                    (order_id, ingredient_id))
                continue
            if "qty" in change:
                self.db.execute(
                    "UPDATE order_lines SET qty = ?, rationale = rationale || ?"
                    " WHERE order_id = ? AND ingredient_id = ?",
                    (float(change["qty"]), " [edited by chef]", order_id,
                     ingredient_id))

    def _set_order_status(self, order_id: str, status: OrderStatus) -> None:
        # deliberate direct write: this is the gate the guard in Ordering
        # (set_status refuses APPROVED) funnels everything through.
        self.db.execute("UPDATE orders SET status = ? WHERE id = ?",
                        (status.value, order_id))

    def _audit(self, review_id: str, decision: str, actor: str, reason: str) -> None:
        self.db.execute(
            "INSERT INTO review_log (review_id, decision, actor, reason, ts)"
            " VALUES (?,?,?,?,?)",
            (review_id, decision, actor, reason, self.db.now()))
