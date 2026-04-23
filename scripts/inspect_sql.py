"""
One-off discovery tool for the Rapid Crews Azure SQL database.

Lists every base table, its columns, and the first few rows, so we can
work out which tables hold:
  - the per-shutdown "required" headcount (targets)
  - the per-shutdown "filled" roster (confirmed workers)

and which column identifies a shutdown.

Run this LOCALLY — it uses ActiveDirectoryInteractive auth, which opens
a browser window for the MFA prompt. Not suitable for CI.

Usage:
    pip install pyodbc
    # also install "ODBC Driver 18 for SQL Server" for your OS:
    #   macOS:  brew install msodbcsql18
    #   Ubuntu: https://learn.microsoft.com/sql/connect/odbc/linux-mac/installing-the-microsoft-odbc-driver-for-sql-server
    export AZURE_UPN="firstname.lastname@srgglobal.com.au"   # optional, pre-fills the login box
    python3 scripts/inspect_sql.py                           # dumps every table
    python3 scripts/inspect_sql.py --table dbo.Rosters       # deep-dive one table
"""
from __future__ import annotations

import argparse
import os
import sys

try:
    import pyodbc
except ImportError:
    sys.exit("pyodbc not installed. Run: pip install pyodbc")

SERVER = "rapidcrews-srg.database.windows.net"
DATABASE = "rapidcrews-srg"
SAMPLE_ROWS = 3
CELL_MAX = 80


def build_conn_str() -> str:
    upn = os.environ.get("AZURE_UPN", "").strip()
    parts = [
        "Driver={ODBC Driver 18 for SQL Server}",
        f"Server=tcp:{SERVER},1433",
        f"Database={DATABASE}",
        "Encrypt=yes",
        "TrustServerCertificate=no",
        "Connection Timeout=30",
        "Authentication=ActiveDirectoryInteractive",
    ]
    if upn:
        parts.append(f"UID={upn}")
    return ";".join(parts) + ";"


def list_tables(cur) -> list[tuple[str, str]]:
    cur.execute(
        """
        SELECT TABLE_SCHEMA, TABLE_NAME
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE = 'BASE TABLE'
        ORDER BY TABLE_SCHEMA, TABLE_NAME
        """
    )
    return [(r[0], r[1]) for r in cur.fetchall()]


def describe_table(cur, schema: str, table: str) -> None:
    print(f"\n=== {schema}.{table} ===")
    cur.execute(
        """
        SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
        ORDER BY ORDINAL_POSITION
        """,
        schema,
        table,
    )
    cols = cur.fetchall()
    width = max((len(c[0]) for c in cols), default=0)
    for name, dtype, maxlen, nullable in cols:
        tail = f"({maxlen})" if maxlen and maxlen > 0 else ""
        null = "NULL" if nullable == "YES" else "NOT NULL"
        print(f"  {name:<{width}}  {dtype}{tail:<6}  {null}")

    try:
        cur.execute(f"SELECT TOP {SAMPLE_ROWS} * FROM [{schema}].[{table}]")
        headers = [d[0] for d in cur.description]
        rows = cur.fetchall()
    except pyodbc.Error as e:
        print(f"  (sample query failed: {e.args[0] if e.args else e})")
        return

    if not rows:
        print("  (table is empty)")
        return
    print(f"  sample ({len(rows)} rows):")
    for r in rows:
        cells = []
        for h, v in zip(headers, r):
            s = "NULL" if v is None else str(v)
            if len(s) > CELL_MAX:
                s = s[: CELL_MAX - 1] + "…"
            cells.append(f"{h}={s}")
        print("    " + " | ".join(cells))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--table",
        help="Only inspect this one table (e.g. dbo.Rosters). Default: every table.",
    )
    args = ap.parse_args()

    try:
        cnx = pyodbc.connect(build_conn_str(), timeout=30)
    except pyodbc.Error as e:
        print(f"Connection failed: {e}", file=sys.stderr)
        print(
            "\nChecks:\n"
            "  1. Is the ODBC Driver 18 for SQL Server installed?\n"
            "  2. Is your Entra account granted access to the DB?\n"
            "     (ask the DB owner to run `CREATE USER [you@...] FROM EXTERNAL PROVIDER;`\n"
            "      and `ALTER ROLE db_datareader ADD MEMBER [you@...];`)\n"
            "  3. Is your IP whitelisted on the Azure SQL firewall?",
            file=sys.stderr,
        )
        return 2

    with cnx:
        cur = cnx.cursor()
        if args.table:
            schema, _, table = args.table.partition(".")
            if not table:
                schema, table = "dbo", schema
            describe_table(cur, schema, table)
        else:
            tables = list_tables(cur)
            print(f"Found {len(tables)} base tables:")
            for sch, t in tables:
                print(f"  {sch}.{t}")
            for sch, t in tables:
                describe_table(cur, sch, t)

    return 0


if __name__ == "__main__":
    sys.exit(main())
