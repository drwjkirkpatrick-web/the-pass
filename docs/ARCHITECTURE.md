# The Pass — Architecture (framework level)

This documents the intended architecture the 30 prompts will build. It will be
kept current as modules land. Per-module detail lives in `PROMPTS.md`.

## Module graph

```
core/types.py      every module imports; zero imports outward
core/config.py     env-aware config (OPEN_GLOBAL_RECIPES_DB, photo dirs, HACCP defaults)
core/database.py   SQLite, DDL constants, lazy connect, row_to()
core/events.py     typed in-process EventBus (10 event types)
core/agent.py      PassAgent: module registry, tick(), service state machine

modules/
  ingredient_vision.py   photo -> IngredientObservation (MockVisionAnalyzer)
  inventory.py           freshness, par levels, 86 warnings
  recipe_db.py           read-only bridge to global recipe DB (graceful when absent)
  recipes.py             scaling, unit conversion, requirement aggregation, cost
  menu_planner.py        service plan + shortfall list
  deliveries.py          supplier delay history: median, p90, grades
  order_ahead.py         backward-dated order-by recommendations (p90 + cutoff + shelf life)
  ordering.py            draft POs with per-line rationale; export
  review_queue.py        universal human gate: submit/approve/reject/escalate + audit
  templog.py             cold chain zones, excursion escalation
  reminders_boh.py       BOH reminder engine (shared ReminderEngine)
  reminders_foh.py       FOH templates/hooks on the same engine
  dish_counter.py        fire/plated/picked_up append-only events
  wash_counter.py        racks in/out, backlog alerts, sanitizer log
  pass_photo.py          plate photos chained to dish_events
  review_app.py          Flask live review surface (score in seconds)
  comms.py               URGENT (ack-required) / NON_URGENT (batched) line, transcripts
  kanban.py              generic board engine (WIP limits, audit)
  boards.py              Chef / Agent / Server-Host boards with event-driven cards
  journal.py             nightly service journal + markdown report
  consistency.py         drift detection across services (variance, trend)
  michelin.py            five-criteria dashboard, evidence-cited
main.py                 composition root; verify_wiring()
cli.py                  argparse surface; exit code 2 = blocked by human review
hermes_bridge.py        Telegram intent routing (never auto-approves)
```

## Event routing (summary)

| Event | Published by | Listened by |
|---|---|---|
| INGREDIENTS_UPDATED | inventory (05) | menu_planner, foh reminders (86), michelin |
| DRAFT_ORDER_READY | ordering (11) | review_queue, agent board |
| ORDER_APPROVED | review_queue (12) | agent board |
| TEMP_EXCURSION | templog (13) | reminders (escalation), agent board |
| REMINDER_DUE | agent tick via engines (14/15) | comms/FOH surfaces, hermes_bridge |
| MESSAGE_URGENT | comms (20) | ack surfaces, journal |
| DISH_PLATED | dish_counter (16) | pass_photo |
| DISH_REVIEWED | review_app (19) | journal, consistency |
| WASH_CYCLE_DONE | wash_counter (17) | reminders (backlog), journal |
| SERVICE_OPEN / CLOSE | agent (03) | all engines, journal (compiles on CLOSE) |

## The three data chains

1. **Ingredient chain:** delivery photo → curated draft (04) → human approval → inventory (05) → recipe requirements (07) → shortfall (08) → dated order-by rec (10) → draft PO with rationale (11) → human approval (12) → export.
2. **Plate chain:** fire → plated → photo (18) → live score (19) → journal (23) → drift (24) → Michelin consistency (25).
3. **Order chain:** covers forecast → aggregated requirements → supplier delay model (09) → order-ahead schedule (10) → curated draft → review gate → sent PO (outside the system).

## Degradation model

Only `core/` (01-03) is required. Every module absent means the agent logs it and
runs with what's present. Global recipe DB absent → local recipes only. Sensor
absent → manual temp entry. Gateway absent → CLI + web.

## Human review gates (invariant)

Curated ingredient lists, purchase orders, and any externally-shared conclusions
each require explicit human approval through `review_queue` (12). This invariant
is tested at every gate (prompts 04, 11, 12, 25, 28, 29).
