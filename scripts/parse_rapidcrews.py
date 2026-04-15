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


# Each ROSTER_MAP entry is (company_key, client_display_name, project_label,
# site). An optional 5th element — shutdown_id_override — lets us
# disambiguate multiple shutdowns at the same client in the same month
# (e.g. Tianqi running a Construction Ramp-Up and a Scaffold Shutdown in
# parallel through April-June 2026). Without the override, shutdown_id
# defaults to "<company_key>-<YYYY-MM of start_date>" and the two would
# collide.
#
# File-key conventions:
#   - Numeric leading token  -> Rapid Crews roster id (from the RosterCut
#                                filename convention)
#   - Anything else          -> the whole filename stem (minus .xlsx,
#                                whitespace-trimmed)
ROSTER_MAP: dict[str, tuple] = {
    # Rapid Crews RosterCut exports — keyed by numeric roster_id.
    "1353": ("tronox",   "Tronox",   "Major Shutdown May 2026", "Kwinana"),
    "1359": ("covalent", "Covalent", "Mt Holland April 2026",   "Mt Holland"),
    "1375": ("csbp",     "CSBP",     "NAAN2 June 2026",         "Kwinana"),

    # CSBP is the umbrella WesCEF client — their Kwinana estate covers the
    # KPF LNG (Kleenheat-branded) plant and the NAAN2 fertiliser unit. Both
    # roll up under the same company_key so the unified dashboard treats
    # them as one client for filter/retention purposes. The shutdown_id for
    # the historical KPF LNG roster stays "kleenheat-2026-03" so the
    # existing target file (data/targets/kleenheat-2026-03.json) keeps
    # working without a rename.
    "Kleenheat Major March 2026":
        ("csbp", "CSBP", "KPF LNG Major Shutdown March 2026 (Kleenheat)", "Kwinana",
         "kleenheat-2026-03"),

    # Tianqi (Kwinana lithium hydroxide plant) — the TLK-1596 construction
    # ramp-up scope. A parallel Scaffold Shutdown roster was tracked briefly
    # and then dropped; the shutdown_id_override machinery is kept in place
    # (5th tuple element) so the Construction roster doesn't collapse into
    # "tianqi-2026-04" if another April-starting scope gets added later.
    "Tianqi Construction Ramp Up Project":
        ("tianqi", "Tianqi", "Construction Ramp Up Project", "Kwinana",
         "tianqi-construction-2026-04"),
}

REPO_ROOT   = pathlib.Path(__file__).resolve().parent.parent
RAW_DIR     = REPO_ROOT / "data" / "raw"
DATA_DIR    = REPO_ROOT / "data"
TARGETS_DIR = DATA_DIR / "targets"     # optional override: targets/<shutdown_id>.json

RAPIDCREWS_COLS = ["Company", "Name", "Surname", "Position", "Position On Project",
                   "Start Date", "End Date", "Confirmed", "Crew Type", "Mobilised"]
KLEENHEAT_COLS  = ["Name", "Trade", "Company", "On Site", "Off Site", "Crew"]
PEGASUS_COLS    = ["Company", "Date In", "Date Out", "Shift", "Surname", "First Name",
                   "Pegasus Job Role"]


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


def _standardise_mobile(raw) -> str:
    """Normalise Australian mobile numbers to canonical '04XX XXX XXX' form.

    The rosters come from three different XLSX schemas and the team enter
    mobiles inconsistently: some are local-style with sensible spacing
    ('0493 038 522'), some are mis-spaced ('049 759 4673' — should group as
    0497 594 673), some are international without a plus ('61420397028'), and
    a handful carry stray dashes or parentheses. Strip every non-digit, then
    re-pad/re-space to a single canonical form so the matrix renders cleanly
    and the tel: links dial correctly on both desktop and mobile.

    Non-mobile or unrecognisable input is returned empty — better to hide a
    dodgy number than to dial the wrong person.
    """
    if raw is None:
        return ""
    digits = re.sub(r"\D", "", str(raw))
    if not digits:
        return ""
    # International with country code: 61 4XX XXX XXX (11 digits total, leading 61)
    if len(digits) == 11 and digits.startswith("61") and digits[2] == "4":
        digits = "0" + digits[2:]
    # Nine-digit local (missing leading 0): 4XX XXX XXX -> 04XX XXX XXX
    elif len(digits) == 9 and digits.startswith("4"):
        digits = "0" + digits
    # Anything else that doesn't conform to the 10-digit 04XX mobile pattern
    # is kept as raw digits (not formatted) — unusual but preserved for ops
    # review rather than silently dropped.
    if len(digits) == 10 and digits.startswith("04"):
        return f"{digits[0:4]} {digits[4:7]} {digits[7:10]}"
    return digits


# --------------------------------------------------------------------------- roster parsers

def _detect_format(headers: list) -> str:
    """Detect which of our three supported roster schemas the XLSX is using."""
    hs = {h for h in headers if h}
    if set(RAPIDCREWS_COLS).issubset(hs):
        return "rapidcrews"
    if set(PEGASUS_COLS).issubset(hs):
        return "pegasus"
    if set(KLEENHEAT_COLS).issubset(hs):
        return "kleenheat"
    return "unknown"


def parse_rapidcrews_roster(xlsx_path: pathlib.Path) -> list[dict]:
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active
    headers = [c.value for c in ws[1]]
    idx = {h: i for i, h in enumerate(headers)}
    mobile_col = idx.get("Mobile")

    rows: list[dict] = []
    for raw in ws.iter_rows(min_row=2, values_only=True):
        if not any(raw):
            continue
        name = f"{raw[idx['Name']] or ''} {raw[idx['Surname']] or ''}".strip()
        if not name:
            continue
        role = raw[idx["Position On Project"]] or raw[idx["Position"]] or "Unknown"
        mobile = _standardise_mobile(raw[mobile_col]) if mobile_col is not None else ""
        rows.append({
            "labour_hire": (raw[idx["Company"]] or "").strip(),
            "name":        name,
            "role":        str(role).strip(),
            "mobile":      mobile,
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
    mobile_col  = idx.get("Mobile")

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
        mobile = _standardise_mobile(raw[mobile_col]) if mobile_col is not None else ""
        rows.append({
            "labour_hire":       (raw[idx["Company"]] or "").strip(),
            "name":              name,
            "first_name":        first,
            "role":              role,
            "mobile":            mobile,
            "start":             to_iso(raw[idx["On Site"]]),
            "end":               to_iso(raw[idx["Off Site"]]),
            "confirmed":         True,
            "crew_type":         crew,
            "mobilised":         True,
            "_name_resolution":  resolution,      # None until enrichment fills it
        })
    return rows


def parse_pegasus_roster(xlsx_path: pathlib.Path) -> list[dict]:
    """Pegasus-style labour list: First Name + Surname columns, Date In/Out,
    Shift (DS/NS), Pegasus Job Role. Every row is treated as confirmed +
    mobilised — the shutdown has already happened."""
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active
    headers = [c.value for c in ws[1]]
    idx = {h: i for i, h in enumerate(headers)}
    shift_label = {"DS": "Day", "NS": "Night", "DAY": "Day", "NIGHT": "Night"}
    mobile_col = idx.get("Contractor Mobile Number") or idx.get("Mobile")

    rows: list[dict] = []
    for raw in ws.iter_rows(min_row=2, values_only=True):
        if not any(raw):
            continue
        first = (raw[idx["First Name"]] or "").strip()
        last  = (raw[idx["Surname"]]    or "").strip()
        name  = f"{first} {last}".strip()
        if not name:
            continue
        role  = str(raw[idx["Pegasus Job Role"]] or "Unknown").strip()
        shift = str(raw[idx["Shift"]] or "").strip().upper()
        mobile = _standardise_mobile(raw[mobile_col]) if mobile_col is not None else ""
        rows.append({
            "labour_hire":      (raw[idx["Company"]] or "").strip(),
            "name":             name,
            "role":             role,
            "mobile":           mobile,
            "start":            to_iso(raw[idx["Date In"]]),
            "end":              to_iso(raw[idx["Date Out"]]),
            "confirmed":        True,
            "crew_type":        shift_label.get(shift, shift.title() or "Unknown"),
            "mobilised":        True,
            "_name_resolution": "explicit_column",
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
    if fmt == "pegasus":
        return fmt, parse_pegasus_roster(xlsx_path)
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

def merge_targets(shutdown_id: str,
                  filled_by_role: dict[str, int]
                  ) -> tuple[dict[str, int], dict[str, int], dict | None]:
    """Read optional overrides from data/targets/<shutdown_id>.json and merge
    them onto the Rapid Crews-derived counts.

    Two file shapes are supported:

    - Flat dict `{role: int}` — legacy / Kleenheat style — treated as the
      required-by-role override. `filled_by_role` is left as derived.
    - Nested `{"required_by_role": {...}, "filled_by_role": {...}, "_source": {...}}`
      produced by `scripts/sync_source_targets.py`. Both required AND filled
      are replaced by the per-site dashboard counts (the Rapid Crews roster
      diverges from the site dashboard's own named list; trust the site).

    Returns (required_by_role, filled_by_role, source_meta | None).
    """
    path = TARGETS_DIR / f"{shutdown_id}.json"
    if not path.exists():
        return dict(filled_by_role), dict(filled_by_role), None
    data: dict = json.loads(path.read_text())

    if "required_by_role" in data:
        required_override: dict[str, int] = data["required_by_role"]
        filled_override:   dict[str, int] | None = data.get("filled_by_role")
        source_meta = data.get("_source")
    else:
        # Legacy flat shape — all keys are required-by-role overrides.
        required_override = data
        filled_override   = None
        source_meta       = None

    if filled_override is not None:
        # Full override from the per-site dashboard — trust it completely.
        # Don't mix in roles the Rapid Crews roster has but the site doesn't,
        # or the totals diverge from what the site shows.
        all_keys = set(required_override) | set(filled_override)
        required = {r: int(required_override.get(r, 0)) for r in all_keys}
        filled   = {r: int(filled_override.get(r, 0))   for r in all_keys}
    else:
        # Legacy / Kleenheat: target covers required only; filled stays as
        # the RC-derived count (with required defaulting to filled for any
        # role the target file didn't mention — the placeholder behaviour).
        all_keys = set(filled_by_role) | set(required_override)
        required = {r: int(required_override.get(r, filled_by_role.get(r, 0)))
                    for r in all_keys}
        filled   = dict(filled_by_role)
    return required, filled, source_meta


# --------------------------------------------------------------------------- build a shutdown payload

def _infer_status(start_day: dt.date, end_day: dt.date, today: dt.date) -> str:
    if end_day < today:
        return "completed"
    if start_day <= today:
        return "in_progress"
    return "booked"


def build_shutdown(file_key: str, xlsx: pathlib.Path, rows: list[dict], fmt: str) -> tuple[str, str, dict]:
    entry = ROSTER_MAP[file_key]
    company_key, client_name, project_label, site = entry[:4]
    shutdown_id_override = entry[4] if len(entry) > 4 else None
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

    shutdown_id = shutdown_id_override or f"{company_key}-{sd[:7]}"
    required, filled_final, target_source_meta = merge_targets(shutdown_id, filled_by_role)
    today       = dt.date.today()
    status      = _infer_status(dt.date.fromisoformat(sd),
                                dt.date.fromisoformat(ed), today)

    target_exists = (TARGETS_DIR / f"{shutdown_id}.json").exists()
    shutdown = {
        "id":               shutdown_id,
        "name":             project_label,
        "site":             site,
        "start_date":       sd,
        "end_date":         ed,
        "status":           status,
        "required_by_role": required,
        "filled_by_role":   filled_final,
        "crew_split":       crew_split,
        "mobilised_by_role": mobilised_by_role,
        "labour_hire_split": labour_hire_split,
        "roster": [
            {"name": r["name"],
             "role": r["role"],
             **({"mobile": r["mobile"]} if r.get("mobile") else {}),
             # Per-worker start/end drive the consolidated ops roster (tab 2).
             # They can differ from the shutdown's overall span when a worker
             # only covers part of the window (e.g. a supervisor arrives early,
             # a trade assistant demobs mid-shutdown).
             **({"start": r["start"]} if r.get("start") else {}),
             **({"end":   r["end"]}   if r.get("end")   else {})}
            for r in confirmed
        ],
        "_source": {
            "rapid_crews_roster_id":   file_key,
            "rapid_crews_export_file": xlsx.name,
            "source_format":           fmt,
            "required_target_source": (
                "REAL_TARGET" if target_exists else "PLACEHOLDER_FROM_ROSTER"
            ),
            "rapid_crews_roster_size": len(confirmed),
            "rapid_crews_filled_by_role": filled_by_role,   # preserved for audit
            "target_source":           target_source_meta,  # None for Kleenheat
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
    """Lookup key into ROSTER_MAP.
      - Numeric leading token (Rapid Crews RosterCut) -> roster_id (e.g. "1353")
      - Anything else -> full filename stem, trimmed (e.g.
        "Tianqi Construction Ramp Up Project", "Kleenheat Major March 2026").
    Trailing whitespace in filenames is tolerated — the uploader for
    "Tianqi Construction Ramp Up Project .xlsx" accidentally left a space
    before the extension."""
    first = xlsx.name.split(" ", 1)[0]
    if first.isdigit():
        return first
    return xlsx.stem.strip()


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
    referenced = {"covalent", "tronox", "csbp", "tianqi"}
    for company_key in referenced - by_company.keys():
        path = DATA_DIR / f"{company_key}.json"
        if not path.exists():
            payload = {"company": company_key.title(), "generated_at": now, "shutdowns": []}
            path.write_text(json.dumps(payload, indent=2))
            print(f"Wrote {path.relative_to(REPO_ROOT)}: empty (no roster supplied)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
