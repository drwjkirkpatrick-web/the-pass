"""The Pass — composition root (Prompt 26).

NOTE: this is the only file that knows the whole shape of the program. Every
module is constructed here, in dependency order, and wired to the event bus.

WHY graceful degradation is systematic rather than accidental: only core/ is
required. Anything optional (the global recipe database, a sensor, Flask) that
is missing simply logs and the agent keeps working with what it has.

The wiring table, for reference:
  INGREDIENTS_UPDATED  -> FOHReminders (86 warnings)
  SERVICE_CLOSE        -> Boards (close checklist cards)
  SERVICE_OPEN/CLOSE   -> published by the agent's own tick
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

from core.agent import PassAgent
from core.config import Config
from core.database import Database
from core.events import (ALL_EVENTS, DRAFT_ORDER_READY, INGREDIENTS_UPDATED,
                         SERVICE_CLOSE, SERVICE_OPEN, EventBus)

log = logging.getLogger("the_pass")

# Events that MUST have a listener for the program to be considered wired.
EXPECTED_LISTENED = (INGREDIENTS_UPDATED, SERVICE_CLOSE)


def build_agent(config: Optional[Config] = None,
                config_path: Optional[str] = None) -> PassAgent:
    """Construct the whole agent. Optional pieces degrade, they do not abort."""
    if config is None:
        config = (Config.from_yaml(config_path) if config_path
                  else Config.default())
    db = Database(config.db_path).migrate()
    bus = EventBus()
    agent = PassAgent(config=config, db=db, bus=bus)

    # --- Phase 1: ingredients --------------------------------------------
    from modules.ingredient_vision import IngredientCurator
    from modules.inventory import Inventory
    inventory = agent.register("inventory", Inventory(config, db, bus))
    agent.register("ingredient_vision",
                   IngredientCurator(config, db, bus, inventory=inventory))

    # --- Phase 2: recipes -------------------------------------------------
    from modules.menu_planner import MenuPlanner
    from modules.recipe_db import GlobalRecipeDB
    from modules.recipes import RecipeBook
    global_db = agent.register("recipe_db", GlobalRecipeDB(config, db, bus))
    recipe_book = agent.register("recipes",
                                 RecipeBook(config, db, bus, global_db=global_db))
    planner = agent.register("menu_planner",
                             MenuPlanner(config, db, bus, inventory=inventory,
                                         recipe_book=recipe_book))

    # --- Phase 3: ordering ------------------------------------------------
    from modules.deliveries import Deliveries
    from modules.order_ahead import OrderAhead
    from modules.ordering import Ordering
    from modules.review_queue import ReviewQueue
    deliveries = agent.register("deliveries", Deliveries(config, db, bus))
    order_ahead = agent.register("order_ahead",
                                 OrderAhead(config, db, bus, deliveries=deliveries,
                                            inventory=inventory))
    ordering = agent.register("ordering",
                              Ordering(config, db, bus, inventory=inventory,
                                       recipe_book=recipe_book, planner=planner,
                                       order_ahead=order_ahead,
                                       deliveries=deliveries))
    review_queue = agent.register("review_queue",
                                  ReviewQueue(config, db, bus, ordering=ordering))

    # --- Phase 4: kitchen operations --------------------------------------
    from modules.dish_counter import DishCounter
    from modules.reminders_boh import BOHReminders, ReminderEngine
    from modules.reminders_foh import FOHReminders
    from modules.templog import TempLog
    from modules.wash_counter import WashCounter
    templog = agent.register("templog", TempLog(config, db, bus))
    engine = ReminderEngine(config, db, bus)
    agent.register("reminders_boh", BOHReminders(config, db, bus, engine=engine))
    agent.register("reminders_foh",
                   FOHReminders(config, db, bus, engine=engine, inventory=inventory))
    dish_counter = agent.register("dish_counter", DishCounter(config, db, bus))
    agent.register("wash_counter",
                   WashCounter(config, db, bus, dish_counter=dish_counter))

    # --- Phase 5: the pass ------------------------------------------------
    from modules.comms import Comms
    from modules.pass_photo import PassPhoto
    from modules.review_app import ReviewLogic
    pass_photo = agent.register("pass_photo",
                                PassPhoto(config, db, bus, dish_counter=dish_counter))
    review_logic = agent.register("review_logic",
                                  ReviewLogic(config, db, bus, pass_photo=pass_photo,
                                              dish_counter=dish_counter))
    comms = agent.register("comms", Comms(config, db, bus))

    # --- Phase 6: boards --------------------------------------------------
    from modules.boards import Boards
    from modules.kanban import Kanban
    kanban = agent.register("kanban", Kanban(config, db, bus))
    boards = agent.register("boards",
                            Boards(config, db, bus, kanban=kanban,
                                   review_queue=review_queue, templog=templog,
                                   order_ahead=order_ahead,
                                   wash_counter=agent.get("wash_counter"),
                                   recipe_book=recipe_book, planner=planner,
                                   inventory=inventory))

    # --- Phase 7: intelligence -------------------------------------------
    from modules.consistency import Consistency
    from modules.journal import Journal
    from modules.michelin import Michelin
    journal = agent.register("journal",
                             Journal(config, db, bus, dish_counter=dish_counter,
                                     review_logic=review_logic, templog=templog,
                                     wash_counter=agent.get("wash_counter"),
                                     comms=comms))
    consistency = agent.register("consistency",
                                 Consistency(config, db, bus, kanban=kanban,
                                             boards=boards, templog=templog))
    agent.register("michelin",
                   Michelin(config, db, bus, inventory=inventory,
                            deliveries=deliveries, review_logic=review_logic,
                            consistency=consistency, recipe_book=recipe_book,
                            boards=boards, journal=journal, kanban=kanban))
    return agent


def verify_wiring(agent: PassAgent) -> Dict[str, Any]:
    """Report which events anybody is listening to.

    WHY: an event nobody listens to is a feature nobody wired. This is how the
    parent finds out after a build wave, instead of during a service.
    """
    bus = agent.bus
    listeners = {event: bus.listeners(event) for event in ALL_EVENTS}
    orphans = sorted(event for event, count in listeners.items() if count == 0)
    return {
        "modules": sorted(agent.modules),
        "listeners": listeners,
        "orphan_events": orphans,
        "ok": all(listeners[event] > 0 for event in EXPECTED_LISTENED),
    }


def serve(agent: PassAgent, host: Optional[str] = None, port: Optional[int] = None,
          block: bool = True):
    """Start the web surfaces (review app + Michelin dashboard).

    Optional: without Flask installed the agent runs headless (CLI + Telegram).
    """
    try:
        from modules.review_app import create_app
    except ImportError as exc:
        log.warning("web surfaces unavailable: %s", exc)
        return None

    logic = agent.get("review_logic")
    app = create_app(logic)
    michelin = agent.get("michelin")
    if michelin is not None:
        @app.route("/michelin")
        def michelin_page():  # pragma: no cover - thin wrapper
            return michelin.dashboard_html()

    @app.route("/health")
    def health():  # pragma: no cover - thin wrapper
        return {"status": "ok", "modules": len(agent.modules)}

    host = host or agent.config.serve_host
    port = port or agent.config.serve_port
    if block:
        app.run(host=host, port=port)
        return app
    thread = threading.Thread(target=app.run, kwargs={"host": host, "port": port},
                              daemon=True)
    thread.start()
    return app


def run(config: Optional[Config] = None, once: bool = False,
        interval: Optional[int] = None) -> Dict[str, Any]:
    """One tick, or the loop a cron job / systemd unit drives."""
    agent = build_agent(config)
    if once:
        return agent.tick()
    if interval is None:
        interval = agent.config.temp_interval_sec
    while True:
        agent.tick()
        time.sleep(interval)
