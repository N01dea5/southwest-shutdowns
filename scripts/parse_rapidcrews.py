#!/usr/bin/env python3
"""Convert roster XLSX exports into the dashboard's per-company JSON.

Two roster flavours are supported:

1. **Rapid Crews "RosterCut" export** — filename `<roster_id> (RosterCut) <ts>.xlsx`.
   The canonical export used for the three live shutdowns (Covalent, Tronox, CSBP).
2. **Kleenheat-style spreadsheet** — a looser, older format with first-name-only
   entries, used as a historical seed for retention/carry-over stats. Surnames
   are reconstructed by cross-referencing the first-name + role against the
   other rosters (so e.g. Kleenheat's "Joe, Intermediate Rigger" resolves to
   "Joe DACK" because Covalent has exactly one Joe DACK as Intermediate Rigger).

Workflow
--------
1. Drop the XLSX file into `data/raw/`.
2. Register it in ROSTER_MAP below (key = first filename token before the space).
3. Run `python3 scripts/parse_rapidcrews.py`.
4. Commit the regenerated `data/<company>.json` files.

Data NOT in the Rapid Crews export
----------------------------------
- **Required headcount per role** (the original target on the request). The
  parser writes `required_by_role = filled_by_role` as a placeholder; override
  with real targets at `data/targets/<shutdown_id>.json`, which
  `scripts/sync_source_targets.py` populates from each site's own dashboard repo.
- **Client name** (Covalent / Tronox / CSBP / Kleenheat). The "Company" column
  inside Rapid Crews is the labour-hire firm (SRG South West, MMFS, …), not
  the client whose plant the shutdown is for — that mapping lives in
  ROSTER_MAP.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import sys

import openpyxl


# Map the filename key (first space-delimited token) to
# (company_key, client_display_name, project_label, site).
ROSTER_MAP: dict[str, tuple[str, str, str, str]] = {
    # Rapid Crews RosterCut exports — keyed by numeric roster_id
    "1353":      ("tronox",    "Tronox",    "Major Shutdown May 2026", "Kwinana"),
    "1359":      ("covalent",  "Covalent",  "Mt Holland April 2026",   "Mt Holland"),
    "1375":      ("csbp",      "CSBP",      "NAAN2 June 2026",         "Kwinana"),
    # Kleenheat historical shutdown — keyed by filename prefix
    "Kleenheat": ("kleenheat", "Kleenheat", "Kwinana Major March 2026", "Kwinana"),
}

REPO_ROOT   = pathlib.Path(__file__).resolve().parent.parent
RAW_DIR     = REPO_ROOT / "data" / "raw"
DATA_DIR    = REPO_ROOT / "data"
TARGETS_DIR = DATA_DIR / "targets"     # optional override: targets/<shutdown_id>.json

RAPIDCREWS_COLS = ["Company", "Name", "Surname", "Position", "Position On Project",
                   "Start Date", "End Date", "Confirmed", "Crew Type", "Mobilised"]
KLEENHEAT_COLS  = ["Name", "Trade", "Company", "On Site", "Off Site", "Crew"]


# --------------------------------------------------------------------------- helpers

def to_iso(d) -> str | None:
    if isinstance(d, dt.datetime):
        return d.date().isoformat()
    if isinstance(d, dt.date):
        return d.isoformat()
    if isinstance(d, str):
        # Spreadsheets sometimes store ISO-ish strings with a trailing 'Z'
        m = re.match(r"(\d{4}-\d{2}-\d{2})", d)
        return m.group(1) if m else None
    return None


def truthy(v) -> bool:
    return str(v or "").strip().upper() in {"YES", "Y", "TRUE", "1"}


# --------------------------------------------------------------------------- roster parsers

def _detect_format(headers: list) -> str:
    """Return either 'rapidcrews' or 'kleenheat' based on the header row."""
    hs = {h for h in headers if h}
    if set(RAPIDCREWS_COLS).issubset(hs):
        return "rapidcrews"
    if set(KLEENHEAT_COLS).issubset(hs):
        return "kleenheat"
    return "unknown"


def parse_rapidcrews_roster(xlsx_path: pathlib.Path) -> list[dict]:
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active
    headers = [c.value for c in ws[1]]
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


_LAST_NAME_COL_CANDIDATES = ("Last Dna", "Last Name", "Surname", "Last")


def _surname_from_email(email: str | None, first_name: str) -> str:
    """Best-effort surname extraction from an email local-part. Handles the two
    common patterns we see in the Kleenheat export:
      - `firstname.surname@...`  -> "surname"
      - `surname_first@...`,  `firstsurname@...`,  `firstsurname12@...`
        (strip digits/punct, then strip the first-name prefix/suffix if present).
    Returns "" if nothing reasonable can be derived.
    """
    if not email or "@" not in email:
        return ""
    local = email.split("@", 1)[0].lower()
    local = re.sub(r"[^a-z._]+", "", local)     # drop digits, plus, etc.
    if "." in local:
        parts = [p for p in local.split(".") if p]
        first_low = first_name.lower()
        # drop the piece that matches the first name (anywhere in the split)
        parts = [p for p in parts if p != first_low]
        if parts:
            return parts[-1].capitalize()
    # no dot: try stripping the first-name as a prefix or suffix
    flat = local.replace("_", "")
    first_low = first_name.lower()
    if flat.startswith(first_low) and len(flat) > len(first_low):
        return flat[len(first_low):].capitalize()
    if flat.endswith(first_low) and len(flat) > len(first_low):
        return flat[:-len(first_low)].capitalize()
    return ""


def parse_kleenheat_roster(xlsx_path: pathlib.Path) -> list[dict]:
    """Looser spreadsheet format: first-name-only entries, no Confirmed column.
    Every row present in the spreadsheet is treated as confirmed + mobilised
    (the shutdown has already happened).

    Surnames can come from three places, in priority order:
      1. An explicit surname column (Last Dna / Last Name / Surname / Last)
      2. The email column's local-part (firstname.surname@... style)
      3. Cross-reference against the other rosters — applied later in
         `enrich_kleenheat_names`.
    """
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active
    headers = [c.value for c in ws[1]]
    idx = {h: i for i, h in enumerate(headers)}
    surname_col = next((idx[c] for c in _LAST_NAME_COL_CANDIDATES if c in idx), None)
    email_col   = idx.get("Email")

    rows: list[dict] = []
    for raw in ws.iter_rows(min_row=2, values_only=True):
        if not any(raw):
            continue
        first = (raw[idx["Name"]] or "").strip()
        if not first:
            continue
        surname = ""
        resolution = None
        if surname_col is not None and raw[surname_col]:
            surname = str(raw[surname_col]).strip()
            if surname:
                resolution = "explicit_column"
        if not surname and email_col is not None:
            surname = _surname_from_email(raw[email_col], first)
            if surname:
                resolution = "email_heuristic"
        name = f"{first} {surname}".strip()
        role = str(raw[idx["Trade"]] or "Unknown").strip()
        crew = str(raw[idx["Crew"]] or "Unknown").strip().title()  # "DAY" -> "Day"
        rows.append({
            "labour_hire":       (raw[idx["Company"]] or "").strip(),
            "name":              name,
            "first_name":        first,
            "role":              role,
            "start":             to_iso(raw[idx["On Site"]]),
            "end":               to_iso(raw[idx["Off Site"]]),
            "confirmed":         True,
            "crew_type":         crew,
            "mobilised":         True,
            "_name_resolution":  resolution,      # None until enrichment fills it
        })
    return rows


def parse_roster(xlsx_path: pathlib.Path) -> tuple[str, list[dict]]:
    """Sniff the XLSX, dispatch to the right parser, return (format, rows)."""
    wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True)
    ws = wb.active
    # values_only=True yields plain values, not Cell objects.
    headers = list(next(ws.iter_rows(max_row=1, values_only=True)))
    fmt = _detect_format(headers)
    wb.close()
    if fmt == "rapidcrews":
        return fmt, parse_rapidcrews_roster(xlsx_path)
    if fmt == "kleenheat":
        return fmt, parse_kleenheat_roster(xlsx_path)
    raise ValueError(f"{xlsx_path.name}: unrecognised roster columns {headers}")


# --------------------------------------------------------------------------- surname enrichment

def build_surname_lookup(rosters_by_company: dict[str, list[dict]]) -> dict:
    """From the other companies' rosters, build a map
        (first_name_lower, role_lower) -> set of full names
    so Kleenheat's first-name-only rows can be resolved to a full name when the
    first-name + role combination is unique across SRG's other live rosters.
    """
    lookup: dict[tuple[str, str], set[str]] = {}
    for company, rows in rosters_by_company.items():
        if company == "kleenheat":
            continue
        for r in rows:
            full = r["name"].strip()
            if " " not in full:
                continue
            first = full.split()[0].lower()
            role  = r["role"].strip().lower()
            key   = (first, role)
            lookup.setdefault(key, set()).add(full)
    return lookup


def enrich_kleenheat_names(rows: list[dict], lookup: dict) -> dict[str, int]:
    """Fill in surnames on first-name-only Kleenheat rows by cross-referencing
    first-name + role against the other rosters. Rows that already picked up a
    surname from the spreadsheet's own surname column or the email heuristic
    are left alone but still counted. Resolutions are tagged on each row via
    `_name_resolution` for the data-quality warnings panel."""
    stats = {
        "explicit_column": 0,
        "email_heuristic": 0,
        "xref_exact":      0,
        "xref_ambiguous":  0,
        "unmatched":       0,
    }
    for r in rows:
        if r.get("_name_resolution") == "explicit_column":
            stats["explicit_column"] += 1
            continue
        if r.get("_name_resolution") == "email_heuristic":
            stats["email_heuristic"] += 1
            continue
        first = r["first_name"].lower()
        role  = r["role"].strip().lower()
        candidates = lookup.get((first, role), set())
        if len(candidates) == 1:
            r["name"] = next(iter(candidates))
            r["_name_resolution"] = "xref_exact"
            stats["xref_exact"] += 1
        elif len(candidates) > 1:
            r["_name_resolution"] = f"xref_ambiguous:{len(candidates)}"
            stats["xref_ambiguous"] += 1
        else:
            r["_name_resolution"] = "unmatched"
            stats["unmatched"] += 1
    return stats


# --------------------------------------------------------------------------- targets

def merge_targets(shutdown_id: str, filled_by_role: dict[str, int]) -> dict[str, int]:
    """Optional override: data/targets/<shutdown_id>.json with {role: target}."""
    path = TARGETS_DIR / f"{shutdown_id}.json"
    if not path.exists():
        return dict(filled_by_role)
    overrides: dict = json.loads(path.read_text())
    return {role: int(overrides.get(role, filled_by_role.get(role, 0)))
            for role in set(filled_by_role) | set(overrides)}


# --------------------------------------------------------------------------- build a shutdown payload

def _infer_status(start_day: dt.date, end_day: dt.date, today: dt.date) -> str:
    if end_day < today:
        return "completed"
    if start_day <= today:
        return "in_progress"
    return "booked"


def build_shutdown(file_key: str, xlsx: pathlib.Path, rows: list[dict], fmt: str) -> tuple[str, str, dict]:
    company_key, client_name, project_label, site = ROSTER_MAP[file_key]
    confirmed = [r for r in rows if r["confirmed"]]
    if not confirmed:
        raise ValueError(f"{xlsx.name}: no confirmed rows")

    starts = [r["start"] for r in confirmed if r["start"]]
    ends   = [r["end"]   for r in confirmed if r["end"]]
    sd, ed = min(starts), max(ends)

    filled_by_role:    dict[str, int] = {}
    crew_split:        dict[str, int] = {}
    mobilised_by_role: dict[str, int] = {}
    labour_hire_split: dict[str, int] = {}

    for r in confirmed:
        filled_by_role[r["role"]]       = filled_by_role.get(r["role"], 0) + 1
        crew_split[r["crew_type"]]      = crew_split.get(r["crew_type"], 0) + 1
        labour_hire_split[r["labour_hire"]] = labour_hire_split.get(r["labour_hire"], 0) + 1
        if r["mobilised"]:
            mobilised_by_role[r["role"]] = mobilised_by_role.get(r["role"], 0) + 1

    shutdown_id = f"{company_key}-{sd[:7]}"
    required    = merge_targets(shutdown_id, filled_by_role)
    today       = dt.date.today()
    status      = _infer_status(dt.date.fromisoformat(sd),
                                dt.date.fromisoformat(ed), today)

    shutdown = {
        "id":               shutdown_id,
        "name":             project_label,
        "site":             site,
        "start_date":       sd,
        "end_date":         ed,
        "status":           status,
        "required_by_role": required,
        "filled_by_role":   filled_by_role,
        "crew_split":       crew_split,
        "mobilised_by_role": mobilised_by_role,
        "labour_hire_split": labour_hire_split,
        "roster":           [{"name": r["name"], "role": r["role"]} for r in confirmed],
        "_source": {
            "rapid_crews_roster_id":   file_key,
            "rapid_crews_export_file": xlsx.name,
            "source_format":           fmt,
            "required_target_source": (
                "REAL_TARGET" if (TARGETS_DIR / f"{shutdown_id}.json").exists()
                else "PLACEHOLDER_FROM_ROSTER"
            ),
        },
    }
    # Provenance for enriched rows — surfaced in data-quality warnings
    buckets = {k: 0 for k in ("explicit_column", "email_heuristic", "xref_exact", "unmatched")}
    ambiguous_samples: list[dict] = []
    for r in confirmed:
        nr = r.get("_name_resolution")
        if nr is None:
            continue
        if isinstance(nr, str) and nr.startswith("xref_ambiguous"):
            if len(ambiguous_samples) < 10:
                ambiguous_samples.append({"first_name": r["first_name"], "role": r["role"]})
            buckets["unmatched"] += 0     # counted separately below
            buckets.setdefault("xref_ambiguous", 0)
            buckets["xref_ambiguous"] += 1
        else:
            buckets[nr] = buckets.get(nr, 0) + 1
    if any(buckets.values()):
        shutdown["_source"]["name_resolution"] = {**buckets,
                                                  "ambiguous_samples": ambiguous_samples}
    return company_key, client_name, shutdown


# --------------------------------------------------------------------------- main

def _file_key(xlsx: pathlib.Path) -> str:
    """Leading token of the filename (stops at the first space). For RosterCut
    files that's the numeric roster_id; for the Kleenheat export it's the word
    "Kleenheat"."""
    return xlsx.name.split(" ", 1)[0]


def main() -> int:
    if not RAW_DIR.exists():
        print(f"No raw dir at {RAW_DIR}", file=sys.stderr)
        return 1

    # -- 1. Parse every mapped file first, so the Kleenheat enrichment step
    #       can cross-reference first-name + role against the other rosters.
    parsed: list[tuple[str, pathlib.Path, str, list[dict]]] = []
    rows_by_company: dict[str, list[dict]] = {}
    for xlsx in sorted(RAW_DIR.glob("*.xlsx")):
        key = _file_key(xlsx)
        if key not in ROSTER_MAP:
            print(f"  skip unmapped roster {key}: {xlsx.name}")
            continue
        fmt, rows = parse_roster(xlsx)
        company_key = ROSTER_MAP[key][0]
        parsed.append((key, xlsx, fmt, rows))
        rows_by_company.setdefault(company_key, []).extend(rows)

    if not parsed:
        print("No mapped roster files processed.", file=sys.stderr)
        return 1

    # -- 2. Enrich first-name-only rows (Kleenheat) with surnames from the
    #       RosterCut rosters, so retention can match across rosters.
    lookup = build_surname_lookup(rows_by_company)
    for key, xlsx, fmt, rows in parsed:
        if fmt != "kleenheat":
            continue
        stats = enrich_kleenheat_names(rows, lookup)
        print(f"  enrich {xlsx.name}: "
              f"{stats['explicit_column']} explicit col · "
              f"{stats['email_heuristic']} email · "
              f"{stats['xref_exact']} xref · "
              f"{stats['xref_ambiguous']} ambiguous · "
              f"{stats['unmatched']} unmatched")

    # -- 3. Build per-shutdown payloads and group by company.
    by_company: dict[str, dict] = {}
    for key, xlsx, fmt, rows in parsed:
        company_key, client_name, shutdown = build_shutdown(key, xlsx, rows, fmt)
        by_company.setdefault(company_key, {"company": client_name, "shutdowns": []})
        by_company[company_key]["shutdowns"].append(shutdown)
        print(f"  {key:>10}  {client_name:<10} {shutdown['id']:<22} "
              f"roster={len(shutdown['roster']):>3}  "
              f"{shutdown['start_date']} → {shutdown['end_date']}  "
              f"[{shutdown['status']}]")

    # -- 4. Write per-company JSON files.
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    for company_key, payload in by_company.items():
        payload["generated_at"] = now
        payload["shutdowns"].sort(key=lambda s: s["start_date"])
        out = DATA_DIR / f"{company_key}.json"
        out.write_text(json.dumps(payload, indent=2))
        total = sum(len(s["roster"]) for s in payload["shutdowns"])
        print(f"Wrote {out.relative_to(REPO_ROOT)}: "
              f"{len(payload['shutdowns'])} shutdown(s), {total} confirmed heads")

    # -- 5. Backfill empty payloads for any client the dashboard lists but
    #       which got no rosters this run (prevents 404s on page load).
    referenced = {"kleenheat", "covalent", "tronox", "csbp"}
    for company_key in referenced - by_company.keys():
        path = DATA_DIR / f"{company_key}.json"
        if not path.exists():
            payload = {"company": company_key.title(), "generated_at": now, "shutdowns": []}
            path.write_text(json.dumps(payload, indent=2))
            print(f"Wrote {path.relative_to(REPO_ROOT)}: empty (no roster supplied)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
