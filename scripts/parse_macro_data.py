#!/usr/bin/env python3
"""Build dashboard shutdowns from `Rapidcrews Macro Data.xlsx`.

End-user workflow
-----------------
Open `Rapidcrews Macro Data.xlsx`, go to the `ACTIVE_SHUTDOWNS` sheet, add or
delete rows in the `JobNo` column. Each row = one shutdown the dashboard
should show. When the file lands in the repo the GitHub Action reparses and
the dashboard updates — no code changes, no RosterCut drops required.

How the sheet interacts with the existing RosterCut flow
--------------------------------------------------------
- Sheet missing or no data rows -> legacy behaviour: every RosterCut file in
  `data/raw/` still appears exactly as before.
- Sheet present with rows -> acts as an allow-list:
    * RosterCut shutdowns whose numeric roster_id is in the list pass through
      (they're richer than macro-derived data — Position-On-Project, Crew
      Type, Confirmed flag — so they win on collisions).
    * JobNos in the list without a matching RosterCut file get built from the
      macro workbook's JobPlanningView + PersonnelRosterView.
    * Non-numeric rosters (Kleenheat historical / Pegasus imports) always
      pass — they're not keyed by JobNo.

Macro-derived shutdowns have slightly lower fidelity than RosterCut ones —
per-worker `role` is the employee's Primary Role (from `xll01 Personnel`)
rather than Position-On-Project, and there's no "Contingency" crew bucket.
JobPlanningView still drives the Required/Filled aggregates accurately.
"""
from __future__ import annotations

import datetime as dt
import pathlib
from collections import Counter, defaultdict

import openpyxl

import parse_rapidcrews as rc


REPO_ROOT  = pathlib.Path(__file__).resolve().parent.parent
MACRO_FILE = REPO_ROOT / "Rapidcrews Macro Data.xlsx"

CONTROL_SHEET      = "ACTIVE_SHUTDOWNS"
JOB_PLANNING_SHEET = "xpbi02 JobPlanningView"
ROSTER_VIEW_SHEET  = "xpbi02 PersonnelRosterView"
TRADE_SHEET        = "xpbi02 DisciplineTrade"
PERSONNEL_SHEET    = "xll01 Personnel"

# (Client string, Site string from PersonnelRosterView) ->
#     (company_key, client_display_name, dashboard_site, project_label_base)
#
# Covalent and Tronox share the 'SOUTH WEST' client banner in the source
# system and are disambiguated by Site. The dashboard_site column is what
# gets rendered on the tiles — it deliberately differs from the source Site
# so "Covalent Lithium" reads as "Mt Holland" (the plant's location) to
# match the existing dashboard copy.
CLIENT_SITE_MAP: dict[tuple[str, str], tuple[str, str, str, str]] = {
    ("SOUTH WEST", "Covalent Lithium"): ("covalent", "Covalent", "Mt Holland", "Mt Holland"),
    ("SOUTH WEST", "Tronox"):            ("tronox",   "Tronox",   "Kwinana",    "Major Shutdown"),
    ("CSBP",       "CSBP Kwinana"):      ("csbp",     "CSBP",     "Kwinana",    "CSBP Kwinana"),
    # Kleenheat rolls up under CSBP (WesCEF umbrella — see ROSTER_MAP note
    # in parse_rapidcrews.py). Dashboard_site stays "Kwinana" so the tile
    # lines up with the historical KPF LNG March 2026 entry.
    ("SOUTH WEST", "Kleenheat"):         ("csbp",     "CSBP",     "Kwinana",    "KPF LNG Kleenheat"),
}

# Schedule Types that count as "on the job" for start/end + crew_split
# purposes. Anything else ("Personal Event", "Annual Leave", "Working
# Elsewhere", ...) is a scheduling artefact, not actual attendance.
ONSITE_SCHED_TYPES = {"Day Shift", "Night Shift", "RNR"}
CREW_LABEL = {"Day Shift": "Day", "Night Shift": "Night", "RNR": "RNR"}

_MONTH_NAME = ("", "January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December")


# --------------------------------------------------------------------------- sheet loaders

def _open() -> openpyxl.Workbook:
    return openpyxl.load_workbook(MACRO_FILE, data_only=True, read_only=True)


def active_shutdowns_jobnos() -> set[int] | None:
    """Return the set of JobNos from the ACTIVE_SHUTDOWNS control sheet.

    Returns None if the sheet doesn't exist at all (legacy mode — every
    RosterCut file in data/raw/ passes through). Returns a set (possibly
    empty) when the sheet is present, signalling the allow-list is active.
    """
    if not MACRO_FILE.exists():
        return None
    wb = _open()
    try:
        if CONTROL_SHEET not in wb.sheetnames:
            return None
        ws = wb[CONTROL_SHEET]
        rows = ws.iter_rows(values_only=True)
        try:
            header = next(rows)
        except StopIteration:
            return set()
        try:
            jobno_col = [h for h in header if h].index("JobNo")
        except ValueError:
            print(f"  warn: {CONTROL_SHEET} sheet is missing 'JobNo' header")
            return set()
        jobnos: set[int] = set()
        for row in rows:
            if not row or row[jobno_col] is None:
                continue
            try:
                jobnos.add(int(row[jobno_col]))
            except (TypeError, ValueError):
                print(f"  warn: {CONTROL_SHEET} ignoring non-numeric JobNo {row[jobno_col]!r}")
        return jobnos
    finally:
        wb.close()


def _load_trade_names(wb: openpyxl.Workbook) -> dict[str, str]:
    """TradeId GUID -> Trade display name (from DisciplineTrade)."""
    ws = wb[TRADE_SHEET]
    headers = [c.value for c in next(ws.iter_rows(max_row=1))]
    idx = {h: i for i, h in enumerate(headers)}
    out: dict[str, str] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not any(row):
            continue
        tid = row[idx["TradeId"]]
        name = (row[idx["Trade"]] or "").strip() or "Unknown"
        if tid:
            out[tid] = name
    return out


def _load_personnel(wb: openpyxl.Workbook) -> dict[str, dict]:
    """Personnel Id GUID -> {name, role, mobile, hire_company}."""
    ws = wb[PERSONNEL_SHEET]
    headers = [c.value for c in next(ws.iter_rows(max_row=1))]
    idx = {h: i for i, h in enumerate(headers)}
    out: dict[str, dict] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not any(row):
            continue
        pid = row[idx["Personnel Id"]]
        if not pid:
            continue
        first = (row[idx["Given Names"]] or "").strip()
        last  = (row[idx["Surname"]]      or "").strip()
        out[pid] = {
            "name":         f"{first} {last}".strip() or "Unknown",
            "role":         (row[idx["Primary Role"]] or "Unknown").strip(),
            "mobile":       rc._standardise_mobile(row[idx["Mobile"]]),
            "hire_company": (row[idx["Hire Company"]] or "").strip(),
        }
    return out


def _load_job_planning(wb: openpyxl.Workbook,
                       jobnos: set[int],
                       trade_names: dict[str, str]
                       ) -> dict[int, dict]:
    """JobNo -> {required_by_role, filled_by_role} aggregated from
    JobPlanningView (one row per CompetencyId -> Trade name)."""
    ws = wb[JOB_PLANNING_SHEET]
    headers = [c.value for c in next(ws.iter_rows(max_row=1))]
    idx = {h: i for i, h in enumerate(headers)}
    out: dict[int, dict] = {
        j: {"required_by_role": defaultdict(int),
            "filled_by_role":   defaultdict(int)}
        for j in jobnos
    }
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not any(row):
            continue
        job = row[idx["JobNo"]]
        if job not in out:
            continue
        trade = trade_names.get(row[idx["CompetencyId"]], "Unknown")
        out[job]["required_by_role"][trade] += int(row[idx["Required"]] or 0)
        out[job]["filled_by_role"]  [trade] += int(row[idx["Filled"]]   or 0)
    # Collapse defaultdicts + strip zero-both rows (stale placeholders).
    for job, d in out.items():
        req = {k: v for k, v in d["required_by_role"].items() if v or d["filled_by_role"].get(k)}
        fil = {k: v for k, v in d["filled_by_role"].items()   if v or d["required_by_role"].get(k)}
        d["required_by_role"] = req
        d["filled_by_role"]   = fil
    return out


def _load_roster(wb: openpyxl.Workbook,
                 jobnos: set[int]
                 ) -> dict[int, dict]:
    """JobNo -> {client, site, workers: {PersonnelId -> {dates, sched_types,
    is_on_location}}}. Daily schedule rows are folded into one entry per
    (JobNo, Personnel Id)."""
    ws = wb[ROSTER_VIEW_SHEET]
    headers = [c.value for c in next(ws.iter_rows(max_row=1))]
    idx = {h: i for i, h in enumerate(headers)}
    out: dict[int, dict] = {
        j: {"client": None, "site": None, "workers": defaultdict(lambda: {
            "dates": [], "sched_types": Counter(), "is_on_location": False,
        })}
        for j in jobnos
    }
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not any(row):
            continue
        job = row[idx["Job No"]]
        if job not in out:
            continue
        bucket = out[job]
        if bucket["client"] is None:
            bucket["client"] = row[idx["Client"]]
            bucket["site"]   = row[idx["Site"]]
        pid = row[idx["Personnel Id"]]
        if not pid:
            continue
        w = bucket["workers"][pid]
        date = row[idx["Schedule Date"]]
        if isinstance(date, dt.datetime):
            w["dates"].append(date.date())
        elif isinstance(date, dt.date):
            w["dates"].append(date)
        sched = row[idx["Schedule Type"]]
        if sched:
            w["sched_types"][sched] += 1
        if row[idx["IsOnLocation"]]:
            w["is_on_location"] = True
    return out


# --------------------------------------------------------------------------- builder

def _project_label(base: str, start: dt.date) -> str:
    return f"{base} {_MONTH_NAME[start.month]} {start.year}"


def _build_one(job_no: int,
               planning: dict,
               roster_raw: dict,
               personnel: dict[str, dict]) -> tuple[str, str, dict] | None:
    client = roster_raw["client"]
    site   = roster_raw["site"]
    if (client, site) not in CLIENT_SITE_MAP:
        print(f"  warn: JobNo {job_no} has unmapped client/site "
              f"({client!r}, {site!r}) — skipping. Add to CLIENT_SITE_MAP.")
        return None
    company_key, client_name, dashboard_site, label_base = CLIENT_SITE_MAP[(client, site)]

    workers = roster_raw["workers"]
    # Build one roster row per unique Personnel Id, using only on-site
    # schedule types to pick start/end/crew. Workers who *only* have
    # Personal Event / Annual Leave / Working Elsewhere rows for this
    # job get skipped — they aren't actually on the shutdown.
    roster_entries: list[dict] = []
    crew_split:        Counter  = Counter()
    mobilised_by_role: Counter  = Counter()
    labour_hire_split: Counter  = Counter()
    filled_by_role:    Counter  = Counter()
    for pid, w in workers.items():
        onsite_dates = [d for d, st in zip(w["dates"],
                                           _explode_sched_types(w["sched_types"], len(w["dates"])))
                        if st in ONSITE_SCHED_TYPES]
        if not onsite_dates:
            continue
        person = personnel.get(pid)
        if not person:
            continue
        role   = person["role"]
        mobile = person["mobile"]
        start  = min(w["dates"])
        end    = max(w["dates"])
        # Dominant on-site schedule type drives crew label.
        onsite_only = Counter({k: v for k, v in w["sched_types"].items()
                               if k in ONSITE_SCHED_TYPES})
        dom_sched = onsite_only.most_common(1)[0][0]
        crew_label = CREW_LABEL.get(dom_sched, dom_sched)

        entry = {
            "name":  person["name"],
            "role":  role,
            "start": start.isoformat(),
            "end":   end.isoformat(),
        }
        if mobile:
            entry["mobile"] = mobile
        roster_entries.append(entry)

        filled_by_role[role]  += 1
        crew_split[crew_label] += 1
        if w["is_on_location"]:
            mobilised_by_role[role] += 1
        labour_hire_split[person["hire_company"]] += 1

    if not roster_entries:
        print(f"  warn: JobNo {job_no} has no on-site workers — skipping")
        return None

    # Shutdown window = span of on-site workers' earliest/latest dates.
    all_starts = [dt.date.fromisoformat(e["start"]) for e in roster_entries]
    all_ends   = [dt.date.fromisoformat(e["end"])   for e in roster_entries]
    sd, ed = min(all_starts), max(all_ends)
    shutdown_id = f"{company_key}-{sd.isoformat()[:7]}"

    # JobPlanningView aggregates — authoritative for Required; prefer its
    # Filled when non-zero, otherwise fall back to the roster-derived count.
    planning_req = planning["required_by_role"]
    planning_fil = planning["filled_by_role"]
    required_seed: dict[str, int] = dict(planning_req) if planning_req else dict(filled_by_role)
    filled_seed:   dict[str, int] = dict(planning_fil) if any(planning_fil.values()) else dict(filled_by_role)

    required, filled_final, target_source_meta = rc.merge_targets(shutdown_id, filled_seed)
    # merge_targets falls back to filled_seed for required when no target
    # file exists — override with JobPlanningView's required when we have
    # real values and no target file has been written.
    target_exists = (rc.TARGETS_DIR / f"{shutdown_id}.json").exists()
    if not target_exists and required_seed:
        required = dict(required_seed)

    status = rc._infer_status(sd, ed, dt.date.today())

    shutdown = {
        "id":               shutdown_id,
        "name":             _project_label(label_base, sd),
        "site":             dashboard_site,
        "start_date":       sd.isoformat(),
        "end_date":         ed.isoformat(),
        "status":           status,
        "required_by_role": required,
        "filled_by_role":   filled_final,
        "crew_split":       dict(crew_split),
        "mobilised_by_role": dict(mobilised_by_role),
        "labour_hire_split": dict(labour_hire_split),
        "roster":           sorted(roster_entries, key=lambda r: r["name"]),
        "_source": {
            "macro_data_job_no":          job_no,
            "macro_data_client":          client,
            "macro_data_site":            site,
            "source_format":              "macro_data",
            "required_target_source":     "REAL_TARGET" if target_exists else (
                "JOB_PLANNING_VIEW" if required_seed else "PLACEHOLDER_FROM_ROSTER"
            ),
            "macro_data_roster_size":     len(roster_entries),
            "macro_data_filled_by_role":  dict(filled_by_role),
            "target_source":              target_source_meta,
        },
    }
    return company_key, client_name, shutdown


def _explode_sched_types(counter: Counter, n_dates: int) -> list[str]:
    """Best-effort alignment between each schedule date and its Schedule
    Type for the worker. We only keep aggregate counts per type in the
    loader (simpler + lower memory), so reconstruct a per-date sequence
    by repeating each type by its count. Order won't match date order,
    but for the onsite filter we only care about set membership per
    worker, which this preserves."""
    seq: list[str] = []
    for st, c in counter.items():
        seq.extend([st] * c)
    # Pad/truncate to requested length (guards against divergence from
    # len(dates) when a row had no Schedule Type at all).
    if len(seq) < n_dates:
        seq.extend([None] * (n_dates - len(seq)))
    return seq[:n_dates]


# --------------------------------------------------------------------------- public entry point

def shutdowns_from_macro_data() -> list[tuple[str, str, dict]]:
    """Return (company_key, client_name, shutdown_dict) triples for every
    JobNo in the ACTIVE_SHUTDOWNS sheet. Empty list if sheet is absent or
    empty, if the macro file doesn't exist, or if no JobNo resolves to a
    mapped client/site + non-empty roster."""
    if not MACRO_FILE.exists():
        return []
    jobnos = active_shutdowns_jobnos()
    if not jobnos:
        return []
    print(f"  macro: ACTIVE_SHUTDOWNS lists {len(jobnos)} JobNo(s): "
          f"{sorted(jobnos)}")

    wb = _open()
    try:
        trades    = _load_trade_names(wb)
        personnel = _load_personnel(wb)
        planning  = _load_job_planning(wb, jobnos, trades)
        roster    = _load_roster(wb, jobnos)
    finally:
        wb.close()

    out: list[tuple[str, str, dict]] = []
    for job in sorted(jobnos):
        if job not in roster or not roster[job]["workers"]:
            print(f"  warn: JobNo {job} has no roster rows in macro data — skipping")
            continue
        result = _build_one(job, planning.get(job, {"required_by_role": {}, "filled_by_role": {}}),
                            roster[job], personnel)
        if result is not None:
            out.append(result)
            _, client_name, shutdown = result
            print(f"  macro: JobNo {job:>4} -> {client_name:<10} "
                  f"{shutdown['id']:<22} roster={len(shutdown['roster']):>3}  "
                  f"{shutdown['start_date']} → {shutdown['end_date']}  "
                  f"[{shutdown['status']}]")
    return out


if __name__ == "__main__":
    # Ad-hoc: print what the macro parser would emit, no JSON write.
    for ck, cn, s in shutdowns_from_macro_data():
        print(ck, cn, s["id"], s["name"], s["start_date"], s["end_date"],
              f"roster={len(s['roster'])}")
