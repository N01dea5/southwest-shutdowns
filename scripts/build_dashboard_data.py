#!/usr/bin/env python3
"""Build the Southwest Shutdowns dashboard data in one command.

This wraps the existing pipeline scripts so the GitHub Action and local users
have a single supported entry point. Individual scripts remain available for
focused debugging.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

PIPELINE = [
    "parse_rapidcrews.py",
    "ensure_active_shutdowns.py",
    "apply_hiring_company.py",
    "apply_personnel_calendar.py",
    "apply_shutdown_display_labels.py",
    "normalise_dashboard_data.py",
    "validate_dashboard_data.py",
    "export_tronox_client_dashboard.py",
]


def run_script(script_name: str) -> None:
    script = REPO_ROOT / "scripts" / script_name
    print(f"\n==> {script_name}")
    subprocess.run([sys.executable, str(script)], cwd=REPO_ROOT, check=True)


def main() -> int:
    for script_name in PIPELINE:
        run_script(script_name)
    print("\nDashboard data build complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
