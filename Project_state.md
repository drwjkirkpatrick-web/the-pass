# Project State: The Pass

> **Last updated:** 2026-10-01
> **Current phase:** maintenance / hardware integration
> **Overall health:** green

---

## 1. Goal

A Hermes agent program that is the perfect restaurant agent for the pass —
helping one chef and one server achieve Michelin-star success: ingredients,
ordering, temperature, reminders, pass photos, live dish review, FOH<->BOH comms,
counters, three planning boards, a nightly journal, drift detection, and a
five-criteria dashboard — with human review gates on everything that spends money
or changes the menu.

## 2. Current Status

### Done
- [x] 30 build prompts authored and then **executed** — one commit per prompt
- [x] 22 working modules + core + orchestrator + CLI + Telegram bridge
- [x] 316 tests green, offline, ~30s (`pytest tests/ -q`)
- [x] End-to-end simulated service day (delivery -> order -> service -> journal -> drift -> criteria)
- [x] Docs: README, ARCHITECTURE, RUNBOOK, ONBOARDING, PRIVACY
- [x] Repo published: drwjkirkpatrick-web/the-pass

### Not Started (hardware + real data)
- [ ] Pass camera adapter (`RealPlateAnalyzer` / camera capture) on the Jetson
- [ ] Temperature probe adapter (a `SensorSource` implementation)
- [ ] Real vision backend for ingredient photos (`RealVisionAnalyzer`)
- [ ] The restaurant's own data: recipes, suppliers, zones, sidework lists

## 3. Architecture & Key Decisions

| Decision | Rationale | Date |
|---|---|---|
| Prompts-first build, executed one prompt per commit | Resumable and reviewable; every module arrived with its tests | 2026-10-01 |
| Mock-first (vision, sensors, gateway) | The suite runs offline; hardware is an adapter, not a prerequisite | 2026-10-01 |
| Human gate enforced in code | `Ordering.set_status` refuses APPROVED; only `ReviewQueue.approve()` passes | 2026-10-01 |
| p90 supplier delay, not the mean | Order early to survive the bad delivery; worst-recent reported separately | 2026-10-01 |
| One reminder engine, one bus, one schema | Consistent behaviour across CLI, web and Telegram | 2026-10-01 |
| No hardcoded restaurant content | Recipes, suppliers, zones and lists are the team's own data | 2026-10-01 |
| Timestamps are local wall-clock | The Jetson sits in the restaurant; local time is the chef's time | 2026-10-01 |

## 4. Blockers & Risks

- **Risk:** a real plating model will score differently from `MockPlateAnalyzer` —
  **Mitigation:** one-method interface; thresholds live in config.
- **Risk:** photo storage growth — **Mitigation:** monthly check in the RUNBOOK;
  the database stores paths only, so archiving directories is safe.
- **Risk:** Telegram delivery failure could lose an urgent call — **Mitigation:**
  comms writes the message row before announcing (tested).

## 5. Next Step (only ONE)

> **Next:** wire one real hardware adapter — start with the temperature probe
> (`SensorSource`): smallest interface, and the cold-chain log is the most
> immediately valuable record.

## 6. Environment & Tooling Notes

- Runtime: Python 3.10+ stdlib; Flask optional for the web surfaces
- Interpreter used for the build: `/home/walker/projects/scary-auntie/venv/bin/python`
  (3.11 with pytest + flask — the project's own venv does not exist yet)
- Key env var: `OPEN_GLOBAL_RECIPES_DB`
- Hermes: Telegram bridge via `hermes_bridge.py`
- Skills used: software-development-workflows (prompts-first build), project-state-management

## 7. Recent Session Log

- 2026-10-01: Framework session — 30 build prompts authored, repo published.
- 2026-10-01: Build session — all 30 prompts executed and committed (316 tests).
  The end-to-end day caught 5 real bugs (unit conversion, no-plan crash, plan
  lookup, path traversal, article in "86 the X"). Docs written; every RUNBOOK
  command verified runnable.

## 8. References

- Build contract: `PROMPTS.md`
- Architecture (as built): `docs/ARCHITECTURE.md`
- Operations: `docs/RUNBOOK.md`
- Onboarding: `docs/ONBOARDING.md`
- Data notes: `docs/PRIVACY.md`
