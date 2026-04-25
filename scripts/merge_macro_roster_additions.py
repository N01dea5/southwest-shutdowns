#!/usr/bin/env python3
"""Merge live RapidCrews macro roster additions into generated dashboard data.

Why this exists
---------------
The legacy pipeline deliberately let RosterCut XLSX rows win for live jobs
because they carried richer fields such as Position-On-Project and Crew Type.
That meant late additions made directly in RapidCrews / the macro workbook
updated required/filled counts but did not always appear in the named roster
until a fresh RosterCut was exported.

This script keeps the RosterCut richness for existing personnel, but appends
any missing live workers from `xpbi02 PersonnelRosterView` for active JobNos.
This makes the macro workbook the current source of truth for named personnel.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from typing import Any

import parse_macro_data as pmd

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

JOB_TO_SHUTDOWN = {
    1353: ("tronox", "tronox-2026-05"),
    1359: ("covalent", "covalent-2026-04"),
    1375: ("csbp", "csbp-2026-05"),
}


def _load(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


def _write(path: pathlib.Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2))


def _norm_name(value: Any) -> str:
    return re.sub(r"[^a-z]+", "", str(value or "").lower())


def _worker_key(worker: dict[str, Any]) -> str:
    pid = str(worker.get("personnel_id") or "").strip().lower()
    if pid:
        return f"pid:{pid}"
    return f"name:{_norm_name(worker.get('name'))}"


def _find_shutdown(payload: dict[str, Any], shutdown_id: str) -> dict[str, Any] | None:
    for shutdown in payload.get("shutdowns", []):
        if shutdown.get("id") == shutdown_id:
            return shutdown
    return None


def _merge_worker(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    """Fill missing fields only. RosterCut data remains authoritative."""
    for key, value in incoming.items():
        if value in (None, "", {}, []):
            continue
        if key not in existing or existing.get(key) in (None, "", {}, []):
            existing[key] = value


def _merge_job(job_no: int, macro_shutdown: dict[str, Any]) -> bool:
    company_key, shutdown_id = JOB_TO_SHUTDOWN.get(job_no, (None, None))
    if not company_key or not shutdown_id:
        return False

    path = DATA_DIR / f"{company_key}.json"
    payload = _load(path)
    shutdown = _find_shutdown(payload, shutdown_id)
    if not shutdown:
        return False

    roster = shutdown.setdefault("roster", [])
    existing_by_key = {_worker_key(w): w for w in roster if isinstance(w, dict)}
    existing_by_name = {f"name:{_norm_name(w.get('name'))}": w for w in roster if isinstance(w, dict)}
    added = 0
    updated = 0

    for worker in macro_shutdown.get("roster", []) or []:
        if not isinstance(worker, dict):
            continue
        key = _worker_key(worker)
        existing = existing_by_key.get(key)
        if existing is None:
            existing = existing_by_name.get(f"name:{_norm_name(worker.get('name'))}")
        if existing is not None:
            before = dict(existing)
            _merge_worker(existing, worker)
            if existing != before:
                updated += 1
            continue
        roster.append(worker)
        existing_by_key[_worker_key(worker)] = worker
        existing_by_name[f"name:{_norm_name(worker.get('name'))}"] = worker
        added += 1

    # Macro planning remains authoritative for required/filled aggregates.
    for field in ("required_by_role", "filled_by_role", "mobilised_by_role"):
        if isinstance(macro_shutdown.get(field), dict):
            shutdown[field] = macro_shutdown[field]

    # Refresh rollups that are safe to replace from macro schedule data.
    for field in ("crew_split", "labour_hire_split", "required_target_source", "target_source_meta"):
        if macro_shutdown.get(field):
            shutdown[field] = macro_shutdown[field]

    if added or updated:
        _write(path, payload)
        print(f"merge_macro_roster_additions: Job {job_no} {shutdown_id}: added {added}, updated {updated}")
        return True
    print(f"merge_macro_roster_additions: Job {job_no} {shutdown_id}: no missing macro personnel")
    return False


def main() -> int:
    active = pmd.active_shutdowns_jobnos()
    if not active:
        print("merge_macro_roster_additions: no ACTIVE_SHUTDOWNS job list; skipped")
        return 0

    cache = pmd._load_cache()
    if not pmd.MACRO_FILE.exists():
        print("merge_macro_roster_additions: macro workbook not found; skipped")
        return 0

    wanted = {int(j) for j in active if int(j) in JOB_TO_SHUTDOWN}
    if not wanted:
        print("merge_macro_roster_additions: no mapped live jobs to merge")
        return 0

    wb = pmd._open()
    try:
        roster_by_job = pmd._load_roster(wb, wanted)
    finally:
        wb.close()

    changed = False
    for job_no in sorted(wanted):
        bucket = roster_by_job.get(job_no)
        planning = cache.get("planning_all", {}).get(job_no, {})
        if not bucket:
            print(f"merge_macro_roster_additions: Job {job_no}: no macro roster bucket")
            continue
        required_by_role = {
            trade: cell.get("required", 0)
            for trade, cell in planning.items()
            if cell.get("required") or cell.get("filled")
        }
        filled_by_role = {
            trade: cell.get("filled", 0)
            for trade, cell in planning.items()
            if cell.get("required") or cell.get("filled")
        }
        built = pmd._build_one(
            job_no,
            {"required_by_role": required_by_role, "filled_by_role": filled_by_role},
            bucket,
            cache.get("personnel", {}),
        )
        if built is None:
            continue
        _company_key, _client_name, macro_shutdown = built
        changed = _merge_job(job_no, macro_shutdown) or changed

    print("merge_macro_roster_additions: complete" + (" with changes" if changed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
