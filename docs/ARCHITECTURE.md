# The Pass — Architecture (as built)

Companion to `PROMPTS.md`, which holds the original build contract. This
document describes what actually shipped.

## Layers

```
core/                     no project imports; everything else depends on it
  types.py                the shared vocabulary (13 dataclasses, 7 enums)
  config.py               every path, threshold and window; YAML optional
  database.py             one SQLite schema (28 tables), lazy connect, row->dataclass
  events.py               in-process event bus, 14 event types, handler isolation
  agent.py                PassAgent: module registry, tick(), service state machine

modules/                  one concern each, all constructed in main.py
main.py                   composition root + verify_wiring()
cli.py                    argparse surface; exit 0 / 1 / 2
hermes_bridge.py          Telegram intent routing (report-only, never approves)
```

## Modules (22)

| Phase | Module | Does |
|---|---|---|
| 1 | `ingredient_vision` | photos in → draft ingredient list (mock analyzer; real model is an adapter) |
| 1 | `inventory` | on-hand, par levels, freshness scores, 86 flag + log |
| 2 | `recipe_db` | read-only adapter over the external recipe DB, introspects its schema |
| 2 | `recipes` | recipe book, portion scaling, unit conversion, costing, requirements |
| 2 | `menu_planner` | service plan, shortfalls, use-first, substitution ideas |
| 3 | `deliveries` | supplier delay history: median, p90, worst-recent, grades |
| 3 | `order_ahead` | backward date math: service → arrival (day before) → order-by |
| 3 | `ordering` | draft POs per supplier, every line carrying its reason |
| 3 | `review_queue` | the human gate: approve / edit / reject / escalate, full audit |
| 4 | `templog` | HACCP zones, breach streaks, escalation, append-only readings |
| 4 | `reminders_boh` | the shared ReminderEngine + kitchen timers |
| 4 | `reminders_foh` | FOH vocabulary + the 86 warning hooked to inventory events |
| 4 | `dish_counter` | fired / plated / picked up; gaps; pace; histograms |
| 4 | `wash_counter` | racks in/out, throughput, backlog warning, sanitizer log |
| 5 | `pass_photo` | a photo per plate, chained to the plate event |
| 5 | `review_app` | scoring logic + optional Flask shell (live strip, trend) |
| 5 | `comms` | urgent (ack-required) vs non-urgent (digest) lanes, transcripts |
| 6 | `kanban` | generic board engine: columns, WIP limits, move history |
| 6 | `boards` | Cuisine / Operations / Hospitality, fed by live state |
| 7 | `journal` | nightly service report; every section degrades honestly |
| 7 | `consistency` | variance, trend, plating drift, findings, agent-board cards |
| 7 | `michelin` | five criteria, each signal computed from records, plus focus |

## Event routing

| Event | Published by | Listened by |
|---|---|---|
| `service.open` | agent tick | — (informational) |
| `service.close` | agent tick | `boards` (close-checklist cards) |
| `ingredients.updated` | `inventory`, `ingredient_vision` | `reminders_foh` (86 warnings) |
| `menu.planned` | `menu_planner` | — |
| `order.draft_ready` | `ordering` | — |
| `order.approved` | `review_queue` | — |
| `temp.excursion` | `templog` | — (templog raises the reminder itself) |
| `reminder.due` | `reminders_boh`/`foh`, `review_queue` | gateway push |
| `message.urgent` | `comms` | gateway push, repeat until acked |
| `dish.plated` | `dish_counter` | `pass_photo` (chain) |
| `dish.reviewed` | `review_app` | `journal`, `consistency` |
| `wash.cycle_done` | `wash_counter` | — |
| `wash.backlog` | `wash_counter` | — |
| `review.pending` | `review_queue` | `boards` |

`main.verify_wiring()` reports every event with no listener, and fails the
`ok` check if a *required* listener is missing (`ingredients.updated`,
`service.close`). Orphans are informational: an event with no consumer today is
a hook for tomorrow, not a bug.

## The three data chains

1. **Ingredient chain** — delivery photo → draft → human approval → inventory →
   recipe requirements → shortfall → order-by date → draft PO (with rationale)
   → human approval → export.
2. **Plate chain** — fire → plated (photographed) → picked up → scored → journal
   → drift report → consistency criterion.
3. **Order chain** — covers forecast → aggregated requirements → supplier p90 →
   order-by schedule → curated drafts → review gate → purchase order text.

## Human review gates

Curated ingredient lists, purchase orders, and anything the agent wants to
publish outside the kitchen. `Ordering.set_status()` raises on `APPROVED`, so the
only route to an approved order is `ReviewQueue.approve()`. Tested at every gate
(04, 11, 12, 19, 25, 28, 29).

## Degradation model

Only `core/` is required. `main.build_agent()` constructs everything else and
logs what is missing:

| Missing | Effect |
|---|---|
| `OPEN_GLOBAL_RECIPES_DB` | local recipes only; substitutions and global search return empty |
| pass camera | `pass_photo` writes placeholder files and records the slot; the chain stays complete |
| temperature probes | manual readings via `the-pass temp`, or the mock sensor |
| Flask | CLI and Telegram still work; `serve` logs that web surfaces are unavailable |
| any module | the agent ticks the rest and reports the error for that module only |

## Testing

316 tests, no network, no hardware, ~30s on a Jetson Orin Nano. Mock vision
(filenames are the fixture), mock sensors (a fixed sequence), in-memory SQLite
per test, and a full simulated service day in `tests/fixtures/service_sim.py`.
