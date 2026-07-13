# Deploy / Go-Live Runbook — Cubby → Quo sync

The production engine is `engine.py`. It is **externalId-anchored**: every Quo
contact is keyed by the Cubby `customerId` stored as the contact's `externalId`.
One write path (`upsert`): look up by externalId → PATCH if found, else POST.
Every write sends the **full payload** (Quo PATCH replaces omitted fields).

Commands: `adopt` (one-time bridge), `run` (incremental), `baseline` (full reconcile + cursor).

## Order of operations — DO NOT reorder
1. **Adoption 100%.** Every existing Quo tenant card must carry its `externalId`
   before any `--commit` sync. Confirm none remain:
   ```
   python engine.py adopt          # dry; want "unique customers to adopt: 0"
   ```
   Why it matters: an un-adopted card has no externalId, so a sync can't find it and
   would **POST a duplicate** instead of updating it.
2. **Seed the cursor** with one full reconcile (idempotent — mostly no-ops after adoption):
   ```
   python engine.py baseline --commit
   ```
   This also writes `state.json.cursor`.
3. **Go live on GitHub Actions** (below).

## GitHub Actions setup (owner steps)
1. Create the repo and push (this folder is already a git repo with an initial commit).
2. Repo → **Settings → Secrets and variables → Actions**:
   - **Secrets:** `CUBBY_API_KEY`, `QUO_API_KEY`
   - **Variable:** `CUBBY_FACILITY_IDS = fac_UyjSQX2ys9L,fac_UV1y8r1xdy4,fac_G2VMi2ukY8Y`
3. Seed the cursor in CI: **Actions → "Cubby to Quo baseline" → Run workflow** once.
   It reconciles everyone and commits `state.json` back (force-added past `.gitignore`).
4. The scheduled workflows are then live:
   - `cubby-quo-sync.yml` — `run --commit` 3×/day (11:00 / 18:00 / 23:00 UTC).
   - `cubby-quo-baseline.yml` — `baseline --commit` weekly (Sun 09:00 UTC).

**Safety net:** `run` refuses to write without a cursor, so if a schedule fires before
step 3 it simply no-ops — it cannot create duplicates.

## Rollback / safety properties
- Every `adopt --commit` writes a before-state record to `exports/adopt_audit_*.json`.
- `run` / `baseline` are idempotent and full-payload — never partial, so never wipe.
- No delete operation exists anywhere in the code (contact removal is manual, in the Quo app).
- `.env` and `state.json` are gitignored; the Actions force-add only `state.json`.

## Facilities
`org_Qt8ANrFqg84` → Southern Storage `fac_UyjSQX2ys9L`, Ocean City Storage
`fac_UV1y8r1xdy4`, Vaulta Storage `fac_G2VMi2ukY8Y`.
