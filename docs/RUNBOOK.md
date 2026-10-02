# The Pass — Ops Runbook

Everything here assumes you are in the repo root with a working interpreter:

```bash
cd ~/projects/the-pass
PASS=".venv/bin/python cli.py --db data/the_pass.db"
```

Create the venv once:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

`--db` points at the restaurant's database. Back it up before you first run
anything against real data.

---

## 1. Pre-service (about 30 minutes before doors)

```bash
$PASS status                      # modules up, pending approvals, outstanding reminders
$PASS plan --covers '{"r-rib": 40, "r-salad": 20}'   # if the plan is not written yet
$PASS orders --curate             # exits 2: drafts now wait for you
$PASS orders --pending            # read what the agent wants to buy (exits 2 while it waits)
$PASS orders --approve <review-id>
$PASS orders --export <order-id>  # the text to send your supplier
$PASS temp --init-zones           # first time only: create your zones, then edit them
$PASS temp --zone z-walkin --c 3.0
```

**The order of operations matters:** the agent never sends an order. `--curate`
leaves drafts, `--approve` is the only path forward, `--export` gives you text to
paste into a supplier email or SMS.

**Exit codes are the signal:** `0` done, `1` usage error, `2` a human has to
decide something (`orders --curate`, `orders --pending`, `review --pending`,
an out-of-bounds temperature, an unapproved ingredient draft). Cron can tell the
difference between "fine" and "waiting on you" without parsing text.

## 2. During service

```bash
$PASS temp --zone z-walkin --c 9.0     # exits 2: out of bounds, a human must act
$PASS review --pending                 # plates waiting to be scored (exits 2 while any wait)
$PASS boards --role agent              # what the agent needs from a human right now
```

From a phone, on Telegram:

```
86 the scallops          how's the pit            order status
show me tonight          temps                    reminders
journal                  michelin                 ack <message-id>
```

Urgent calls repeat until somebody acknowledges them. `ack <id>` is the
acknowledgement.

## 3. Close (after the last table)

```bash
$PASS journal                                  # the night's report
$PASS journal --note "sauce broke on the last two plates"
$PASS michelin                                 # five criteria + this week's focus
```

Read the journal before you leave. That habit is the whole point of the module —
two minutes of review while the night is still fresh.

## 4. Cron / systemd

Two beats a day is enough to start:

```cron
# one beat every 5 minutes through prep, every minute during service
*/5 9-15 * * * cd /home/walker/projects/the-pass && .venv/bin/python cli.py tick >> logs/tick.log 2>&1
*   16-23 * * * cd /home/walker/projects/the-pass && .venv/bin/python cli.py tick >> logs/tick.log 2>&1

# the nightly journal and drift check
0 23 * * *     cd /home/walker/projects/the-pass && .venv/bin/python cli.py journal >> logs/journal.log 2>&1
```

`tick` is idempotent: reminders fire once, service open/close is announced once
per day, and nothing stacks up if cron runs twice.

For a long-running install with the web surfaces:

```bash
setsid .venv/bin/python cli.py serve >> logs/serve.log 2>&1 < /dev/null &
```

## 5. Backup and restore

Nightly backup (keeps 7 days):

```bash
mkdir -p data/backups
sqlite3 data/the_pass.db ".backup data/backups/the_pass-$(date +%F).db"
find data/backups -name 'the_pass-*.db' -mtime +7 -delete
```

Restore drill — do this once before you need it:

```bash
cp data/the_pass.db data/the_pass.db.broken
cp data/backups/the_pass-2026-10-01.db data/the_pass.db
$PASS status        # the agent should report its modules and today's state
```

The photos directory (`data/photos/`) grows with every plate. Check it monthly:

```bash
du -sh data/photos/*
```

Photos are files; the database only stores paths, so archiving old service
directories is safe and does not break the records.

## 6. When something looks wrong

| Symptom | First thing to check |
|---|---|
| No reminders arrived | `$PASS status` — is `reminders_boh` in the module list? then `$PASS boards --role agent` |
| Orders are not appearing | the plan: `$PASS plan --covers ...` then `$PASS orders --curate` |
| Temperature alerts stopped | `$PASS temp` — are zones defined, and is the latest reading recent? |
| The journal is empty | check the service date (`$PASS journal --date YYYY-MM-DD`) and that plates were counted (`$PASS status`) |
| `serve` fails | Flask is not installed; the agent itself does not need it |
| An event seems ignored | `python3 -c "from main import build_agent, verify_wiring; print(verify_wiring(build_agent()))"` |

## 7. Weekly

```bash
$PASS michelin --json | python3 -m json.tool    # criteria signals + the focus
$PASS boards --role chef                        # R&D still moving?
$PASS journal --date <each day>                 # read the week back
```

Drift cards on the agent board are the agent's own list of dishes that slipped.
They belong on the chef board as work, not in a report nobody opens.
