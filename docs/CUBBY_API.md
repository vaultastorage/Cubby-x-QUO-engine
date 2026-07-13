# Cubby API — what this project uses

Base URL: `https://api.cubbystorage.com/v1`
Auth: `Authorization: Bearer <CUBBY_API_KEY>`
Style: JSON over HTTP, action-oriented. Search endpoints are `POST /v1/<res>/search`
with a `{ "where": { ... }, "expansions": [ ... ] }` body. Responses wrap payload in
`{ "status": 200, "data": { ... } }`.

IDs are prefixed strings: `cust_…`, `lease_…`, `unit_…`, `fac_…`, `org_…`.
Dates are `yyyy-MM-dd`; timestamps are ISO-8601 UTC ending in `Z`.

## PII gating (critical)
If the key lacks PII access, customer **names, phones, emails** are stripped from
responses. This project needs name + phone, so the key must be PII-enabled. The
`check` command flags this.

## Incremental cursor
`customers/search`, `leases/search`, `leads/search` accept `updatedAfter` (strict,
exclusive UTC timestamp). Pass the previous run's timestamp to get only records
changed since. They also return `createdAt` / `updatedAt` on each object.

## Customers — `POST /v1/customers/search`
Request `where` keys used: `facilityId` (baseline), `customerId` (single lookup),
`updatedAfter` (incremental).
Relevant response fields per customer:
```
customerId        cust_...
updatedAt         ISO-8601 UTC
name              "Bebe Flatley"            <- full display name (PII)
firstName/lastName
contact: { phone: "+15943590263", email, address, ... }   <- phone is E.164 (PII)
leases: [ "lease_..." ]   <- ids only
```

## Leases — `POST /v1/leases/search`
Request `where` keys used: `facilityId` (baseline), `customerId` (recompute one
customer), `updatedAfter` (incremental). Expansions used: `["unit"]` (and `customer`
is available if needed). With the `unit` expansion the response includes a top-level
`units` array.
Relevant response fields per lease:
```
leaseId           lease_...
updatedAt
moveInDate        yyyy-MM-dd        <- used for chronological unit ordering
moveOutDate       yyyy-MM-dd | null <- null/future = active; past = moved out
scheduledMoveOut  { moveOutDate, noticeGivenDate, moveOutReason, ... } | absent
customerId        cust_...
unitId            unit_...
facilityId        fac_...
```
Top-level auxiliary arrays when expanded: `units: [{ unitId, name, ... }]`,
`customers: [{ customerId, name, contact.phone, ... }]`.

## Units — `POST /v1/units/search` (reference)
Not called directly by the sync (we get unit names via the lease `unit` expansion),
but for context a unit has: `unitId`, `name` (e.g. `"M001"`, `"43"`), `facilityId`,
`leaseId` (current occupying lease), `rentability.unrentableReason` (includes
`ACTIVE_LEASE`).

## Roles
The read-only **Search** role covers `customers/search`, `leases/search`,
`units/search` — sufficient for this one-way sync. We never write to Cubby.
