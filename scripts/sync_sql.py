"""
Sync required + filled headcount (and roster names) from the Rapid Crews
Azure SQL database into `data/<company>.json`.

Runs in the refresh-data.yml workflow AFTER parse_rapidcrews.py, so when
configured it supersedes the XLSX-based data. No-ops silently when the
auth env vars are missing — the XLSX pipeline keeps working untouched.

Auth
----
Two modes, both via ODBC Driver 18 for SQL Server:

  SERVICE PRINCIPAL (CI — GitHub Actions):
      AZURE_CLIENT_ID       app registration's client id
      AZURE_CLIENT_SECRET   app registration's client secret
      AZURE_TENANT_ID       directory tenant id
    The app registration needs `db_datareader` on rapidcrews-srg — ask
    the DB owner to run:
      CREATE USER [<app-name>] FROM EXTERNAL PROVIDER;
      ALTER ROLE db_datareader ADD MEMBER [<app-name>];

  INTERACTIVE (local dev — MFA prompt in a browser):
      python3 scripts/sync_sql.py --interactive
      (optional) AZURE_UPN=firstname.lastname@srgglobal.com.au

NOTE: MFA / ActiveDirectoryInteractive auth does NOT work in CI. A
service principal (or OIDC federated credential) is required for
unattended runs. See README "Connecting the Rapid Crews SQL database".

Filling in the queries
----------------------
Each entry in SHUTDOWN_MAP maps one SQL shutdown identifier (whatever
column Rapid Crews keys its rosters by — `RosterID`, `ProjectID`, etc)
to the dashboard's (company_key, client_name, project_label, site,
shutdown_id) tuple — the same shape as parse_rapidcrews.ROSTER_MAP.

The two TODOs below (`TARGETS_SQL`, `ROSTER_SQL`) are the only
schema-specific bits. Run `python3 scripts/inspect_sql.py` first to
discover the real table/column names, then paste them in.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import pathlib
import sys

# pyodbc is optional — if it's not installed, treat it like a missing
# secret and no-op. Keeps the CI job green on runners without the
# driver and lets `pip install pyodbc` live in the workflow step only.
try:
    import pyodbc  # type: ignore
except ImportError:
    pyodbc = None  # type: ignore


SERVER = "rapidcrews-srg.database.windows.net"
DATABASE = "rapidcrews-srg"

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"


# ---------------------------------------------------------------------------
# SHUTDOWN_MAP — SQL shutdown id -> dashboard shutdown identity.
#
# The SQL key on the left is whatever value uniquely identifies the shutdown
# in the Rapid Crews DB (probably the same numeric roster id used in the
# RosterCut XLSX filename — 1353/1359/1375). Run inspect_sql.py to confirm.
# ---------------------------------------------------------------------------
SHUTDOWN_MAP: dict[str, tuple[str, str, str, str, str]] = {
    # sql_key        company_key  client      project_label               site          shutdown_id
    "1353":         ("tronox",   "Tronox",   "Major Shutdown May 2026",  "Kwinana",    "tronox-2026-05"),
    "1359":         ("covalent", "Covalent", "Mt Holland April 2026",    "Mt Holland", "covalent-2026-04"),
    "1375":         ("csbp",     "CSBP",     "NAAN2 June 2026",          "Kwinana",    "csbp-2026-06"),
}


# ---------------------------------------------------------------------------
# SQL QUERIES — fill these in after running inspect_sql.py.
#
# Each must return EXACTLY the columns listed in its docstring (names +
# order), keyed so we can bucket rows by sql_key (the SHUTDOWN_MAP lookup).
# Placeholders live behind a `None` sentinel so the script no-ops loudly
# if someone deploys it before the queries are filled in.
# ---------------------------------------------------------------------------

# Per-role required headcount.
#   columns: sql_key, role, required
#   one row per (shutdown, role)
TARGETS_SQL: str | None = None
# TARGETS_SQL = """
#     SELECT RosterID   AS sql_key,
#            TradeName  AS role,
#            Requested  AS required
#     FROM   dbo.RosterTargets
#     WHERE  RosterID IN ({placeholders})
# """

# Per-worker confirmed roster. Filled counts are aggregated from this.
#   columns: sql_key, worker_name, role, confirmed, mobilised
#   one row per worker per shutdown
#   confirmed + mobilised are booleans (1/0 or "Y"/"N") — used to scope
#   which rows count towards `filled_by_role`.
ROSTER_SQL: str | None = None
# ROSTER_SQL = """
#     SELECT RosterID                     AS sql_key,
#            CONCAT(FirstName, ' ', Surname) AS worker_name,
#            Position                     AS role,
#            CAST(Confirmed AS INT)       AS confirmed,
#            CAST(Mobilised AS INT)       AS mobilised
#     FROM   dbo.RosterWorkers
#     WHERE  RosterID IN ({placeholders})
# """


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def _build_conn_str(mode: str) -> str:
    parts = [
        "Driver={ODBC Driver 18 for SQL Server}",
        f"Server=tcp:{SERVER},1433",
        f"Database={DATABASE}",
        "Encrypt=yes",
        "TrustServerCertificate=no",
        "Connection Timeout=30",
    ]
    if mode == "interactive":
        parts.append("Authentication=ActiveDirectoryInteractive")
        upn = os.environ.get("AZURE_UPN", "").strip()
        if upn:
            parts.append(f"UID={upn}")
    elif mode == "service-principal":
        parts.append("Authentication=ActiveDirectoryServicePrincipal")
        parts.append(f"UID={os.environ['AZURE_CLIENT_ID']}")
        parts.append(f"PWD={os.environ['AZURE_CLIENT_SECRET']}")
    else:
        raise ValueError(f"Unknown auth mode: {mode}")
    return ";".join(parts) + ";"


def _detect_mode(cli_interactive: bool) -> str | None:
    if cli_interactive:
        return "interactive"
    if os.environ.get("AZURE_CLIENT_ID") and os.environ.get("AZURE_CLIENT_SECRET"):
        return "service-principal"
    return None


# ---------------------------------------------------------------------------
# Query + assemble
# ---------------------------------------------------------------------------

def _in_placeholders(n: int) -> str:
    """Render `?,?,?` for an IN (...) clause of n values."""
    return ",".join(["?"] * n)


def fetch_targets(cur, sql_keys: list[str]) -> dict[str, dict[str, int]]:
    """Returns { sql_key: { role: required } }."""
    if not TARGETS_SQL:
        print("  (TARGETS_SQL not configured — skipping required_by_role)")
        return {}
    q = TARGETS_SQL.format(placeholders=_in_placeholders(len(sql_keys)))
    cur.execute(q, *sql_keys)
    out: dict[str, dict[str, int]] = collections.defaultdict(dict)
    for sql_key, role, required in cur.fetchall():
        out[str(sql_key)][str(role)] = int(required)
    return out


def fetch_roster(cur, sql_keys: list[str]) -> dict[str, list[dict]]:
    """Returns { sql_key: [ {name, role, confirmed, mobilised}, ... ] }."""
    if not ROSTER_SQL:
        print("  (ROSTER_SQL not configured — skipping roster + filled_by_role)")
        return {}
    q = ROSTER_SQL.format(placeholders=_in_placeholders(len(sql_keys)))
    cur.execute(q, *sql_keys)
    out: dict[str, list[dict]] = collections.defaultdict(list)
    for sql_key, name, role, confirmed, mobilised in cur.fetchall():
        out[str(sql_key)].append({
            "name": str(name).strip(),
            "role": str(role).strip(),
            "confirmed": bool(int(confirmed)) if confirmed is not None else False,
            "mobilised": bool(int(mobilised)) if mobilised is not None else False,
        })
    return out


def _merge_into_company_json(company_key: str, shutdown_id: str,
                             client: str, project_label: str, site: str,
                             required_by_role: dict[str, int],
                             roster_rows: list[dict]) -> None:
    """Upsert one shutdown into data/<company_key>.json (same shape as
    parse_rapidcrews.py output). Loads the existing file so siblings are
    preserved; replaces or appends the matching shutdown entry."""
    path = DATA_DIR / f"{company_key}.json"
    payload = json.loads(path.read_text()) if path.exists() else {
        "company": client,
        "shutdowns": [],
    }
    payload["company"] = client  # source of truth wins
    payload["generated_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    confirmed_rows = [r for r in roster_rows if r["confirmed"]]
    filled_by_role: dict[str, int] = collections.Counter(r["role"] for r in confirmed_rows)
    mobilised_by_role: dict[str, int] = collections.Counter(
        r["role"] for r in roster_rows if r["mobilised"]
    )

    # Placeholder target if SQL doesn't have targets yet — mirrors parse_rapidcrews.
    if not required_by_role:
        required_by_role = dict(filled_by_role)
        required_target_source = "PLACEHOLDER_FROM_ROSTER"
    else:
        required_target_source = "REAL_TARGET"

    # Shape roster the way app.js expects: [{name, role}, ...].
    roster = [{"name": r["name"], "role": r["role"]} for r in confirmed_rows]

    new_entry = {
        "id": shutdown_id,
        "name": project_label,
        "site": site,
        # start_date / end_date / status are NOT set here — leave the existing
        # values alone (parse_rapidcrews sets them from the XLSX). If that
        # file is ever dropped, TARGETS_SQL / ROSTER_SQL should be extended to
        # return start / end dates and this block updated accordingly.
        "required_by_role": {k: int(v) for k, v in required_by_role.items()},
        "filled_by_role":   {k: int(v) for k, v in filled_by_role.items()},
        "mobilised_by_role": {k: int(v) for k, v in mobilised_by_role.items()},
        "roster": roster,
        "_source": {
            "rapid_crews_sql_server": SERVER,
            "rapid_crews_sql_key":    shutdown_id,
            "required_target_source": required_target_source,
        },
    }

    shutdowns = payload.setdefault("shutdowns", [])
    for i, s in enumerate(shutdowns):
        if s.get("id") == shutdown_id:
            # Preserve dates + anything we don't own.
            new_entry = {**s, **new_entry}
            shutdowns[i] = new_entry
            break
    else:
        shutdowns.append(new_entry)

    path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"  wrote {path.relative_to(REPO_ROOT)}  "
          f"({len(roster)} confirmed, {sum(required_by_role.values())} required)")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--interactive",
        action="store_true",
        help="Force ActiveDirectoryInteractive auth (browser MFA prompt). "
             "Local use only — not viable in CI.",
    )
    args = ap.parse_args()

    mode = _detect_mode(args.interactive)
    if mode is None:
        print("sync_sql: no Azure SQL auth configured — skipping.")
        print("  Set AZURE_CLIENT_ID + AZURE_CLIENT_SECRET + AZURE_TENANT_ID "
              "in CI, or run with --interactive locally.")
        return 0

    if pyodbc is None:
        print("sync_sql: pyodbc not installed — skipping.", file=sys.stderr)
        return 0

    if TARGETS_SQL is None and ROSTER_SQL is None:
        print("sync_sql: neither TARGETS_SQL nor ROSTER_SQL is configured. "
              "Run scripts/inspect_sql.py to discover the schema, then fill "
              "them in at the top of this file.")
        return 0

    sql_keys = list(SHUTDOWN_MAP.keys())
    print(f"sync_sql: connecting to {SERVER}/{DATABASE} as {mode}…")

    try:
        cnx = pyodbc.connect(_build_conn_str(mode), timeout=30)
    except pyodbc.Error as e:
        print(f"sync_sql: connection failed: {e}", file=sys.stderr)
        return 1

    with cnx:
        cur = cnx.cursor()
        targets_by_key = fetch_targets(cur, sql_keys)
        roster_by_key  = fetch_roster(cur, sql_keys)

    for sql_key, (company_key, client, project_label, site, shutdown_id) in SHUTDOWN_MAP.items():
        rreq = targets_by_key.get(sql_key, {})
        rows = roster_by_key.get(sql_key, [])
        if not rreq and not rows:
            print(f"  {sql_key} ({shutdown_id}): no rows returned — skipping")
            continue
        _merge_into_company_json(
            company_key=company_key,
            shutdown_id=shutdown_id,
            client=client,
            project_label=project_label,
            site=site,
            required_by_role=rreq,
            roster_rows=rows,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
