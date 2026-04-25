#!/usr/bin/env python3
"""Normalise dashboard JSON so the browser renderer cannot crash on shape drift.

The front-end assumes each shutdown has:
  - required_by_role: object
  - filled_by_role: object
  - roster: array
  - every role present in both required_by_role and filled_by_role

RapidCrews source data can legitimately emit roles on one side only. This pass
makes the JSON safe without changing the actual counts: missing counterparts
are set to 0.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
HISTORY_DIR = DATA_DIR / "history"
COMPANY_FILES = ("covalent.json", "tronox.json", "csbp.json")


def _as_int(value: Any) -> int:
    try:
        if value is None or value == "":
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _date_or_today(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return dt.date.today().isoformat()


def _normalise_shutdown(s: dict) -> bool:
    changed = False

    if not isinstance(s.get("required_by_role"), dict):
        s["required_by_role"] = {}
        changed = True
    if not isinstance(s.get("filled_by_role"), dict):
        s["filled_by_role"] = {}
        changed = True
    if not isinstance(s.get("roster"), list):
        s["roster"] = []
        changed = True

    required = {str(k): _as_int(v) for k, v in s["required_by_role"].items() if str(k).strip()}
    filled = {str(k): _as_int(v) for k, v in s["filled_by_role"].items() if str(k).strip()}
    all_roles = set(required) | set(filled)
    safe_required = {role: required.get(role, 0) for role in sorted(all_roles)}
    safe_filled = {role: filled.get(role, 0) for role in sorted(all_roles)}
    if s["required_by_role"] != safe_required:
        s["required_by_role"] = safe_required
        changed = True
    if s["filled_by_role"] != safe_filled:
        s["filled_by_role"] = safe_filled
        changed = True

    for key in ("crew_split", "mobilised_by_role", "labour_hire_split"):
        if not isinstance(s.get(key), dict):
            s[key] = {}
            changed = True
        safe = {str(k): _as_int(v) for k, v in s[key].items() if str(k).strip()}
        if s[key] != safe:
            s[key] = safe
            changed = True

    if not s.get("id"):
        s["id"] = f"shutdown-{_date_or_today(s.get('start_date'))}-{abs(hash(json.dumps(s, sort_keys=True, default=str))) % 100000}"
        changed = True
    if not s.get("name"):
        s["name"] = s["id"]
        changed = True
    if not s.get("site"):
        s["site"] = ""
        changed = True
    if not s.get("start_date"):
        s["start_date"] = _date_or_today(None)
        changed = True
    if not s.get("end_date"):
        s["end_date"] = s["start_date"]
        changed = True
    if not isinstance(s.get("_source"), dict):
        s["_source"] = {}
        changed = True

    safe_roster = []
    for i, worker in enumerate(s.get("roster") or []):
        if not isinstance(worker, dict):
            changed = True
            continue
        w = dict(worker)
        if not w.get("name"):
            w["name"] = "Unknown"
            changed = True
        if not w.get("role"):
            w["role"] = "Unknown"
            changed = True
        if "tickets" not in w or not isinstance(w.get("tickets"), dict):
            w["tickets"] = {}
            changed = True
        safe_roster.append(w)
    if s.get("roster") != safe_roster:
        s["roster"] = safe_roster
        changed = True

    return changed


def _patch_payload_file(path: pathlib.Path) -> bool:
    try:
        payload = json.loads(path.read_text())
    except Exception as exc:
        print(f"::warning::normalise_dashboard_data could not parse {path}: {exc}", file=sys.stderr)
        return False
    changed = False
    if isinstance(payload.get("shutdowns"), list):
        for s in payload["shutdowns"]:
            if isinstance(s, dict):
                changed = _normalise_shutdown(s) or changed
    elif isinstance(payload.get("shutdown"), dict):
        changed = _normalise_shutdown(payload["shutdown"]) or changed
    if changed:
        path.write_text(json.dumps(payload, indent=2))
    return changed


def main() -> int:
    changed_files = []
    for name in COMPANY_FILES:
        path = DATA_DIR / name
        if path.exists() and _patch_payload_file(path):
            changed_files.append(str(path.relative_to(REPO_ROOT)))
    if HISTORY_DIR.exists():
        for path in sorted(HISTORY_DIR.glob("*.json")):
            if _patch_payload_file(path):
                changed_files.append(str(path.relative_to(REPO_ROOT)))
    if changed_files:
        print("normalise_dashboard_data: updated")
        for f in changed_files:
            print(f"  - {f}")
    else:
        print("normalise_dashboard_data: no changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
