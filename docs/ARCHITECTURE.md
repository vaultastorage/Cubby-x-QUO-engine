# Architecture

## The funnel
```
Cubby API  ──►  Python sync (GitHub Actions, 2–3×/day)  ──►  Quo (OpenPhone) cards
                         │
                   state.json (cursor + customerId → quo_id map)
```
This replaced the old chain (BigQuery → Apps Script → Google Sheet → Zapier → Quo).
Everything between BigQuery and Quo was middleware that existed only because nothing
wrote to Quo directly. With both APIs live, it collapses to one script.

## Why it's built this way

**Key on `customerId`, not phone.** The old pipeline keyed on phone, which produced
duplicates whenever a number changed, a tenant had two lines, or two people shared a
number. Cubby's `customerId` (`cust_...`) is the stable identity. We map it to the
Quo contact `id` once, at creation, and patch by that id forever.

**Recompute, don't react.** A card's name is a pure function of the customer's
current leases: their active units joined chronologically, or "Former" if none. So
move-in, second unit, partial move-out, and full move-out are all one code path
(`desired_card`). A missed run self-heals on the next one — no event replay needed.

**Incremental via `updatedAfter`.** Cubby's `customers/search`, `leases/search`, and
`leads/search` accept a strict `updatedAfter` UTC timestamp cursor. Each run pulls
only records changed since the last cursor, finds the affected customers, recomputes
their cards, and advances the cursor. Typical run touches a handful of records, not
all ~170.

**API-owned contacts.** Quo can only patch contacts the API created (it keys off the
`id` returned at creation). Contacts created by CSV/in-app can't be adopted by the
API. So the baseline is created via the API, which makes every future change a clean
PATCH. This is also why the one-time wipe + API baseline is required rather than a
CSV import. See `QUO_API.md`.

## Determining "active"
A lease is active if it hasn't been moved out of: `moveOutDate` is null, or is a date
today/in the future. Active unit names come from the `unit` expansion on
`leases/search`. A customer with at least one active lease shows those unit numbers;
a customer whose leases are all moved-out shows "Former"; a customer who never leased
(lead only) gets no card.

## State and idempotency
`state.json` holds the cursor and the `customerId → {quo_id, sig}` map. `sig` is a
hash of the card's name+phone, used to skip no-op PATCHes. The GitHub Action commits
`state.json` back to the repo after each run so the cursor and map persist for free.

## Cost
GitHub Actions free tier (~1 min/run, 3×/day). Cubby API included. Quo contact
create/update included (no message credits). No Zapier subscription. ≈ $0/month.

## Known assumptions to validate
- Cubby search pagination isn't documented in the field schema; the code fetches one
  page. Verify the baseline count against the known tenant count; add paging if capped.
- Quo auth scheme: raw key vs. `Bearer ` (set `QUO_AUTH_SCHEME` if needed).
