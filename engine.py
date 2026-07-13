#!/usr/bin/env python3
"""Cubby -> Quo sync ENGINE (externalId-anchored, full-payload writes).

Core model:
  * Each card is keyed on the Cubby customerId, stored in Quo as `externalId`.
  * upsert(card): GET Quo by externalId -> PATCH if found, else POST.
  * EVERY write sends the COMPLETE card (defaultFields + customFields + externalId).
    Quo's PATCH replaces omitted arrays, so a partial write wipes data. Never partial.
  * No per-change logic: we recompute the whole card from current leases and push it.

Side-effect free until a write is called with commit=True. Reuses cubby_quo_sync.py
for Cubby fetches, config, and the name/phone helpers.
"""
import copy
import sys
import requests

import cubby_quo_sync as cq

QUO_BASE = cq.QUO_API_BASE
SOURCE_TAG = "cubby-sync"                       # never a reserved word (csv*/openphone*/...)
FIELDS = ("ID", "Facility", "MoveType", "MoveDate")


# --------------------------------------------------------------------------- #
# Quo client
# --------------------------------------------------------------------------- #
def _h():
    return cq.quo_headers()


def quo_get_by_external_id(customer_id):
    """The pivot lookup: find our card by the immutable Cubby id."""
    r = requests.get(QUO_BASE + "/contacts", headers=_h(),
                     params={"maxResults": 50, "externalIds": [customer_id]}, timeout=30)
    r.raise_for_status()
    data = r.json().get("data", [])
    return data[0] if data else None


def resolve_field_keys():
    """Map our 4 custom fields -> this workspace's field keys, read from a live card."""
    r = requests.get(QUO_BASE + "/contacts", headers=_h(), params={"maxResults": 50}, timeout=30)
    r.raise_for_status()
    for c in r.json().get("data", []):
        keys = {}
        for cf in (c.get("customFields") or []):
            n = (cf.get("name") or "").lower()
            if n == "id": keys["ID"] = cf["key"]
            elif n == "move type": keys["MoveType"] = cf["key"]
            elif n == "move date": keys["MoveDate"] = cf["key"]
            elif "facility" in n: keys["Facility"] = cf["key"]
        if all(k in keys for k in FIELDS):
            return keys
    raise RuntimeError("Could not resolve custom-field keys from any existing card.")


def resolve_facility_names():
    names = {}
    for fid in cq.CUBBY_FACILITY_IDS:
        data = cq.cubby_post("/facilities/search", {"where": {"facilityId": fid}})
        facs = data.get("facilities", []) if isinstance(data, dict) else []
        names[fid] = facs[0].get("name") if facs and facs[0].get("name") else fid
    return names


# --------------------------------------------------------------------------- #
# Card builder: current Cubby data -> desired card (facility-aggregated per customer)
# --------------------------------------------------------------------------- #
def build_card(customer, leases, units_by_id, facility_names):
    cid = customer["customerId"]
    name = cq.customer_name(customer)
    contact = customer.get("contact") or {}
    phone = cq.norm_phone(contact.get("phone")) or ""
    email = contact.get("email") or ""

    active = [l for l in leases if not l.get("moveOutDate")]      # trigger: null moveOutDate = active
    if active:
        active.sort(key=lambda l: (l.get("moveInDate") or "", units_by_id.get(l.get("unitId"), "")))
        units, seen = [], set()
        for l in active:
            u = units_by_id.get(l.get("unitId"), l.get("unitId"))
            if u and u not in seen:
                seen.add(u); units.append(u)
        unit_str = ", ".join(units)
        move_type = "Move In"
        move_date = min((l.get("moveInDate") for l in active if l.get("moveInDate")), default="")
        fac_ids = {l.get("facilityId") for l in active}
    else:
        unit_str = "Former"
        move_type = "Move Out"
        move_date = max((l.get("moveOutDate") for l in leases if l.get("moveOutDate")), default="")
        fac_ids = {l.get("facilityId") for l in leases}

    facility = ", ".join(sorted(facility_names.get(f, f) for f in fac_ids if f))
    return {
        "customerId": cid,
        "firstName": f"{name} {unit_str}".strip(),
        "lastName": " ",
        "phone": phone,
        "email": email,
        "moveType": move_type,
        "moveDate": move_date or "",
        "facility": facility,
    }


# --------------------------------------------------------------------------- #
# Desired card -> Quo request body (ALWAYS the full payload)
# --------------------------------------------------------------------------- #
def to_quo_body(card, field_keys, set_source=True):
    body = {
        "defaultFields": {
            "firstName": card["firstName"],
            "lastName": card["lastName"] or " ",
            "phoneNumbers": [{"name": "primary", "value": card["phone"]}] if card["phone"] else [],
            "emails": [{"name": "primary", "value": card["email"]}] if card["email"] else [],
        },
        "customFields": [
            {"key": field_keys["ID"], "value": card["customerId"]},
            {"key": field_keys["Facility"], "value": card["facility"]},
            {"key": field_keys["MoveType"], "value": card["moveType"]},
            {"key": field_keys["MoveDate"], "value": card["moveDate"]},
        ],
        "externalId": card["customerId"],
    }
    if set_source:
        body["source"] = SOURCE_TAG
    return body


# --------------------------------------------------------------------------- #
# No-op detection: which fields differ between the live card and the desired card
# --------------------------------------------------------------------------- #
def _first(lst):
    return (lst[0].get("value") if lst else "") or ""


def card_diff(existing, card, field_keys):
    df = existing.get("defaultFields") or {}
    diffs = []
    if (df.get("firstName") or "") != card["firstName"]: diffs.append("firstName")
    if _first(df.get("phoneNumbers")) != card["phone"]: diffs.append("phone")
    if _first(df.get("emails")) != card["email"]: diffs.append("email")
    have = {cf.get("key"): (cf.get("value") or "") for cf in (existing.get("customFields") or [])}
    for label, key, val in (("ID", field_keys["ID"], card["customerId"]),
                            ("Facility", field_keys["Facility"], card["facility"]),
                            ("MoveType", field_keys["MoveType"], card["moveType"]),
                            ("MoveDate", field_keys["MoveDate"], card["moveDate"])):
        if (have.get(key) or "") != (val or ""): diffs.append(label)
    return diffs


# --------------------------------------------------------------------------- #
# Upsert: the single write path
# --------------------------------------------------------------------------- #
def upsert(card, field_keys, commit=False):
    existing = quo_get_by_external_id(card["customerId"])
    body = to_quo_body(card, field_keys, set_source=True)
    if existing:
        diffs = card_diff(existing, card, field_keys)
        if not diffs:
            return {"action": "skip", "id": existing["id"], "diffs": [], "body": body}
        if commit:
            requests.patch(f"{QUO_BASE}/contacts/{existing['id']}", headers=_h(), json=body, timeout=30).raise_for_status()
        return {"action": "patch", "id": existing["id"], "diffs": diffs, "body": body}
    if commit:
        r = requests.post(QUO_BASE + "/contacts", headers=_h(), json=body, timeout=30)
        r.raise_for_status()
        return {"action": "create", "id": (r.json().get("data") or {}).get("id"), "diffs": None, "body": body}
    return {"action": "create", "id": None, "diffs": None, "body": body}


# --------------------------------------------------------------------------- #
# Fetch one customer's inputs from Cubby (all facilities, aggregated by customerId)
# --------------------------------------------------------------------------- #
def fetch_customer(customer_id):
    custs = cq.cubby_customers({"customerId": customer_id})
    if not custs:
        return None, [], {}
    ldata = cq.cubby_leases({"customerId": customer_id}, expansions=["unit"])
    units_by_id = {u["unitId"]: u.get("name") for u in ldata.get("units", [])}
    return custs[0], ldata.get("leases", []), units_by_id


def _redact(body):
    b = copy.deepcopy(body)
    for arr in ("phoneNumbers", "emails"):
        for item in b["defaultFields"].get(arr, []):
            if item.get("value"): item["value"] = "«redacted»"
    return b


# --------------------------------------------------------------------------- #
# Dry demo: engine.py [customerId ...]   (defaults to Robin). Writes NOTHING.
# --------------------------------------------------------------------------- #
def _demo(ids):
    import json
    field_keys = resolve_field_keys()
    facility_names = resolve_facility_names()
    print("resolved field keys :", field_keys)
    print("resolved facilities :", facility_names)
    for cid in ids:
        cust, leases, units = fetch_customer(cid)
        if not cust:
            print(f"\n{cid}: not found in Cubby"); continue
        card = build_card(cust, leases, units, facility_names)
        res = upsert(card, field_keys, commit=False)      # DRY
        print(f"\n=== {cid} ===")
        print(f"  firstName : {card['firstName']!r}")
        print(f"  moveType  : {card['moveType']!r}  moveDate: {card['moveDate']!r}  facility: {card['facility']!r}")
        print(f"  phone     : {'(present)' if card['phone'] else '(none)'}   email: {'(present)' if card['email'] else '(none)'}")
        note = "already in sync — no write" if res["action"] == "skip" else \
               (f"would PATCH (differs: {res['diffs']})" if res["action"] == "patch" else "would POST (new externalId)")
        print(f"  DECISION  : {res['action'].upper()} — {note}")
        print("  full body that would be sent:")
        print("   " + json.dumps(_redact(res["body"]), indent=2).replace("\n", "\n   "))


# --------------------------------------------------------------------------- #
# adopt: bridge existing csv-v2 cards -> API-managed (stamp externalId, full payload)
# --------------------------------------------------------------------------- #
def list_csv_v2_contacts():
    """Every contact still tagged source=csv-v2 (not yet adopted), paginated."""
    out, token = [], None
    while True:
        p = {"maxResults": 50, "sources": ["csv-v2"]}
        if token:
            p["pageToken"] = token
        j = requests.get(QUO_BASE + "/contacts", headers=_h(), params=p, timeout=30).json()
        out.extend(j.get("data", []))
        token = j.get("nextPageToken")
        if not token:
            return out


def _cust_from_card(contact, field_keys):
    """Read the Cubby customerId from the card's ID custom field."""
    cfs = contact.get("customFields") or []
    idkey = field_keys["ID"]
    for cf in cfs:
        if cf.get("key") == idkey and str(cf.get("value") or "").startswith("cust_"):
            return cf["value"]
    for cf in cfs:                                   # fallback: any cust_-looking value
        v = cf.get("value")
        if isinstance(v, str) and v.startswith("cust_"):
            return v
    return None


def _verify_card(contact_id, card, cid):
    """After a write, GET the card back and confirm nothing was wiped."""
    o = requests.get(f"{QUO_BASE}/contacts/{contact_id}", headers=_h(), timeout=30).json()
    o = o.get("data", o)
    df = o.get("defaultFields") or {}
    return (bool(df.get("phoneNumbers")) == bool(card["phone"]) and
            bool(df.get("emails")) == bool(card["email"]) and
            len(o.get("customFields") or []) >= 4 and
            o.get("externalId") == cid)


def load_csv_phone_map():
    """phone -> customerId from the export CSVs (unique phones only). Recovers the
    customerId for 'orphan' cards imported without the ID custom field (e.g. Southern)."""
    import csv as _csv
    import glob as _glob
    from collections import defaultdict
    m = defaultdict(set)
    for path in _glob.glob("exports/*.csv"):
        with open(path, encoding="utf-8-sig") as f:
            for row in _csv.DictReader(f):
                ph = (row.get("Phone Number") or "").strip()
                cid = (row.get("Customer ID") or "").strip()
                if ph and cid.startswith("cust_"):
                    m[ph].add(cid)
    return {ph: next(iter(v)) for ph, v in m.items() if len(v) == 1}


def cmd_adopt(commit=False, limit=None):
    field_keys = resolve_field_keys()
    phone_map = load_csv_phone_map()
    contacts = list_csv_v2_contacts()
    print(f"csv-v2 (un-adopted) contacts: {len(contacts)}")

    by_cust, anomalies, src_of = {}, [], {}
    via_id = via_phone = 0
    for c in contacts:
        cid = _cust_from_card(c, field_keys)
        src = "id"
        if not cid:                              # orphan card -> recover customerId by phone
            ph = ((c.get("defaultFields") or {}).get("phoneNumbers") or [{}])[0].get("value", "")
            cid = phone_map.get(ph)
            src = "phone"
        if cid:
            by_cust.setdefault(cid, []).append(c)
            src_of[cid] = src
            via_id += (src == "id")
            via_phone += (src == "phone")
        else:
            anomalies.append(c["id"])
    duplicates = {cid: [c["id"] for c in cs] for cid, cs in by_cust.items() if cid and len(cs) > 1}
    unique = {cid: cs[0] for cid, cs in by_cust.items() if cid and len(cs) == 1}

    print(f"  resolved via ID field        : {via_id}")
    print(f"  resolved via phone (orphans) : {via_phone}")
    print(f"  unique customers to adopt    : {len(unique)}")
    print(f"  duplicates (same cust on >1 card, SKIP): {len(duplicates)}")
    print(f"  anomalies (unresolvable, SKIP)         : {len(anomalies)}")
    for cid, ids in list(duplicates.items())[:15]:
        print(f"     dup {cid}: {ids}")
    if anomalies:
        print(f"     anomaly card ids: {anomalies[:15]}")

    if not commit:
        print("  sample customerIds that would be adopted:", list(unique.keys())[:8])
        print(f"\nDRY RUN — {len(unique)} would be adopted, writing nothing. "
              "Next: `adopt --commit --limit 5` (small batch) -> verify -> `adopt --commit`.")
        return

    facility_names = resolve_facility_names()
    if limit is not None:                        # small batch: cover BOTH paths (orphans + id-resolved)
        orphans = [cid for cid in unique if src_of.get(cid) == "phone"]
        ided = [cid for cid in unique if src_of.get(cid) == "id"]
        k = min(limit // 2, len(orphans))
        chosen = orphans[:k] + ided[:limit - k]
        targets = [(cid, unique[cid]) for cid in chosen]
    else:
        targets = list(unique.items())
    print(f"COMMIT — adopting {len(targets)} card(s), verifying each round-trip...")

    # AUDIT: snapshot every target's current state BEFORE any write (restore record).
    import json as _json
    import datetime as _dt
    before = []
    print("\n  CARDS TO BE MODIFIED (before-state):")
    for cid, contact in targets:
        cur = requests.get(f"{QUO_BASE}/contacts/{contact['id']}", headers=_h(), timeout=30).json()
        cur = cur.get("data", cur)
        before.append({"customerId": cid, "contactId": contact["id"],
                       "resolvedBy": src_of.get(cid), "before": cur})
        cdf = cur.get("defaultFields") or {}
        print(f"    {contact['id']}  {cid}  [{src_of.get(cid)}]  {cdf.get('firstName')!r}"
              f"  (phones={len(cdf.get('phoneNumbers') or [])} emails={len(cdf.get('emails') or [])}"
              f" cf={len(cur.get('customFields') or [])} externalId={cur.get('externalId')!r})")
    audit = f"exports/adopt_audit_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(audit, "w", encoding="utf-8") as f:
        _json.dump(before, f, indent=2, default=str)
    print(f"  -> full before-state written to {audit}  (restore record)\n")

    done = missing = failed = 0
    for cid, contact in targets:
        cust, leases, units = fetch_customer(cid)
        if not cust:
            missing += 1
            print(f"  MISSING in Cubby: {cid} — skipped")
            continue
        card = build_card(cust, leases, units, facility_names)
        body = to_quo_body(card, field_keys, set_source=True)
        r = requests.patch(f"{QUO_BASE}/contacts/{contact['id']}", headers=_h(), json=body, timeout=30)
        if r.status_code >= 400:
            failed += 1
            print(f"  FAIL {cid}: {r.status_code} {r.text[:150]}")
            continue
        if not _verify_card(contact["id"], card, cid):
            failed += 1
            print(f"  VERIFY FAILED {cid} (card {contact['id']}) — fields did not round-trip! STOP and inspect.")
            continue
        done += 1
        print(f"  ok [{src_of.get(cid)}] {cid} -> {card['firstName']!r}")
    print(f"\nadopted+verified: {done}   missing-in-cubby: {missing}   failed: {failed}"
          f"   (dups {len(duplicates)}, anomalies {len(anomalies)} left for manual review)")


def cmd_run(commit=False):
    """Incremental sync: only customers changed in Cubby since the stored cursor."""
    field_keys = resolve_field_keys()
    facility_names = resolve_facility_names()
    state = cq.load_state()
    cursor = state.get("cursor")
    if not cursor:
        print("No cursor in state.json — run `baseline --commit` first to seed it.")
        raise SystemExit(1)
    run_start = cq.utcnow_iso()
    affected = set()
    for fac in cq.CUBBY_FACILITY_IDS:
        for c in cq.cubby_customers({"facilityId": fac, "updatedAfter": cursor}):
            affected.add(c["customerId"])
        for l in cq.cubby_leases({"facilityId": fac, "updatedAfter": cursor}).get("leases", []):
            if l.get("customerId"):
                affected.add(l["customerId"])
    print(f"run: {len(affected)} customer(s) changed since {cursor}")
    created = updated = skipped = missing = 0
    for cid in sorted(affected):
        cust, leases, units = fetch_customer(cid)
        if not cust:
            missing += 1
            continue
        card = build_card(cust, leases, units, facility_names)
        res = upsert(card, field_keys, commit=commit)
        if res["action"] == "skip":
            skipped += 1
        elif res["action"] == "patch":
            updated += 1
            print(f"  {'PATCH' if commit else 'would PATCH'} {cid} -> {card['firstName']!r}  changed={res.get('diffs')}")
        else:
            created += 1
            print(f"  {'POST' if commit else 'would POST'} {cid} -> {card['firstName']!r}  (new tenant)")
    if commit:
        state["cursor"] = run_start
        cq.save_state(state)
        print(f"\nrun --commit: created {created}, updated {updated}, unchanged {skipped}, missing {missing}. cursor -> {run_start}")
    else:
        print(f"\nDRY RUN: would create {created}, update {updated}, skip {skipped} unchanged, {missing} missing. cursor unchanged.")


def cmd_baseline(commit=False):
    """Full reconcile across every customer; also (re)seeds the cursor. Idempotent."""
    field_keys = resolve_field_keys()
    facility_names = resolve_facility_names()
    customers_by_id, leases_by_customer, units_by_id = {}, {}, {}
    for fac in cq.CUBBY_FACILITY_IDS:
        for c in cq.cubby_customers({"facilityId": fac}):
            customers_by_id[c["customerId"]] = c
        ldata = cq.cubby_leases({"facilityId": fac}, expansions=["unit"])
        for u in ldata.get("units", []):
            units_by_id[u["unitId"]] = u.get("name")
        for l in ldata.get("leases", []):
            leases_by_customer.setdefault(l.get("customerId"), []).append(l)
    carded = [cid for cid in customers_by_id if leases_by_customer.get(cid)]  # skip lead-only
    print(f"baseline: {len(carded)} customers with leases across {len(cq.CUBBY_FACILITY_IDS)} facilities")
    run_start = cq.utcnow_iso()
    state = cq.load_state()
    created = updated = skipped = 0
    for cid in carded:
        card = build_card(customers_by_id[cid], leases_by_customer[cid], units_by_id, facility_names)
        res = upsert(card, field_keys, commit=commit)
        if res["action"] == "skip":
            skipped += 1
        elif res["action"] == "patch":
            updated += 1
        else:
            created += 1
    if commit:
        state["cursor"] = run_start
        cq.save_state(state)
        print(f"baseline --commit: created {created}, updated {updated}, unchanged {skipped}. cursor -> {run_start}")
    else:
        print(f"baseline DRY RUN: would create {created}, update {updated}, skip {skipped}. cursor unchanged (would be {run_start}).")


def main():
    import argparse
    p = argparse.ArgumentParser(description="Cubby -> Quo sync engine")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="dry card preview for given customerIds")
    d.add_argument("ids", nargs="*")
    a = sub.add_parser("adopt", help="bridge existing csv-v2 cards (stamp externalId)")
    a.add_argument("--commit", action="store_true", help="actually write (default: dry)")
    a.add_argument("--limit", type=int, default=None, help="only process the first N (small batch)")
    r = sub.add_parser("run", help="incremental sync of customers changed since the cursor")
    r.add_argument("--commit", action="store_true", help="actually write (default: dry)")
    b = sub.add_parser("baseline", help="full reconcile of all customers + seed the cursor")
    b.add_argument("--commit", action="store_true", help="actually write (default: dry)")
    args = p.parse_args()
    if args.cmd == "demo":
        _demo(args.ids or ["cust_XGqZS69GExx"])
    elif args.cmd == "adopt":
        cmd_adopt(commit=args.commit, limit=args.limit)
    elif args.cmd == "run":
        cmd_run(commit=args.commit)
    elif args.cmd == "baseline":
        cmd_baseline(commit=args.commit)


if __name__ == "__main__":
    main()
