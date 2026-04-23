# Southwest Shutdowns — Unified Dashboard

Internal-only roll-up of every shutdown SRG Global runs for the three
Southwest clients (Covalent, Tronox, CSBP). The CSBP umbrella covers both
the NAAN2 fertiliser shutdown and the KPF LNG (Kleenheat-branded) March
2026 shutdown — both run by WesCEF. Shows:

- **Fulfillment** — positions required vs. filled, overall and by trade.
  Overstaffing (confirmed > required) is flagged explicitly on the KPI
  tile and the per-shutdown cards rather than clamped to 100%.
- **Retention / carry-over** — for each shutdown, how much of the roster
  is workers returning from the **same company**, workers returning from
  **a different client in the SRG pool**, and **new hires**.
- **Gantt schedule** — swimlane view of every shutdown (completed and
  booked) with a "today" marker and fill shading.
- **Operations roster** — per-worker timeline across every site, with
  click-to-call mobiles and (when configured) click-to-open resumes.

**Source of truth is Rapid Crews.** `Rapidcrews Macro Data.xlsx` at the
repo root drives required, filled, roster and position titles.
`data/targets/` is fallback-only for shutdowns Rapid Crews doesn't cover
(e.g. historical Pegasus rosters). Shutdowns that roll off the live SQL
view are re-hydrated from `data/history/` automatically.

Static site, no server. Each source dashboard pushes its canonical data
into this repo as JSON; this page re-reads on load.

## Layout

```
index.html                       unified dashboard
assets/app.js                    load → normalise → compute → render
assets/styles.css
Rapidcrews Macro Data.xlsx       Rapid Crews SQL export (authoritative)
                                 sheets: xpbi02 JobPlanningView,
                                         xpbi02 PersonnelRosterView,
                                         xpbi02 DisciplineTrade,
                                         xll01 Personnel,
                                         ACTIVE_SHUTDOWNS (control sheet)
Resumes.xlsx                     standalone resume link index
                                 columns: Name | Personnel Id | Role |
                                          Mobile | Resume URL | Updated | Notes
data/
  covalent.json                  per-company dashboard payload (generated)
  tronox.json
  csbp.json
  resumes.json                   consolidated {name: resume_url} (generated)
  schema.md                      JSON contract documented
  raw/                           Rapid Crews "RosterCut" XLSX exports +
                                 historic Pegasus rosters
  targets/                       optional {role: required} overrides (only
                                 consulted when Rapid Crews has nothing)
  imports/                       full planned roster snapshots from each
                                 site dashboard (provenance only)
  history/                       per-shutdown snapshots that outlive the
                                 live SQL view — restored automatically
                                 when a JobNo rolls off Rapid Crews
scripts/
  parse_rapidcrews.py            full pipeline: raw/*.xlsx + macro data
                                 + history restore → data/*.json + resumes.json
  parse_macro_data.py            Rapid Crews macro-workbook reader (shared)
  sync_source_targets.py         OPTIONAL: pull per-site dashboard targets
                                 (no longer in the default workflow)
  sync_sharepoint.py             OPTIONAL: pull fresh XLSX drops from SharePoint
```

## Run locally

```sh
python3 -m http.server 8000
# browse http://localhost:8000/
```

Chart.js + Google Fonts (Barlow Condensed / Bebas Neue) are loaded via CDN; no build step.

## Updating data

### Adding or removing shutdowns (end users, no code required)

Open `Rapidcrews Macro Data.xlsx` → go to the `ACTIVE_SHUTDOWNS` sheet → add
or delete rows in the **JobNo** column. One JobNo per row = one shutdown on
the dashboard. Save, and once the updated workbook lands in the repo the
GitHub Action regenerates the JSONs and the dashboard refreshes on next
page-load.

Rules of thumb:

- **Sheet missing or empty** — legacy behaviour: every RosterCut file in
  `data/raw/` appears (backwards compatible).
- **Sheet has rows** — it becomes an allow-list. Only shutdowns whose JobNo
  is listed show up. Historical retention seeds (Kleenheat) always pass.
- When a JobNo is in both a RosterCut file (`data/raw/`) and the macro
  workbook's `PersonnelRosterView`, the RosterCut data wins on that
  shutdown — it carries Position-On-Project, Confirmed flag, and Crew Type
  which the macro export doesn't have.
- For a brand-new shutdown that doesn't have a RosterCut file, the parser
  builds it straight from `xpbi02 JobPlanningView` + `xpbi02 PersonnelRosterView`.
  Per-worker `role` falls back to the employee's **Primary Role** from the
  Personnel master — close but not identical to the shutdown-specific
  Position-On-Project that RosterCut would give.
- If a JobNo's client/site pair in `PersonnelRosterView` isn't one of
  Covalent Lithium / Tronox / CSBP Kwinana, the parser skips it with a
  warning — update `CLIENT_SITE_MAP` in `scripts/parse_macro_data.py` to
  add a new client.

### Full-fidelity workflow (maintainers — RosterCut exports)

The current source of truth for the three live shutdowns is **Rapid Crews**.
Every refresh follows the same loop:

1. **Export** a roster from Rapid Crews → "RosterCut" → XLSX.
2. **Drop** the file into `data/raw/`. The filename's leading numeric token is
   the Rapid Crews roster id (e.g. `1353`).
3. **Map** that roster id to a client + project + site by adding a line to
   `ROSTER_MAP` in `scripts/parse_rapidcrews.py`.
4. **Run** `python3 scripts/parse_rapidcrews.py` — it regenerates
   `data/<company>.json` from every roster in `data/raw/` (and pulls in any
   active macro-workbook shutdowns that aren't already covered).
5. **Commit** the regenerated JSONs. The dashboard re-reads on every page load,
   so the next refresh picks the change up — no code deploy needed.

### Kleenheat-style rosters (alternate XLSX format)

The parser also accepts a looser spreadsheet schema (columns: `Name`, `Trade`,
`Company`, `On Site`, `Off Site`, `Crew`, `Email`, …), used for the Kleenheat
March 2026 historical roster that seeds retention stats. When the spreadsheet
only carries first names, surnames are reconstructed in priority order:

1. An explicit `Last Dna` / `Last Name` / `Surname` column if populated.
2. The `Email` local-part — e.g. `dackjoe@outlook.com` → `Joe Dack`.
3. Cross-reference against the three Rapid Crews rosters by first-name + role
   (when a Kleenheat "Joe, Intermediate Rigger" has exactly one match in the
   other companies' Intermediate Riggers, the full name from there is copied).

Each row is tagged with its `_name_resolution` (`explicit_column` /
`email_heuristic` / `xref_exact` / `xref_ambiguous` / `unmatched`) and a
roll-up lands in `_source.name_resolution` for ops review.

### Required headcount (Required vs Filled)

Rapid Crews drives `required_by_role` from `xpbi02 JobPlanningView.Required`
and `filled_by_role` from the roster count (either RosterCut rows or
`xpbi02 PersonnelRosterView` unique personnel). Each shutdown's
`_source.required_target_source` records where its number came from:

| Value                       | Meaning |
|-----------------------------|---------|
| `RAPID_CREWS_JOB_PLANNING`  | Required pulled from Rapid Crews JobPlanningView (preferred) |
| `TARGET_FILE`               | Required pulled from `data/targets/<id>.json` — RC had no row |
| `PLACEHOLDER_FROM_ROSTER`   | RC roster only, required defaulted to filled — banner shown |

When filled exceeds required (the team mobilised more people than the plan
requested), the KPI tile shows a green **+N** surplus pill, the overall fill
rate reads above 100%, and the affected shutdowns get an **"Over plan +N"**
header pill. No clamping — the delta matters.

The full planned roster from each source dashboard — names, shifts, trade
groups, shift-days, TBC flags and contingency workforce — is archived raw in
`data/imports/<company>-source.json`. Those files are provenance, not inputs
to the parser. Since Rapid Crews is now authoritative, `sync_source_targets.py`
is no longer in the default workflow — run it manually when you need to
snapshot a per-site dashboard's planned roster to `data/imports/`.

Override per-shutdown by editing the file at:

```
data/targets/<shutdown_id>.json
```

Example (`data/targets/tronox-2026-05.json`):

```json
{
  "Mechanical Fitter": 40,
  "Boilermaker": 10,
  "Coded Welder": 10,
  "Trade Assistant": 20,
  "Advanced Rigger": 10,
  "Intermediate Rigger": 10,
  "Supervisor - Mechanical": 4
}
```

Re-run the parser. The banner disappears for that shutdown and fill-rate
reflects the gap to target.

### Refreshing from the source dashboards (manual only)

`scripts/sync_source_targets.py` fetches each source dashboard's `index.html`,
extracts its planned roster, and writes `data/imports/<company>-source.json`
plus `data/targets/<shutdown_id>.json`. It's no longer wired into the default
GitHub Actions workflow — Rapid Crews wins for live shutdowns, so those
target files would never apply. Run it manually when you want to take a
provenance snapshot of a per-site dashboard's plan, or to seed a target
file for a shutdown that Rapid Crews doesn't cover.

### Resume handover (SharePoint workflow)

`Resumes.xlsx` at the repo root is a standalone workbook owned by ops, not
Rapid Crews. The schema is one row per worker:

| Name | Personnel Id | Role | Mobile | Resume URL | Updated | Notes |

`Resume URL` is typically a SharePoint share link to the candidate's CV
PDF. End-user update flow:

1. Save the latest CV PDF into the SharePoint resume library.
2. Right-click → **Copy link** → share scope is whatever the library is set
   to (ops-only is sensible).
3. Open `Resumes.xlsx`, add a new row (or update an existing one), paste
   the link into `Resume URL`, set `Updated` to today's date. Save.
4. Commit + push the workbook (or rely on the SharePoint → GitHub Power
   Automate flow if that's configured).

The parser ingests `Resumes.xlsx` on the next run and writes
`data/resumes.json`. The dashboard decorates every worker whose name has a
URL on file with a small red **CV** badge in the matrix + ops roster —
click-through opens the PDF in a new tab.

### Historical retention (SQL roll-over safety)

Every parser run snapshots each shutdown to `data/history/<id>.json`. When
Rapid Crews' live SQL view eventually rolls a JobNo off (the view is
time-windowed to ~12 months), the next parser run:

1. Sees the JobNo is in `ACTIVE_SHUTDOWNS` but missing from the live data
   and `data/raw/`.
2. Loads the last-known snapshot from `data/history/`.
3. Re-inserts the shutdown into the output JSON with
   `_source.restored_from_archive = true`.

The dashboard shows an **"Archived"** pill on those tiles so users know
the numbers are frozen, not live. Delete the snapshot from `data/history/`
to retire a shutdown permanently.

## Automation — GitHub Actions

`.github/workflows/refresh-data.yml` runs the full refresh loop without any
manual invocation of the scripts. Three triggers:

| Trigger         | When it fires                                        | Use |
|-----------------|------------------------------------------------------|-----|
| **Push**        | A roster XLSX, target file, import, or script changes on `main` or the default branch | Auto-regenerates `data/*.json` after you upload a new RosterCut |
| **Schedule**    | 22:00 UTC (~06:00 AWST) every day                    | Picks up overnight edits made on any per-site dashboard, even if nothing landed in this repo |
| **Manual**      | "Run workflow" button on the Actions tab             | Force a refresh whenever you like |

Each run:

1. `pip install openpyxl`
2. `python3 scripts/sync_sharepoint.py` — no-op unless the five SHAREPOINT_*
   secrets are configured. When set, pulls fresh rosters into `data/raw/`.
3. `python3 scripts/parse_rapidcrews.py` — parses every XLSX in `data/raw/`,
   consults `Rapidcrews Macro Data.xlsx` (active shutdowns + required
   counts), snapshots history, and emits `data/*.json` + `data/resumes.json`.
4. If any file under `data/` changed, the workflow bumps the `?v=…`
   cache-buster on `index.html` (so iOS Safari refetches the CSS/JS after GH
   Pages redeploys) and commits the lot back with
   `Auto-refresh dashboard data [skip ci]`

The `[skip ci]` in the auto-commit message stops the workflow from
re-triggering itself. `concurrency: refresh-data-${ref}` lets a newer push
queue up behind the current run rather than stepping on it.

### What's still manual

1. Uploading a RosterCut XLSX to `data/raw/` when a new Rapid Crews snapshot
   is available (three options below: GitHub web UI, SharePoint drop-zone, or
   `git add && push`).
2. Editing `data/targets/*.json` or `data/imports/*.json` by hand when the
   per-site dashboard role mapping needs a tweak — everything else is picked
   up by the sync script.

Everything after those two things is automatic.

### Logs and manual runs

Actions tab → "Refresh dashboard data" workflow. Click a run to see the
per-step logs and the summary (which site dashboards were polled, whether
anything changed). Hit "Run workflow" in the top-right for a manual refresh.

## SharePoint drop-zone

The refresh workflow already calls `scripts/sync_sharepoint.py` as its first
step. It's a no-op unless five secrets are configured; once they are, any
`.xlsx` dropped into the configured SharePoint folder is pulled into
`data/raw/` on the next workflow run.

Two ways to wire up the drop-zone — pick one:

### Option A — Power Automate flow (no code, near-instant)

SharePoint drop-zone → Power Automate → commits the XLSX to `data/raw/` on
GitHub → the existing `refresh-data` workflow sees the push and reruns the
whole pipeline. End-to-end latency ~ 60-90 seconds.

#### 1. Create a GitHub personal access token

Needed once, stored in the Power Automate GitHub connection.

1. GitHub → your avatar → **Settings** → **Developer settings** →
   **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
2. **Resource owner**: `N01dea5` · **Repository access**: "Only select
   repositories" → `southwest-shutdowns`.
3. **Permissions → Repository**:
   - *Contents*: **Read and write** (so the flow can commit files)
   - *Metadata*: **Read-only** (auto-enabled)
   - *Actions*: **Read and write** (only if you want the optional
     `workflow_dispatch` step below — otherwise skip)
4. **Expiration**: 90 days is a sensible default; set a calendar reminder
   to rotate.
5. **Generate token** → copy the value. You will not see it again.

#### 2. Build the flow

1. **Power Automate** → **My flows** → **New flow** → **Automated cloud flow**.
2. Name it "Southwest shutdowns — roster drop". Trigger: **When a file is
   created (properties only)** (SharePoint connector).
3. Trigger config:
   - **Site Address**: the SharePoint site hosting the drop folder.
   - **Library Name**: the document library (usually "Documents").
   - **Folder**: the drop-zone subfolder (e.g. `/Rosters`). Leave blank to
     watch the library root.
4. **+ New step → Condition**:
   - Left: `ends with` — drag in **File name with extension** from the
     trigger's dynamic content.
   - Operator: `ends with`
   - Right: `.xlsx`
   - This filters out Office temp files and anything that isn't a roster
     export.
5. Under **If yes**: **+ Add an action → SharePoint → Get file content**.
   - **Site Address**: same site.
   - **File Identifier**: select **Identifier** from the trigger's dynamic
     content.
6. **+ Add an action → GitHub → Create or update file contents**
   (connection: sign in with your GitHub PAT when prompted).
   - **Repository Owner**: `N01dea5`
   - **Repository**: `southwest-shutdowns`
   - **Branch**: `main`
   - **File Path**: expression
     ```
     concat('data/raw/', triggerOutputs()?['body/{FilenameWithExtension}'])
     ```
   - **Commit Message**: expression
     ```
     concat('roster: drop ', triggerOutputs()?['body/{FilenameWithExtension}'])
     ```
   - **File Content**: **File Content** from the "Get file content" step's
     output (the connector handles base64 encoding internally).
7. **Save**.

That's the minimum. The push to `data/raw/` hits the existing `on: push`
trigger in `refresh-data.yml`, which re-parses and commits the regenerated
`data/*.json` back to the same branch. GH Pages rebuilds and the dashboard
picks up the new cache-buster.

#### 3. (Optional) Kick the workflow manually for instant feedback

Useful when the push event is rate-limited or delayed. Add one more step
after **Create or update file contents**:

- **+ Add an action → HTTP**:
  - Method: `POST`
  - URI: `https://api.github.com/repos/N01dea5/southwest-shutdowns/actions/workflows/refresh-data.yml/dispatches`
  - Headers:
    ```
    Authorization: Bearer <same PAT as above>
    Accept:        application/vnd.github+json
    Content-Type:  application/json
    ```
  - Body: `{ "ref": "main" }`

Drop the PAT into a Power Automate connection (**Settings → Connections**)
rather than hard-coding it in the step, so it doesn't show up in run history.

#### 4. Test it

1. Drop a file named `9999 (RosterCut) 2026-04-14_18-00-00.xlsx` into the
   SharePoint folder (use a copy of an existing roster).
2. Flow run should appear green within a minute.
3. GitHub → **Actions** tab → the "Refresh dashboard data" run should follow
   seconds later, parse the file (skip-unmapped for unknown roster ids, or
   parse normally if it's a mapped id), and commit the regenerated JSONs.
4. Remove the test file from SharePoint and the repo when done.

### Option B — GitHub Actions + Microsoft Graph (code-first)

Keeps the entire pipeline inside this repo; no Power Automate needed.

1. **Azure AD (Entra ID)** → register a new application.
2. Grant it the **application** permission `Files.Read.All` (or the narrower
   `Sites.Selected` with admin-consented read on the one site). Admin consent
   required.
3. Create a client secret; copy the value.
4. **Repo → Settings → Secrets and variables → Actions** — add:

   | Secret | Value |
   |---|---|
   | `SHAREPOINT_TENANT_ID` | Directory (tenant) ID |
   | `SHAREPOINT_CLIENT_ID` | Application (client) ID |
   | `SHAREPOINT_CLIENT_SECRET` | client secret value |
   | `SHAREPOINT_SITE` | `<tenant>.sharepoint.com:/sites/<site-name>` |
   | `SHAREPOINT_FOLDER` | path to the drop-zone inside the site's drive, e.g. `Shared Documents/Rosters` |

5. The scheduled cron in `refresh-data.yml` now picks up any new XLSX inside
   the folder on each run (nightly 22:00 UTC by default). Bump the cron to
   `*/30 * * * *` or similar if you want sub-hourly polling.

No secrets set → the SharePoint step logs `SharePoint sync skipped — no
secrets configured` and the workflow continues as normal. Safe to enable
later without touching the workflow file.

## Retention semantics

No stable employee IDs exist in the source data, so matching is on a normalised `name + role` key (lowercased, punctuation stripped, whitespace collapsed). Two retention views are shown side-by-side:

- **Same-company retention** — for each shutdown, share of its roster who were also on that company's previous shutdown (chronological). Measures site loyalty.
- **Cross-company carry-over** — for each shutdown, share of its roster who appeared on *any* prior shutdown at *any* of the three companies. Measures regional workforce stickiness and is mathematically ≥ same-company retention.

A "new hires" column on the retention table equals `roster − cross-company returning`.

A data-quality panel flags cases where the same normalised name+role is on two companies' rosters with overlapping dates — almost certainly two different people, surfaced for ops review.
