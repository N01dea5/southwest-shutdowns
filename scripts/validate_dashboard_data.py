#!/usr/bin/env python3
"""Validate generated dashboard JSON before it is committed.

This is intentionally strict on structural fields and tolerant on operational
values. It catches the issues that can blank the static dashboard: missing maps,
non-array rosters, invalid dates and required/filled role mismatches.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
COMPANY_FILES = ("covalent.json", "tronox.json", "csbp.json")
REQUIRED_SHUTDOWN_FIELDS = ("id", "name", "site", "start_date", "end_date")
VALID_STATUS = {"booked", "in_progress", "completed"}


def _is_iso_date(value: Any) -> bool:
    try:
        dt.date.fromisoformat(str(value))
        return True
    except Exception:
        return False


def _is_number_map(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            return False
        if not isinstance(item, int):
            return False
        if item < 0:
            return False
    return True


def _validate_worker(path: pathlib.Path, shutdown_id: str, index: int, worker: Any, errors: list[str]) -> None:
    prefix = f"{path}: {shutdown_id}: roster[{index}]"
    if not isinstance(worker, dict):
        errors.append(f"{prefix} is not an object")
        return
    if not str(worker.get("name", "")).strip():
        errors.append(f"{prefix} missing name")
    if not str(worker.get("role", "")).strip():
        errors.append(f"{prefix} missing role")
    if "tickets" in worker and not isinstance(worker["tickets"], dict):
        errors.append(f"{prefix}.tickets must be an object")
    for date_field in ("start", "end"):
        if worker.get(date_field) and not _is_iso_date(worker[date_field]):
            errors.append(f"{prefix}.{date_field} is not YYYY-MM-DD: {worker[date_field]!r}")


def _validate_shutdown(path: pathlib.Path, shutdown: Any, errors: list[str]) -> None:
    if not isinstance(shutdown, dict):
        errors.append(f"{path}: shutdown entry is not an object")
        return

    shutdown_id = str(shutdown.get("id", "<missing-id>"))
    for field in REQUIRED_SHUTDOWN_FIELDS:
        if not str(shutdown.get(field, "")).strip():
            errors.append(f"{path}: {shutdown_id}: missing {field}")

    if shutdown.get("status") and shutdown["status"] not in VALID_STATUS:
        errors.append(f"{path}: {shutdown_id}: invalid status {shutdown['status']!r}")

    for field in ("start_date", "end_date"):
        if shutdown.get(field) and not _is_iso_date(shutdown[field]):
            errors.append(f"{path}: {shutdown_id}: {field} is not YYYY-MM-DD: {shutdown[field]!r}")

    required = shutdown.get("required_by_role")
    filled = shutdown.get("filled_by_role")
    if not _is_number_map(required):
        errors.append(f"{path}: {shutdown_id}: required_by_role must be a role -> non-negative integer object")
        required = {}
    if not _is_number_map(filled):
        errors.append(f"{path}: {shutdown_id}: filled_by_role must be a role -> non-negative integer object")
        filled = {}

    if isinstance(required, dict) and isinstance(filled, dict):
        missing_from_required = sorted(set(filled) - set(required))
        missing_from_filled = sorted(set(required) - set(filled))
        if missing_from_required:
            errors.append(f"{path}: {shutdown_id}: filled roles missing from required_by_role: {missing_from_required}")
        if missing_from_filled:
            errors.append(f"{path}: {shutdown_id}: required roles missing from filled_by_role: {missing_from_filled}")

    roster = shutdown.get("roster")
    if not isinstance(roster, list):
        errors.append(f"{path}: {shutdown_id}: roster must be an array")
    else:
        for index, worker in enumerate(roster):
            _validate_worker(path, shutdown_id, index, worker, errors)

    for optional_map in ("crew_split", "mobilised_by_role", "labour_hire_split"):
        if optional_map in shutdown and not _is_number_map(shutdown[optional_map]):
            errors.append(f"{path}: {shutdown_id}: {optional_map} must be a string -> non-negative integer object")


def _validate_file(path: pathlib.Path, errors: list[str]) -> None:
    try:
        payload = json.loads(path.read_text())
    except Exception as exc:
        errors.append(f"{path}: invalid JSON: {exc}")
        return

    if not isinstance(payload, dict):
        errors.append(f"{path}: top-level payload must be an object")
        return

    if not str(payload.get("company", "")).strip():
        errors.append(f"{path}: missing top-level company")

    shutdowns = payload.get("shutdowns")
    if not isinstance(shutdowns, list):
        errors.append(f"{path}: top-level shutdowns must be an array")
        return

    seen_ids: set[str] = set()
    for shutdown in shutdowns:
        if isinstance(shutdown, dict):
            sid = str(shutdown.get("id", ""))
            if sid in seen_ids:
                errors.append(f"{path}: duplicate shutdown id {sid}")
            if sid:
                seen_ids.add(sid)
        _validate_shutdown(path, shutdown, errors)


def main() -> int:
    errors: list[str] = []
    for filename in COMPANY_FILES:
        path = DATA_DIR / filename
        if not path.exists():
            errors.append(f"missing required data file: {path}")
            continue
        _validate_file(path, errors)

    if errors:
        print("Dashboard data validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("Dashboard data validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
