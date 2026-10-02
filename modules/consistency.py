"""The Pass — consistency scoring and drift detection (Prompt 24).

NOTE: Michelin's fifth criterion is consistency, and consistency is exactly the
thing a kitchen cannot feel from the inside. This module measures it: is dish X
drifting — in scores, in plating, in timing, in temperature?

WHY plain-Python statistics: the Jetson runs this nightly. Variance and a
least-squares slope are a dozen lines; a numeric dependency is not worth it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.config import Config
from core.database import Database
from core.events import EventBus
from core.types import Role


def mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def variance(values: List[float]) -> float:
    """Population variance — how erratic the dish is service to service."""
    if len(values) < 2:
        return 0.0
    avg = mean(values)
    return sum((v - avg) ** 2 for v in values) / len(values)


def slope(values: List[float]) -> float:
    """Least-squares slope per step. Negative = getting worse."""
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    x_bar, y_bar = mean([float(x) for x in xs]), mean(values)
    numerator = sum((x - x_bar) * (y - y_bar) for x, y in zip(xs, values))
    denominator = sum((x - x_bar) ** 2 for x in xs)
    return numerator / denominator if denominator else 0.0


@dataclass(frozen=True)
class DriftReport:
    dish_id: str
    drift_score: float
    metrics: Dict[str, Any]
    findings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {"dish_id": self.dish_id, "drift_score": self.drift_score,
                "metrics": self.metrics, "findings": self.findings}


class Consistency:
    def __init__(self, config: Config, db: Database, bus: Optional[EventBus] = None,
                 kanban: Any = None, boards: Any = None, templog: Any = None) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.kanban = kanban
        self.boards = boards
        self.templog = templog

    # -- series ------------------------------------------------------------
    def review_series(self, dish_id: str) -> List[float]:
        rows = self.db.query(
            "SELECT service_date, AVG((presentation + portion + color + execution)/4.0)"
            " AS score FROM dish_reviews WHERE dish_id = ?"
            " GROUP BY service_date ORDER BY service_date", (dish_id,))
        return [float(r["score"]) for r in rows]

    def timing_series(self, dish_id: str) -> List[float]:
        """Seconds from fired to plated, averaged per service.

        NOTE: read from the append-only dish events, so a plate that was fired
        and never plated simply does not contribute a timing.
        """
        rows = self.db.query(
            "SELECT service_date, ts, action FROM dish_events WHERE dish_id = ?"
            " ORDER BY id", (dish_id,))
        fired: Dict[str, List[str]] = {}
        for row in rows:
            if row["action"] == "fired":
                fired.setdefault(row["service_date"], []).append(row["ts"])
        plated: Dict[str, List[str]] = {}
        for row in rows:
            if row["action"] == "plated":
                plated.setdefault(row["service_date"], []).append(row["ts"])
        series = []
        for day in sorted(set(fired) & set(plated)):
            pairs = min(len(fired[day]), len(plated[day]))
            if not pairs:
                continue
            deltas = []
            for i in range(pairs):
                start = self.db.parse(fired[day][i])
                end = self.db.parse(plated[day][i])
                deltas.append(max(0.0, (end - start).total_seconds()))
            series.append(mean(deltas))
        return series

    def plating_series(self, dish_id: str, metric: str = "portion") -> List[float]:
        rows = self.db.query(
            "SELECT metrics FROM dish_photos WHERE dish_id = ? ORDER BY ts", (dish_id,))
        import json
        out = []
        for row in rows:
            metrics = json.loads(row["metrics"] or "{}")
            if metric in metrics:
                out.append(float(metrics[metric]))
        return out

    def excursion_count(self, dish_id: str) -> int:
        if self.templog is None:
            return 0
        station_row = self.db.query_one(
            "SELECT station FROM recipes WHERE id = ?", (dish_id,))
        station = station_row["station"] if station_row else None
        if not station:
            return 0
        zones = [z.id for z in self.templog.zones() if z.station == station]
        if not zones:
            return 0
        placeholders = ",".join("?" for _ in zones)
        return int(self.db.scalar(
            f"SELECT COUNT(*) FROM temp_readings WHERE in_bounds = 0 AND zone_id IN"
            f" ({placeholders})", tuple(zones)))

    # -- the report --------------------------------------------------------
    def drift_report(self, dish_id: str) -> DriftReport:
        scores = self.review_series(dish_id)
        timings = self.timing_series(dish_id)
        plating = self.plating_series(dish_id, "portion")
        excursions = self.excursion_count(dish_id)

        metrics: Dict[str, Any] = {
            "services": len(scores),
            "score_first": round(scores[0], 2) if scores else None,
            "score_last": round(scores[-1], 2) if scores else None,
            "score_change": round(scores[-1] - scores[0], 2) if len(scores) > 1 else 0.0,
            "score_variance": round(variance(scores), 3),
            "score_trend": round(slope(scores), 3),
            "timing_variance": round(variance(timings), 1),
            "plating_first": round(plating[0], 3) if plating else None,
            "plating_last": round(plating[-1], 3) if plating else None,
            "temp_excursions": excursions,
        }

        findings: List[str] = []
        penalty = 0.0

        change = metrics["score_change"]
        if len(scores) > 1 and change <= -self.config.score_drop_threshold:
            findings.append(
                f"Review scores down {abs(change):.2f} over {len(scores)} services")
            penalty += min(40.0, abs(change) * 20.0)
        elif len(scores) > 1 and change >= self.config.score_drop_threshold:
            findings.append(
                f"Review scores up {change:.2f} over {len(scores)} services")

        if metrics["score_variance"] > 0.25:
            findings.append(
                f"Scores are erratic (variance {metrics['score_variance']})")
            penalty += min(25.0, metrics["score_variance"] * 40.0)

        if plating and len(plating) > 1:
            delta = plating[-1] - plating[0]
            if abs(delta) >= self.config.portion_delta_threshold:
                direction = "lighter" if delta < 0 else "heavier"
                findings.append(
                    f"Plating photos suggest portions {direction} by "
                    f"{abs(delta) * 100:.0f}% since the first service")
                penalty += min(20.0, abs(delta) * 60.0)

        if metrics["timing_variance"] > 400:
            findings.append(
                f"Plate timing is uneven (variance {metrics['timing_variance']}s)")
            penalty += min(10.0, metrics["timing_variance"] / 100.0)

        if excursions:
            findings.append(f"{excursions} temperature excursion(s) on this station")
            penalty += min(15.0, excursions * 3.0)

        if not findings:
            findings.append("No drift detected in the recorded history")

        return DriftReport(dish_id=dish_id, drift_score=round(min(100.0, penalty), 1),
                           metrics=metrics, findings=findings)

    def dish_ids(self) -> List[str]:
        rows = self.db.query(
            "SELECT DISTINCT dish_id FROM dish_reviews UNION"
            " SELECT DISTINCT dish_id FROM dish_events ORDER BY dish_id")
        return [r["dish_id"] for r in rows]

    def worst_drifts(self, limit: int = 5) -> List[DriftReport]:
        reports = [self.drift_report(dish_id) for dish_id in self.dish_ids()]
        return sorted(reports, key=lambda r: -r.drift_score)[:limit]

    # -- clock -------------------------------------------------------------
    def tick(self, when: Optional[Any] = None) -> Dict[str, Any]:
        """Anything past threshold becomes a card on the agent board."""
        flagged = []
        for report in self.worst_drifts(limit=10):
            if report.drift_score < 20.0:
                continue
            if self.kanban is not None and self.boards is not None:
                board = self.boards.agent_board()
                title = f"Drift: {report.dish_id}"
                existing = self.db.query_one(
                    "SELECT id FROM kanban_cards WHERE board_id = ? AND title = ?"
                    " AND column != 'Done'", (board["id"], title))
                if not existing:
                    self.kanban.add_card(board["id"], title,
                                         "; ".join(report.findings), "Backlog",
                                         Role.CHEF.value)
                    flagged.append(title)
        return {"flagged": flagged, "count": len(flagged)}
