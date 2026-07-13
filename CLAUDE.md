# CLAUDE.md — instructions for Claude Code

You are working on a one-way contact sync that keeps Quo (OpenPhone) contact cards
in lockstep with tenant data in Cubby (self-storage property management). Read this
whole file before doing anything. The reference docs in `docs/` are the source of
truth for both APIs — use them instead of guessing or re-fetching.

## Current implementation (2026-07 — the production path)
The live sync is **`engine.py`** (externalId-anchored) — it supersedes the original
`state.json` id_map design in the notes below. Model: each card is keyed by the Cubby
`customerId` stored as the Quo `externalId`; the one write path `upsert` looks up by
externalId → PATCH (full payload) if found, else POST. Commands: `adopt` (one-time
bridge of the CSV-imported cards — completed 277/277), `run` (incremental via the
cursor), `baseline` (full reconcile + cursor). Deploy: `docs/DEPLOY.md`. Verify:
`docs/VERIFICATION.md`. `cubby_quo_sync.py` remains for `check` and the shared
Cubby/Quo helpers that `engine.py` imports.

## Goal
For each Cubby customer, maintain exactly one Quo contact card whose name is the
customer's name plus their currently-active units, or "Former" if they have none:
- 1 active unit  -> `Kelly Soverns - 43`
- 2 active units -> `Kelly Soverns - 43, 12`  (chronological rental order)
- moved out      -> `Robert Sandoval - Former`

The card is keyed on the Cubby `customerId` (`cust_...`), never on phone number.

## Golden rules (do not break these)
1. **Dry run before every write.** `check` -> `baseline` (dry) -> review CSV ->
   `baseline --commit`. For ongoing work, `run` (dry) before `run --commit`.
2. **Never commit secrets or state.** `.env` and `state.json` are gitignored. Do
   not print API keys. Do not paste real keys into any file you create.
3. **Never delete Quo contacts from code.** The one-time wipe is done by the user
   manually in the Quo app. There is no destructive delete in this project, and you
   must not add one.
4. **Anchor on the Cubby `customerId` as the Quo `externalId`; never match on name/phone.**
   Look up each card by `externalId` (`GET /v1/contacts?externalIds=`) → PATCH if found,
   else POST. Every write sends the FULL payload (defaultFields + customFields +
   externalId) — Quo PATCH REPLACES omitted arrays, so a partial write wipes data.
   CSV/in-app cards CAN be adopted by PATCHing in their externalId (`engine.py adopt`).
   `source` can't be a reserved word (csv*/openphone*/…); ours is `cubby-sync`. See
   `docs/QUO_API.md` and the memory note quo-write-gotchas.
5. **Keep the sync idempotent.** Every change recomputes the full card from the
   customer's current leases. Do not write event-specific handlers; if you think you
   need one, you've misunderstood — re-read `cubby_quo_sync.py:desired_card`.
6. **Preserve the cursor model.** `run` uses `state.json.cursor` as `updatedAfter`.
   Do not reset or skip the cursor. Losing it means a full re-scan.

## How it works (data flow)
Cubby `customers/search` + `leases/search` (with `unit` expansion) -> build the
desired card per customer -> create-or-patch in Quo by the stored `customerId ->
quo_id` map -> advance cursor. Incremental runs only touch customers whose
customer or lease record changed since the last cursor (`updatedAfter`).

## Commands
```
python cubby_quo_sync.py check              # validate env, Cubby auth + PII, Quo auth
python cubby_quo_sync.py baseline           # dry run -> baseline_preview.csv
python cubby_quo_sync.py baseline --commit  # create cards, write state.json
python cubby_quo_sync.py run                # incremental dry run
python cubby_quo_sync.py run --commit       # incremental, writes to Quo
```
(Or use the `make` targets — see `Makefile`.)

## First-time order of operations
1. User fills `.env` from `.env.example` (Cubby key, Quo key, facility ids).
2. `python cubby_quo_sync.py check` — must PASS. If phones are blank, the Cubby key
   lacks PII access; stop and tell the user to fix the key.
3. User bulk-deletes existing Quo contacts in the Quo app.
4. `baseline` (dry) -> open `baseline_preview.csv`, confirm row count matches the
   known tenant count and names/phones are populated.
5. `baseline --commit`.
6. Wire `run --commit` to the GitHub Actions workflow (already in `.github/`).

## Things to verify (don't assume)
- **Pagination:** the field schema doesn't document Cubby search paging, so the
  code fetches one page. If `check` or the preview shows a round-number count
  (100/250/1000), add a paging loop and confirm against the real tenant count.
- **Quo auth header:** OpenPhone normally wants the raw key in `Authorization`
  (no `Bearer`). If Quo calls 401, set `QUO_AUTH_SCHEME="Bearer "` in `.env`.
- **Quo field mapping:** the whole "Name - Unit" string goes in `firstName` with
  `lastName` blank (`QUO_NAME_FIELD`). Change only if the user asks.

## Files
- `cubby_quo_sync.py` — the whole program (check / baseline / run).
- `state.json` — cursor + `customerId -> quo_id` map. Gitignored locally; the
  GitHub Action commits it so the cursor persists between runs. Do not hand-edit.
- `baseline_preview.csv` — written by `baseline`; the human review artifact.
- `docs/` — API + architecture reference. Read these, don't re-derive.
- `.github/workflows/cubby-quo-sync.yml` — 3x/day cron + state commit.

## Out of scope (ask before doing)
- Writing back to Cubby (this is read-only on the Cubby side).
- Sending SMS/email from Quo (not part of contact sync; costs message credits).
- Any bulk delete, merge, or destructive Quo operation.
