"""The Pass — the nightly service journal (Prompt 23).

NOTE: this is the review artifact. One page, two minutes, every number pulled
from the night's own records: what we cooked, how fast, how well, what went
wrong, what the pit looked like, what the two rooms said to each other.

WHY the habit matters more than the report: stars are earned by reviewing the
night while it is still fresh. The agent's job is to make that review take two
minutes instead of an hour.

NOTE: every section degrades honestly. A missing module says "not tracked yet"
rather than printing a zero that looks like a bad service.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from core.config import Config
from core.database import Database
from core.events import EventBus

NOT_TRACKED = "not tracked yet"


@dataclass(frozen=True)
class ServiceJournal:
    service_date: str
    covers: int = 0
    dishes: Dict[str, Any] = field(default_factory=dict)
    pace: Dict[str, Any] = field(default_factory=dict)
    reviews: Dict[str, Any] = field(default_factory=dict)
    low_plates: List[Dict[str, Any]] = field(default_factory=list)
    temp_excursions: List[Dict[str, Any]] = field(default_factory=list)
    pit: Dict[str, Any] = field(default_factory=dict)
    comms: Dict[str, Any] = field(default_factory=dict)
    eighty_six: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[Dict[str, Any]] = field(default_factory=list)
    gaps: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ServiceJournal":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def markdown_report(self) -> str:
        lines = [f"# Service journal — {self.service_date}", ""]
        lines.append(f"**Covers:** {self.covers}")
        lines.append("")

        lines.append("## Plates")
        if self.dishes:
            for dish_id, counts in sorted(self.dishes.items()):
                gap = counts.get("gap", 0)
                flag = f" — {gap} fired but never plated" if gap else ""
                lines.append(f"- {dish_id}: {counts.get('plated', 0)} plated, "
                             f"{counts.get('fired', 0)} fired{flag}")
        else:
            lines.append(f"- {NOT_TRACKED}")
        if self.gaps:
            lines.append("")
            lines.append("**Pass bottlenecks:**")
            for gap in self.gaps:
                lines.append(f"- {gap['dish_id']}: {gap['gap']} plate(s) unaccounted for")
        lines.append("")

        lines.append("## Pace")
        if self.pace:
            lines.append(f"- {self.pace.get('pace_per_hour', 0):g} plates/hour")
            histogram = self.pace.get("per_hour", {})
            for hour, count in histogram.items():
                lines.append(f"    - {hour}:00 — {count}")
        else:
            lines.append(f"- {NOT_TRACKED}")
        lines.append("")

        lines.append("## How the plates looked")
        if self.reviews.get("n"):
            lines.append(f"- Average: {self.reviews.get('overall')} over "
                         f"{self.reviews.get('n')} scored plates")
            for low in self.low_plates:
                lines.append(f"- Lowest: {low['dish_id']} scored {low['score']} "
                             f"({low.get('note') or 'no note'})")
        else:
            lines.append(f"- {NOT_TRACKED}")
        lines.append("")

        lines.append("## Cold chain")
        if self.temp_excursions:
            for excursion in self.temp_excursions:
                lines.append(f"- {excursion['zone']} at {excursion['celsius']}C "
                             f"(bounds {excursion['bounds']}) {excursion['ts']}")
        else:
            lines.append("- No excursions logged")
        lines.append("")

        lines.append("## The pit")
        if self.pit:
            lines.append(f"- {self.pit.get('racks_done', 0)} racks, "
                         f"{self.pit.get('throughput_per_hour', 0):g}/hour, "
                         f"peak {self.pit.get('peak', {}).get('hour')}:00")
        else:
            lines.append(f"- {NOT_TRACKED}")
        lines.append("")

        lines.append("## Front and back of house")
        if self.comms:
            lines.append(f"- {self.comms.get('urgent_total', 0)} urgent calls, "
                         f"{self.comms.get('urgent_unacked', 0)} left unacknowledged, "
                         f"average ack {self.comms.get('avg_ack_seconds')}s")
        else:
            lines.append(f"- {NOT_TRACKED}")
        if self.eighty_six:
            lines.append("")
            lines.append("**86'd:**")
            for item in self.eighty_six:
                lines.append(f"- {item['ingredient_id']} ({item['source']})")
        lines.append("")

        if self.notes:
            lines.append("## Chef's notes")
            for note in self.notes:
                lines.append(f"- {note['note']} ({note.get('author', 'chef')})")
        return "\n".join(lines)


class Journal:
    def __init__(self, config: Config, db: Database, bus: Optional[EventBus] = None,
                 dish_counter: Any = None, review_logic: Any = None,
                 templog: Any = None, wash_counter: Any = None,
                 comms: Any = None) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.dish_counter = dish_counter
        self.review_logic = review_logic
        self.templog = templog
        self.wash_counter = wash_counter
        self.comms = comms

    # -- compile -----------------------------------------------------------
    def compile_service(self, service_date: Optional[str] = None) -> ServiceJournal:
        service_date = service_date or self.db.today()

        dishes: Dict[str, Any] = {}
        gaps: List[Dict[str, Any]] = []
        pace: Dict[str, Any] = {}
        if self.dish_counter is not None:
            dishes = self.dish_counter.per_service(service_date)
            gaps = self.dish_counter.gaps(service_date)
            pace = {"pace_per_hour": self.dish_counter.pace_per_hour(service_date),
                    "per_hour": self.dish_counter.per_hour_histogram(service_date)}

        reviews: Dict[str, Any] = {}
        low_plates: List[Dict[str, Any]] = []
        if self.review_logic is not None:
            reviews = self.review_logic.average_scores(service_date=service_date)
            low_plates = self._low_plates(service_date)

        excursions: List[Dict[str, Any]] = []
        if self.templog is not None:
            excursions = self.templog.excursion_report(since=f"{service_date}T00:00:00")

        pit: Dict[str, Any] = {}
        if self.wash_counter is not None:
            pit = self.wash_counter.summary(service_date)

        comms_summary: Dict[str, Any] = {}
        if self.comms is not None:
            comms_summary = self.comms.summary()

        eighty_six = [dict(r) for r in self.db.query(
            "SELECT ingredient_id, ts, source FROM eighty_six_log WHERE service_date = ?"
            " ORDER BY ts", (service_date,))]

        journal = ServiceJournal(
            service_date=service_date,
            covers=(self.dish_counter.covers_total(service_date)
                    if self.dish_counter else 0),
            dishes=dishes, pace=pace, reviews=reviews, low_plates=low_plates,
            temp_excursions=excursions, pit=pit, comms=comms_summary,
            eighty_six=eighty_six, notes=self.notes(service_date), gaps=gaps)

        self.db.execute(
            "INSERT OR REPLACE INTO journals (service_date, payload, created_at)"
            " VALUES (?,?,?)",
            (service_date, json.dumps(journal.to_dict()), self.db.now()))
        return journal

    def _low_plates(self, service_date: str, limit: int = 3) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT dish_id, note, (presentation + portion + color + execution)/4.0"
            " AS score FROM dish_reviews WHERE service_date = ?"
            " ORDER BY score ASC LIMIT ?", (service_date, limit))
        return [{"dish_id": r["dish_id"], "score": round(r["score"], 2),
                 "note": r["note"]} for r in rows]

    # -- reads -------------------------------------------------------------
    def get(self, service_date: Optional[str] = None) -> Optional[ServiceJournal]:
        service_date = service_date or self.db.today()
        row = self.db.query_one("SELECT payload FROM journals WHERE service_date = ?",
                                (service_date,))
        return ServiceJournal.from_dict(json.loads(row["payload"])) if row else None

    def markdown(self, service_date: Optional[str] = None) -> str:
        journal = self.get(service_date) or self.compile_service(service_date)
        return journal.markdown_report()

    def annotate(self, note: str, service_date: Optional[str] = None,
                 author: str = "chef") -> None:
        self.db.execute(
            "INSERT INTO journal_notes (service_date, note, author, ts)"
            " VALUES (?,?,?,?)",
            (service_date or self.db.today(), note, author, self.db.now()))

    def notes(self, service_date: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT note, author, ts FROM journal_notes WHERE service_date = ?"
            " ORDER BY id", (service_date or self.db.today(),))
        return [dict(r) for r in rows]

    def tick(self, when: Optional[Any] = None) -> Dict[str, Any]:
        journal = self.compile_service()
        return {"service_date": journal.service_date, "covers": journal.covers,
                "gaps": len(journal.gaps), "excursions": len(journal.temp_excursions)}
