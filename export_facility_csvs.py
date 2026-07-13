#!/usr/bin/env python3
"""Export one CSV per facility for contact import.

Columns: Customer ID, Name, Last Name, Phone Number, Email, Facility Name, Move Type, Move Date
  Customer ID = Cubby customerId (cust_...), the stable key for each record.
  Name = the custom name = "<customer name> <units>"  e.g. "Kelly Soverns 43, 12"
         (units joined in chronological rental order; "<name> Former" if moved out).
         The whole formatted name lives here; it is left untouched.
  Last Name = a single blank space " " on every row. The import target requires a
         last-name field, but we want the full name to stay in Name. A literal space
         (NOT empty, NOT a dash/bar) satisfies the required field while displaying blank.

Move Type / Move Date — the trigger is the lease's top-level moveOutDate:
  * moveOutDate null/empty  -> active  -> "Move In",  Move Date = earliest moveInDate
  * moveOutDate is set      -> former  -> "Move Out", Move Date = latest moveOutDate
  (No future-dated move-outs exist in the data, so this matches the active/former split.)

Scope: customers with lease history (active + former). Lead-only customers excluded.

Reuses the Cubby fetch helpers from cubby_quo_sync.py. Reads .env. Facilities come
from CUBBY_FACILITY_IDS so this stays in sync with config. Writes exports/<facility>_contacts.csv.
Prints no secrets.
"""
import csv
import re
from pathlib import Path

import cubby_quo_sync as s

OUT_DIR = Path("exports")
OUT_DIR.mkdir(exist_ok=True)
HEADERS = ["Customer ID", "Name", "Last Name", "Phone Number", "Email",
           "Facility Name", "Move Type", "Move Date"]
BLANK_LAST_NAME = " "  # single literal space — required field, displays blank


def slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def facility_name(fid):
    data = s.cubby_post("/facilities/search", {"where": {"facilityId": fid}})
    facs = data.get("facilities", []) if isinstance(data, dict) else []
    return (facs[0].get("name") if facs and facs[0].get("name") else fid)


def build_row(c, cls, units_by_id, fac_name):
    name = s.customer_name(c)
    contact = c.get("contact") or {}
    phone = s.norm_phone(contact.get("phone")) or ""
    email = contact.get("email") or ""

    # Trigger: a lease is active while its moveOutDate is null/empty.
    active = [l for l in cls if not l.get("moveOutDate")]
    if active:
        active.sort(key=lambda l: (l.get("moveInDate") or "",
                                   units_by_id.get(l.get("unitId"), "")))
        units, seen = [], set()
        for l in active:
            nm = units_by_id.get(l.get("unitId"), l.get("unitId"))
            if nm and nm not in seen:
                seen.add(nm)
                units.append(nm)
        unit_str = ", ".join(units)
        move_type = "Move In"
        ins = [l.get("moveInDate") for l in active if l.get("moveInDate")]
        move_date = min(ins) if ins else ""
    else:
        unit_str = "Former"
        move_type = "Move Out"
        outs = [l.get("moveOutDate") for l in cls if l.get("moveOutDate")]
        move_date = max(outs) if outs else ""

    row = {
        "Customer ID": c["customerId"],
        "Name": f"{name} {unit_str}".strip(),
        "Last Name": BLANK_LAST_NAME,
        "Phone Number": phone,
        "Email": email,
        "Facility Name": fac_name,
        "Move Type": move_type,
        "Move Date": move_date,
    }
    return row, name


def main():
    fac_ids = s.CUBBY_FACILITY_IDS
    if not fac_ids:
        raise SystemExit("CUBBY_FACILITY_IDS not set in .env")

    grand = 0
    for fid in fac_ids:
        fac_name = facility_name(fid)
        customers = s.cubby_customers({"facilityId": fid})
        ldata = s.cubby_leases({"facilityId": fid}, expansions=["unit"])
        units_by_id = {u["unitId"]: u.get("name") for u in ldata.get("units", [])}
        leases_by_customer = {}
        for l in ldata.get("leases", []):
            leases_by_customer.setdefault(l.get("customerId"), []).append(l)

        built, move_in, move_out, no_phone, no_email = [], 0, 0, 0, 0
        for c in customers:
            cls = leases_by_customer.get(c["customerId"], [])
            if not cls:
                continue  # lead-only, never leased -> excluded
            row, sort_name = build_row(c, cls, units_by_id, fac_name)
            built.append((sort_name.lower(), row))
            move_in += row["Move Type"] == "Move In"
            move_out += row["Move Type"] == "Move Out"
            no_phone += not row["Phone Number"]
            no_email += not row["Email"]

        built.sort(key=lambda t: t[0])  # by underlying customer name
        out = OUT_DIR / f"{slug(fac_name)}_contacts.csv"
        with out.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=HEADERS)
            w.writeheader()
            w.writerows(row for _, row in built)
        grand += len(built)
        print(f"{fac_name} ({fid}): {len(built)} rows | "
              f"{move_in} Move In, {move_out} Move Out | "
              f"{no_phone} no-phone, {no_email} no-email -> {out}")

    print(f"TOTAL: {grand} rows across {len(fac_ids)} files")


if __name__ == "__main__":
    main()
