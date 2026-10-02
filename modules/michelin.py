"""The Pass — the Michelin aspiration dashboard (Prompt 25).

NOTE: the five criteria are Michelin's own: quality of ingredients, mastery of
technique, personality of the cuisine, value, and consistency. This module does
not score them out of thin air. Every signal is computed from records this
restaurant actually produced, and every claim carries its evidence.

WHY the agent never says "a star is coming": it cannot know that, and pretending
would be the single most corrosive thing it could do to the two people it works
for. It reports what the data shows and what to work on next.
"""
from __future__ import annotations

import html
import json
from typing import Any, Dict, List, Optional

from core.config import Config
from core.database import Database
from core.events import EventBus

CRITERIA = ("quality of ingredients", "mastery of technique",
            "personality of the cuisine", "value", "consistency")


class Michelin:
    def __init__(self, config: Config, db: Database, bus: Optional[EventBus] = None,
                 inventory: Any = None, deliveries: Any = None,
                 review_logic: Any = None, consistency: Any = None,
                 recipe_book: Any = None, boards: Any = None,
                 journal: Any = None, kanban: Any = None) -> None:
        self.config = config
        self.db = db
        self.bus = bus
        self.inventory = inventory
        self.deliveries = deliveries
        self.review_logic = review_logic
        self.consistency = consistency
        self.recipe_book = recipe_book
        self.boards = boards
        self.journal = journal
        self.kanban = kanban

    # -- the five criteria -------------------------------------------------
    def criteria_status(self) -> List[Dict[str, Any]]:
        return [
            self._ingredient_quality(),
            self._technique(),
            self._personality(),
            self._value(),
            self._consistency(),
        ]

    def _ingredient_quality(self) -> Dict[str, Any]:
        evidence: List[str] = []
        signals: List[float] = []

        if self.inventory is not None:
            ingredients = [i for i in self.inventory.all() if i.on_hand > 0]
            if ingredients:
                scores = [self.inventory.freshness_score(i)["score"] for i in ingredients]
                average = round(sum(scores) / len(scores), 1)
                signals.append(average)
                evidence.append(
                    f"average freshness {average}/100 across {len(ingredients)} ingredients on hand")
                expiring = len(self.inventory.expiring_within(2))
                evidence.append(f"{expiring} item(s) going off within 2 days")
            else:
                evidence.append("no ingredients logged yet")

        if self.deliveries is not None:
            report = self.deliveries.supplier_report()
            if report:
                graded = [r for r in report if r["grade"] in ("A", "B", "C", "D")]
                if graded:
                    points = {"A": 100.0, "B": 80.0, "C": 55.0, "D": 25.0}
                    supplier_signal = round(
                        sum(points[r["grade"]] for r in graded) / len(graded), 1)
                    signals.append(supplier_signal)
                    worst = min(graded, key=lambda r: points[r["grade"]])
                    evidence.append(
                        f"supplier grades average {supplier_signal}/100 "
                        f"(weakest: {worst['name']} {worst['grade']}, "
                        f"p90 {worst['p90_delay']:g}d)")
                else:
                    evidence.append("no supplier has delivery history yet")

        return self._criterion("quality of ingredients", signals, evidence,
                              "Tighten the weakest supplier or chase fresher drops")

    def _technique(self) -> Dict[str, Any]:
        evidence: List[str] = []
        signals: List[float] = []

        if self.review_logic is not None:
            average = self.review_logic.average_scores()
            if average.get("n"):
                execution = average["execution"]
                signals.append(execution * 20.0)  # 1-5 -> 0-100
                evidence.append(
                    f"execution scored {execution}/5 over {average['n']} reviewed plates")
            else:
                evidence.append("no plates scored yet")

        if self.consistency is not None:
            reports = [self.consistency.drift_report(d) for d in self.consistency.dish_ids()]
            if reports:
                worst = max(reports, key=lambda r: r.drift_score)
                signals.append(max(0.0, 100.0 - worst.drift_score))
                evidence.append(
                    f"worst drift {worst.drift_score}/100 on {worst.dish_id}")

        return self._criterion("mastery of technique", signals, evidence,
                              "Work the lowest-scoring plate on the pass until it repeats")

    def _personality(self) -> Dict[str, Any]:
        evidence: List[str] = []
        signals: List[float] = []

        if self.recipe_book is not None:
            recipes = self.recipe_book.local_recipes()
            evidence.append(f"{len(recipes)} recipes in the book")
            created = self.db.query(
                "SELECT COUNT(*) AS n FROM recipes WHERE created_at >= ?",
                ((self.db.now()[:10] + "T00:00:00"),))
            recent = int(created[0]["n"]) if created else 0
            evidence.append(f"{recent} recipe(s) added today")
            signals.append(min(100.0, 40.0 + recent * 15.0) if recipes else 20.0)

        if self.boards is not None and self.kanban is not None:
            board = self.boards.chef_board()
            cards = self.kanban.cards(board["id"])
            rnd = [c for c in cards if c.column in ("R&D", "Testing")]
            evidence.append(f"{len(rnd)} dish idea(s) in R&D or Testing")
            signals.append(min(100.0, 30.0 + len(rnd) * 10.0))

        return self._criterion("personality of the cuisine", signals, evidence,
                              "Put one new idea on the menu this week")

    def _value(self) -> Dict[str, Any]:
        evidence: List[str] = []
        signals: List[float] = []

        if self.recipe_book is not None:
            margins = []
            for recipe in self.recipe_book.local_recipes():
                if not recipe.menu_price:
                    continue
                cost = self.recipe_book.portion_cost(recipe, self.inventory)
                if cost["missing_prices"] or not cost["per_portion"]:
                    continue
                margin = recipe.menu_price - cost["per_portion"]
                margins.append((recipe.name, recipe.menu_price, cost["per_portion"],
                                margin))
            if margins:
                average_margin = round(sum(m[3] for m in margins) / len(margins), 2)
                average_price = round(sum(m[1] for m in margins) / len(margins), 2)
                ratio = average_margin / average_price if average_price else 0.0
                signals.append(max(0.0, min(100.0, ratio * 200.0)))
                evidence.append(
                    f"average margin {average_margin} on an average price of "
                    f"{average_price} ({ratio * 100:.0f}% of price)")
                thinnest = min(margins, key=lambda m: m[3])
                evidence.append(
                    f"thinnest: {thinnest[0]} at {thinnest[3]} margin")
            else:
                evidence.append("no menu prices entered yet — add prices to see value")

        return self._criterion("value", signals, evidence,
                              "Reprice the thinnest dish or rework its cost")

    def _consistency(self) -> Dict[str, Any]:
        evidence: List[str] = []
        signals: List[float] = []

        if self.consistency is not None:
            reports = [self.consistency.drift_report(d) for d in self.consistency.dish_ids()]
            if reports:
                average_drift = round(
                    sum(r.drift_score for r in reports) / len(reports), 1)
                signals.append(max(0.0, 100.0 - average_drift))
                evidence.append(
                    f"average drift {average_drift}/100 across {len(reports)} dish(es)")

        if self.journal is not None:
            today = self.journal.get()
            if today is not None:
                if today.low_plates:
                    lowest = today.low_plates[0]
                    evidence.append(
                        f"lowest plate tonight: {lowest['dish_id']} at {lowest['score']}")
                if today.gaps:
                    evidence.append(f"{len(today.gaps)} dish(es) with unplated plates")

        return self._criterion("consistency", signals, evidence,
                              "Cook the same dish identically three services running")

    def _criterion(self, name: str, signals: List[float], evidence: List[str],
                   recommendation: str) -> Dict[str, Any]:
        if signals:
            signal = round(sum(signals) / len(signals), 1)
            summary = "; ".join(evidence) if evidence else "no evidence recorded yet"
        else:
            signal = None
            summary = "; ".join(evidence) if evidence else "no evidence recorded yet"
        return {"criterion": name, "signal": signal, "evidence": evidence,
                "evidence_summary": summary, "focus_recommendation": recommendation}

    # -- the honest summary ------------------------------------------------
    def focus(self) -> Dict[str, Any]:
        """The weakest criterion — the one thing worth working on this week."""
        scored = [c for c in self.criteria_status() if c["signal"] is not None]
        if not scored:
            return {"criterion": None, "signal": None,
                    "reason": "not enough recorded history to judge yet"}
        weakest = min(scored, key=lambda c: c["signal"])
        return {"criterion": weakest["criterion"], "signal": weakest["signal"],
                "reason": weakest["evidence_summary"],
                "focus_recommendation": weakest["focus_recommendation"]}

    def star_readiness_note(self) -> str:
        """What the data says. Never a prediction, never a promise."""
        cards = self.criteria_status()
        scored = [c for c in cards if c["signal"] is not None]
        lines = ["What the records show, criterion by criterion:"]
        for card in cards:
            signal = card["signal"]
            shown = f"{signal}/100" if signal is not None else "no data yet"
            lines.append(f"- {card['criterion']}: {shown} — {card['evidence_summary']}")
        if not scored:
            lines.append("There is not enough recorded history to say anything useful "
                         "yet. Keep logging services and the picture will fill in.")
        else:
            weakest = min(scored, key=lambda c: c["signal"])
            lines.append(
                f"The weakest signal is {weakest['criterion']} at "
                f"{weakest['signal']}/100. {weakest['focus_recommendation']}.")
        lines.append("This is evidence, not a verdict — Michelin's judgement is not "
                     "the agent's to make.")
        return "\n".join(lines)

    def dashboard_html(self) -> str:
        cards = self.criteria_status()
        focus = self.focus()
        blocks = []
        for card in cards:
            signal = card["signal"]
            shown = f"{signal}/100" if signal is not None else "no data yet"
            evidence = "".join(f"<li>{html.escape(e)}</li>" for e in card["evidence"])
            blocks.append(
                f"<div class='criterion' data-criterion='{html.escape(card['criterion'])}'>"
                f"<h2>{html.escape(card['criterion'])}</h2>"
                f"<p class='signal'>{html.escape(shown)}</p>"
                f"<ul>{evidence}</ul>"
                f"<p class='focus'>{html.escape(card['focus_recommendation'])}</p></div>")
        focus_text = (f"Focus: {html.escape(str(focus['criterion']))} "
                      f"({focus['signal']}/100)" if focus.get("criterion")
                      else "Focus: still gathering evidence")
        return (f"<div class='michelin'><h1>The five criteria</h1>"
                f"<p class='focus-line'>{focus_text}</p>{''.join(blocks)}</div>")

    def tick(self, when: Optional[Any] = None) -> Dict[str, Any]:
        focus = self.focus()
        return {"focus": focus.get("criterion"), "signal": focus.get("signal"),
                "criteria_scored": len([c for c in self.criteria_status()
                                        if c["signal"] is not None])}
