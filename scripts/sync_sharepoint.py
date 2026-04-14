#!/usr/bin/env python3
"""Pull any new roster XLSX files from a SharePoint folder into data/raw/.

Uses the Microsoft Graph API with client-credentials (app-only) auth — no
interactive login needed, so this runs cleanly from a scheduled GitHub Action.

Setup (one-time)
----------------
1. In Azure AD (Entra ID), register a new application.
2. Grant it application permission: `Files.Read.All` (or `Sites.Read.All` if
   you want it scoped to a single site — more narrow). Admin consent required.
3. Create a client secret; copy the value immediately (only shown once).
4. Add these five values to the repo's GitHub Secrets
   (Settings -> Secrets and variables -> Actions):

     SHAREPOINT_TENANT_ID      # Directory (tenant) ID
     SHAREPOINT_CLIENT_ID      # Application (client) ID
     SHAREPOINT_CLIENT_SECRET  # client secret value
     SHAREPOINT_SITE           # "<tenant>.sharepoint.com:/sites/<site-name>"
     SHAREPOINT_FOLDER         # "Shared Documents/Rosters" (path inside the site's drive)

5. The refresh-data workflow's "Pull new rosters from SharePoint" step runs
   this script before the parser, so any .xlsx files dropped into that
   SharePoint folder land in data/raw/ and feed straight into the pipeline.

The script is idempotent: it skips files already present locally with the
same size, so re-runs cost only a directory listing.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.parse
import urllib.request


GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
REPO_ROOT  = pathlib.Path(__file__).resolve().parent.parent
RAW_DIR    = REPO_ROOT / "data" / "raw"


def _required_env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        print(f"Missing required env var: {name}", file=sys.stderr)
        sys.exit(78)   # 78 = "skip" in some CI conventions; keeps workflow green
    return v


def _token(tenant: str, client_id: str, client_secret: str) -> str:
    payload = urllib.parse.urlencode({
        "client_id":     client_id,
        "client_secret": client_secret,
        "scope":         "https://graph.microsoft.com/.default",
        "grant_type":    "client_credentials",
    }).encode()
    req = urllib.request.Request(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["access_token"]


def _get_json(path: str, token: str) -> dict:
    req = urllib.request.Request(
        f"{GRAPH_ROOT}{path}",
        headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def _download(url: str, dest: pathlib.Path, token: str) -> int:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=180) as r:
        body = r.read()
    dest.write_bytes(body)
    return len(body)


def main() -> int:
    # Gate on presence of the env vars — the workflow runs this unconditionally
    # but we want to no-op (not fail) when nobody's configured SharePoint yet.
    required = ["SHAREPOINT_TENANT_ID", "SHAREPOINT_CLIENT_ID", "SHAREPOINT_CLIENT_SECRET",
                "SHAREPOINT_SITE", "SHAREPOINT_FOLDER"]
    missing = [k for k in required if not os.environ.get(k, "").strip()]
    if missing:
        print(f"SharePoint sync skipped — no secrets configured ({', '.join(missing)}).")
        return 0

    tenant        = os.environ["SHAREPOINT_TENANT_ID"].strip()
    client_id     = os.environ["SHAREPOINT_CLIENT_ID"].strip()
    client_secret = os.environ["SHAREPOINT_CLIENT_SECRET"].strip()
    site_path     = os.environ["SHAREPOINT_SITE"].strip()
    folder        = os.environ["SHAREPOINT_FOLDER"].strip().strip("/")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Authenticating against tenant {tenant[:8]}...")
    token = _token(tenant, client_id, client_secret)

    site = _get_json(f"/sites/{urllib.parse.quote(site_path, safe=':/.-_')}", token)
    site_id = site["id"]
    print(f"Resolved site id: {site_id.split(',')[0]}...")

    listing = _get_json(
        f"/sites/{site_id}/drive/root:/{urllib.parse.quote(folder)}:/children",
        token)

    pulled = skipped = 0
    for item in listing.get("value", []):
        if "file" not in item:        # skip subfolders
            continue
        name = item["name"]
        if not name.lower().endswith(".xlsx"):
            continue
        remote_size = int(item.get("size") or 0)
        dest = RAW_DIR / name
        if dest.exists() and dest.stat().st_size == remote_size:
            skipped += 1
            continue
        download_url = item.get("@microsoft.graph.downloadUrl")
        if not download_url:
            print(f"  ! {name}: no downloadUrl, skipping")
            continue
        n = _download(download_url, dest, token)
        print(f"  pulled {name} ({n:,} bytes)")
        pulled += 1

    print(f"SharePoint sync: {pulled} new · {skipped} already present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
