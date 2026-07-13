#!/usr/bin/env python3
"""
Cubby -> Quo (OpenPhone) one-way contact sync.

See README.md for setup and docs/ for API + architecture reference.

COMMANDS
  check      Non-destructive preflight: validates env, confirms Cubby auth + PII,
             confirms Quo auth. Writes nothing. Run this first.
  baseline   One-time. Builds every card from scratch. Always writes a preview
             CSV. Only creates cards in Quo with --commit.
  run        Incremental. Touches only customers changed since the last run.
             Only writes to Quo with --commit.

CARD RULE (the four triggers are all one code path)
  active 1 unit   -> "Kelly Soverns - 43"
  active 2 units  -> "Kelly Soverns - 43, 12"   (chronological rental order)
  no active lease -> "Robert Sandoval - Former"

DEBUG NOTE: lines tagged "# [debug]" are diagnostics for the first runs. They can
be stripped once the sync is stable.
"""

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

# Load a local .env if present (handy for Claude Code / desktop use). Optional.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
CUBBY_API_BASE = "https://api.cubbystorage.com/v1"
QUO_API_BASE = "https://api.openphone.com/v1"

CUBBY_API_KEY = os.environ.get("CUBBY_API_KEY", "")
QUO_API_KEY = os.environ.get("QUO_API_KEY", "")

CUBBY_FACILITY_IDS = [
    f.strip() for f in os.environ.get("CUBBY_FACILITY_IDS", "").split(",") if f.strip()
]

# Quo default fields are firstName / lastName / phoneNumbers. Convention: the whole
# "Name - Unit" string in firstName, lastName blank. Change here to split it.
QUO_NAME_FIELD = os.environ.get("QUO_NAME_FIELD", "firstName")

# OpenPhone/Quo passes the raw API key in Authorization (no "Bearer "). If a Quo
# call 401s, set QUO_AUTH_SCHEME="Bearer " in your .env.
QUO_AUTH_SCHEME = os.environ.get("QUO_AUTH_SCHEME", "")

STATE_PATH = Path(os.environ.get("STATE_PATH", "state.json"))
PREVIEW_PATH = Path(os.environ.get("PREVIEW_PATH", "baseline_preview.csv"))
DEBUG = os.environ.get("SYNC_DEBUG", "1") == "1"


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
def log(*a):
    print(*a, file=sys.stderr)


def dbg(*a):
    if DEBUG:
        print("DEBUG:", *a, file=sys.stderr)  # [debug]


# --------------------------------------------------------------------------- #
# HTTP with light retry/backoff (Quo allows 10 req/s)
# --------------------------------------------------------------------------- #
def _request(method, url, headers, json_body=None, tries=5):
    last = None
    for i in range(tries):
        resp = requests.request(method, url, headers=headers, json=json_body, timeout=30)
        last = resp
        if resp.status_code == 429:
            wait = 2 ** i
            dbg(f"429 from {url} -> sleeping {wait}s")  # [debug]
            time.sleep(wait)
            continue
        if resp.status_code >= 500:
            wait = 2 ** i
            dbg(f"{resp.status_code} from {url} -> retry in {wait}s")  # [debug]
            time.sleep(wait)
            continue
        if resp.status_code >= 400:
            log(f"ERROR {resp.status_code} {method} {url}: {resp.text[:500]}")
            resp.raise_for_status()
        return resp
    last.raise_for_status()


# --------------------------------------------------------------------------- #
# Cubby
# --------------------------------------------------------------------------- #
def cubby_post(path, body):
    headers = {"Authorization": f"Bearer {CUBBY_API_KEY}", "Content-Type": "application/json"}
    r = _request("POST", CUBBY_API_BASE + path, headers, body)
    return r.json().get("data", {})


def cubby_customers(where):
    return cubby_post("/customers/search", {"where": where}).get("customers", [])


def cubby_leases(where, expansions=None):
    body = {"where": where}
    if expansions:
        body["expansions"] = expansions
    return cubby_post("/leases/search", body)


# --------------------------------------------------------------------------- #
# Quo (OpenPhone)
# --------------------------------------------------------------------------- #
def quo_headers():
    return {"Authorization": f"{QUO_AUTH_SCHEME}{QUO_API_KEY}", "Content-Type": "application/json"}


def quo_create(formatted_name, phone):
    body = {"defaultFields": {QUO_NAME_FIELD: formatted_name,
                              "phoneNumbers": [{"name": "primary", "value": phone}]}}
    r = _request("POST", QUO_API_BASE + "/contacts", quo_headers(), body)
    j = r.json()
    return (j.get("data") or j).get("id")


def quo_patch(contact_id, formatted_name, phone):
    body = {"defaultFields": {QUO_NAME_FIELD: formatted_name,
                              "phoneNumbers": [{"name": "primary", "value": phone}]}}
    _request("PATCH", f"{QUO_API_BASE}/contacts/{contact_id}", quo_headers(), body)


# --------------------------------------------------------------------------- #
# Card logic
# --------------------------------------------------------------------------- #
def today_str():
    return dt.date.today().isoformat()


def utcnow_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def lease_is_active(lease, today):
    mo = lease.get("moveOutDate")
    if not mo:
        return True
    return mo >= today  # yyyy-MM-dd compares correctly as a string


def active_unit_names(leases, units_by_id, today):
    active = [l for l in leases if lease_is_active(l, today)]
    active.sort(key=lambda l: (l.get("moveInDate") or "", units_by_id.get(l.get("unitId"), "")))
    out, seen = [], set()
    for l in active:
        name = units_by_id.get(l.get("unitId"), l.get("unitId"))
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def customer_name(c):
    name = (c.get("name") or "").strip()
    if name:
        return name
    return f"{c.get('firstName', '')} {c.get('lastName', '')}".strip()


def norm_phone(p):
    if not p:
        return None
    p = str(p).strip()
    if p.startswith("+"):
        return p
    digits = re.sub(r"\D", "", p)
    if len(digits) == 10:
        digits = "1" + digits
    elif len(digits) == 11 and not digits.startswith("1"):
        digits = "1" + digits
    return "+" + digits if digits else None


def build_formatted_name(name, active_units):
    if active_units:
        return f"{name} - {', '.join(active_units)}"
    return f"{name} - Former"


def card_signature(formatted_name, phone):
    return f"{formatted_name}||{phone}"


def desired_card(customer, leases, units_by_id):
    name = customer_name(customer)
    phone = norm_phone((customer.get("contact") or {}).get("phone"))
    units = active_unit_names(leases, units_by_id, today_str())
    return {
        "customerId": customer["customerId"],
        "formatted_name": build_formatted_name(name, units),
        "phone": phone,
        "units": ", ".join(units),
        "status": "active" if units else "former",
    }


# --------------------------------------------------------------------------- #
# State (cursor + id map persisted as JSON)
#   id_map: { cubby_customer_id: {"quo_id": ..., "sig": ...} }
# --------------------------------------------------------------------------- #
def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"cursor": None, "id_map": {}}


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2))
    dbg(f"state saved -> {STATE_PATH} ({len(state['id_map'])} cards, cursor={state['cursor']})")  # [debug]


# --------------------------------------------------------------------------- #
# check (preflight, writes nothing)
# --------------------------------------------------------------------------- #
def cmd_check():
    ok = True
    print("Environment:")
    print(f"  CUBBY_API_KEY      : {'set' if CUBBY_API_KEY else 'MISSING'}")
    print(f"  QUO_API_KEY        : {'set' if QUO_API_KEY else 'MISSING'}")
    print(f"  CUBBY_FACILITY_IDS : {CUBBY_FACILITY_IDS or 'MISSING'}")

    if CUBBY_API_KEY and CUBBY_FACILITY_IDS:
        print("Cubby:")
        try:
            custs = cubby_customers({"facilityId": CUBBY_FACILITY_IDS[0]})
            sample = custs[0] if custs else {}
            has_name = bool(customer_name(sample)) if sample else False
            has_phone = bool((sample.get("contact") or {}).get("phone")) if sample else False
            print(f"  auth OK — {len(custs)} customers at {CUBBY_FACILITY_IDS[0]}")
            print(f"  PII: name={'yes' if has_name else 'NO'}  phone={'yes' if has_phone else 'NO'}")
            if custs and not has_phone:
                ok = False
                print("  -> phones blank: this key lacks PII access. Fix before baseline.")
            if len(custs) in (50, 100, 200, 250, 500, 1000):
                print(f"  -> count is a round number ({len(custs)}); may be paginated — verify.")
        except Exception as e:
            ok = False
            print(f"  FAILED: {e}")
    else:
        ok = False
        print("Cubby: skipped (missing key or facility ids)")

    if QUO_API_KEY:
        print("Quo:")
        try:
            r = requests.get(QUO_API_BASE + "/contacts/quo_preflight_nonexistent",
                             headers=quo_headers(), timeout=30)
            if r.status_code == 404:
                print("  auth OK (404 on bogus id, as expected)")
            elif r.status_code in (401, 403):
                ok = False
                print(f"  auth FAILED ({r.status_code}). Try QUO_AUTH_SCHEME='Bearer ' in .env.")
            else:
                print(f"  reachable, status {r.status_code}")
        except Exception as e:
            ok = False
            print(f"  FAILED: {e}")
    else:
        ok = False
        print("Quo: skipped (missing key)")

    print("\nCHECK:", "PASS — safe to run baseline (dry run first)" if ok else "ISSUES FOUND — fix above")
    sys.exit(0 if ok else 1)


# --------------------------------------------------------------------------- #
# baseline
# --------------------------------------------------------------------------- #
def cmd_baseline(commit):
    state = load_state()
    rows = []
    for fac in CUBBY_FACILITY_IDS:
        dbg(f"baseline: facility {fac}")  # [debug]
        customers = cubby_customers({"facilityId": fac})
        ldata = cubby_leases({"facilityId": fac}, expansions=["unit"])
        leases = ldata.get("leases", [])
        units_by_id = {u["unitId"]: u.get("name") for u in ldata.get("units", [])}
        leases_by_customer = {}
        for l in leases:
            leases_by_customer.setdefault(l.get("customerId"), []).append(l)
        dbg(f"  {len(customers)} customers, {len(leases)} leases, {len(units_by_id)} units")  # [debug]
        for c in customers:
            cls = leases_by_customer.get(c["customerId"], [])
            if not cls:
                continue  # lead-only customer, never leased -> no card
            rows.append(desired_card(c, cls, units_by_id))

    with PREVIEW_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["customerId", "formatted_name", "phone", "units", "status"])
        w.writeheader()
        for r in rows:
            w.writerow(r)

    n_phone = sum(1 for r in rows if r["phone"])
    log(f"baseline: {len(rows)} cards -> {PREVIEW_PATH}  ({n_phone} with phone, {len(rows) - n_phone} without)")
    if len(rows) and n_phone == 0:
        log("WARNING: no phones present. Key likely lacks PII access — fix before --commit.")

    if not commit:
        log("DRY RUN. Review the CSV, then re-run with --commit to create cards in Quo.")
        return

    created = 0
    for r in rows:
        if not r["phone"]:
            dbg(f"  skip (no phone): {r['customerId']}")  # [debug]
            continue
        quo_id = quo_create(r["formatted_name"], r["phone"])
        state["id_map"][r["customerId"]] = {"quo_id": quo_id, "sig": card_signature(r["formatted_name"], r["phone"])}
        created += 1
    state["cursor"] = utcnow_iso()
    save_state(state)
    log(f"baseline --commit: created {created} cards in Quo. Cursor set to {state['cursor']}.")


# --------------------------------------------------------------------------- #
# run (incremental)
# --------------------------------------------------------------------------- #
def fetch_customer_inputs(customer_id):
    custs = cubby_customers({"customerId": customer_id})
    if not custs:
        return None
    customer = custs[0]
    ldata = cubby_leases({"customerId": customer_id}, expansions=["unit"])
    leases = ldata.get("leases", [])
    units_by_id = {u["unitId"]: u.get("name") for u in ldata.get("units", [])}
    return desired_card(customer, leases, units_by_id)


def cmd_run(commit):
    state = load_state()
    cursor = state.get("cursor")
    if not cursor:
        log("No cursor in state.json. Run `baseline --commit` first.")
        sys.exit(1)

    run_start = utcnow_iso()
    affected = set()
    for fac in CUBBY_FACILITY_IDS:
        for cust in cubby_customers({"facilityId": fac, "updatedAfter": cursor}):
            affected.add(cust["customerId"])
        for lease in cubby_leases({"facilityId": fac, "updatedAfter": cursor}).get("leases", []):
            if lease.get("customerId"):
                affected.add(lease["customerId"])

    log(f"run: {len(affected)} customers changed since {cursor}")
    created = updated = skipped = 0
    for cid in sorted(affected):
        card = fetch_customer_inputs(cid)
        if card is None or not card["phone"]:
            dbg(f"  skip {cid} (missing / no phone)")  # [debug]
            skipped += 1
            continue
        sig = card_signature(card["formatted_name"], card["phone"])
        entry = state["id_map"].get(cid)
        if entry and entry.get("quo_id"):
            if entry.get("sig") == sig:
                dbg(f"  no-op {cid} ({card['formatted_name']})")  # [debug]
                skipped += 1
                continue
            log(f"  UPDATE {cid}: {card['formatted_name']}")
            if commit:
                quo_patch(entry["quo_id"], card["formatted_name"], card["phone"])
                entry["sig"] = sig
            updated += 1
        else:
            log(f"  CREATE {cid}: {card['formatted_name']}")
            if commit:
                quo_id = quo_create(card["formatted_name"], card["phone"])
                state["id_map"][cid] = {"quo_id": quo_id, "sig": sig}
            created += 1

    if commit:
        state["cursor"] = run_start
        save_state(state)
        log(f"run --commit: {created} created, {updated} updated, {skipped} skipped. Cursor -> {run_start}.")
    else:
        log(f"DRY RUN: would create {created}, update {updated}, skip {skipped}. Cursor unchanged.")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main():
    p = argparse.ArgumentParser(description="Cubby -> Quo contact sync")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="preflight: validate env + auth + PII (writes nothing)")
    b = sub.add_parser("baseline", help="one-time build of all cards (dry run unless --commit)")
    b.add_argument("--commit", action="store_true", help="actually create cards in Quo")
    r = sub.add_parser("run", help="incremental sync (dry run unless --commit)")
    r.add_argument("--commit", action="store_true", help="actually write to Quo")
    args = p.parse_args()

    if args.cmd == "check":
        cmd_check()
        return

    if not CUBBY_API_KEY or not QUO_API_KEY:
        log("Set CUBBY_API_KEY and QUO_API_KEY (see .env.example).")
        sys.exit(1)
    if not CUBBY_FACILITY_IDS:
        log("Set CUBBY_FACILITY_IDS (comma-separated).")
        sys.exit(1)

    if args.cmd == "baseline":
        cmd_baseline(args.commit)
    elif args.cmd == "run":
        cmd_run(args.commit)


if __name__ == "__main__":
    main()
