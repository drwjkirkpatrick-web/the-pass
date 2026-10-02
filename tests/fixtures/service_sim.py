"""One simulated day, end to end (used by Prompt 29's integration test).

NOTE: this is a day in the life of the restaurant, driven through the same
public APIs the CLI and the Telegram bridge use. No shortcuts, no direct
database pokes — if the day works here, it works on the pass.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List

from core.config import Config
from core.events import SERVICE_CLOSE
from core.types import Channel, Recipe, RecipeLine, Role
from main import build_agent

# NOTE: the agent timestamps everything with the wall clock, so the simulated
# day has to be *today*. A synthetic future date leaves date-filtered reports
# (temperature excursions, journals) mysteriously empty.
TODAY = date.today()
DAY = TODAY.isoformat()
PREV = (TODAY - timedelta(days=1)).isoformat()
WHEN = datetime.combine(TODAY, time(19, 0))


class DaySim:
    def __init__(self, tmp_path) -> None:
        self.config = Config.default().with_overrides(
            db_path=str(tmp_path / "pass.db"), photo_dir=str(tmp_path / "photos"))
        self.agent = build_agent(self.config)
        self.report: Dict[str, Any] = {}

    # -- morning -----------------------------------------------------------
    def morning_delivery(self, photo_dir) -> Dict[str, Any]:
        """Photos of what came in, curated by the agent, approved by the chef."""
        curator = self.agent.get("ingredient_vision")
        photos = []
        for name in ("heirloom_tomato_4kg_crisp.jpg", "beef_short_rib_9kg_prime.jpg",
                     "carrot_6kg.jpg"):
            path = photo_dir / name
            path.write_bytes(b"fake-jpeg")
            photos.append(str(path))
        draft = curator.ingest(photos)
        written = curator.approve(draft["draft_id"], edits={
            line["id"]: {"par_level": 6.0} for line in draft["lines"]})
        inventory = self.agent.get("inventory")
        for ingredient in written:
            inventory.set_supplier(ingredient.id, self._supplier_id())
        self.report["ingredients"] = [i.name for i in written]
        return draft

    def _supplier_id(self) -> str:
        deliveries = self.agent.get("deliveries")
        existing = deliveries.suppliers()
        if existing:
            return existing[0].id
        return deliveries.add_supplier("Green Ridge Farms", order_cutoff="07:00",
                                       typical_delay_days=2.0).id

    def build_recipe_book(self) -> None:
        book = self.agent.get("recipes")
        inventory = self.agent.get("inventory")
        beef = inventory.by_name("beef short rib")
        carrot = inventory.by_name("carrot")
        tomato = inventory.by_name("heirloom tomato")
        book.save(Recipe(id="r-rib", name="Braised Short Rib", portions=4,
                         station="meat", menu_price=42.0,
                         lines=[RecipeLine(beef.id, 250.0, "g", "sear first")]))
        book.save(Recipe(id="r-salad", name="Heirloom Tomato Salad", portions=2,
                         station="garde", menu_price=16.0,
                         lines=[RecipeLine(tomato.id, 200.0, "g"),
                                RecipeLine(carrot.id, 40.0, "g")]))

    def delivery_history(self) -> None:
        """Three weeks of drops, one of them badly late."""
        deliveries = self.agent.get("deliveries")
        supplier_id = self._supplier_id()
        delays = [0, 0, 1, 1, 1, 2, 2, 3, 3, 5]
        for index, delay in enumerate(delays):
            promised = TODAY - timedelta(days=25 - index)
            actual = promised + timedelta(days=delay)
            deliveries.record_arrival(supplier_id, promised.isoformat(),
                                      promised.isoformat(), actual.isoformat())
        self.report["p90_delay"] = deliveries.p90_delay(supplier_id)
        self.report["supplier_id"] = supplier_id
        self.report["median_delay"] = deliveries.median_delay(supplier_id)
        self.report["supplier_grade"] = deliveries.reliability_grade(supplier_id)["grade"]

    # -- afternoon ---------------------------------------------------------
    def plan_and_order(self) -> Dict[str, Any]:
        planner = self.agent.get("menu_planner")
        ordering = self.agent.get("ordering")
        queue = self.agent.get("review_queue")

        plan = planner.plan_service({"r-rib": 40, "r-salad": 20}, DAY)
        self.report["covers"] = int(sum(plan.covers.values()))
        self.report["shortfalls"] = [s.to_dict() for s in plan.shortfalls]

        drafts = ordering.curate_orders(DAY)
        self.report["draft_orders"] = [o.id for o in drafts]
        self.report["order_dates"] = [
            {"supplier": o.supplier_id,
             "lines": [l.rationale for l in o.lines]} for o in drafts]

        review_ids = [queue.submit_order(order).id for order in drafts]
        self.report["queued_for_review"] = review_ids
        return plan

    def chef_approves_orders(self) -> None:
        queue = self.agent.get("review_queue")
        ordering = self.agent.get("ordering")
        approved = []
        for item in queue.pending():
            if item.item_type != "order":
                continue
            # the chef trims the first line, then approves
            first_line = ordering.get_order(item.item_id).lines[0]
            queue.approve(item.id, edits={first_line.ingredient_id: {"qty": 2500.0}},
                          actor="chef")
            approved.append(item.item_id)
        self.report["approved_orders"] = approved

    # -- service -----------------------------------------------------------
    def service(self) -> None:
        templog = self.agent.get("templog")
        comms = self.agent.get("comms")
        dishes = self.agent.get("dish_counter")
        wash = self.agent.get("wash_counter")
        photos = self.agent.get("pass_photo")
        reviews = self.agent.get("review_logic")

        templog.default_zones()
        templog.log("z-walkin", celsius=3.0)
        templog.log("z-walkin", celsius=9.0)      # first breach
        escalated = templog.log("z-walkin", celsius=9.5)   # second: escalate
        self.report["temp_escalated"] = escalated["escalated"]

        # the line talks
        urgent = comms.send(Role.SERVER.value, Channel.URGENT.value,
                            "Table 6 shellfish allergy", now=WHEN)
        comms.send(Role.SERVER.value, Channel.NON_URGENT.value,
                   "We need more polished forks", now=WHEN)
        self.report["urgent_id"] = urgent.id
        comms.ack(urgent.id, by="chef")

        # plates through the pass
        for index in range(6):
            dishes.fire("r-rib", service_date=DAY)
            dishes.plated("r-rib", service_date=DAY)
            dishes.picked_up("r-rib", service_date=DAY)
            photo = photos.capture("r-rib", service_date=DAY)
            score = 5 if index < 3 else 3      # the dish starts drifting mid-service
            reviews.record_score(photo["photo_id"],
                                 {"presentation": score, "portion": score,
                                  "color": score, "execution": score},
                                 note="service plate")
        dishes.fire("r-salad", service_date=DAY)
        dishes.plated("r-salad", service_date=DAY)
        dishes.picked_up("r-salad", service_date=DAY)

        for _ in range(5):
            rack = wash.rack_in(service_date=DAY)
            wash.rack_out(rack, service_date=DAY)

        self.report["covers_served"] = dishes.covers_total(DAY)
        self.report["wash_racks"] = wash.racks_done(DAY)

    def service_with_a_slow_dish(self) -> None:
        """Yesterday, for comparison: the dish was cooked well and slowly."""
        db = self.agent.db
        db.execute("INSERT INTO dish_reviews (id, photo_id, dish_id, presentation,"
                   " portion, color, execution, note, reviewer, ts, service_date)"
                   " VALUES ('rv-prev','ph-prev','r-rib',5,5,5,5,'','chef',?,?)",
                   (f"{PREV}T19:00:00", PREV))
        db.execute("INSERT INTO dish_events (dish_id, action, ts, service_date)"
                   " VALUES ('r-rib','fired',?,?)", (f"{PREV}T19:00:00", PREV))
        db.execute("INSERT INTO dish_events (dish_id, action, ts, service_date)"
                   " VALUES ('r-rib','plated',?,?)", (f"{PREV}T19:06:00", PREV))

    # -- close -------------------------------------------------------------
    def close(self) -> Dict[str, Any]:
        boards = self.agent.get("boards")
        journal = self.agent.get("journal")
        consistency = self.agent.get("consistency")
        michelin = self.agent.get("michelin")

        self.agent.bus.publish(SERVICE_CLOSE, {"date": DAY})
        boards.sync()
        report = journal.compile_service(DAY)
        self.report["journal_sections"] = {
            "covers": report.covers,
            "dishes": len(report.dishes),
            "reviews": report.reviews.get("n", 0),
            "excursions": len(report.temp_excursions),
            "pit_racks": report.pit.get("racks_done", 0),
            "urgent_calls": report.comms.get("urgent_total", 0),
        }
        self.report["low_plates"] = report.low_plates

        drift = consistency.drift_report("r-rib")
        self.report["drift"] = drift.to_dict()
        self.report["drift_cards"] = consistency.tick()["flagged"]

        agent_board = boards.agent_board()
        self.report["agent_board"] = [
            {"title": c.title, "column": c.column}
            for c in self.agent.get("kanban").cards(agent_board["id"])]

        self.report["michelin_cards"] = len(michelin.criteria_status())
        self.report["michelin_focus"] = michelin.focus().get("criterion")
        return report

    def run_full_day(self, photo_dir) -> Dict[str, Any]:
        self.morning_delivery(photo_dir)
        self.build_recipe_book()
        self.delivery_history()
        self.service_with_a_slow_dish()
        self.plan_and_order()
        self.chef_approves_orders()
        self.service()
        self.close()
        return self.report
