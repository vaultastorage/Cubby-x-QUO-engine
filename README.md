# Cubby → Quo contact sync

Keeps Quo (OpenPhone) contact cards in sync with tenant data in Cubby. One card per
customer, named `Customer Name - Unit(s)` while active or `Customer Name - Former`
after move-out. Keyed on the Cubby `customerId`, so there are no duplicate cards and
"new tenant vs. existing tenant" is a lookup, not a guess.

```
Cubby API  ──►  this script (GitHub Actions, 2–3×/day)  ──►  Quo contact cards
```

## Setup (5 minutes)

1. **Python deps**
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Credentials and config** — copy the template and fill it in:
   ```bash
   cp .env.example .env
   ```
   Set `CUBBY_API_KEY`, `QUO_API_KEY`, and `CUBBY_FACILITY_IDS` (your two facility
   ids, comma-separated). `.env` is gitignored — it never leaves your machine.

3. **Preflight**
   ```bash
   python cubby_quo_sync.py check
   ```
   Must print `CHECK: PASS`. If phones come back blank, your Cubby key lacks PII
   access — get a PII-enabled key before going further. If Quo auth fails, set
   `QUO_AUTH_SCHEME="Bearer "` in `.env`.

## First run (one time)

1. In the **Quo app**, bulk-delete your existing contacts. (API-created cards
   re-appear for any tenant with call/text history — your existing ~95%.)
2. Build the baseline as a dry run and review it:
   ```bash
   python cubby_quo_sync.py baseline
   open baseline_preview.csv     # confirm count + names + phones look right
   ```
3. Commit it:
   ```bash
   python cubby_quo_sync.py baseline --commit
   ```
   This creates the cards in Quo and writes `state.json` (the `customerId → quo_id`
   map and the sync cursor).

## Ongoing (automated)

The recurring sync only touches customers who changed since the last run:
```bash
python cubby_quo_sync.py run            # dry run — shows what would change
python cubby_quo_sync.py run --commit   # writes to Quo, advances the cursor
```
The included GitHub Actions workflow runs `run --commit` 3×/day and commits the
updated `state.json` back to the repo so the cursor persists. Set the two keys as
repository **Secrets** (`CUBBY_API_KEY`, `QUO_API_KEY`) and the facility ids as a
repository **Variable** (`CUBBY_FACILITY_IDS`).

## Make targets

```bash
make setup           # venv + deps
make check           # preflight
make baseline-dry    # dry run, writes preview CSV
make baseline        # baseline --commit
make run-dry         # incremental dry run
make run             # run --commit
make clean           # remove venv + generated files (keeps .env and state.json)
```

## Reference

- `docs/ARCHITECTURE.md` — the design and why it's built this way.
- `docs/CUBBY_API.md` — the Cubby endpoints and fields used.
- `docs/QUO_API.md` — the Quo contact API and its important caveats.
- `docs/RUNBOOK.md` — operations and troubleshooting.
- `CLAUDE.md` — guardrails for Claude Code.

## Cost

GitHub Actions free tier, Cubby API (included), Quo contact create/update
(included — no message credits used). No Zapier. Effectively $0/month.
