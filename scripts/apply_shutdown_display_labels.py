#!/usr/bin/env python3
"""Apply consistent dashboard display labels to every shutdown JSON record.

The dashboard renders `shutdown.name` in several places. Rather than editing
all renderers, keep the source data consistent:

    <RapidCrews JobNo> – <job description>

When a description is already available in `_source.job_description` it wins.
Known historical/live roster IDs have short fallback descriptions here so the
whole dashboard uses the same heading style.
"""
from __future__ import annotations

import json
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
HISTORY_DIR = DATA_DIR / "history"

JOB_DESCRIPTION_OVERRIDES: dict[int, str] = {
    1110: "Mt Holland October 2025",
    1116: "Tronox Major Shutdown November 2025",
    1147: "CSBP NAAN3 November 2025",
    1353: "Tronox Major Shutdown May 2026",
    1359: "Mt Holland April 2026",
    1375: "CSBP NAAN2 June 2026",
    1405: "CSBP - NAAN1 Shut",
}


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def _job_no(shutdown: dict) -> int | None:
    src = shutdown.get("_source", {}) or {}
    candidates = (
        src.get("job_no"),
        src.get("rapid_crews_roster_id"),
        (src.get("target_source") or {}).get("job_no"),
    )
    for raw in candidates:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return None


def _description(shutdown: dict, job_no: int) -> str:
    src = shutdown.get("_source", {}) or {}
    target_source = src.get("target_source") or {}
    for raw in (
        src.get("job_description"),
        target_source.get("job_description"),
        JOB_DESCRIPTION_OVERRIDES.get(job_no),
        shutdown.get("name"),
    ):
        desc = _clean(raw)
        if not desc:
            continue
        # Avoid double-prefixing when the name already contains the job number.
        desc = re.sub(rf"^\s*{job_no}\s*[-–:|]\s*", "", desc).strip()
        return desc
    return ""


def _apply_to_shutdown(shutdown: dict) -> bool:
    job_no = _job_no(shutdown)
    if job_no is None:
        return False
    desc = _description(shutdown, job_no)
    if not desc:
        return False
    wanted = f"{job_no} – {desc}"
    changed = shutdown.get("name") != wanted
    if changed:
        shutdown["name"] = wanted
    src = shutdown.setdefault("_source", {})
    if not src.get("job_no"):
        src["job_no"] = job_no
        changed = True
    if not src.get("job_description"):
        src["job_description"] = desc
        changed = True
    target = src.get("target_source")
    if isinstance(target, dict):
        if not target.get("job_no"):
            target["job_no"] = job_no
            changed = True
        if not target.get("job_description"):
            target["job_description"] = desc
            changed = True
    return changed


def _patch_company_file(path: pathlib.Path) -> bool:
    payload = json.loads(path.read_text())
    changed = False
    for shutdown in payload.get("shutdowns", []):
        changed = _apply_to_shutdown(shutdown) or changed
    if changed:
        path.write_text(json.dumps(payload, indent=2))
    return changed


def _patch_history_file(path: pathlib.Path) -> bool:
    payload = json.loads(path.read_text())
    shutdown = payload.get("shutdown")
    if not isinstance(shutdown, dict):
        return False
    changed = _apply_to_shutdown(shutdown)
    if changed:
        path.write_text(json.dumps(payload, indent=2))
    return changed


def main() -> int:
    changed_files = []
    for name in ("covalent", "tronox", "csbp"):
        path = DATA_DIR / f"{name}.json"
        if path.exists() and _patch_company_file(path):
            changed_files.append(str(path.relative_to(REPO_ROOT)))
    if HISTORY_DIR.exists():
        for path in sorted(HISTORY_DIR.glob("*.json")):
            if _patch_history_file(path):
                changed_files.append(str(path.relative_to(REPO_ROOT)))
    if changed_files:
        print("apply_shutdown_display_labels: updated")
        for f in changed_files:
            print(f"  - {f}")
    else:
        print("apply_shutdown_display_labels: no label changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
