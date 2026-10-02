"""The Pass — the live dish review app (Prompt 19).

NOTE: the chef scores a plate in seconds, on a phone, standing at the pass. The
scoring logic is plain Python; the web layer is a thin optional shell so the
agent works headless (CLI, Telegram) on a machine without Flask installed.

WHY it never blocks the line: an unreviewed plate is a missing data point, not
a stopped service. Unreviewed photos queue on the agent's board instead.
"""
from __future__ import annotations

import html
import uuid
from typing import Any, Dict, List, Optional

from core.config import Config
from core.database import Database
from core.events import DISH_REVIEWED, EventBus
from core.types import DishReview, filter_fields

SCORE_FIELDS = ("presentation", "portion", "color", "execution")


class ReviewLogic:
    def __init__(self, config: Config, db: Database, bus: Optional[EventBus] = None,
                 pass_photo: Any = None, dish_counter: Any = None) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.pass_photo = pass_photo
        self.dish_counter = dish_counter

    # -- the queue ---------------------------------------------------------
    def next_pending(self) -> Optional[Dict[str, Any]]:
        """The oldest unreviewed plate (oldest first: nothing starves)."""
        if self.pass_photo is not None:
            pending = self.pass_photo.unreviewed(limit=1)
            return pending[0] if pending else None
        row = self.db.query_one(
            "SELECT * FROM dish_photos WHERE pending_review = 1 ORDER BY ts ASC LIMIT 1")
        return dict(row) if row else None

    # -- scoring -----------------------------------------------------------
    def record_score(self, photo_id: str, scores: Dict[str, int],
                     note: str = "", reviewer: str = "chef") -> DishReview:
        photo = (self.pass_photo.get(photo_id) if self.pass_photo is not None else
                 self.db.query_one("SELECT * FROM dish_photos WHERE id = ?", (photo_id,)))
        if photo is None:
            raise KeyError(f"no such photo: {photo_id}")
        dish_id = photo["dish_id"]
        cleaned = {field: int(scores.get(field, 0)) for field in SCORE_FIELDS}
        review = DishReview(id=f"rv-{uuid.uuid4().hex[:8]}", dish_id=dish_id,
                            photo_id=photo_id, note=note or "", reviewer=reviewer,
                            ts=self.db.now(), service_date=photo["service_date"],
                            **cleaned)
        self.db.execute(
            "INSERT INTO dish_reviews (id, photo_id, dish_id, presentation, portion,"
            " color, execution, note, reviewer, ts, service_date)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (review.id, review.photo_id, review.dish_id, review.presentation,
             review.portion, review.color, review.execution, review.note,
             review.reviewer, review.ts, review.service_date))
        self.db.execute("UPDATE dish_photos SET pending_review = 0 WHERE id = ?",
                        (photo_id,))
        if self.bus is not None:
            self.bus.publish(DISH_REVIEWED, review.to_dict())
        return review

    def average_scores(self, dish_id: Optional[str] = None,
                       service_date: Optional[str] = None) -> Dict[str, Any]:
        sql = ("SELECT AVG(presentation) AS presentation, AVG(portion) AS portion,"
               " AVG(color) AS color, AVG(execution) AS execution, COUNT(*) AS n"
               " FROM dish_reviews WHERE 1=1")
        params: List[Any] = []
        if dish_id:
            sql += " AND dish_id = ?"
            params.append(dish_id)
        if service_date:
            sql += " AND service_date = ?"
            params.append(service_date)
        row = self.db.query_one(sql, tuple(params))
        if row is None or not row["n"]:
            return {"n": 0}
        out = {field: round(row[field], 2) for field in SCORE_FIELDS}
        out["n"] = row["n"]
        out["overall"] = round(sum(out[f] for f in SCORE_FIELDS) / 4.0, 2)
        return out

    def trend(self, dish_id: str) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT service_date, AVG(presentation) AS presentation,"
            " AVG(portion) AS portion, AVG(color) AS color, AVG(execution) AS execution,"
            " COUNT(*) AS n FROM dish_reviews WHERE dish_id = ?"
            " GROUP BY service_date ORDER BY service_date", (dish_id,))
        out = []
        for row in rows:
            scores = {f: round(row[f], 2) for f in SCORE_FIELDS}
            out.append({"service_date": row["service_date"], "n": row["n"],
                        **scores,
                        "overall": round(sum(scores.values()) / 4.0, 2)})
        return out

    def live(self, limit: int = 10) -> List[Dict[str, Any]]:
        """The strip the chef glances at: last plates, their scores, the pace."""
        rows = self.db.query(
            "SELECT p.id, p.dish_id, p.path, p.ts, p.metrics, p.pending_review,"
            " (SELECT AVG((r.presentation + r.portion + r.color + r.execution)/4.0)"
            "  FROM dish_reviews r WHERE r.photo_id = p.id) AS score"
            " FROM dish_photos p ORDER BY p.ts DESC LIMIT ?", (limit,))
        out = []
        for row in rows:
            out.append({"photo_id": row["id"], "dish_id": row["dish_id"],
                        "path": row["path"], "ts": row["ts"],
                        "score": round(row["score"], 2) if row["score"] else None,
                        "pending": bool(row["pending_review"])})
        return out

    def pace(self) -> float:
        if self.dish_counter is None:
            return 0.0
        return self.dish_counter.pace_per_hour()

    def service_average(self) -> Dict[str, Any]:
        return self.average_scores(service_date=self.db.today())

    # -- headless HTML (used by the Flask shell and by tests) --------------
    def html_next(self) -> str:
        pending = self.next_pending()
        if pending is None:
            return "<div class='empty'>No plates waiting for review.</div>"
        note = html.escape(str(pending.get("dish_id", "")))
        return (f"<div class='plate' data-photo='{html.escape(pending['id'])}'>"
                f"<img src='{html.escape(pending['path'])}' alt='plate of {note}'>"
                f"<h2>{note}</h2>"
                "<form method='post' action='/review/score'>"
                f"<input type='hidden' name='photo_id' value='{html.escape(pending['id'])}'>"
                + "".join(f"<label>{field}<input name='{field}' type='number' min='1'"
                          f" max='5' value='4'></label>" for field in SCORE_FIELDS)
                + "<textarea name='note'></textarea><button>Score</button></form></div>")

    def html_live(self, limit: int = 10) -> str:
        rows = self.live(limit)
        items = "".join(
            f"<li data-photo='{html.escape(r['photo_id'])}'>{html.escape(r['dish_id'])}"
            f" — {'pending' if r['pending'] else (r['score'] or '')}</li>"
            for r in rows)
        return (f"<div class='live'><p>Pace: {self.pace():g} plates/hour</p>"
                f"<ul>{items}</ul></div>")

    def html_trend(self, dish_id: str) -> str:
        rows = self.trend(dish_id)
        if not rows:
            return "<div class='empty'>Not enough history for this dish yet.</div>"
        items = "".join(
            f"<li>{html.escape(r['service_date'])}: {r['overall']} "
            f"({r['n']} plates)</li>" for r in rows)
        return f"<div class='trend'><h2>{html.escape(dish_id)}</h2><ul>{items}</ul></div>"


def create_app(logic: ReviewLogic):
    """The optional web shell. Requires Flask; the agent does not."""
    try:
        from flask import Flask, request
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ImportError(
            "Flask is not installed. The review app needs it; the rest of the "
            "agent does not. Install with: pip install flask") from exc

    app = Flask(__name__)

    @app.route("/review/next")
    def review_next():
        return logic.html_next()

    @app.route("/review/score", methods=["POST"])
    def review_score():
        payload = request.form.to_dict() or (request.get_json(silent=True) or {})
        photo_id = payload.get("photo_id")
        if not photo_id:
            return {"error": "photo_id is required"}, 400
        scores = {field: payload.get(field, 0) for field in SCORE_FIELDS}
        review = logic.record_score(photo_id, scores,
                                    note=payload.get("note", ""),
                                    reviewer=payload.get("reviewer", "chef"))
        return review.to_dict(), 201

    @app.route("/review/live")
    def review_live():
        return logic.html_live()

    @app.route("/review/trend")
    def review_trend():
        return logic.html_trend(request.args.get("dish", ""))

    return app
