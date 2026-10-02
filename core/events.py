"""The Pass — in-process event bus (Prompt 03).

NOTE: modules do not call each other to announce things; they publish events.
WHY: with 30 modules, direct calls would be spaghetti. One bus means there is
exactly one place to see who reacts to what.

NOTE: a listener that raises must never take the kitchen down with it — its
error is recorded on the bus and the remaining listeners still run.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

SERVICE_OPEN = "service.open"
SERVICE_CLOSE = "service.close"
INGREDIENTS_UPDATED = "ingredients.updated"
MENU_PLANNED = "menu.planned"
DRAFT_ORDER_READY = "order.draft_ready"
ORDER_APPROVED = "order.approved"
TEMP_EXCURSION = "temp.excursion"
REMINDER_DUE = "reminder.due"
MESSAGE_URGENT = "message.urgent"
DISH_PLATED = "dish.plated"
DISH_REVIEWED = "dish.reviewed"
WASH_CYCLE_DONE = "wash.cycle_done"
WASH_BACKLOG = "wash.backlog"
REVIEW_PENDING = "review.pending"

ALL_EVENTS: Tuple[str, ...] = (
    SERVICE_OPEN, SERVICE_CLOSE, INGREDIENTS_UPDATED, MENU_PLANNED,
    DRAFT_ORDER_READY, ORDER_APPROVED, TEMP_EXCURSION, REMINDER_DUE,
    MESSAGE_URGENT, DISH_PLATED, DISH_REVIEWED, WASH_CYCLE_DONE,
    WASH_BACKLOG, REVIEW_PENDING,
)

Handler = Callable[[str, Any], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: Dict[str, List[Handler]] = {}
        self.errors: List[Tuple[str, str]] = []

    def subscribe(self, event_type: str, handler: Handler) -> Handler:
        self._handlers.setdefault(event_type, []).append(handler)
        return handler

    def publish(self, event_type: str, payload: Any = None) -> int:
        """Deliver to every listener; returns how many ran without raising."""
        delivered = 0
        for handler in list(self._handlers.get(event_type, [])):
            try:
                handler(event_type, payload)
                delivered += 1
            except Exception as exc:  # isolation is the point
                self.errors.append((event_type, repr(exc)))
        return delivered

    def listeners(self, event_type: str) -> int:
        return len(self._handlers.get(event_type, []))

    def subscribed_types(self) -> List[str]:
        return sorted(t for t, h in self._handlers.items() if h)

    def clear_errors(self) -> None:
        self.errors = []
