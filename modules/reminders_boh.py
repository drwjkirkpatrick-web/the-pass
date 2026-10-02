"""The Pass — BOH reminders and the shared reminder engine (Prompt 14).

NOTE: ReminderEngine is the shared clock-driven piece. BOH and FOH both use it;
neither owns it, so there is exactly one implementation of "when is it due",
"what happens if nobody acknowledges", and "does this repeat".

WHY re-fire: an urgent reminder nobody acknowledged is an urgent reminder that
did not happen. It repeats until a person closes it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from core.config import Config
from core.database import TS_FMT, Database
from core.events import REMINDER_DUE, EventBus
from core.types import Reminder, filter_fields


class ReminderEngine:
    def __init__(self, config: Config, db: Database,
                 bus: Optional[EventBus] = None) -> None:
        self.config = config
        self.db = db
        self.bus = bus

    # -- scheduling --------------------------------------------------------
    def schedule(self, audience: str, urgency: str, title: str, body: str = "",
                 fire_at: Optional[str] = None, recurrence: str = "none",
                 dedupe_key: str = "") -> Reminder:
        fire_at = fire_at or self.db.now()
        if dedupe_key:
            existing = self.db.query_one(
                "SELECT * FROM reminders WHERE dedupe_key = ? AND status IN"
                " ('scheduled','fired')", (dedupe_key,))
            if existing:
                return self._to_reminder(existing)
        reminder = Reminder(id=f"rem-{uuid.uuid4().hex[:8]}", audience=audience,
                            urgency=urgency, title=title, body=body,
                            fire_at=fire_at, recurrence=recurrence,
                            dedupe_key=dedupe_key)
        self.db.execute(
            "INSERT INTO reminders (id, audience, urgency, title, body, fire_at,"
            " recurrence, status, dedupe_key, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (reminder.id, reminder.audience, reminder.urgency, reminder.title,
             reminder.body, reminder.fire_at, reminder.recurrence, reminder.status,
             reminder.dedupe_key, self.db.now()))
        return reminder

    # -- firing ------------------------------------------------------------
    def due(self, when: Optional[datetime] = None) -> List[Reminder]:
        when = when or datetime.now()
        stamp = when.strftime(TS_FMT)
        rows = self.db.query(
            "SELECT * FROM reminders WHERE status = 'scheduled' AND fire_at <= ?"
            " ORDER BY fire_at", (stamp,))
        fired: List[Reminder] = []
        for row in rows:
            reminder = self._to_reminder(row)
            self.db.execute(
                "UPDATE reminders SET status = 'fired', last_notify_at = ?"
                " WHERE id = ?", (stamp, reminder.id))
            if self.bus is not None:
                self.bus.publish(REMINDER_DUE, {
                    "id": reminder.id, "audience": reminder.audience,
                    "urgency": reminder.urgency, "title": reminder.title,
                    "body": reminder.body})
            nxt = self._next_occurrence(reminder, when)
            if nxt:
                self.schedule(reminder.audience, reminder.urgency, reminder.title,
                              reminder.body, fire_at=nxt,
                              recurrence=reminder.recurrence)
            fired.append(self.get(reminder.id))
        return fired

    def refire(self, when: Optional[datetime] = None) -> List[Reminder]:
        """Urgent, unacknowledged, and past the cooldown -> say it again."""
        when = when or datetime.now()
        stamp = when.strftime(TS_FMT)
        cutoff = (when - timedelta(minutes=self.config.refire_cooldown_min)).strftime(TS_FMT)
        rows = self.db.query(
            "SELECT * FROM reminders WHERE urgency = 'urgent' AND status = 'fired'"
            " AND (last_notify_at = '' OR last_notify_at <= ?)", (cutoff,))
        out: List[Reminder] = []
        for row in rows:
            reminder = self._to_reminder(row)
            self.db.execute("UPDATE reminders SET last_notify_at = ? WHERE id = ?",
                            (stamp, reminder.id))
            if self.bus is not None:
                self.bus.publish(REMINDER_DUE, {
                    "id": reminder.id, "audience": reminder.audience,
                    "urgency": "urgent", "title": reminder.title,
                    "body": reminder.body, "repeat": True})
            out.append(reminder)
        return out

    def ack(self, reminder_id: str, by: str = "") -> Optional[Reminder]:
        self.db.execute(
            "UPDATE reminders SET status = 'acked', ack_at = ?, ack_by = ?"
            " WHERE id = ?", (self.db.now(), by, reminder_id))
        return self.get(reminder_id)

    # -- reads -------------------------------------------------------------
    def _to_reminder(self, row) -> Reminder:
        return Reminder(**filter_fields(Reminder, {k: row[k] for k in row.keys()}))

    def get(self, reminder_id: str) -> Optional[Reminder]:
        row = self.db.query_one("SELECT * FROM reminders WHERE id = ?", (reminder_id,))
        return self._to_reminder(row) if row else None

    def pending(self, audience: Optional[str] = None) -> List[Reminder]:
        """Reminders still awaiting a human: scheduled, or fired and unacked.

        WHY fired items count as pending: a fired reminder nobody acknowledged
        is exactly the thing the kitchen still owes attention to.
        """
        if audience:
            rows = self.db.query(
                "SELECT * FROM reminders WHERE status IN ('scheduled','fired')"
                " AND audience IN (?, 'both') ORDER BY fire_at", (audience,))
        else:
            rows = self.db.query(
                "SELECT * FROM reminders WHERE status IN ('scheduled','fired')"
                " ORDER BY fire_at")
        return [self._to_reminder(r) for r in rows]

    # -- clock -------------------------------------------------------------
    def tick(self, when: Optional[datetime] = None) -> Dict[str, Any]:
        when = when or datetime.now()
        fired = self.due(when)
        repeated = self.refire(when)
        return {"fired": len(fired), "repeated": len(repeated),
                "pending": len(self.pending())}

    # -- internal ----------------------------------------------------------
    def _next_occurrence(self, reminder: Reminder,
                         when: datetime) -> Optional[str]:
        if reminder.recurrence == "daily":
            return (self.db.parse(reminder.fire_at) + timedelta(days=1)).strftime(TS_FMT)
        if reminder.recurrence == "pre_service":
            start = datetime.combine(when.date(),
                                     datetime.strptime(self.config.service_start,
                                                       "%H:%M").time())
            candidate = start - timedelta(minutes=self.config.reminder_lead_min)
            if candidate <= when:
                candidate += timedelta(days=1)
            return candidate.strftime(TS_FMT)
        return None


class BOHReminders:
    """Back-of-house: prep timers, station checks, ordering deadlines."""

    def __init__(self, config: Config, db: Database,
                 bus: Optional[EventBus] = None,
                 engine: Optional[ReminderEngine] = None) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.engine = engine or ReminderEngine(config, db, bus)

    def prep_timer(self, name: str, minutes: int,
                   when: Optional[datetime] = None) -> Reminder:
        when = when or datetime.now()
        fire_at = (when + timedelta(minutes=minutes)).strftime(TS_FMT)
        return self.engine.schedule("boh", "urgent", f"Prep timer: {name}",
                                    f"{minutes} minutes are up — check it.",
                                    fire_at=fire_at,
                                    dedupe_key=f"prep-{name}-{fire_at}")

    def mise_check(self, when: Optional[datetime] = None) -> Reminder:
        """Station mise en place, T-minus the configured lead before service."""
        when = when or datetime.now()
        start = datetime.combine(when.date(),
                                 datetime.strptime(self.config.service_start,
                                                   "%H:%M").time())
        fire_at = (start - timedelta(minutes=self.config.reminder_lead_min)).strftime(TS_FMT)
        return self.engine.schedule(
            "boh", "routine", "Mise en place check",
            "Stations set, garnish trays prepped, pass wiped down.",
            fire_at=fire_at, recurrence="pre_service",
            dedupe_key=f"mise-{when.date().isoformat()}")

    def ordering_deadline(self, supplier_name: str, cutoff: str,
                          when: Optional[datetime] = None) -> Reminder:
        """Heads-up before a supplier stops taking orders today."""
        when = when or datetime.now()
        cutoff_dt = datetime.combine(when.date(), datetime.strptime(cutoff, "%H:%M").time())
        fire_at = (cutoff_dt - timedelta(minutes=self.config.review_lead_min)).strftime(TS_FMT)
        return self.engine.schedule(
            "boh", "urgent", f"Order cutoff: {supplier_name}",
            f"Orders for {supplier_name} close at {cutoff}.",
            fire_at=fire_at,
            dedupe_key=f"cutoff-{supplier_name}-{when.date().isoformat()}")

    def delivery_expected(self, supplier_name: str, at: str) -> Reminder:
        return self.engine.schedule(
            "boh", "routine", f"Delivery expected: {supplier_name}",
            "Check the drop against the order before signing.",
            fire_at=at, dedupe_key=f"delivery-{supplier_name}-{at}")

    def family_meal(self, at: str) -> Reminder:
        return self.engine.schedule("boh", "routine", "Family meal",
                                    "Feed the team before doors.",
                                    fire_at=at, recurrence="daily",
                                    dedupe_key=f"family-{at}")

    def close_checklist(self, at: Optional[str] = None) -> Reminder:
        return self.engine.schedule(
            "boh", "routine", "BOH close-down",
            "Label and date everything, log temps, sweep the line, kill the flat top.",
            fire_at=at or self.config.service_end,
            dedupe_key=f"boh-close-{self.db.today()}")

    def tick(self, when: Optional[datetime] = None) -> Dict[str, Any]:
        return self.engine.tick(when)
