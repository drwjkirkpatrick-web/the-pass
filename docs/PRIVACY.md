# The Pass — Privacy and Data Notes

Written for the two people who run this, not for a lawyer. The principle is
simple: **the agent needs to know about food and timing, not about people.**

## What is stored

| Data | Where | Why |
|---|---|---|
| Ingredients, par levels, freshness | `data/the_pass.db` | ordering and 86 warnings |
| Recipes, portions, menu prices | same | scaling, costing, value criterion |
| Suppliers, delivery history | same | the order-by date math |
| Temperature readings | same | cold chain evidence (append-only) |
| Plate photos | `data/photos/pass/` | consistency and drift |
| Dish scores and notes | database | drift detection, journal |
| Front/back-of-house messages | database | the service transcript, acknowledgements |
| Reminders and acknowledgements | database | who was told what, and when |
| Chef's journal notes | database | the nightly review |

## What is deliberately not stored

- **Guest names, contact details, payment data — nothing.** Occasion reminders
  carry a table number and an occasion ("anniversary, table 12"), never an
  identity. The floor does not need a database of people to be hospitable.
- **Staff identity beyond a role label.** Actions are attributed to `chef`,
  `server`, `host` or `agent`. There is no per-person tracking, no scoring of
  people, and no analytics on who did what.
- **Anything about guests' health.** Allergies live on a ticket as a kitchen
  instruction ("shellfish allergy, table 6") and are not linked to a person.
- **Camera footage.** The pass camera is for plates. Photos are of food.

## Retention

- Messages, reminders and readings: keep them. They are the evidence behind the
  journal, the supplier grades and the cold-chain log. They are small.
- Plate photos: the big files. Check monthly (`du -sh data/photos/*`) and archive
  or delete old service directories when you need the space — the database only
  stores paths, so the records stay valid.
- Backups: nightly, seven days, in `data/backups/`.

## Access

Everything is local: one SQLite file and one photo directory on the machine in
the kitchen. There is no cloud service, no account, and no analytics. The
Telegram bridge sends messages through Telegram (that is how you get them on
your phone) — nothing else leaves the building.

## If you need to delete something

```bash
# one service's photos
rm -rf data/photos/pass/2026-10-01

# a single message from the transcript
sqlite3 data/the_pass.db "DELETE FROM pass_messages WHERE id = 'm-abc123'"

# start the whole record over (keeps a backup first)
cp data/the_pass.db data/backups/pre-reset-$(date +%F).db
rm data/the_pass.db && the-pass status     # recreates an empty database
```
