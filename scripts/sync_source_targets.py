#!/usr/bin/env python3
"""Sync headcount targets from each site's source dashboard repo.

For every client whose dashboard the unified roll-up reads from, this script
fetches the site's own `index.html` (raw from GitHub), extracts the planned
roster encoded in its JavaScript, and writes two artefacts:

- `data/imports/<company>-source.json` — full planned roster (names, roles,
  groups, shifts, shift-days, TBC flags, contingency workforce). This is the
  authoritative "what was originally requested" snapshot. Not consumed by the
  parser — it exists as provenance and for cross-referencing.
- `data/targets/<shutdown_id>.json` — per-shutdown `{role: required_headcount}`
  overrides keyed by the Rapid Crews role names the parser sees. These ARE
  consumed by `scripts/parse_rapidcrews.py` via `merge_targets`.

The ROLE_MAP below translates each source dashboard's role vocabulary
(e.g. Covalent's "Fitter - Inspections", Tronox's "Rigger - Advanced") into
the Rapid Crews vocabulary the unified dashboard reads ("Mechanical Fitter",
"Advanced Rigger", ...). Source roles that don't appear in Rapid Crews at all
(e.g. Covalent "Roller Tech" mapping to "Basic Rigger") are noted in the map.

Run: `python3 scripts/sync_source_targets.py` then re-run
`python3 scripts/parse_rapidcrews.py`.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import urllib.request


REPO_ROOT    = pathlib.Path(__file__).resolve().parent.parent
IMPORTS_DIR  = REPO_ROOT / "data" / "imports"
TARGETS_DIR  = REPO_ROOT / "data" / "targets"


# Per-company source dashboard description.
# - `role_map` collapses source roles into the Rapid Crews role vocabulary the
#   parser writes to `filled_by_role`. Multiple source roles may map to the
#   same RC role (values are summed).
# - `split` (optional) takes one source role and splits it across several RC
#   roles in fixed counts, for the case where the source uses a coarser
#   category than Rapid Crews (CSBP lumps Advanced/Intermediate into "Rigger").
SOURCES: dict[str, dict] = {
    "covalent": {
        "source_repo": "N01dea5/Covalent-Mt-Holland---April-2026",
        "source_ref":  "main",
        "source_url":  "https://raw.githubusercontent.com/N01dea5/Covalent-Mt-Holland---April-2026/main/index.html",
        "client":      "Covalent Lithium",
        "project":     "Mt Holland Shutdown",
        "site":        "Mt Holland",
        "shutdown_id": "covalent-2026-04",
        "dates_text":  "17 April – 4 May 2026 (18 days)",
        "start_date":  "2026-04-17",
        "end_date":    "2026-05-04",
        "extractor":   "e_array",
        "role_map": {
            "Superintendent":                "Superintendent",
            "Supervisor DS":                 "Supervisor - Mechanical",
            "Supervisor NS":                 "Supervisor - Mechanical",
            "Supervisor Elec":               "Supervisor",
            "Roller Crew Supervisor":        "Supervisor",
            "Coordinator":                   "Site Coordinator",
            "HSE":                           "HSE Advisor",
            "Boilermaker":                   "Boilermaker",
            "Coded Welder":                  "Coded Welder",
            "Electrician":                   "Electrician",
            "Fitter":                        "Mechanical Fitter",
            "Fitter - Conveyor Inspections": "Mechanical Fitter",
            "Fitter - Inspections":          "Mechanical Fitter",
            "Rigger Advanced":               "Advanced Rigger",
            "Roller Tech":                   "Basic Rigger",
            "Lube Tech T/A":                 "Lube Technician",
            "T.A. (Sentry)":                 "Trade Assistant",
            "T.A. Telehandler":              "Telehandler Operator",
            "Bus Driver":                    "Trade Assistant",
        },
    },
    "tronox": {
        "source_repo": "N01dea5/tronox-major-shutdown-may-2026",
        "source_ref":  "main",
        "source_url":  "https://raw.githubusercontent.com/N01dea5/tronox-major-shutdown-may-2026/main/index.html",
        "client":      "Tronox",
        "project":     "Tronox May Major Shutdown",
        "site":        "Kwinana",
        "shutdown_id": "tronox-2026-05",
        "dates_text":  "5-day shutdown, May 2026 (104 planned positions; 59 day / 45 night)",
        "start_date":  "2026-05-18",
        "end_date":    "2026-05-22",
        "extractor":   "crew_array",
        "role_map": {
            "Supervisor - Mechanical": "Supervisor - Mechanical",
            "Boilermaker":             "Boilermaker",
            "Coded Welder":            "Coded Welder",
            "Mechanical Fitter":       "Mechanical Fitter",
            "Rigger - Advanced":       "Advanced Rigger",
            "Rigger - Intermediate":   "Intermediate Rigger",
            "Trade Assistant":         "Trade Assistant",
        },
    },
    "csbp": {
        "source_repo": "N01dea5/csbp-naan2-shutdown-workforce-dashboard",
        "source_ref":  "claude/csbp-demo-dashboard-6OFlN",
        "source_url":  "https://raw.githubusercontent.com/N01dea5/csbp-naan2-shutdown-workforce-dashboard/claude/csbp-demo-dashboard-6OFlN/index.html",
        "client":      "CSBP",
        "project":     "CSBP NAAN2 Shutdown 2026",
        "site":        "Kwinana",
        "shutdown_id": "csbp-2026-05",
        "dates_text":  "25 May – 21 Jun 2026 (28 days). Phases: Pre-Shut 25 May–5 Jun · Shutdown 6–18 Jun · Post-Shut 19–21 Jun",
        "start_date":  "2026-05-25",
        "end_date":    "2026-06-21",
        "extractor":   "e_array",
        "role_map": {
            "Supervisor":      "Supervisor - Mechanical",
            "Fitter":          "Mechanical Fitter",
            "Trade Assistant": "Trade Assistant",
        },
        # Source uses a single "Rigger" role; Rapid Crews splits into Advanced
        # and Intermediate. Split the 4 planned Riggers 3+1 to match RC's breakdown.
        "split": {
            "Rigger": {"Advanced Rigger": 3, "Intermediate Rigger": 1},
        },
    },
}


# --------------------------------------------------------------------------- extractors

def _extract_e_array(txt: str) -> list[dict]:
    """Rows of the form `{id:N, name:"X", role:"Y", shift:"S", days:D, group:"G", ...}`
    used by the Covalent and CSBP source dashboards."""
    rows: list[dict] = []
    pat = re.compile(
        r"\{\s*id\s*:\s*(\d+)\s*,\s*name\s*:\s*\"([^\"]*)\"\s*,\s*role\s*:\s*\"([^\"]*)\"\s*,"
        r"\s*shift\s*:\s*\"([^\"]*)\"\s*,\s*days\s*:\s*(\d+)\s*,\s*group\s*:\s*\"([^\"]*)\""
    )
    for m in pat.finditer(txt):
        rows.append({
            "id":    int(m.group(1)),
            "name":  m.group(2),
            "role":  m.group(3),
            "shift": m.group(4),
            "days":  int(m.group(5)),
            "group": m.group(6),
            "tbc":   m.group(2) == "TBC",
        })
    return rows


def _extract_crew_array(txt: str) -> list[dict]:
    """Tronox uses `CREW = [[name, role, shift], ..., ...tbc(n, role, shift), ...]`."""
    m = re.search(r"const\s+CREW\s*=\s*\[(.*?)\n\s*\];", txt, re.DOTALL)
    if not m:
        raise RuntimeError("CREW block not found")
    body = m.group(1)
    rows: list[dict] = []
    tok = re.compile(
        r'\[\s*"([^"]*)"\s*,\s*"([^"]*)"\s*,\s*"([^"]*)"\s*\]'      # literal tuple
        r'|'
        r'\.\.\.tbc\(\s*(\d+)\s*,\s*"([^"]*)"\s*,\s*"([^"]*)"\s*\)',  # spread tbc
    )
    for t in tok.finditer(body):
        if t.group(1) is not None:
            rows.append({"name": t.group(1), "role": t.group(2),
                         "shift": t.group(3), "tbc": False})
        else:
            n = int(t.group(4))
            for _ in range(n):
                rows.append({"name": "TBC", "role": t.group(5),
                             "shift": t.group(6), "tbc": True})
    for i, r in enumerate(rows, 1):
        r["id"] = i
        # Tronox groups by role directly; 5-day shutdown per dashboard comments.
        r.setdefault("group", r["role"])
        r.setdefault("days", 5)
    return rows


EXTRACTORS = {"e_array": _extract_e_array, "crew_array": _extract_crew_array}


def _extract_contingency(txt: str) -> dict | None:
    m = re.search(r"const\s+CONTINGENCY\s*=\s*\{(.*?)\n\s*\};", txt, re.DOTALL)
    if not m:
        return None
    body = m.group(1)
    total_m = re.search(r"total\s*:\s*(\d+)", body)
    by_group: dict[str, int] = {}
    bg = re.search(r"byGroup\s*:\s*\{(.*?)\}", body, re.DOTALL)
    if bg:
        for km in re.finditer(r'"([^"]+)"\s*:\s*(\d+)', bg.group(1)):
            by_group[km.group(1)] = int(km.group(2))
    return {
        "total":    int(total_m.group(1)) if total_m else sum(by_group.values()),
        "by_group": by_group,
    }


# --------------------------------------------------------------------------- helpers

def _summarise(rows: list[dict]) -> dict:
    by_role: dict[str, int]  = {}
    by_group: dict[str, int] = {}
    by_shift: dict[str, int] = {}
    confirmed = tbc = 0
    for r in rows:
        by_role[r["role"]]   = by_role.get(r["role"], 0) + 1
        by_group[r["group"]] = by_group.get(r["group"], 0) + 1
        by_shift[r["shift"]] = by_shift.get(r["shift"], 0) + 1
        if r.get("tbc"): tbc       += 1
        else:            confirmed += 1
    return {
        "total_planned": len(rows),
        "confirmed":     confirmed,
        "tbc":           tbc,
        "by_role":       by_role,
        "by_group":      by_group,
        "by_shift":      by_shift,
    }


def _map_targets(by_role: dict[str, int], role_map: dict[str, str],
                 split: dict[str, dict[str, int]] | None) -> dict[str, int]:
    """Translate source role counts into Rapid Crews role keys."""
    out: dict[str, int] = {}
    for src_role, count in by_role.items():
        if split and src_role in split:
            for rc_role, n in split[src_role].items():
                out[rc_role] = out.get(rc_role, 0) + n
            if sum(split[src_role].values()) != count:
                print(f"  ! split for {src_role!r} sums to "
                      f"{sum(split[src_role].values())} but source has {count}",
                      file=sys.stderr)
            continue
        if src_role not in role_map:
            print(f"  ! unmapped source role {src_role!r} "
                  f"({count} planned) — skipped", file=sys.stderr)
            continue
        rc_role = role_map[src_role]
        out[rc_role] = out.get(rc_role, 0) + count
    return out


def _fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read().decode("utf-8")


# --------------------------------------------------------------------------- main

def main() -> int:
    IMPORTS_DIR.mkdir(parents=True, exist_ok=True)
    TARGETS_DIR.mkdir(parents=True, exist_ok=True)

    for company, cfg in SOURCES.items():
        print(f"[{company}] fetching {cfg['source_repo']}@{cfg['source_ref']}")
        try:
            html = _fetch(cfg["source_url"])
        except Exception as e:
            print(f"  ! fetch failed: {e}", file=sys.stderr)
            return 1

        rows         = EXTRACTORS[cfg["extractor"]](html)
        summary      = _summarise(rows)
        contingency  = _extract_contingency(html)
        targets      = _map_targets(summary["by_role"], cfg["role_map"],
                                    cfg.get("split"))

        # data/imports/<company>-source.json — full planned roster (provenance)
        import_doc = {
            "source_repo": cfg["source_repo"],
            "source_file": "index.html",
            "source_ref":  cfg["source_ref"],
            "source_url":  cfg["source_url"],
            "client":      cfg["client"],
            "project":     cfg["project"],
            "site":        cfg["site"],
            "shutdown_id": cfg["shutdown_id"],
            "dates_text":  cfg["dates_text"],
            "start_date":  cfg["start_date"],
            "end_date":    cfg["end_date"],
            "contingency": contingency,
            "summary":     summary,
            "roster":      rows,
        }
        imp_path = IMPORTS_DIR / f"{company}-source.json"
        imp_path.write_text(json.dumps(import_doc, indent=2))
        print(f"  wrote {imp_path.relative_to(REPO_ROOT)} "
              f"({summary['total_planned']} planned, {summary['tbc']} TBC)")

        # data/targets/<shutdown_id>.json — RC-role-keyed target counts (parser input)
        tgt_path = TARGETS_DIR / f"{cfg['shutdown_id']}.json"
        tgt_path.write_text(json.dumps(targets, indent=2) + "\n")
        print(f"  wrote {tgt_path.relative_to(REPO_ROOT)} "
              f"(sum={sum(targets.values())}, keys={len(targets)})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
