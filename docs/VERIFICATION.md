# Verification Checklist — Cubby ↔ Quo sync

Checkable now: §1–§3. §4 applies once the GitHub Actions schedule is live.

## 1 · Connections (both APIs reachable & authorized)
Run `python cubby_quo_sync.py check` → want `CHECK: PASS`.
- **Cubby**: `auth OK — N customers at …` + `PII: name=yes phone=yes`.
  - 🚩 `phone=NO` → Cubby key lost PII access. 🚩 `403 / FAILED` → malformed request / key scope.
- **Quo**: `auth OK (404 on bogus id)`.
  - 🚩 `401 / 403` → wrong Quo key; try `QUO_AUTH_SCHEME="Bearer "` in `.env`.

## 2 · Adoption complete & healthy
- `python engine.py adopt` (dry) → `unique to adopt: 0`, `duplicates: 0`, `anomalies: 0`.
- Every adopt batch ended `failed: 0` (any `VERIFY FAILED` = stop and inspect).
- In the **Quo app**, open 2–3 contacts: name = `Name unit(s)` / `Name Former`;
  **phone + email present**; the 4 custom fields (ID / Storage Facility / Move type /
  Move date) populated; ID = the `cust_…`.
- **Call history**: a tenant who's texted/called shows the contact **name**, not a bare number.

## 3 · Engine reflects reality (spot-checks)
- **Idempotent**: `python engine.py demo <customerId>` on an unchanged tenant → `DECISION: SKIP`.
- **Move-out**: a moved-out tenant → name ends `Former`, Move type `Move Out`.
- **Active**: a current tenant → unit number(s) in the name, Move type `Move In`.
- **One card per tenant**: search a name in Quo — exactly one card, no duplicates.

## 4 · Ongoing automation (once the schedule is live)
- GitHub → Actions → both workflows show green runs on schedule.
- `state.json` cursor timestamp advances each run (committed back by the Action).
- After a real Cubby change (e.g. a move-out), the next `run` updates that card and
  reports it touched **only** changed customers.
- No failure emails from Actions.

## 🚩 Stop & investigate if you see
Any `VERIFY FAILED` · a card with an empty phone (breaks caller ID) · duplicate contacts
for one person · `check` showing blank phones · a run touching far more customers than
actually changed (cursor lost/reset).
