"""The Pass — command line (Prompt 27).

NOTE: this file contains no business logic. Every command is a thin call onto a
module, so the terminal, the Telegram bridge and the web app all do the same
thing through the same code.

EXIT CODES (this is the contract for cron and shell scripts):
  0  done
  1  usage error
  2  blocked — a human has to decide something before the agent can proceed
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

from core.config import Config
from main import build_agent, serve

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_BLOCKED = 2


def _print(payload: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
    elif isinstance(payload, str):
        print(payload)
    else:
        print(json.dumps(payload, indent=2, default=str))


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on usage errors; our contract says 1 = usage."""

    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write(f"the-pass: {message}\n")
        raise SystemExit(EXIT_USAGE)


def build_parser() -> argparse.ArgumentParser:
    # NOTE: --json is available both before and after the subcommand, because
    # muscle memory should not be part of the interface.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true",
                        help="machine-readable output")

    parser = _Parser(
        prog="the-pass", description="The Pass — restaurant agent for the pass",
        parents=[common])
    parser.add_argument("-c", "--config", help="path to config.yaml")
    parser.add_argument("--db", help="override the SQLite path")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("serve", help="run the agent's web surfaces", parents=[common])
    sub.add_parser("tick", help="one agent beat (for cron)", parents=[common])
    sub.add_parser("status", help="what the agent knows right now", parents=[common])

    plan = sub.add_parser("plan", help="plan a service", parents=[common])
    plan.add_argument("--date")
    plan.add_argument("--covers", help='JSON, e.g. \'{"r-1": 40}\'')

    orders = sub.add_parser("orders", help="curate, list and approve purchase orders",
                            parents=[common])
    orders.add_argument("--curate", action="store_true")
    orders.add_argument("--date")
    orders.add_argument("--pending", action="store_true")
    orders.add_argument("--approve", metavar="REVIEW_ID")
    orders.add_argument("--export", metavar="ORDER_ID")

    temp = sub.add_parser("temp", help="temperature log", parents=[common])
    temp.add_argument("--init-zones", action="store_true")
    temp.add_argument("--zone")
    temp.add_argument("--c", type=float)
    temp.add_argument("--report", action="store_true")

    review = sub.add_parser("review", help="plates waiting for scoring",
                            parents=[common])
    review.add_argument("--pending", action="store_true")
    review.add_argument("--dish")

    journal = sub.add_parser("journal", help="service journal", parents=[common])
    journal.add_argument("--date")
    journal.add_argument("--note")

    boards = sub.add_parser("boards", help="the three planning boards",
                            parents=[common])
    boards.add_argument("--role", default="agent")

    sub.add_parser("michelin", help="the five criteria", parents=[common])

    ingest = sub.add_parser("ingest", help="ingest ingredient photos",
                            parents=[common])
    ingest.add_argument("photos", nargs="+")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return EXIT_USAGE

    config = (Config.from_yaml(args.config) if args.config else Config.default())
    if args.db:
        config = config.with_overrides(db_path=args.db)
    agent = build_agent(config)
    db = agent.db

    if args.command == "serve":
        serve(agent)
        return EXIT_OK

    if args.command == "tick":
        _print(agent.tick(), args.json)
        return EXIT_OK

    if args.command == "status":
        _print({"service_state": agent.state().value,
                "modules": sorted(agent.modules),
                "inventory": agent.get("inventory").summary(),
                "pending_reviews": len(agent.get("review_queue").pending()),
                "reminders": len(agent.get("reminders_boh").engine.pending())},
               args.json)
        return EXIT_OK

    if args.command == "plan":
        covers = json.loads(args.covers) if args.covers else {}
        if not covers:
            parser.error("plan needs --covers, e.g. --covers '{\"r-1\": 40}'")
        plan = agent.get("menu_planner").plan_service(covers, args.date)
        _print(plan.to_dict() if args.json else plan.markdown(), args.json)
        return EXIT_OK

    if args.command == "orders":
        ordering = agent.get("ordering")
        queue = agent.get("review_queue")
        if args.approve:
            item = queue.approve(args.approve, actor="chef")
            _print({"approved": item.id, "status": item.status}, args.json)
            return EXIT_OK
        if args.export:
            order = ordering.get_order(args.export)
            if order is None:
                parser.error(f"no such order: {args.export}")
            _print(ordering.export_order(order), args.json)
            return EXIT_OK
        if args.curate:
            orders = ordering.curate_orders(args.date)
            queued = [queue.submit_order(o).id for o in orders]
            _print({"drafts": [o.id for o in orders], "queued_for_review": queued},
                   args.json)
            # nothing can be sent until a person approves
            return EXIT_BLOCKED if queued else EXIT_OK
        pending = queue.pending()
        if args.pending or not pending:
            _print({"pending_reviews": [
                {"id": i.id, "summary": i.summary, "deadline": i.deadline}
                for i in pending]}, args.json)
            return EXIT_OK
        _print({"pending_reviews": [i.summary for i in pending],
                "drafts": [o.id for o in ordering.orders(status="draft")]}, args.json)
        return EXIT_BLOCKED

    if args.command == "temp":
        templog = agent.get("templog")
        if args.init_zones:
            zones = templog.default_zones()
            _print({"zones": [{"id": z.id, "name": z.name, "min_c": z.min_c,
                               "max_c": z.max_c} for z in zones]}, args.json)
            return EXIT_OK
        if args.zone and args.c is not None:
            result = templog.log(args.zone, celsius=args.c)
            _print(result, args.json)
            return EXIT_BLOCKED if not result["in_bounds"] else EXIT_OK
        _print(templog.zone_status(), args.json)
        return EXIT_OK

    if args.command == "review":
        logic = agent.get("review_logic")
        if args.dish:
            _print(logic.trend(args.dish), args.json)
            return EXIT_OK
        pending = logic.next_pending()
        _print({"next": pending, "live": logic.live(5)}, args.json)
        return EXIT_BLOCKED if pending else EXIT_OK

    if args.command == "journal":
        journal = agent.get("journal")
        if args.note:
            journal.annotate(args.note, service_date=args.date)
            journal.compile_service(args.date)
        _print(journal.markdown(args.date), args.json)
        return EXIT_OK

    if args.command == "boards":
        boards = agent.get("boards")
        board = boards.board_for_role(args.role)
        if board is None:
            parser.error(f"no board for role: {args.role}")
        cards = agent.get("kanban").cards(board["id"])
        _print({"board": board["name"], "columns": board["columns"],
                "cards": [{"id": c.id, "title": c.title, "column": c.column,
                           "owner": c.owner_role, "blocked": c.blocked}
                          for c in cards]}, args.json)
        return EXIT_OK

    if args.command == "michelin":
        michelin = agent.get("michelin")
        _print(michelin.dashboard_html() if not args.json
               else {"criteria": michelin.criteria_status(),
                     "focus": michelin.focus()}, args.json)
        return EXIT_OK

    if args.command == "ingest":
        curator = agent.get("ingredient_vision")
        draft = curator.ingest(args.photos)
        _print({"draft_id": draft["draft_id"], "flagged": draft["flagged"],
                "lines": [{"name": l["name"], "qty": l["qty"], "unit": l["unit"],
                           "flagged": l["flagged"]} for l in draft["lines"]]}, args.json)
        # nothing enters inventory until a human approves the draft
        return EXIT_BLOCKED

    parser.print_help()
    return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
