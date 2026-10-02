🔥 # The Pass

**A Hermes-powered restaurant agent that lives at the pass — built to help one chef and one server earn a Michelin star.**

The pass is where a kitchen wins or loses its night. Every plate crosses it; every fire, every 86, every "how long?" lands there. *The Pass* is a local-first agent that sits at that exact spot — watching ingredients, timing orders, guarding the cold chain, photographing plates, and keeping front and back of house in one conversation — so the two people it serves can put their whole attention on food and hospitality.

It runs on a Jetson in the corner, talks to you on Telegram, and keeps every number it reports traceable to something that actually happened in your kitchen.

**Status:** fully implemented — 30 modules, 316 tests, one simulated service day end to end. Hardware still to come: the pass camera and the temperature probes (both are single adapters behind interfaces that already exist).

---

## What it does

**For the chef (back of house)**
- 📷 **Reads photos of what came in** and drafts an ingredient list — you approve or correct it, nothing lands in inventory on its own
- 📖 **Scales recipes and adds up the whole menu**, so ordering starts from arithmetic instead of memory
- 🚚 **Learns each supplier's real delivery record** and tells you the day to order so it lands the day before you need it (it plans against their *p90* delay, not their average — you order early to survive the bad delivery, not the typical one)
- 🌡️ **Logs temperatures against your own HACCP zones**; two breaches in a row becomes an urgent call to the kitchen
- ⏲️ **Prep timers, mise checks, order cutoffs, delivery windows** — back-of-house reminders that know your service time
- 🔁 **Counts plates and racks** — fired, plated, picked up, and what the pit is carrying

**For the server/host (front of house)**
- 🍽️ **Briefings, sidework checklists, restock and occasion reminders** — from your own lists, never a preset
- ⚠️ **86 warnings before you have to pull the dish**, straight from live inventory
- 💬 **A two-lane line to the kitchen**: urgent calls (allergies, 86s, a plate dying) need an acknowledgement and repeat until they get one; everything else queues into a digest so nobody is interrupted for rosemary

**For the star track**
- 📸 **Photographs every dish on the pass**, linked to the plate and the ticket
- 📱 **Live dish review** — score a plate in seconds on a phone; the trend is what tells you whether tonight looked like last night
- 📓 **A nightly service journal** — one page, two minutes: covers, pace, plate scores, cold chain, the pit, the urgent calls
- 🎯 **Consistency scoring and drift detection**, because consistency is the fifth criterion and the one a kitchen cannot feel from the inside
- ⭐ **A five-criteria dashboard** (ingredients, technique, personality, value, consistency) where every signal is computed from your records and the agent says plainly what the data shows — it never tells you a star is coming

**Three boards, one kitchen:** *Cuisine* for the chef (R&D → Testing → On Menu → 86'd), *Operations* for the agent (its own queue: approvals waiting, temperature trouble, order windows closing today, dishes drifting), and *Hospitality* for the server/host.

**And it answers the phone:** the agent runs inside Hermes on Telegram.

```
86 the scallops        → pulled from inventory, floor warned, urgent line notified
how's the pit          → open racks, throughput, peak hour
order status           → what is waiting on your approval
show me tonight        → the plan, the shortfalls, what to use first
temps / journal / michelin / ack <id>
```

## Two rules it never breaks

1. **The human is the gate.** The agent drafts; a person approves. Orders cannot be approved from chat, no code path can turn a draft into an approved order, and that invariant has its own tests.
2. **No invented data.** Every grade, score and recommendation cites rows from your own records. When there is not enough history to say anything useful, it says that instead of guessing.

## Quick start

```bash
git clone https://github.com/drwjkirkpatrick-web/the-pass.git
cd the-pass

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt     # Flask is optional; the agent runs without it

# one beat — what the agent knows right now
.venv/bin/python cli.py --db data/the_pass.db status

# define your temperature zones, then log a reading
.venv/bin/python cli.py --db data/the_pass.db temp --init-zones
.venv/bin/python cli.py --db data/the_pass.db temp --zone z-walkin --c 3.0

# plan a service: {"recipe-id": covers}
.venv/bin/python cli.py --db data/the_pass.db plan --covers '{"r-rib": 40}'

# curate purchase orders (exits 2: they are drafts until you approve them)
.venv/bin/python cli.py --db data/the_pass.db orders --curate
.venv/bin/python cli.py --db data/the_pass.db orders --pending
.venv/bin/python cli.py --db data/the_pass.db orders --approve <review-id>

# the web surfaces (live dish review + the five criteria)
.venv/bin/python cli.py --db data/the_pass.db serve   # http://127.0.0.1:8788
```

Exit codes are a contract: **0** done, **1** usage error, **2** blocked — a human
has to decide something (`orders --curate`, `orders --pending`,
`review --pending`, an out-of-bounds temperature reading, an unapproved
ingredient draft). Cron and shell scripts can tell the difference between "fine"
and "waiting on you" without parsing text.

Day-to-day operation lives in [`docs/RUNBOOK.md`](docs/RUNBOOK.md); a new team member can be productive in one shift with [`docs/ONBOARDING.md`](docs/ONBOARDING.md).

## How it is built

The build contract is [`PROMPTS.md`](PROMPTS.md): **30 numbered, testable prompts** across 9 phases, each one a self-contained brief with its own files, dependencies and acceptance criteria. That is also how it was built — one prompt at a time, tests green, commit, next.

```
core/      types, config, SQLite schema, event bus, agent clock
modules/   the 22 working modules (ingredients → recipes → ordering → kitchen
           → pass → boards → journal → drift → criteria)
main.py    composition root; every module optional, none required except core/
cli.py     the command surface (thin: no business logic)
hermes_bridge.py  Telegram intent routing
tests/     316 tests, mocks everywhere, offline in ~30s
```

Design decisions worth knowing:
- **Mock-first.** Vision, sensors and the messaging gateway all have deterministic mocks, so the whole suite runs offline and a missing camera is never a blocker.
- **Degrade, never abort.** A missing global recipe database, a dead sensor, no Flask — the agent logs it and keeps working.
- **No hardcoded restaurant content.** Recipes, suppliers, zones, sidework and occasions are your data. The agent supplies structure and arithmetic.
- **One shared reminder engine**, one event bus, one SQLite schema — so behaviour is consistent across the CLI, the web app and Telegram.

## Testing

```bash
.venv/bin/python -m pytest tests/ -q          # 316 passed
```

## License

MIT — see [LICENSE](LICENSE).
