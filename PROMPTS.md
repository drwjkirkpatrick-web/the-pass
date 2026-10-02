# THE PASS — 30 Build Prompts

A Michelin-aspiration restaurant agent that lives at the pass. This file is the
build contract: 30 numbered, testable prompts organized into 9 phases. Each
prompt is self-contained — a cold-start subagent can execute it without any
other context. Build in phase order; within a phase, prompts are parallelizable.

**Hard rules for every prompt:**
- One agent per file. Each prompt names the file(s) it owns — no other agent touches them.
- Every module ships with its test file in the same prompt.
- Mock mode first: every hardware/vision/network dependency gets a mock implementation; real backends are optional adapters behind the same interface.
- SQLite for persistence, `sqlite3.Row`, parameterized queries, DDL as module constants, `:memory:` for tests.
- Teaching notes: `NOTE:` / `WHY:` comments so a non-coder collaborator can follow the logic.
- Human review gates: the agent drafts, the human approves. Nothing orders, fires, or announces without an approval step where food or money is involved.

---

## Phase 0 — Foundation

| #  | Module | File | Depends On |
|----|--------|------|------------|
| 01 | Core types & domain model | `core/types.py` | — |
| 02 | Config & persistence layer | `core/config.py`, `core/database.py` | 01 |
| 03 | Event bus & agent core loop | `core/events.py`, `core/agent.py` | 01, 02 |

### Prompt 01 — Core Types & Domain Model
**Build:** `core/types.py` + `tests/test_types.py`
**Depends on:** —

Create the frozen-dataclass vocabulary every other module imports: `Ingredient` (id, name, category, unit, par_level, on_hand, freshness_date, photo_ids), `Recipe` (id, name, ingredients as list of `RecipeLine`, portions, station, technique_tags, plating_notes), `RecipeLine` (ingredient_id, qty_per_portion, unit, prep_note), `Order` (id, supplier, lines, status: DRAFT→APPROVED→SENT, created_by), `Supplier` (id, name, order_cutoff, typical_delay_days), `TempReading` (id, station, sensor_id, celsius, timestamp), `TempZone` (id, name, min_c, max_c, station), `Reminder` (id, audience: FOH/BOH/BOTH, urgency, title, body, fire_at, ack_by), `PassMessage` (id, channel: URGENT/NON_URGENT, sender_role, body, timestamp, acked), `DishEvent` (id, dish_id, action: FIRED/PLATED/PICKED_UP, timestamp), `KanbanCard` (id, board, title, detail, column, owner_role, due), `DishReview` (id, dish_id, photo_id, scores dict, reviewer, timestamp). Enum: `Role` (CHEF, SERVER, HOST, AGENT), `Urgency` (ROUTINE, URGENT), `Audience` (FOH, BOH, BOTH).

Include `to_dict()`/`from_dict()` on each. **Pitfall:** if any `to_dict()` includes a computed `@property`, `from_dict()` must filter to `__dataclass_fields__` before `cls(**data)` — implement one shared helper `filter_fields(cls, data)`. Tests: round-trip every dataclass, verify the property-filter helper drops unknown keys, verify enum values.

**Acceptance:** `pytest tests/test_types.py` green; every later module can import these without circular imports.

---

### Prompt 02 — Config & Persistence Layer
**Build:** `core/config.py`, `core/database.py` + `tests/test_config.py`, `tests/test_database.py`
**Depends on:** 01

`config.py`: a frozen `Config` dataclass loaded from `config.yaml` with `from_yaml()` and `default()`. Fields: `db_path` (default `the_pass.db`; tests use `:memory:`), `global_recipes_db` (env-var aware: `os.environ.get("OPEN_GLOBAL_RECIPES_DB", "")` — Walker's global recipe DB), `photo_dir`, `mock_vision: bool = True`, `temp_interval_sec`, `service_start` / `service_end` times, reminder lead times, supplier defaults. **No hardcoded restaurant content** — the user supplies menu, suppliers, and staff; the config points at them.

`database.py`: a `Database` class owning the SQLite connection. DDL as module constants (`SCHEMA_SQL`): tables `ingredients, recipes, recipe_lines, suppliers, orders, order_lines, temp_readings, temp_zones, reminders, pass_messages, dish_events, dish_photos, dish_reviews, kanban_cards, delivery_delays, dish_counter, wash_counter, service_journal`. Include `migrate()` (executescript), `row_to()` helper mapping `sqlite3.Row` → dataclass, and `now()` (UTC ISO timestamps). **Pitfall:** no module-level connection creation — lazy `Database.connect()` so tests can pass `:memory:`. **Pitfall:** `SELECT SUM(...)` on an empty table returns `None`, not 0 — every SUM accessor must guard.

Tests: migrate on `:memory:` succeeds twice (idempotent), round-trip insert/read for 3 representative tables, `row_to()` fidelity.

---

### Prompt 03 — Event Bus & Agent Core Loop
**Build:** `core/events.py`, `core/agent.py` + `tests/test_events.py`, `tests/test_agent.py`
**Depends on:** 01, 02

`events.py`: a synchronous in-process `EventBus` with typed events: `INGREDIENTS_UPDATED`, `DRAFT_ORDER_READY`, `ORDER_APPROVED`, `TEMP_EXCURSION`, `REMINDER_DUE`, `MESSAGE_URGENT`, `DISH_PLATED`, `DISH_REVIEWED`, `WASH_CYCLE_DONE`, `SERVICE_OPEN`, `SERVICE_CLOSE`. `subscribe(event_type, handler)`, `publish(event_type, payload)` — handlers must not raise the bus down (catch + log per handler). Handlers receive `(event_type, payload)`.

`agent.py`: `PassAgent` — the resident orchestrator. Constructor takes `Config`, `Database`, `EventBus`. Holds a registry of modules (all `None`-able; agent degrades gracefully if a module is absent). Core loop `tick()`: called by the CLI / Hermes gateway / a timer; checks due reminders, polls temperature bounds, advances service state (PRE_SERVICE → SERVICE → POST_SERVICE based on config times). Every tick is idempotent and safe to call twice.

Tests: bus delivers to multiple subscribers, one crashing handler doesn't kill others, `tick()` with empty DB is a no-op, state machine transitions on configured times.

**Acceptance:** Phase 0 complete — `python -m pytest tests/ -q` green with zero real modules yet.

---

## Phase 1 — Ingredient Intelligence

| #  | Module | File | Depends On |
|----|--------|------|------------|
| 04 | Ingredient photo intake & curation | `modules/ingredient_vision.py` | 01, 02 |
| 05 | Ingredient inventory & freshness | `modules/inventory.py` | 01, 02, 04 |

### Prompt 04 — Ingredient Photo Intake & Curation
**Build:** `modules/ingredient_vision.py` + `tests/test_ingredient_vision.py`
**Depends on:** 01, 02

The agent reviews photos of deliveries/walk-ins to curate the available-ingredient list. Define an abstract `VisionAnalyzer` with one method: `analyze(photo_path) -> list[IngredientObservation]` where `IngredientObservation = (name_guess, confidence, quantity_guess, quality_note)`. Ship two implementations: `MockVisionAnalyzer` (rule-based: filename-keyed, e.g. `tomatoes_5lb_heirloom.jpg` → parsed observation — deterministic for tests) and a documented extension point for a real CV model later (no heavy deps; leave a `NotImplementedError` stub class `RealVisionAnalyzer`).

`IngredientCurator.ingest(photo_paths, analyzer) -> DraftIngredientList`: dedupes observations against current inventory by normalized name, flags low-confidence rows for human review, produces a **draft** list with `PROPOSED` status — it never writes to the live inventory directly. `curator.approve(draft_id, edits)` is the human gate. Photo files are stored under `Config.photo_dir/ingredients/` with UTC-timestamped names; store the path, never the blob, in the DB.

Tests: mock analyzer parses filename fixture → curated draft; duplicate names merge; low-confidence flagged; approval promotes to inventory; nothing lands in `ingredients` table without `approve()`.

---

### Prompt 05 — Ingredient Inventory & Freshness
**Build:** `modules/inventory.py` + `tests/test_inventory.py`
**Depends on:** 01, 02, 04

CRUD + intelligence over the `ingredients` table: `upsert()`, `adjust_qty()`, `list_available()` (on_hand > 0 and not expired), `below_par()` (on_hand < par_level), `expiring_within(days)`. Freshness model: `freshness_score(ingredient) -> (score 0-100, label)` from days-to-expiry vs. shelf-life expectations by category (a `SHELF_LIFE_DAYS` reference dict keyed by category — structure, not a hardcoded food encyclopedia; unknown categories get a conservative default). Emits `INGREDIENTS_UPDATED` on change. `suggest_uses(ingredient) -> list[Recipe]` joins against recipes to show what can be made from what's on hand.

Tests: par-level breach detection, expiry math including already-expired, unknown-category default, event emission, `SUM` guard on aggregate queries.

**Acceptance:** Phase 1 complete — photos in → curated, human-approved, freshness-aware ingredient list out.

---

## Phase 2 — Recipes & Global DB Integration

| #  | Module | File | Depends On |
|----|--------|------|------------|
| 06 | Global recipe DB integration | `modules/recipe_db.py` | 01, 02 |
| 07 | Recipes & portion scaling | `modules/recipes.py` | 01, 02, 06 |
| 08 | Menu planner | `modules/menu_planner.py` | 05, 07 |

### Prompt 06 — Global Recipe DB Integration
**Build:** `modules/recipe_db.py` + `tests/test_recipe_db.py`
**Depends on:** 01, 02

Read-only bridge to the external global recipe database at `Config.global_recipes_db` (env var `OPEN_GLOBAL_RECIPES_DB`; probe its actual schema at runtime — do not assume). `GlobalRecipeDB.connect()` → introspects available tables, maps whatever recipe/ingredient columns exist into the-pass `Recipe`/`IngredientObservation` shapes via a `normalize_recipe(row) -> Recipe` per-layout adapter (Walker's heterogeneous-loader pattern: probe first, one normalizer, `try/except KeyError` passthrough, never abort on a novel row). If the DB path is missing, the module degrades: `available() -> False`, and every caller skips global lookups with a logged warning — the agent still works with locally-entered recipes.

Tests: normalize against 2 synthetic layout fixtures (different column names for the same concept), missing-DB graceful degradation, `available()` gate checked before any query.

---

### Prompt 07 — Recipes & Portion Scaling
**Build:** `modules/recipes.py` + `tests/test_recipes.py`
**Depends on:** 01, 02, 06

Recipe CRUD + the portion engine the chef orders against: `scale(recipe, portions) -> list[RecipeLine]` (exact-quantity scaling, unit-aware — a `UNITS` conversion table for g/kg, ml/l, oz/lb, count), `portion_cost(recipe)` (sums line qty × ingredient unit cost where known), `portion_size(recipe)` (grams/units per plate, for plating consistency), `ingredient_requirements(recipes, covers_per_recipe) -> list[(ingredient, total_qty)]` — the aggregated shopping math. Merge `GlobalRecipeDB` results with local recipes behind one interface (`search(name)`, `get(id)`, `all_recipes()`), local-first, global as enrichment.

Tests: scaling math incl. fractional portions, unit conversion both directions, requirements aggregation with overlapping ingredients across recipes, cost rollup with missing prices skipped not crashed.

---

### Prompt 08 — Menu Planner
**Build:** `modules/menu_planner.py` + `tests/test_menu_planner.py`
**Depends on:** 05, 07

Turns "tonight's menu + covers forecast" into a prep and ordering basis: `plan_service(menu_recipe_ids, covers_per_recipe, service_date)`. Cross-references ingredient requirements (07) against availability (05) and produces `ServicePlan`: per-recipe prep quantities, shortfall list (`needed but below_par or expiring`), and substitution suggestions from global DB (06) for shortfalls. Stores the plan; re-planning for the same date replaces the old plan (idempotent). Emits an event carrying the shortfall list — the ordering phase (Phase 3) listens for it.

Tests: shortfall computed from par levels, expiring ingredients flagged as "use-first", re-plan idempotency, empty-menu no-op, event payload shape.

**Acceptance:** Phase 2 complete — recipes in, portions scaled, service plans with shortfalls flowing to ordering.

---

## Phase 3 — Ordering & Human Review

| #  | Module | File | Depends On |
|----|--------|------|------------|
| 09 | Delivery delay history | `modules/deliveries.py` | 01, 02 |
| 10 | Order-ahead scheduler | `modules/order_ahead.py` | 09 |
| 11 | Food ordering list curation | `modules/ordering.py` | 05, 07, 08, 09, 10 |
| 12 | Human review queue | `modules/review_queue.py` | 03, 11 |

### Prompt 09 — Delivery Delay History
**Build:** `modules/deliveries.py` + `tests/test_deliveries.py`
**Depends on:** 01, 02

Builds the empirical lead-time model per supplier: `record_arrival(supplier_id, ordered_date, promised_date, actual_date)` → rows in `delivery_delays`. Analytics: `delay_history(supplier) -> list[DelayRecord]`, `median_delay(supplier)`, `p90_delay(supplier)` (pure-Python statistics — median and the 90th percentile via sorted-index math, no scipy), `reliability_grade(supplier) -> (grade A-D, rationale)`. A supplier with no history gets the configured default delay — never a crash, never a silent zero. Manual entry + CSV import (the chef logs reality; the agent does math).

Tests: median/p90 on known fixtures, empty-history default, grade boundaries, CSV import round-trip.

---

### Prompt 10 — Order-Ahead Scheduler
**Build:** `modules/order_ahead.py` + `tests/test_order_ahead.py`
**Depends on:** 09

The "order days in advance for day-before arrival" engine. `required_by_date(service_date, ingredient) -> date`: works backward from service_date — shelf-life check (ingredient must survive from arrival to service), supplier cutoff time, and the delay model (09): `latest_order_date = required_arrival − p90_delay(supplier)` (p90, not median — the safety margin is the point). Output per ingredient: a dated order recommendation `("supplier-X", "order by Mon Sep 29", "arrives Wed Oct 1", "service Thu Oct 2")`. `build_schedule(service_date) -> list[OrderRecommendation]` across all shortfall ingredients, grouped by supplier with their cutoff times.

Tests: backward-date math with known delays (incl. weekend skip if cutoff falls on closed day — a `closed_days` set on Supplier), p90 vs median choice changes the order date, shelf-life violation pushed earlier, empty suppliers list no-op.

---

### Prompt 11 — Food Ordering List Curation
**Build:** `modules/ordering.py` + `tests/test_ordering.py`
**Depends on:** 05, 07, 08, 09, 10

Merges everything into draft purchase orders: `curate_orders(service_date) -> list[Order]` — for each supplier: aggregate shortfalls (08) + below-par staples (05) + par-level top-ups, quantities rounded to pack sizes (a per-supplier `pack_size` map on order lines), delivery dates from the scheduler (10). Every order is born `DRAFT` with an itemized `rationale` per line ("3 kg short for 80 covers of Braised Short Rib; arrives day-before; supplier p90 delay 1.2 days"). Never sends. `export_order(order) -> str` produces a clean text/PO the chef can paste into a supplier email or SMS.

Tests: aggregation across multiple recipes touching the same ingredient, pack-size rounding math, draft-only status invariant, rationale strings present on every line, export formatting.

---

### Prompt 12 — Human Review Queue
**Build:** `modules/review_queue.py` + `tests/test_review_queue.py`
**Depends on:** 03, 11

The universal human gate — used first for orders, later for anything the agent proposes. `submit(item_type, item_id, summary, payload) -> ReviewItem`, `pending()`, `approve(id, edits=None)` (edits applied atomically then approved), `reject(id, reason)`, `escalate(id)` (overdue items re-notify). Deadline-aware: `REMINDER_DUE` events fire at configurable lead times before a supplier cutoff when a review is still pending. Approving a DRAFT order transitions it DRAFT→APPROVED and publishes `ORDER_APPROVED` — the only path by which an order becomes sendable. Full audit trail table (`review_log`: who, when, decision, reason). Every module that drafts (curated ingredients 04, orders 11) registers here; nothing auto-approves.

Tests: submit/approve/reject state machine, edits-then-approve atomicity, overdue escalation event, audit log rows for every transition, approve is the only DRAFT→APPROVED path (attempted direct status writes rejected).

**Acceptance:** Phase 3 complete — data → delay model → dated order recommendations → curated draft POs → human says yes.

---

## Phase 4 — Kitchen Operations

| #  | Module | File | Depends On |
|----|--------|------|------------|
| 13 | Temperature logging & cold chain | `modules/templog.py` | 01, 02, 03 |
| 14 | Back-of-house reminders | `modules/reminders_boh.py` | 03 |
| 15 | Front-of-house reminders | `modules/reminders_foh.py` | 03 |
| 16 | Dish counter | `modules/dish_counter.py` | 01, 02 |
| 17 | Dish washing counter | `modules/wash_counter.py` | 01, 02, 03 |

### Prompt 13 — Temperature Logging & Cold Chain
**Build:** `modules/templog.py` + `tests/test_templog.py`
**Depends on:** 01, 02, 03

HACCP-style cold chain: `TempLogger.log(station, sensor_id, celsius)` (manual entry + a `SensorSource` interface with `MockSensorSource` for the loop; real probe hardware is a later adapter). Zone definitions from `temp_zones` (walk-in, lowboy, pass hot-hold...). `check(reading)` against zone bounds → `TEMP_EXCURSION` event on violation, with escalation: first breach = log; still-breaching-after-2-consecutive = urgent reminder to BOH; excursion always recorded — the record itself is the compliance artifact. `excursion_report(since)`, `zone_status() -> (zone, current, in_bounds, minutes_in_excursion)`. Food-safety defaults in config (e.g., walk-in ≤ 4°C, hot-hold ≥ 60°C) but zone definitions live in the DB — the user sets their own HACCP plan.

Tests: in/out-of-bounds classification, consecutive-breach escalation (1 breach ≠ escalated; 2 does), report windows, mock sensor produces deterministic readings, excursion rows immutable (append-only log).

---

### Prompt 14 — Back-of-House Reminders
**Build:** `modules/reminders_boh.py` + `tests/test_reminders_boh.py`
**Depends on:** 03

BOH reminder engine: prep timers (braise 4h, dough proof 2h), station mise-en-place checks at T-minus before service, ordering-deadline reminders (hooks Phase 3 review deadlines), delivery-arrival confirmations. `schedule(audience=BOH, ...)`, recurring reminders (`daily`, `pre_service_at T-90m`), `due(now)` resolved by the agent tick (03). Reminders have delivery-ack state — `REMINDER_DUE` fires; `ack(id)` closes it; unacked urgent reminders re-fire on a cooldown. Templates parameterized (no hardcoded menus): reminder text is data in the DB.

Tests: schedule/due/ack cycle, recurrence generates next occurrence, re-fire cooldown on unacked, pre-service T-minus math, audience filtering (FOH reminder never lands on BOH list).

---

### Prompt 15 — Front-of-House Reminders
**Build:** `modules/reminders_foh.py` + `tests/test_reminders_foh.py`
**Depends on:** 03

Same engine shape as 14, FOH concerns: reservation count briefing to the host, table-turn pacing checks, polishing/side-work checklists at open and close, wine/water restock from the cellar, VIP/occasion flags (data the user enters), "86 warning" relay — when inventory (05) hits below-par on a menu ingredient, FOH gets a heads-up reminder before the dish has to be pulled. Shares the `reminders` table with 14 via audience; does **not** duplicate its scheduling code — 14 and 15 both import a shared `ReminderEngine` core (14 builds it; 15 reuses — one agent per file rule: 15 owns only its FOH templates and hooks).

Tests: 86-warning triggers from a below-par ingredient, occasion briefing scheduling, shared-engine no-duplication (scheduling a FOH reminder doesn't create a BOH row), open/close checklist generation.

---

### Prompt 16 — Dish Counter
**Build:** `modules/dish_counter.py` + `tests/test_dish_counter.py`
**Depends on:** 01, 02

Counts plates through the pass: `fire(dish_id)`, `plated(dish_id)`, `picked_up(dish_id)` → `dish_events` rows. Aggregates: `per_service(service_date) -> per-dish counts` (fired vs. plated vs. picked_up — a fired-but-never-plated gap is a pass bottleneck signal), `per_hour_histogram`, `covers_total`. Live counters are the service heartbeat; the data feeds consistency scoring (24) and the post-service journal (23). No deletes — append-only.

Tests: event sequence counts, gap detection (fired 5 / plated 4), histogram bucketing by hour, append-only enforcement.

---

### Prompt 17 — Dish Washing Counter
**Build:** `modules/wash_counter.py` + `tests/test_wash_counter.py`
**Depends on:** 01, 02, 03

Tracks the pit: `rack_in()`, `rack_out()` (rack = one dish-washing cycle), `WashCycleDone` events. Metrics: racks/hour throughput, open racks (in > out — backlog building), peak-hour load, and a prep-for-the-rush heads-up: when service pace (16) says covers/hour is climbing and backlog exceeds a threshold, a BOH reminder "pit is falling behind" fires via 03. Also `sanitizer_log` rows (concentration/temperature check per shift — HACCP companion to 13).

Tests: in/out pairing, backlog threshold event, throughput math, peak detection, sanitizer log append.

**Acceptance:** Phase 4 complete — the kitchen's cold chain, timers, plate counts, and pit are all instrumented and cross-talking.

---

## Phase 5 — The Pass: Photos, Review & Comms

| #  | Module | File | Depends On |
|----|--------|------|------------|
| 18 | Pass photo module | `modules/pass_photo.py` | 01, 02, 04 |
| 19 | Dish review live app | `modules/review_app.py` | 18, 16 |
| 20 | FOH↔BOH communication line | `modules/comms.py` | 03, 14, 15 |

### Prompt 18 — Pass Photo Module
**Build:** `modules/pass_photo.py` + `tests/test_pass_photo.py`
**Depends on:** 01, 02, 04

Photographs every dish on the pass for the data review: `capture(dish_id, station) -> PassPhoto` (file into `Config.photo_dir/pass/`, UTC-named, row in `dish_photos` linked to the `dish_events` PLATED row from 16 so photo ↔ plate ↔ ticket are one chain). Reuses the `VisionAnalyzer` interface from 04 — a `PassPhotoAnalyzer` mock scores plate symmetry/portions/garnish placement deterministically from fixture photos; real model is the same extension point. Every plated dish gets photographed; the archive is the restaurant's plating memory. `gallery(service_date, dish_id=None)` for review; `compare(dish_id, date_a, date_b)` puts two plates side by side (path pairs — the review app renders them).

Tests: capture → DB row → event link chain, gallery filters, analyzer mock scoring on fixtures, compare returns both paths, no blob in DB.

---

### Prompt 19 — Dish Review Live App
**Build:** `modules/review_app.py` + `tests/test_review_app.py`
**Depends on:** 18, 16

The instant-results dish review surface — a lightweight served page (Flask app factory, single route family, phone-friendly: the chef thumbs it at the pass). Endpoints: `/review/next` (latest unreviewed pass photo), `/review/score` (POST: 1-5 on presentation, portion, color, execution + note → `dish_reviews` row, `DISH_REVIEWED` event), `/review/live` (auto-refreshing strip: last N plates with scores and pace from 16), `/review/trend?dish=X` (score trend across the service and across days). Live results = the moment a plate is photographed (18) it appears pending review; scores post in seconds. All templates server-rendered strings or one template dir; `html.escape()` every user-supplied note (test that escaped form is present AND raw form absent).

Tests: app factory with `:memory:` DB fixture, post-score round trip, next-unreviewed ordering, trend aggregation math, XSS escaping proof, live strip contains pace stats.

---

### Prompt 20 — FOH↔BOH Communication Line
**Build:** `modules/comms.py` + `tests/test_comms.py`
**Depends on:** 03, 14, 15

Structured two-way line between front and back with urgency routing: `send(sender_role, channel, body)`. `URGENT` channel: interrupt semantics — loud notification on both ends immediately, requires `ack(id)`, unacked urgent re-fires on cooldown (reuses reminder escalation machinery via 03), and urgents from the pass are logged with the dish/ticket they refer to. `NON_URGENT`: queued, surfaced at natural check moments (the next agent tick batch) so nobody gets interrupted for "we need more rosemary." `feed(role) -> (urgents, non_urgents)` for each side's screen. Everything is durable — a full service transcript is queryable afterward ("what did FOH ask for at 19:40?"). Rate-limit guard: identical body within N seconds dedupes (double-tap protection).

Tests: urgent ack cycle + re-fire, non-urgent batching, feed filtering by role, transcript query, dedupe, and — critical — urgent channel content never silently dropped on send failure (write-ahead to DB before notify).

**Acceptance:** Phase 5 complete — every plate photographed, scored live, and the two rooms can talk without shouting.

---

## Phase 6 — Planning Boards

| #  | Module | File | Depends On |
|----|--------|------|------------|
| 21 | Kanban engine | `modules/kanban.py` | 01, 02 |
| 22 | Chef / Agent / Server-Host boards | `modules/boards.py` | 21, 08, 12 |

### Prompt 21 — Kanban Engine
**Build:** `modules/kanban.py` + `tests/test_kanban.py`
**Depends on:** 01, 02

One generic board machine, three instantiations later: boards (id, name), cards (`KanbanCard`: title, detail, column, owner_role, due, blocked flag). API: `create_board`, `add_card`, `move(card_id, to_column)` with legal-column validation (WIP limit per column configurable — over-limit moves rejected with a clear message), `by_board(board)`, `by_owner(role)`, `blocked()`, `overdue()`. Column sets are data (JSON in the board row) — the engine imposes nothing. `move` records an audit row (card history).

Tests: full card lifecycle, WIP-limit rejection, per-owner filters, overdue math, audit trail rows, board isolation (card never leaks between boards).

---

### Prompt 22 — Chef, Agent & Server/Host Boards
**Build:** `modules/boards.py` + `tests/test_boards.py`
**Depends on:** 21, 08, 12

Three role-specific boards wired to live data — the boards think for themselves:
- **Chef board** ("Cuisine"): columns R&D → Testing → On Menu → 86'd. Cards seeded from recipe records; moving a recipe to "Testing" auto-creates a task to shoot a pass photo series (18) for it.
- **Agent board** ("Operations"): the agent's own queue — pending review items (12), temperature escalations (13), ordering deadlines (10), backlog alerts (17) auto-appear as cards when their events fire. This board is the agent's to-do list made visible.
- **Server/Host board ("Hospitality")**: sections/stations, sidework checklists, VIP notes, reservation notes — user-entered cards with open/close automation (close checklist auto-generated at SERVICE_CLOSE).
**No hardcoded content** — recipes, VIPs, and sidework items are user data; this module wires structure + automation only.

Tests: event → card auto-creation for each source (review pending, temp escalation, order deadline, wash backlog), recipe → chef card seeding, close-checklist generation, and the WIP/audit behaviors of 21 still holding through the wrappers.

---

## Phase 7 — Service Intelligence & the Michelin Track

| #  | Module | File | Depends On |
|----|--------|------|------------|
| 23 | Service journal & post-service review | `modules/journal.py` | 13, 16, 17, 19, 20 |
| 24 | Consistency scoring & drift detection | `modules/consistency.py` | 18, 19, 13 |
| 25 | Michelin aspiration dashboard | `modules/michelin.py` | 23, 24, 21 |

### Prompt 23 — Service Journal & Post-Service Review
**Build:** `modules/journal.py` + `tests/test_journal.py`
**Depends on:** 13, 16, 17, 19, 20

At `SERVICE_CLOSE` the agent compiles the evening into one reviewable record — the "restaurant data review": covers, per-dish fired/plated/picked-up gaps (16), pace histogram, dish review score averages and lows (19), temperature excursions (13), pit throughput (17), communication highlights (urgents sent/acked/avg ack time from 20), 86 events. `compile_service(service_date) -> ServiceJournal` (a structured dataclass + a rendered `markdown_report()` the chef reads in 2 minutes). Also captures chef free-text notes (`annotate(service_date, note)`). The journal is the review artifact — the habit of nightly review is where stars come from.

Tests: compile from seeded fixtures of each source module, report contains every section, annotate appends, missing-module graceful sections (no dish photos → section says so, doesn't crash), markdown is valid structure.

---

### Prompt 24 — Consistency Scoring & Drift Detection
**Build:** `modules/consistency.py` + `tests/test_consistency.py`
**Depends on:** 18, 19, 13

Michelin's fifth criterion is consistency — this module measures it. Per dish, across services: score variance (19), plating drift (photo analyzer metrics from 18: portion-size delta, component-count delta between dates), temp stability (13: excursion counts on that dish's station's zones), timing variance (16: fire→plate seconds). `drift_report(dish_id) -> (drift_score, per-metric breakdown, trend)` with plain-language findings ("Portion score down 0.8 over 4 services; plating photo suggests sauce weight −12%"). Pure-Python statistics (variance, linear trend sign) — no numpy dependency required for core math. Thresholds in config; findings fire cards onto the Agent board (22).

Tests: variance/trend on known series, drift detection on synthetic declining scores, cross-metric breakdown completeness, threshold → agent-board card, no-data dish returns "insufficient history" gracefully.

---

### Prompt 25 — Michelin Aspiration Dashboard
**Build:** `modules/michelin.py` + `tests/test_michelin.py`
**Depends on:** 23, 24, 21

The star-track surface. Michelin's five official criteria, each grounded in the agent's own data — no invented scores, every number traceable: **quality of ingredients** (freshness scores 05, supplier reliability 09), **mastery of technique** (dish review execution scores 19, timing consistency 24), **personality in cuisine** (R&D board activity 22, new-dish launch cadence from recipe dates), **value** (portion cost 07 vs. menu price data the user enters), **consistency** (drift scores 24, journal lows 23). `criteria_status() -> per-criterion (evidence_summary, current_signal, focus_recommendation)`. Dashboard page (same Flask factory as 19): five criteria cards, each showing the evidence trail and the ONE recommended focus. Also `star_readiness_note()`: an honest, evidence-cited paragraph — the agent never claims a star is coming; it shows what the data says and what to work on. Human review applies to any conclusions before they're shared outside the kitchen.

Tests: each criterion pulls from the right sources (seed each and verify the summary changes), empty-data graceful cards, focus recommendation picks the weakest criterion, dashboard page renders all five cards, readiness note cites evidence IDs.

---

## Phase 8 — Integration & Delivery

| #  | Module | File | Depends On |
|----|--------|------|------------|
| 26 | Orchestrator wiring | `main.py` | all |
| 27 | CLI | `cli.py` | 26 |
| 28 | Hermes agent integration | `hermes_bridge.py` | 26, 03 |
| 29 | End-to-end integration tests | `tests/test_e2e.py` | all |
| 30 | Docs, deployment & ops runbook | `README.md`, `docs/` | all |

### Prompt 26 — Orchestrator Wiring
**Build:** `main.py` + `tests/test_orchestrator.py`
**Depends on:** all modules
**Parent builds this one directly** — it touches every file.

The single composition root: `build_agent(config_path) -> PassAgent` constructs Config → Database (migrate) → EventBus → every module in dependency order, wires every subscription (which module listens to which event — one table in the docstring), starts the Flask app (19, 25) in a thread when `--serve`, and exposes the tick loop for the CLI/Hermes. Graceful degradation is systematic: every module is optional except 01/02/03; `build_agent` logs what's absent and the agent runs with what's present. A wiring self-check `verify_wiring()` asserts every event published has ≥1 listener (warns on orphans).

Tests: full-construct from `:memory:` with fixtures, event-routing smoke (publish TEMP_EXCURSION → reminder + agent-board card appear), missing-module degradation, `verify_wiring` catches an intentionally-unwired event.

---

### Prompt 27 — CLI
**Build:** `cli.py` + `tests/test_cli.py`
**Depends on:** 26

argparse (stdlib — no dep) command surface for terminal use at the pass laptop: `the-pass serve` (start agent + web), `tick` (one loop — for cron), `plan --date --covers`, `orders --pending|--approve ID`, `temp log --station --c`, `review --pending`, `journal --date`, `boards --role`, `michelin`. Exit codes: 0 ok, 1 usage, 2 blocked-by-review (a human must act). Output is plain text, pipeable — the chef greps it. Every command maps 1:1 onto a module method; the CLI contains no business logic.

Tests: CliRunner-style invocation per command against a fixture DB (subprocess or argv-level), exit codes incl. the review-blocked path, `--json` flag machine output for scripting.

---

### Prompt 28 — Hermes Agent Integration
**Build:** `hermes_bridge.py` + `tests/test_hermes_bridge.py`
**Depends on:** 26, 03

Makes the pass agent conversational inside Hermes on Telegram: `handle(update: dict) -> Reply` where update is a parsed natural-language command from the gateway — intent routing over the same module methods the CLI uses ("86 the risotto" → inventory flag + FOH reminder + comms notice; "how's the pit" → wash counter readout; "order status" → review queue pending summary; "show me tonight" → service plan + pre-service checklist). Urgent pass messages relay to the Hermes home channel with a text the user taps to ack. Reminders due during service are pushed as Telegram messages. A `SUMMARY_CMD` table maps ~10 intents; unknown intents → helpful menu of what the agent understands. The bridge never auto-sends orders or auto-approves reviews — it reports and asks.

Tests: intent routing table hit/miss, 86-flow end state (inventory flagged + reminder + comms logged), ack relay, due-reminder push shape, no-approve invariant under any phrasing.

---

### Prompt 29 — End-to-End Integration Tests
**Build:** `tests/test_e2e.py` + `tests/fixtures/service_sim.py`
**Depends on:** all modules
**Parent builds this one directly.**

Simulate one full day in one test process on `:memory:`: pre-service (photos of a delivery ingested → approved → plan for 2 recipes × 40 covers → delay-model-informed draft order → human approves), service open (temp stream incl. one excursion → escalation; FOH↔BOH urgents; fire/plated/pickup sequences; wash racks; 6 pass photos scored live with one declining-dish pattern), close (journal compiles with every section non-empty; drift flags the declining dish; agent board shows the escalation cards; Michelin dashboard renders five evidence-cited cards). Assert the *narrative* outcomes, not implementation details: the order date respects the p90 delay; the excursion escalated after 2 consecutive readings; the journal names the low-scoring dish.

Tests: this file IS the test. **Acceptance:** the whole simulated day passes in < 60s on the Jetson; every prior phase's suite still green.

---

### Prompt 30 — Docs, Deployment & Ops Runbook
**Build:** `README.md` rewrite, `docs/ARCHITECTURE.md`, `docs/RUNBOOK.md`, `docs/ONBOARDING.md`, `docs/PRIVACY.md`
**Depends on:** all
**Phosphorus 🔥 voice for the README.**

- **README:** what this is (one sentence), the Michelin-star mission, quick start, the 30-prompt build map (links to PROMPTS.md phases), core features by role (Chef / Server-Host / Agent), Hermes + Telegram integration section, data sources & licenses, contributing pointer.
- **ARCHITECTURE.md:** module graph, event-routing table (the one from 26), the three data chains (ingredient chain, plate chain, order chain), degradation model.
- **RUNBOOK.md:** daily ops minute-by-minute — pre-service checklist (verify plan, approve orders, temp check), in-service (urgents protocol), close (journal review ritual), weekly (drift report, supplier grades), cron entries for `tick` and `journal`, backup (`sqlite3 .backup`), restore drill.
- **ONBOARDING.md:** how a new chef/server gets productive with the boards, the review app, and the comms line in one shift.
- **PRIVACY.md:** staff-facing data notes (comms transcripts, review attribution), retention policy, and that guest data never enters the system (no names, no payment data — covers and occasion flags only).

**Acceptance:** docs reviewed against the shipped system, every command in RUNBOOK verified runnable, README passes the "clone-and-first-run in 5 minutes" test.

---

## Dependency Graph (build order)

```
Phase 0:  01 ─ 02 ─ 03                (foundation — build directly, no delegation)
Phase 1:  04 ─ 05                     (parallelizable after 02)
Phase 2:  06, 07 parallel → 08
Phase 3:  09 → 10;  11 (needs 05+07+08+10);  12
Phase 4:  13, 14, 16, 17 parallel;  15 after 14
Phase 5:  18 → 19;  20 (after 14+15)
Phase 6:  21 → 22
Phase 7:  23, 24 (after their sources) → 25
Phase 8:  26 (parent) → 27, 28;  29 (parent);  30
```

## Testing Strategy

- **Mock mode everywhere:** vision, sensors, gateway, supplier comms all have deterministic mocks; the full suite runs offline in < 60s on a Jetson Orin Nano 8GB.
- **Counts:** every prompt ships 5-12 focused tests; target ≥ 250 total by prompt 29.
- **Run:** `python -m pytest tests/ -q` after every phase; fix before proceeding (drift compounds).
- **Human-gate invariants get their own tests:** nothing orders, approves, or publishes without the human step — tested in 04, 11, 12, 25, 28, 29.

## Progress Tracker

Mark prompts as they land:

- [ ] 01-03 Foundation
- [ ] 04-05 Ingredient intelligence
- [ ] 06-08 Recipes & global DB
- [ ] 09-12 Ordering & human review
- [ ] 13-17 Kitchen operations
- [ ] 18-20 Pass, review & comms
- [ ] 21-22 Planning boards
- [ ] 23-25 Service intelligence
- [ ] 26-30 Integration & delivery
