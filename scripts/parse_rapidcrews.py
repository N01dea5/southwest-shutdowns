#!/usr/bin/env python3
"""Convert Rapid Crews roster XLSX exports into the dashboard's per-company JSON.

Workflow
--------
1. Export a roster from Rapid Crews (the "Roster Cut" export). It downloads as
   `<roster_id> (RosterCut) <YYYY-MM-DD>_<HH-MM-SS>.xlsx`.
2. Drop the XLSX file into `data/raw/`.
3. Add a line to ROSTER_MAP below mapping that roster_id to a client + project
   identity (the XLSX itself only knows the labour-hire side, not which
   client's plant the shutdown is for).
4. Run `python3 scripts/parse_rapidcrews.py`.
5. Commit the regenerated `data/<company>.json` files.

Notes on what the Rapid Crews roster does NOT contain
-----------------------------------------------------
- **Required headcount per role** (the original *target* on the request).
  This script writes `required_by_role = filled_by_role` as a placeholder so
  the dashboard renders. Override it with real targets in `data/targets/<id>.json`
  (one optional file per shutdown id) when you have them; the loader merges
  these on top of the parser output.
- **Client name** (Covalent / Tronox / CSBP). The "Company" column in Rapid
  Crews is the *labour-hire firm* (SRG South West, MMFS, Western Workforce,
  etc.). The mapping from roster_id to client is supplied below.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

import openpyxl


# Map each Rapid Crews roster_id to (company_key, client_display_name, project_label, site).
# The roster_id is the leading numeric token of the XLSX filename.
ROSTER_MAP: dict[str, tuple[str, str, str, str]] = {
    "1353": ("tronox",   "Tronox",   "Major Shutdown May 2026", "Kwinana"),
    "1359": ("covalent", "Covalent", "Mt Holland April 2026",   "Mt Holland"),
    "1375": ("csbp",     "CSBP",     "NAAN2 June 2026",         "Kwinana"),
}

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw"
DATA_DIR = REPO_ROOT / "data"
TARGETS_DIR = DATA_DIR / "targets"   # optional override: targets/<shutdown_id>.json

REQUIRED_COLS = ["Company", "Name", "Surname", "Position", "Position On Project",
                 "Start Date", "End Date", "Confirmed", "Crew Type", "Mobilised"]


# --------------------------------------------------------------------------- helpers

def to_iso(d) -> str | None:
    if isinstance(d, dt.datetime):
        return d.date().isoformat()
    if isinstance(d, dt.date):
        return d.isoformat()
    return None


def truthy(v) -> bool:
    return str(v or "").strip().upper() in {"YES", "Y", "TRUE", "1"}


def parse_roster(xlsx_path: pathlib.Path) -> list[dict]:
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active
    headers = [c.value for c in ws[1]]
    missing = [c for c in REQUIRED_COLS if c not in headers]
    if missing:
        raise ValueError(f"{xlsx_path.name}: missing columns {missing}")
    idx = {h: i for i, h in enumerate(headers)}

    rows: list[dict] = []
    for raw in ws.iter_rows(min_row=2, values_only=True):
        if not any(raw):
            continue
        name = f"{raw[idx['Name']] or ''} {raw[idx['Surname']] or ''}".strip()
        if not name:
            continue
        role = raw[idx["Position On Project"]] or raw[idx["Position"]] or "Unknown"
        rows.append({
            "labour_hire": (raw[idx["Company"]] or "").strip(),
            "name":        name,
            "role":        str(role).strip(),
            "start":       to_iso(raw[idx["Start Date"]]),
            "end":         to_iso(raw[idx["End Date"]]),
            "confirmed":   truthy(raw[idx["Confirmed"]]),
            "crew_type":   (raw[idx["Crew Type"]] or "Unknown").strip(),
            "mobilised":   truthy(raw[idx["Mobilised"]]),
        })
    return rows


def merge_targets(shutdown_id: str, filled_by_role: dict[str, int]) -> dict[str, int]:
    """Optional override: data/targets/<shutdown_id>.json with {role: target}."""
    path = TARGETS_DIR / f"{shutdown_id}.json"
    if not path.exists():
        return dict(filled_by_role)
    overrides: dict = json.loads(path.read_text())
    return {role: int(overrides.get(role, filled_by_role.get(role, 0)))
            for role in set(filled_by_role) | set(overrides)}


def build_shutdown(roster_id: str, xlsx: pathlib.Path) -> tuple[str, str, dict]:
    company_key, client_name, project_label, site = ROSTER_MAP[roster_id]
    rows = parse_roster(xlsx)
    confirmed = [r for r in rows if r["confirmed"]]
    if not confirmed:
        raise ValueError(f"{xlsx.name}: no confirmed rows")

    starts = [r["start"] for r in confirmed if r["start"]]
    ends   = [r["end"]   for r in confirmed if r["end"]]
    sd, ed = min(starts), max(ends)

    filled_by_role: dict[str, int] = {}
    crew_split:     dict[str, int] = {}
    mobilised_by_role: dict[str, int] = {}
    labour_hire_split: dict[str, int] = {}

    for r in confirmed:
        filled_by_role[r["role"]] = filled_by_role.get(r["role"], 0) + 1
        crew_split[r["crew_type"]] = crew_split.get(r["crew_type"], 0) + 1
        labour_hire_split[r["labour_hire"]] = labour_hire_split.get(r["labour_hire"], 0) + 1
        if r["mobilised"]:
            mobilised_by_role[r["role"]] = mobilised_by_role.get(r["role"], 0) + 1

    shutdown_id = f"{company_key}-{sd[:7]}"   # e.g. covalent-2026-04
    required = merge_targets(shutdown_id, filled_by_role)
    today      = dt.date.today()
    start_day  = dt.date.fromisoformat(sd)
    end_day    = dt.date.fromisoformat(ed)
    # Three-way status. A shutdown is only "completed" once every scheduled
    # worker has demobilised (end_date strictly before today). Between start
    # and end it's "in_progress" — the roster is on site but the job isn't
    # done. Before start it's still "booked".
    if end_day < today:
        status = "completed"
    elif start_day <= today:
        status = "in_progress"
    else:
        status = "booked"

    shutdown = {
        "id": shutdown_id,
        "name": project_label,
        "site": site,
        "start_date": sd,
        "end_date": ed,
        "status": status,
        "required_by_role": required,
        "filled_by_role": filled_by_role,
        "crew_split": crew_split,
        "mobilised_by_role": mobilised_by_role,
        "labour_hire_split": labour_hire_split,
        "roster": [{"name": r["name"], "role": r["role"]} for r in confirmed],
        "_source": {
            "rapid_crews_roster_id": roster_id,
            "rapid_crews_export_file": xlsx.name,
            "required_target_source": (
                "REAL_TARGET" if (TARGETS_DIR / f"{shutdown_id}.json").exists()
                else "PLACEHOLDER_FROM_ROSTER"
            ),
        },
    }
    return company_key, client_name, shutdown


# --------------------------------------------------------------------------- main

def main() -> int:
    if not RAW_DIR.exists():
        print(f"No raw dir at {RAW_DIR}", file=sys.stderr)
        return 1

    by_company: dict[str, dict] = {}
    seen_files = 0

    for xlsx in sorted(RAW_DIR.glob("*.xlsx")):
        roster_id = xlsx.name.split(" ", 1)[0]
        if roster_id not in ROSTER_MAP:
            print(f"  skip unmapped roster {roster_id}: {xlsx.name}")
            continue
        seen_files += 1
        company_key, client_name, shutdown = build_shutdown(roster_id, xlsx)
        by_company.setdefault(company_key, {"company": client_name, "shutdowns": []})
        by_company[company_key]["shutdowns"].append(shutdown)
        print(f"  {roster_id:>5}  {client_name:<10} {shutdown['id']:<22} "
              f"roster={len(shutdown['roster']):>3}  "
              f"{shutdown['start_date']} → {shutdown['end_date']}")

    if not by_company:
        print("No mapped roster files processed.", file=sys.stderr)
        return 1

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    for company_key, payload in by_company.items():
        payload["generated_at"] = now
        payload["shutdowns"].sort(key=lambda s: s["start_date"])
        out = DATA_DIR / f"{company_key}.json"
        out.write_text(json.dumps(payload, indent=2))
        total = sum(len(s["roster"]) for s in payload["shutdowns"])
        print(f"Wrote {out.relative_to(REPO_ROOT)}: "
              f"{len(payload['shutdowns'])} shutdown(s), {total} confirmed heads")

    # Companies referenced by the dashboard but not in this batch get an empty
    # payload so the page still loads them without 404s.
    referenced = {"covalent", "tronox", "csbp"}
    for company_key in referenced - by_company.keys():
        path = DATA_DIR / f"{company_key}.json"
        if not path.exists():
            payload = {"company": company_key.title(), "generated_at": now, "shutdowns": []}
            path.write_text(json.dumps(payload, indent=2))
            print(f"Wrote {path.relative_to(REPO_ROOT)}: empty (no roster supplied)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
