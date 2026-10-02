# Project State: The Pass

> **Last updated:** 2026-10-01
> **Current phase:** planning (framework authored, code not started)
> **Overall health:** green

---

## 1. Goal (1-2 sentences)

A Hermes agent program that is the perfect restaurant agent for the pass, helping
one chef and one server achieve Michelin-star success — ingredients, ordering,
temperature, reminders, photos, live dish review, FOH↔BOH comms, counters, boards,
and star-track intelligence, all with human review gates.

## 2. Current Status

### Done
- [x] 30 build prompts authored, phased 0-8 with dependency table and testing strategy (PROMPTS.md)
- [x] Repo scaffolded (README, ARCHITECTURE, LICENSE, .gitignore, Project_state)
- [x] Repo published to GitHub (drwjkirkpatrick-web/the-pass)

### In Progress
- [ ] Nothing — awaiting go-ahead to start Prompt 01

### Not Started
- [ ] Prompts 01-30 (all code)

## 3. Architecture & Key Decisions

| Decision | Rationale | Date |
|---|---|---|
| Prompts-first build (PROMPTS.md as build contract) | Tested pattern; each prompt is self-contained and subagent-executable | 2026-10-01 |
| SQLite + stdlib, mock-first | Runs offline fast on Jetson; real backends are adapters | 2026-10-01 |
| Human review gates on orders, ingredient curation, and shared conclusions | The agent drafts, the human decides — tested invariant | 2026-10-01 |
| Global recipe DB integrated read-only via env var OPEN_GLOBAL_RECIPES_DB | Reuse Walker's existing global recipe database; degrade gracefully when absent | 2026-10-01 |
| No hardcoded restaurant content | User supplies menu, suppliers, zones, checklists — agent supplies structure | 2026-10-01 |
| p90 (not median) supplier delay for order-by dates | Safety margin is the point of ordering ahead | 2026-10-01 |

## 4. Blockers & Risks

- **Risk:** real vision model for ingredient/pass photos is nontrivial — **Mitigation:** mock analyzers ship first (Prompts 04, 18); real CV is a later adapter behind the same interface.
- **Risk:** Michelin dashboard could drift into invented claims — **Mitigation:** Prompt 25 requires evidence-cited output only; human review before external sharing.

## 5. Next Step (only ONE)

> **Next:** Execute Prompt 01 — core types & domain model (`core/types.py` + `tests/test_types.py`).

## 6. Environment & Tooling Notes

- Runtime: Python 3 stdlib + Flask (review app) on Jetson Orin Nano 8GB
- Key env vars: `OPEN_GLOBAL_RECIPES_DB` (global recipe DB path)
- Hermes: Telegram bridge via Prompt 28 (`hermes_bridge.py`)
- Skills used: software-development-workflows (prompts-first build pattern), github-workflows

## 7. Recent Session Log

- 2026-10-01: Framework session — authored 30 build prompts, scaffolded repo, published to GitHub.

## 8. References

- Build contract: `PROMPTS.md`
- Architecture: `docs/ARCHITECTURE.md`
