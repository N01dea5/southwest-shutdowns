#!/usr/bin/env python3
"""Export a sanitised Tronox Major Shutdown client feed.

The internal Southwest dashboard remains the source of truth. This script reads
internal generated JSON, selects the Tronox May 2026 shutdown, strips sensitive
fields, and emits a stable client-facing feed.

Client-facing rules:
  - show same-client retention
  - show SRG carry-over
  - show new-hire flag to support buddy-system planning
  - do not expose or highlight labour-hire usage
  - do not expose mobile numbers, personnel IDs or SharePoint document URLs
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
OUT_DIR = DATA_DIR / "client"
OUT_FILE = OUT_DIR / "tronox-major-2026.json"
TARGET_ID = "tronox-2026-05"
TARGET_JOB_NO = "1353"
SENSITIVE_KEYS = {"mobile", "phone", "personnel_id", "hire_company", "hiring_company", "doc", "source_row"}
TICKET_ORDER = ["cse", "wah", "ewp", "ba", "fork", "hr", "dog", "rig", "gta", "fa", "hrwl"]


def _load_json(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


def _date(value: Any) -> str:
    text = str(value or "").strip()
    return text[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", text) else ""


def _parse_date(value: Any) -> dt.date:
    try:
        return dt.date.fromisoformat(_date(value))
    except Exception:
        return dt.date.min


def _name_key(value: Any) -> str:
    return re.sub(r"[^a-z]+", "", str(value or "").lower())


def _display_name(value: Any) -> str:
    # Remove internal visual markers while preserving normal display name.
    return re.sub(r"[🔷🔹]", "", str(value or "")).strip()


def _role_key(role: str) -> str:
    return re.sub(r"\s+", " ", role or "").strip()


def _find_target_shutdown() -> dict[str, Any]:
    tronox = _load_json(DATA_DIR / "tronox.json")
    for shutdown in tronox.get("shutdowns", []):
        sid = str(shutdown.get("id", ""))
        name = str(shutdown.get("name", ""))
        if sid == TARGET_ID or TARGET_JOB_NO in name:
            return shutdown

    history = _load_json(DATA_DIR / "history" / f"{TARGET_ID}.json")
    if isinstance(history.get("shutdown"), dict):
        return history["shutdown"]

    raise SystemExit(f"Target shutdown {TARGET_ID} / Job {TARGET_JOB_NO} not found")


def _all_shutdowns() -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for company_file in sorted(DATA_DIR.glob("*.json")):
        if company_file.name not in {"covalent.json", "tronox.json", "csbp.json"}:
            continue
        payload = _load_json(company_file)
        company = str(payload.get("company", company_file.stem)).strip()
        for shutdown in payload.get("shutdowns", []):
            if isinstance(shutdown, dict):
                rows.append((company, shutdown))
    history_dir = DATA_DIR / "history"
    if history_dir.exists():
        for path in sorted(history_dir.glob("*.json")):
            payload = _load_json(path)
            shutdown = payload.get("shutdown")
            company = str(payload.get("client_name") or payload.get("company_key") or "").strip() or path.stem.split("-")[0].title()
            if isinstance(shutdown, dict):
                rows.append((company, shutdown))
    return rows


def _prior_worker_sets(target_start: dt.date) -> tuple[set[str], set[str]]:
    seen_any: set[str] = set()
    seen_tronox: set[str] = set()
    for company, shutdown in _all_shutdowns():
        if shutdown.get("id") == TARGET_ID:
            continue
        if _parse_date(shutdown.get("start_date")) >= target_start:
            continue
        for worker in shutdown.get("roster", []) or []:
            key = _name_key(worker.get("name"))
            if not key:
                continue
            seen_any.add(key)
            if company.lower() == "tronox" or str(shutdown.get("id", "")).startswith("tronox"):
                seen_tronox.add(key)
    return seen_any, seen_tronox


def _ticket_summary(tickets: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not isinstance(tickets, dict):
        return out
    for key in TICKET_ORDER:
        value = tickets.get(key)
        if not value:
            continue
        if isinstance(value, dict):
            out[key] = {
                "status": value.get("status") or "current",
                "expiry": _date(value.get("expiry")) or None,
                "level": value.get("level") or None,
            }
            # Remove empty level for cleaner feed.
            if out[key]["level"] is None:
                out[key].pop("level", None)
        elif value is True:
            out[key] = {"status": "current", "expiry": None}
    # Preserve extras/drivers as non-sensitive capability notes.
    return out


def _shift_summary(workers: list[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for worker in workers:
        shift = str(worker.get("shift") or "Unallocated").strip() or "Unallocated"
        result[shift] = result.get(shift, 0) + 1
    return dict(sorted(result.items()))


def _role_shift_summary(workers: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for worker in workers:
        role = _role_key(str(worker.get("role") or "Unallocated"))
        shift = str(worker.get("shift") or "Unallocated").strip() or "Unallocated"
        result.setdefault(role, {})[shift] = result.setdefault(role, {}).get(shift, 0) + 1
    return {role: dict(sorted(shifts.items())) for role, shifts in sorted(result.items())}


def _build_feed() -> dict[str, Any]:
    shutdown = _find_target_shutdown()
    target_start = _parse_date(shutdown.get("start_date"))
    seen_any, seen_tronox = _prior_worker_sets(target_start)

    roster_in = [w for w in shutdown.get("roster", []) or [] if isinstance(w, dict)]
    workers: list[dict[str, Any]] = []
    same_client_count = 0
    srg_carry_count = 0
    new_count = 0
    buddy_count = 0

    for worker in roster_in:
        name = _display_name(worker.get("name"))
        key = _name_key(name)
        same_client = key in seen_tronox
        srg_carry = key in seen_any and not same_client
        new_hire = bool(worker.get("newhire")) or (key not in seen_any)
        buddy_required = bool(new_hire)

        same_client_count += int(same_client)
        srg_carry_count += int(srg_carry)
        new_count += int(new_hire)
        buddy_count += int(buddy_required)

        safe_worker = {
            "name": name,
            "role": _role_key(str(worker.get("role") or "")),
            "shift": str(worker.get("shift") or "").strip(),
            "start": _date(worker.get("start")) or _date(shutdown.get("start_date")),
            "end": _date(worker.get("end")) or _date(shutdown.get("end_date")),
            "same_client_retention": same_client,
            "srg_carry_over": srg_carry,
            "new_hire": new_hire,
            "buddy_required": buddy_required,
            "tickets": _ticket_summary(worker.get("tickets") or {}),
        }
        if worker.get("extras"):
            safe_worker["extras"] = str(worker.get("extras"))
        if worker.get("drivers"):
            safe_worker["drivers"] = str(worker.get("drivers"))
        workers.append(safe_worker)

    required = {str(k): int(v or 0) for k, v in (shutdown.get("required_by_role") or {}).items()}
    filled = {str(k): int(v or 0) for k, v in (shutdown.get("filled_by_role") or {}).items()}
    roles = sorted(set(required) | set(filled))
    role_summary = [
        {
            "role": role,
            "required": required.get(role, 0),
            "confirmed": filled.get(role, 0),
            "gap": max(required.get(role, 0) - filled.get(role, 0), 0),
        }
        for role in roles
    ]

    total = len(workers)
    feed = {
        "schema_version": 1,
        "generated_at": dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "source_of_truth": "N01dea5/southwest-shutdowns:data/tronox.json",
        "client": "Tronox",
        "dashboard": "Tronox Major Shutdown May 2026",
        "shutdown": {
            "id": shutdown.get("id"),
            "job_no": TARGET_JOB_NO,
            "name": shutdown.get("name"),
            "site": shutdown.get("site"),
            "start_date": _date(shutdown.get("start_date")),
            "end_date": _date(shutdown.get("end_date")),
            "status": shutdown.get("status"),
        },
        "summary": {
            "required_total": sum(required.values()),
            "confirmed_total": sum(filled.values()),
            "gap_total": max(sum(required.values()) - sum(filled.values()), 0),
            "same_client_retention": same_client_count,
            "srg_carry_over": srg_carry_count,
            "new_hires": new_count,
            "buddy_required": buddy_count,
            "shift_split": _shift_summary(workers),
            "role_shift_split": _role_shift_summary(workers),
        },
        "roles": role_summary,
        "workers": workers,
    }
    _assert_sanitised(feed)
    return feed


def _assert_sanitised(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_l = str(key).lower()
            if key_l in SENSITIVE_KEYS or "sharepoint" in str(item).lower():
                raise SystemExit(f"Sensitive field leaked at {path}.{key}: {item!r}")
            _assert_sanitised(item, f"{path}.{key}")
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            _assert_sanitised(item, f"{path}[{idx}]")
    elif isinstance(value, str):
        lowered = value.lower()
        if "sharepoint" in lowered or re.search(r"\b04\d{2}\s?\d{3}\s?\d{3}\b", value):
            raise SystemExit(f"Sensitive value leaked at {path}: {value!r}")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    feed = _build_feed()
    OUT_FILE.write_text(json.dumps(feed, indent=2))
    print(f"export_tronox_client_dashboard: wrote {OUT_FILE.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
