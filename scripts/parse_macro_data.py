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


REPO_ROOT    = pathlib.Path(__file__).resolve().parent.parent
MACRO_FILE   = REPO_ROOT / "data" / "raw" / "Rapidcrews Macro Data.xlsx"
RESUMES_FILE = REPO_ROOT / "Resumes.xlsx"   # standalone so end users can own
                                            # it on SharePoint without touching
                                            # the Rapid Crews SQL export

CONTROL_SHEET      = "ACTIVE_SHUTDOWNS"
JOB_PLANNING_SHEET = "xpbi02 JobPlanningView"
ROSTER_VIEW_SHEET  = "xpbi02 PersonnelRosterView"
TRADE_SHEET        = "xpbi02 DisciplineTrade"
PERSONNEL_SHEET    = "xll01 Personnel"
COMPLIANCE_SHEET   = "xll01 PersonnelCompetency"

# The SQL DisciplineTrade table uses "Rigger - Advanced/Intermediate/Basic"
# but the RosterCut Position-On-Project (and every downstream key — target
# files, dashboard role chips, filled_by_role aggregates) uses the flipped
# "Advanced Rigger" / "Intermediate Rigger" / "Basic Rigger". Normalise SQL
# trade names into the canonical vocabulary so macro-derived filled counts
# merge cleanly with existing required_by_role keys.
MACRO_ROLE_RENAME = {
    "Rigger - Advanced":     "Advanced Rigger",
    "Rigger - Intermediate": "Intermediate Rigger",
    "Rigger - Basic":        "Basic Rigger",
}

# Compliance sheet (xll01 PersonnelCompetency) -> per-site dashboard short
# keys. Each per-site dashboard renders ticket columns using these short
# names; translating them here lets the ticket data drop straight into the
# dashboards' existing render code without mapping every consumer.
TICKET_MAP = {
    "Confined Spaces Entry":                              "cse",
    "Working at Heights":                                 "wah",
    "EWP":                                                "ewp",
    "CA-EBS - Compressed Air Emergency Breathing System": "ba",
    "LF - Forklift Truck":                                "fork",
    # HR Class = Heavy Rigid driver's licence. HRWL = High Risk Work Licence
    # (the legal register). Two different tickets — the dashboards label them
    # separately so conflating them under one key would hide which is held.
    "HR Class":                                           "hr",
    "HRWL":                                               "hrwl",
    "DG - Dogging":                                       "dog",
    "Gas Test Atmospheres":                               "gta",
    "First Aid":                                          "fa",
}
# Rigging is special: per-site dashboards expect a single `rig` field whose
# value is the level string ("Advanced" / "Intermediate" / "Basic"), not a
# boolean. Accept both the "RA/RI/RB -" shorthand and the "Rigger -" long form
# and collapse to the highest level held.
RIG_COMPS = {
    "RA - Advanced Rigging":     "Advanced",
    "Rigger - Advanced":         "Advanced",
    "RI - Intermediate Rigging": "Intermediate",
    "Rigger - Intermediate":     "Intermediate",
    "RB - Basic Rigging":        "Basic",
    "Rigger - Basic":            "Basic",
}
RIG_PRIORITY = {"Advanced": 3, "Intermediate": 2, "Basic": 1}
EXPIRING_SOON_DAYS = 30

# Module-level cache so the workbook only gets opened once per run, even when
# both parse_rapidcrews.build_shutdown() (to look up JobPlanningView required
# counts for RosterCut files) and shutdowns_from_macro_data() (to synthesise
# macro-only shutdowns) touch the same sheets.
_CACHE: dict | None = None

# (Client string, Site string from PersonnelRosterView) ->
#     (company_key, client_display_name, dashboard_site, project_label_base)
#
# Covalent and Tronox share the 'SOUTH WEST' client banner in the source
# system and are disambiguated by Site. The dashboard_site column is what
# gets rendered on the tiles — it deliberately differs from the source Site
# so "Covalent Lithium" reads as "Mt Holland" (the plant's location) to
# match the existing dashboard copy.
# Tuple: (company_key, client_display, dashboard_site, label_base, id_prefix)
# id_prefix overrides company_key when generating the shutdown id (company_key-YYYY-MM).
# Use it when a RosterCut-derived id uses a different prefix than company_key so
# the macro-only dedup in parse_rapidcrews.py correctly detects the collision.
CLIENT_SITE_MAP: dict[tuple[str, str], tuple[str, str, str, str, str]] = {
    ("SOUTH WEST", "Covalent Lithium"): ("covalent", "Covalent", "Mt Holland", "Mt Holland",      "covalent"),
    ("SOUTH WEST", "Tronox"):            ("tronox",   "Tronox",   "Kwinana",    "Major Shutdown",  "tronox"),
    ("CSBP",       "CSBP Kwinana"):      ("csbp",     "CSBP",     "Kwinana",    "CSBP Kwinana",    "csbp"),
    # Kleenheat rolls up under CSBP (WesCEF umbrella) but the RosterCut file
    # uses id prefix "kleenheat-", so we must match that to get correct dedup.
    ("SOUTH WEST", "Kleenheat"):         ("csbp",     "CSBP",     "Kwinana",    "KPF LNG Kleenheat", "kleenheat"),
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


def _load_cache() -> dict:
    """Open the macro workbook once per run and cache every sheet we touch.

    Returned structure:
        {
          "active_jobnos":   set[int] | None,     # from ACTIVE_SHUTDOWNS; None if sheet missing
          "trades":          {tradeId -> displayName},
          "personnel":       {personnelId -> {name, role, mobile, hire_company}},
          "planning_all":    {jobNo -> {tradeName -> {"required": n, "filled": m}}},
          "resumes":         list[dict] | None,   # from RESUMES; None if sheet missing
        }

    `planning_all` covers EVERY JobNo in JobPlanningView (not just the ones in
    ACTIVE_SHUTDOWNS) so parse_rapidcrews.build_shutdown() can look up required
    counts for any RosterCut JobNo that happens to be in the live SQL view.
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    if not MACRO_FILE.exists():
        _CACHE = {"active_jobnos": None, "trades": {}, "personnel": {},
                  "personnel_name_index": {}, "planning_all": {},
                  "compliance": {}, "resumes": None}
        return _CACHE
    wb = _open()
    try:
        active_jobnos          = _read_active_shutdowns(wb)
        trades                 = _load_trade_names(wb)
        personnel              = _load_personnel(wb)
        personnel_name_index   = _load_personnel_name_index(wb)
        planning_all           = _load_planning_all(wb, trades)
        compliance             = _load_compliance(wb)
    finally:
        wb.close()
    resumes = _load_resumes_file(personnel) if RESUMES_FILE.exists() else None
    _CACHE = {
        "active_jobnos":        active_jobnos,
        "trades":               trades,
        "personnel":            personnel,
        "personnel_name_index": personnel_name_index,
        "planning_all":         planning_all,
        "compliance":           compliance,
        "resumes":              resumes,
    }
    return _CACHE


def _read_active_shutdowns(wb: openpyxl.Workbook) -> set[int] | None:
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


def _load_planning_all(wb: openpyxl.Workbook,
                       trade_names: dict[str, str]
                       ) -> dict[int, dict[str, dict[str, int]]]:
    """JobNo -> {tradeName -> {"required": n, "filled": m}} for every JobNo."""
    ws = wb[JOB_PLANNING_SHEET]
    headers = [c.value for c in next(ws.iter_rows(max_row=1))]
    idx = {h: i for i, h in enumerate(headers) if h}
    out: dict[int, dict[str, dict[str, int]]] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not any(row):
            continue
        job = row[idx["JobNo"]]
        if job is None:
            continue
        trade = trade_names.get(row[idx["CompetencyId"]], "Unknown")
        bucket = out.setdefault(int(job), {})
        cell   = bucket.setdefault(trade, {"required": 0, "filled": 0})
        cell["required"] += int(row[idx["Required"]] or 0)
        cell["filled"]   += int(row[idx["Filled"]]   or 0)
    return out


def planning_required_for_jobno(job_no: int) -> dict[str, int] | None:
    """Return JobPlanningView-derived `{tradeName: required}` for a JobNo.

    None means either the macro file doesn't exist OR the JobNo isn't in the
    live SQL view (dropped because the date range rolled over). Empty dict
    means the JobNo exists but all Required columns are zero.
    """
    cache = _load_cache()
    bucket = cache["planning_all"].get(int(job_no))
    if bucket is None:
        return None
    # Drop trades with 0 required AND 0 filled — stale placeholder rows.
    return {
        trade: cell["required"]
        for trade, cell in bucket.items()
        if cell["required"] or cell["filled"]
    }


def planning_filled_for_jobno(job_no: int) -> dict[str, int] | None:
    """Return JobPlanningView-derived `{tradeName: filled}` for a JobNo.

    This is the authoritative "slot filled" count — the same figure the
    Rapid Crews website shows on its planning page. Paired with
    `planning_required_for_jobno` so a live shutdown's headline numbers
    match the website without anyone needing to re-export a RosterCut.
    """
    cache = _load_cache()
    bucket = cache["planning_all"].get(int(job_no))
    if bucket is None:
        return None
    return {
        trade: cell["filled"]
        for trade, cell in bucket.items()
        if cell["required"] or cell["filled"]
    }


def active_shutdowns_jobnos() -> set[int] | None:
    """Return the set of JobNos from the ACTIVE_SHUTDOWNS control sheet.

    Returns None if the sheet doesn't exist at all (legacy mode — every
    RosterCut file in data/raw/ passes through). Returns a set (possibly
    empty) when the sheet is present, signalling the allow-list is active.
    """
    return _load_cache()["active_jobnos"]


def _load_trade_names(wb: openpyxl.Workbook) -> dict[str, str]:
    """TradeId GUID -> Trade display name (from DisciplineTrade).

    Trade names pass through MACRO_ROLE_RENAME so SQL's "Rigger - Advanced"
    becomes the "Advanced Rigger" key the rest of the pipeline uses.
    """
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
            out[tid] = MACRO_ROLE_RENAME.get(name, name)
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


# --------------------------------------------------------------------------- compliance (tickets)

def _norm_name_part(s) -> str:
    """Aggressive first/last-name normalisation: lowercase, letters only.
    Drops spaces, hyphens, apostrophes so "O'Brien"/"OBrien" collide,
    "Van Der Zanden"/"VANDERZANDEN" too."""
    import re as _re
    return _re.sub(r"[^a-z]+", "", (s or "").lower())


def _load_personnel_name_index(wb: openpyxl.Workbook) -> dict[tuple[str, str], str]:
    """Build {(norm_first, norm_last): personnel_id} from xll01 Personnel.

    Personnel can have multiple rows (re-hires, profile duplicates). Later
    rows overwrite earlier under the same key; the compliance loader
    aggregates tickets across all of a person's IDs when matched.
    """
    if PERSONNEL_SHEET not in wb.sheetnames:
        return {}
    ws = wb[PERSONNEL_SHEET]
    headers = list(next(ws.iter_rows(max_row=1, values_only=True)))
    idx = {h: n for n, h in enumerate(headers) if h}
    out: dict[tuple[str, str], str] = {}
    for r in ws.iter_rows(min_row=2, values_only=True):
        pid = r[idx["Personnel Id"]]
        if not pid:
            continue
        f = _norm_name_part(r[idx["Given Names"]])
        l = _norm_name_part(r[idx["Surname"]])
        if f and l:
            out[(f, l)] = pid
    return out


def _load_compliance(wb: openpyxl.Workbook) -> dict[str, dict[str, dict]]:
    """Read xll01 PersonnelCompetency -> {pid: {ticket_key: record}}.

    Record shape: {"expiry": iso|None, "doc": url|None,
                   "status": "current"|"expiring_soon",
                   "level": str (rig only)}

    Filtering: skip Archived rows (superseded) and expired rows (Expiry ≤
    today). Rows with no Expiry are kept as permanent certs.

    Collisions:
      - Non-rig: keep the record with the LATEST expiry (permanent > dated).
      - Rig:     keep the HIGHEST level held (Advanced > Intermediate > Basic).
    """
    if COMPLIANCE_SHEET not in wb.sheetnames:
        return {}
    ws = wb[COMPLIANCE_SHEET]
    headers = list(next(ws.iter_rows(max_row=1, values_only=True)))
    idx = {h: n for n, h in enumerate(headers) if h}
    today = dt.date.today()
    soon  = today + dt.timedelta(days=EXPIRING_SOON_DAYS)
    out: dict[str, dict[str, dict]] = {}
    for r in ws.iter_rows(min_row=2, values_only=True):
        if r[idx["Archived"]]:
            continue
        comp = r[idx["Competency"]]
        pid  = r[idx["Personnel Id"]]
        if not comp or not pid:
            continue
        is_rig = comp in RIG_COMPS
        key    = "rig" if is_rig else TICKET_MAP.get(comp)
        if key is None:
            continue
        exp = r[idx["Expiry"]]
        exp_date: dt.date | None = None
        if exp:
            exp_date = exp.date() if isinstance(exp, dt.datetime) else exp
            if not isinstance(exp_date, dt.date):
                exp_date = None
            elif exp_date <= today:
                continue
        record = {
            "expiry": exp_date.isoformat() if exp_date else None,
            "doc":    r[idx["Document Location"]] or None,
            "status": "expiring_soon" if exp_date and exp_date <= soon else "current",
        }
        if is_rig:
            record["level"] = RIG_COMPS[comp]
            existing = out.setdefault(pid, {}).get(key)
            if existing is None or RIG_PRIORITY[record["level"]] > RIG_PRIORITY[existing["level"]]:
                out[pid][key] = record
        else:
            existing = out.setdefault(pid, {}).get(key)
            if existing is None:
                out[pid][key] = record
            else:
                e_exp = existing["expiry"]
                if record["expiry"] is None and e_exp is not None:
                    out[pid][key] = record
                elif record["expiry"] is not None and e_exp is not None and record["expiry"] > e_exp:
                    out[pid][key] = record
    return out


def match_personnel_id(first: str, last: str) -> str | None:
    """Resolve a (first, last) name pair to a Personnel Id via three passes:
    exact normalised match, prefix-on-first-name-with-same-surname (handles
    "Lucrecia" vs "Lucrecia Celeste"), then surname-unique fallback.
    Returns None when no personnel master data is loaded."""
    cache = _load_cache()
    index = cache.get("personnel_name_index") or {}
    f = _norm_name_part(first)
    l = _norm_name_part(last)
    if not f or not l:
        return None
    if (f, l) in index:
        return index[(f, l)]
    same_surname = [(if_, pid) for (if_, il_), pid in index.items() if il_ == l]
    for if_, pid in same_surname:
        if if_.startswith(f) or f.startswith(if_):
            return pid
    if len(same_surname) == 1:
        return same_surname[0][1]
    return None


def tickets_for_person(first: str, last: str) -> dict:
    """Return the tickets dict for a worker, or {} when no match / no data.
    Safe to call even when the macro workbook or compliance sheet is
    missing — returns empty."""
    pid = match_personnel_id(first, last)
    if not pid:
        return {}
    cache = _load_cache()
    return (cache.get("compliance") or {}).get(pid, {})


def _load_resumes_file(personnel: dict[str, dict]) -> list[dict]:
    """Read the standalone Resumes.xlsx file into a flat list of dicts.

    End-user schema (all columns optional except Name OR Personnel Id):
        Name | Personnel Id | Role | Mobile | Resume URL | Updated | Notes

    Resume URL is typically a SharePoint share link. If Personnel Id is set
    we merge in mobile/role from xll01 Personnel where the sheet leaves them
    blank, so the handover form stays minimal ("just paste the link").

    Kept in its own xlsx rather than as a sheet inside the macro workbook
    because: (a) ops owns it and shouldn't have to touch the Rapid Crews SQL
    export, (b) SharePoint sync pulls are lighter when resume updates don't
    require re-uploading a 15MB workbook.
    """
    wb = openpyxl.load_workbook(RESUMES_FILE, data_only=True, read_only=True)
    try:
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        try:
            header = next(rows)
        except StopIteration:
            return []
        hdr = [(h or "").strip() for h in header]
        # Accept both "Resume URL" and "Resume Link" — humans type either.
        def col(*names):
            for n in names:
                if n in hdr:
                    return hdr.index(n)
            return None
        c_name   = col("Name", "Worker")
        c_pid    = col("Personnel Id", "PersonnelId", "Employee ID")
        c_role   = col("Role", "Primary Role", "Trade")
        c_mobile = col("Mobile", "Phone", "Contact")
        c_url    = col("Resume URL", "Resume Link", "URL", "Link")
        c_upd    = col("Updated", "Updated Date", "Last Updated", "Date")
        c_notes  = col("Notes", "Comment")
        out: list[dict] = []
        for row in rows:
            if not row or not any(row):
                continue
            pid  = (row[c_pid]  if c_pid  is not None else None) or ""
            name = (row[c_name] if c_name is not None else None) or ""
            p    = personnel.get(pid) if pid else None
            def pick(col_idx, fallback):
                v = row[col_idx] if col_idx is not None else None
                s = str(v).strip() if v is not None else ""
                return s or (fallback or "")
            # Name: sheet wins, else look up from personnel master by GUID.
            disp_name = str(name).strip() or (p["name"] if p else "")
            if not disp_name:
                continue
            out.append({
                "name":         disp_name,
                "personnel_id": str(pid).strip() or None,
                "role":         pick(c_role, p["role"] if p else ""),
                "mobile":       rc._standardise_mobile(
                                    row[c_mobile] if c_mobile is not None else None
                                ) or (p["mobile"] if p else ""),
                "resume_url":   pick(c_url, ""),
                "updated":      pick(c_upd, ""),
                "notes":        pick(c_notes, ""),
            })
        return out
    finally:
        wb.close()


def resumes_from_macro_data() -> list[dict]:
    """Public entry point — returns parsed rows from Resumes.xlsx (or empty
    list when the file is absent). Dashboard cross-references `resume_url`
    by normalised name to decorate worker rows with a link."""
    return _load_cache()["resumes"] or []


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
    company_key, client_name, dashboard_site, label_base, id_prefix = CLIENT_SITE_MAP[(client, site)]

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
            "name":         person["name"],
            "role":         role,
            "shift":        crew_label,
            "start":        start.isoformat(),
            "end":          end.isoformat(),
            "personnel_id": pid,
            # Tickets are free here — we already have the Personnel Id from
            # PersonnelRosterView. No name-matching pass needed.
            "tickets":      (_load_cache().get("compliance") or {}).get(pid, {}),
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
    shutdown_id = f"{id_prefix}-{sd.isoformat()[:7]}"

    # JobPlanningView drives BOTH Required and Filled — those are the
    # figures the Rapid Crews website shows. PersonnelRosterView's unique
    # personnel count (the old "filled_by_role" Counter above) tends to
    # drift above JobPlanningView.Filled because it counts anyone with a
    # scheduled day, not just officially-filled slots. Rapid Crews is the
    # source of truth; target files only apply when RC has nothing.
    planning_req = planning["required_by_role"]
    planning_fil = planning["filled_by_role"]
    if planning_req or any(planning_fil.values()):
        all_keys = set(filled_by_role) | set(planning_req) | set(planning_fil)
        required     = {r: int(planning_req.get(r, 0)) for r in all_keys}
        filled_final = {r: int(planning_fil.get(r, 0)) for r in all_keys}
        target_source_meta     = {"source": "rapid_crews_job_planning_view",
                                  "job_no": job_no,
                                  "total_required": sum(planning_req.values()),
                                  "total_filled":   sum(planning_fil.values())}
        required_target_source = "RAPID_CREWS_JOB_PLANNING"
    else:
        required, filled_final, target_source_meta = rc.merge_targets(shutdown_id, filled_by_role)
        required_target_source = (
            "TARGET_FILE" if (rc.TARGETS_DIR / f"{shutdown_id}.json").exists()
            else "PLACEHOLDER_FROM_ROSTER"
        )

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
            "required_target_source":     required_target_source,
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
    cache = _load_cache()
    jobnos = cache["active_jobnos"]
    if not jobnos:
        return []
    print(f"  macro: ACTIVE_SHUTDOWNS lists {len(jobnos)} JobNo(s): "
          f"{sorted(jobnos)}")

    # PersonnelRosterView is the only sheet scoped to ACTIVE_SHUTDOWNS (the
    # others are already fully loaded into the cache).
    wb = _open()
    try:
        roster = _load_roster(wb, jobnos)
    finally:
        wb.close()

    # Re-shape the cached planning rows into the legacy {required_by_role,
    # filled_by_role} shape _build_one expects.
    planning: dict[int, dict] = {}
    for job in jobnos:
        bucket = cache["planning_all"].get(int(job), {})
        planning[job] = {
            "required_by_role": {t: c["required"] for t, c in bucket.items()
                                 if c["required"] or c["filled"]},
            "filled_by_role":   {t: c["filled"]   for t, c in bucket.items()
                                 if c["required"] or c["filled"]},
        }

    out: list[tuple[str, str, dict]] = []
    for job in sorted(jobnos):
        if job not in roster or not roster[job]["workers"]:
            print(f"  warn: JobNo {job} has no roster rows in macro data — skipping")
            continue
        result = _build_one(job,
                            planning.get(job, {"required_by_role": {}, "filled_by_role": {}}),
                            roster[job], cache["personnel"])
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
