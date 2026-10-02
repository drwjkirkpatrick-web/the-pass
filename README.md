🔥 # The Pass

**A Hermes-powered restaurant agent that lives at the pass — helping one chef and one server earn a Michelin star.**

The pass is where a kitchen wins or loses its night. Every plate crosses it; every
fire, every 86, every "how long?" lands there. *The Pass* is a local-first agent
that sits at that exact spot — watching ingredients, timing orders, guarding the
cold chain, photographing plates, and keeping front and back of house in one
conversation — so the two people it serves can focus on food and hospitality.

This repository is the **framework**: the complete build contract for the agent,
expressed as 30 numbered, testable build prompts in
[`PROMPTS.md`](PROMPTS.md). Each prompt is self-contained — a fresh agent with
zero context can execute one and produce a working, tested module.

---

## What the agent does

**For the chef (back of house):**
- 📷 Reviews photos of deliveries to curate the live ingredient list (human-approved, never automatic)
- 📖 Lists recipes with exact portion scaling, and aggregates ingredient requirements from covers forecasts
- 🚚 Learns each supplier's real delivery delay history and recommends **order-by dates days in advance** for day-before arrival
- 🌡️ Logs temperatures against your own HACCP zone plan, with escalating excursion alerts
- ⏲️ BOH reminders: prep timers, mise checks at T-minus, ordering deadlines
- 🔁 Dish counter and dish-washing counter — the service heartbeat and the pit, both instrumented

**For the server/host (front of house):**
- 🍽️ FOH reminders: briefings, sidework checklists, restock, VIP occasions
- ⚠️ 86 warnings *before* the dish has to be pulled — straight from live inventory
- 💬 A structured urgent / non-urgent communication line to the kitchen (urgent requires an ack)

**For the star track:**
- 📸 Photographs **every dish on the pass**, scored live in seconds on a phone at the pass
- 📊 Nightly service journal — one 2-minute read covering pace, gaps, scores, temps, and the pit
- 🎯 Consistency scoring and drift detection (portion, plating, timing, temperature) — because consistency is the fifth Michelin criterion
- ⭐ A Michelin dashboard that grounds all five official criteria in the restaurant's own evidence, with one honest focus recommendation — never an invented score

**Three kanban boards, one kitchen:** a Cuisine board for the chef (R&D → Testing → On Menu → 86'd), an Operations board the agent keeps stocked with its own escalations, and a Hospitality board for the server/host.

**And it talks to you:** the agent runs inside Hermes on Telegram — ask "how's the pit?", say "86 the risotto", check order status, all from your phone.

## Design principles

1. **The human is always the gate.** The agent drafts — orders, ingredient approvals, conclusions — a person approves. Every food-and-money decision has a review step, and that invariant is unit-tested.
2. **No invented data.** Every score, grade, and recommendation cites evidence rows from the restaurant's own logs.
3. **Local-first, mock-first.** SQLite, stdlib where possible, deterministic mocks for vision/sensors/gateway — the whole suite runs offline in under a minute on a Jetson.
4. **No hardcoded restaurant content.** Recipes, suppliers, zones, checklists, and VIP notes are your data; the agent provides the structure.
5. **Degrade gracefully.** Missing global recipe DB, missing sensor, missing module — the agent logs and keeps working with what's present.

## Quick start

```bash
git clone https://github.com/drwjkirkpatrick-web/the-pass.git
cd the-pass
open PROMPTS.md        # the build contract — start at Prompt 01
```

The build order, dependency table, and per-phase acceptance criteria are all in
[`PROMPTS.md`](PROMPTS.md). Current status lives in
[`Project_state.md`](Project_state.md).

## Repository layout

```
the-pass/
├── PROMPTS.md            # 30 build prompts — the heart of this repo
├── Project_state.md     # living status: done / in progress / next step
├── docs/
│   └── ARCHITECTURE.md  # module graph, event routing, three data chains
└── (code lands here as prompts are executed)
```

## The three data chains

- **Ingredient chain:** photo → curated draft → human approval → inventory → recipe scaling → shortfall → dated order draft → human approval → PO
- **Plate chain:** fire → plated (photographed) → picked up → scored → journal → drift report
- **Order chain:** covers forecast → requirements → delay model → order-by date → review queue → export

## Contributing

Execute one prompt at a time, test-first, in phase order. Pointers in
`PROMPTS.md` → "Testing Strategy".

## License

MIT — see [LICENSE](LICENSE).
