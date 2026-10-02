"""The Pass — the FOH <-> BOH communication line (Prompt 20).

NOTE: two lanes, because two different things are being asked for.
URGENT means "stop what you are doing and read this": 86s, allergies, a dish
dying on the pass. NON_URGENT means "worth knowing at the next natural pause":
we need more rosemary, table 12 is lingering.

WHY write-ahead: the message row is committed BEFORE anything is announced. A
notification that fails must never lose the fact that somebody said it.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from core.config import Config
from core.database import TS_FMT, Database
from core.events import MESSAGE_URGENT, EventBus
from core.types import Channel, PassMessage, filter_fields

TEMPLATES = {
    "86": "86 {item} — {note}",
    "fire": "Fire {count} x {dish} for table {table}",
    "hold": "Hold {dish} for table {table} — {note}",
    "all_day": "All day: {count} x {dish}",
    "rush": "Rush: {dish} for table {table}",
    "box": "Box {dish} for table {table}",
}


class Comms:
    def __init__(self, config: Config, db: Database,
                 bus: Optional[EventBus] = None) -> None:
        self.config = config
        self.db = db
        self.bus = bus

    # -- sending -----------------------------------------------------------
    def send(self, sender_role: str, channel: str, body: str,
             now: Optional[datetime] = None) -> PassMessage:
        now = now or datetime.now()
        stamp = now.strftime(TS_FMT)
        digest = hashlib.sha1(f"{channel}:{body}".encode("utf-8")).hexdigest()
        window_start = (now - timedelta(seconds=self.config.dedupe_window_sec)).strftime(TS_FMT)
        existing = self.db.query_one(
            "SELECT * FROM pass_messages WHERE dedupe_hash = ? AND ts >= ?"
            " ORDER BY ts DESC LIMIT 1", (digest, window_start))
        if existing:
            # double-tap protection: the same shout twice is one shout
            return self._to_message(existing)

        message = PassMessage(id=f"m-{uuid.uuid4().hex[:8]}", channel=channel,
                              sender_role=sender_role, body=body, ts=stamp)
        self.db.execute(
            "INSERT INTO pass_messages (id, channel, sender_role, body, ts, acked,"
            " last_notify_at, dedupe_hash) VALUES (?,?,?,?,?,0,?,?)",
            (message.id, message.channel, message.sender_role, message.body,
             message.ts, stamp, digest))
        if channel == Channel.URGENT.value and self.bus is not None:
            self.bus.publish(MESSAGE_URGENT, {
                "id": message.id, "sender_role": sender_role, "body": body,
                "channel": channel})
        return message

    def template(self, name: str, **kwargs: Any) -> str:
        if name not in TEMPLATES:
            raise KeyError(f"unknown template: {name}")
        return TEMPLATES[name].format(**kwargs)

    # -- acknowledging -----------------------------------------------------
    def ack(self, message_id: str, by: str = "") -> Optional[PassMessage]:
        self.db.execute(
            "UPDATE pass_messages SET acked = 1, ack_at = ?, ack_by = ? WHERE id = ?",
            (self.db.now(), by, message_id))
        return self.get(message_id)

    def unacked_urgent(self) -> List[PassMessage]:
        rows = self.db.query(
            "SELECT * FROM pass_messages WHERE channel = ? AND acked = 0"
            " ORDER BY ts", (Channel.URGENT.value,))
        return [self._to_message(r) for r in rows]

    def refire(self, now: Optional[datetime] = None) -> List[PassMessage]:
        """An urgent message nobody acknowledged gets said again."""
        now = now or datetime.now()
        cutoff = (now - timedelta(minutes=self.config.refire_cooldown_min)).strftime(TS_FMT)
        out = []
        for message in self.unacked_urgent():
            row = self.db.query_one("SELECT last_notify_at FROM pass_messages WHERE id = ?",
                                    (message.id,))
            last = (row["last_notify_at"] if row else "") or ""
            if last and last > cutoff:
                continue
            self.db.execute("UPDATE pass_messages SET last_notify_at = ? WHERE id = ?",
                            (now.strftime(TS_FMT), message.id))
            if self.bus is not None:
                self.bus.publish(MESSAGE_URGENT, {
                    "id": message.id, "sender_role": message.sender_role,
                    "body": message.body, "channel": Channel.URGENT.value,
                    "repeat": True})
            out.append(message)
        return out

    # -- reading -----------------------------------------------------------
    def _to_message(self, row) -> PassMessage:
        data = {k: row[k] for k in row.keys()}
        data["acked"] = bool(data.get("acked"))
        return PassMessage(**filter_fields(PassMessage, data))

    def get(self, message_id: str) -> Optional[PassMessage]:
        row = self.db.query_one("SELECT * FROM pass_messages WHERE id = ?",
                                (message_id,))
        return self._to_message(row) if row else None

    def feed(self, role: str) -> Dict[str, List[PassMessage]]:
        """What each side sees: unacked urgents, plus the recent digest."""
        urgents = [m for m in self.unacked_urgent() if m.sender_role != role]
        recent = self.db.query(
            "SELECT * FROM pass_messages WHERE channel = ? ORDER BY ts DESC LIMIT 20",
            (Channel.NON_URGENT.value,))
        non_urgent = [self._to_message(r) for r in recent]
        return {"urgent": urgents, "non_urgent": non_urgent}

    def digest(self, minutes: int = 10,
               now: Optional[datetime] = None) -> List[PassMessage]:
        """The non-urgent lane, batched for the next natural pause."""
        now = now or datetime.now()
        since = (now - timedelta(minutes=minutes)).strftime(TS_FMT)
        rows = self.db.query(
            "SELECT * FROM pass_messages WHERE channel = ? AND ts >= ? ORDER BY ts",
            (Channel.NON_URGENT.value, since))
        return [self._to_message(r) for r in rows]

    def transcript(self, since: Optional[str] = None,
                   limit: int = 200) -> List[PassMessage]:
        """The whole service, afterwards — 'what did FOH ask for at 19:40?'"""
        if since:
            rows = self.db.query(
                "SELECT * FROM pass_messages WHERE ts >= ? ORDER BY ts LIMIT ?",
                (since, limit))
        else:
            rows = self.db.query(
                "SELECT * FROM pass_messages ORDER BY ts LIMIT ?", (limit,))
        return [self._to_message(r) for r in rows]

    # -- clock -------------------------------------------------------------
    def tick(self, when: Optional[datetime] = None) -> Dict[str, Any]:
        repeated = self.refire(when)
        return {"unacked_urgent": len(self.unacked_urgent()),
                "repeated": len(repeated),
                "digest": len(self.digest(now=when))}

    def summary(self) -> Dict[str, Any]:
        urgents = self.db.query(
            "SELECT acked, ts, ack_at FROM pass_messages WHERE channel = ?",
            (Channel.URGENT.value,))
        acked = [r for r in urgents if r["acked"]]
        latencies = []
        for row in acked:
            try:
                latencies.append((self.db.parse(row["ack_at"])
                                  - self.db.parse(row["ts"])).total_seconds())
            except (ValueError, TypeError):
                continue
        return {
            "urgent_total": len(urgents),
            "urgent_acked": len(acked),
            "urgent_unacked": len(urgents) - len(acked),
            "avg_ack_seconds": round(sum(latencies) / len(latencies), 1) if latencies else None,
            "non_urgent_total": int(self.db.scalar(
                "SELECT COUNT(*) FROM pass_messages WHERE channel = ?",
                (Channel.NON_URGENT.value,))),
        }
