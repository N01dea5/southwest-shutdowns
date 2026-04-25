# Southwest Shutdowns Dashboard

Static GitHub Pages dashboard for SRG Global Southwest shutdown fulfilment, retention and operations roster visibility.

The live branch is **`main`** and GitHub Pages serves from **`main / root`**. Power Automate pushes the latest SharePoint workbook into this branch and the GitHub Action regenerates the dashboard JSON.

## What the dashboard shows

- **Required / Filled positions** by shutdown, company and trade.
- **Retention and carry-over** across the Southwest labour pool.
- **Shutdown schedule** as a Gantt-style timeline.
- **Shutdown detail** cards with required, filled, gap and over-plan flags.
- **Worker retention matrix** — ✓ rostered, ✗ rejected/unavailable, blank = no clash identified. Includes Hiring Company where available.
- **Operations roster** showing where each worker is scheduled by day.
- **Client dashboards** — sanitised per-client JSON feeds for Tronox and CSBP (no mobile numbers, no labour-hire flags).
- **Last refreshed** timestamp from the generated JSON payloads.

## Source of truth

Primary source:

```text
data/raw/Rapidcrews Macro Data.xlsx
```

Key sheets:

```text
ACTIVE_SHUTDOWNS                  control list of JobNo values to show
xpbi02 JobPlanningView            required / filled planning demand
xpbi02 PersonnelRosterView        worker schedule, client, site, dates
xpbi02 DailyPersonnelSchedule     per-day status rows (used for rejected/declined detection)
xpbi02 PersonnelCalendarView      availability calendar (other bookings, time off)
xpbi02 ClientView                 client name lookup
xpbi02 DisciplineTrade            trade lookup
xll01 Personnel                   personnel master, mobile, hiring company
```

Supporting sources:

```text
Resumes.xlsx                      optional resume link index
data/enrichment/covalent.json     hand-authored enrichment (CV summaries, years experience)
data/enrichment/tronox.json       hand-authored enrichment
```

Generated outputs:

```text
data/covalent.json
data/tronox.json
data/csbp.json
data/resumes.json
data/personnel_calendar.json
data/client/tronox-major-2026.json
data/client/csbp-naan2-2026.json
data/audit/rapidcrews_workbook_schema.json
data/audit/rapidcrews_workbook_schema.md
data/history/*.json
```

## Repository layout

```text
index.html

assets/
  app.js                              main dashboard renderer and worker matrix
  styles.css                          base SRG palette and layout
  dashboard-polish.css                final presentation polish
  executive-redesign.css              executive dashboard layout
  executive-hero.js                   executive hero metadata strip
  retention-table-executive.js        executive retention table formatter
  retention-chart-executive.js        executive retention chart formatter
  matrix-availability.js              availability overlay — marks workers ✗ when
                                        unavailable (other booking, time off, rejected)
  matrix-hiring-company-safe.js       decorates worker matrix with Hiring Company
  matrix-cross-filter.js              legacy — inert with current button-based filter
  refresh-status.js                   last refreshed timestamp display
  client-dashboard.css                shared stylesheet for client dashboard pages
  client-dashboard.js                 shared renderer for client dashboard pages
  srg-global-logo.png

scripts/
  build_dashboard_data.py             single supported build entrypoint (runs all steps below)
  normalise_rapidcrews_workbook.py    normalises workbook column names before parsing
  audit_rapidcrews_workbook.py        captures workbook schema to data/audit/ for debugging
  parse_rapidcrews.py                 core parser — RosterCut XLSXs + macro workbook
  ensure_active_shutdowns.py          creates safe dashboard cards for JobNos with
                                        planning demand but no RosterCut export
  apply_hiring_company.py             writes hire_company onto roster entries
  apply_personnel_calendar.py         writes data/personnel_calendar.json from
                                        PersonnelCalendarView (other bookings, time off)
  apply_rejected_shutdowns.py         appends rejected/declined rows from
                                        DailyPersonnelSchedule to personnel_calendar.json
  merge_macro_roster_additions.py     appends live PersonnelRosterView workers missing
                                        from RosterCut exports for active JobNos
  apply_shutdown_display_labels.py    writes display labels (JobNo – description)
  normalise_dashboard_data.py         normalises JSON shape for browser safety
  validate_dashboard_data.py          fails the build if JSON shape is invalid
  export_tronox_client_dashboard.py   emits sanitised data/client/tronox-major-2026.json
  export_csbp_client_dashboard.py     emits sanitised data/client/csbp-naan2-2026.json
  apply_shutdown_display_labels.py    applies JobNo – description labels
  sync_sharepoint.py                  optional Graph-based SharePoint pull
  sync_source_targets.py              syncs required headcount from per-site dashboards

data/
  raw/                                source workbooks pushed by Power Automate
    Rapidcrews Macro Data.xlsx        primary source workbook
    <JobNo> (RosterCut) <ts>.xlsx     individual RosterCut exports
    Kleenheat Major March 2026.xlsx   historical Kleenheat roster
    <JobNo> <description>.xlsx        planning-only roster sheets
  targets/                            fallback required-headcount overrides
  enrichment/                         hand-authored per-company enrichment data
  imports/                            provenance snapshots only
  history/                            archived shutdown snapshots
  audit/                              workbook schema audit outputs
  client/                             sanitised client-facing dashboard JSON feeds

docs/
  troubleshooting.md

.github/workflows/
  refresh-data.yml                    automated refresh workflow
```

## Build pipeline

`scripts/build_dashboard_data.py` runs these steps in order:

```text
1.  normalise_rapidcrews_workbook.py    column-name normalisation
2.  audit_rapidcrews_workbook.py        schema capture (non-blocking)
3.  parse_rapidcrews.py                 generate data/{covalent,tronox,csbp}.json
4.  ensure_active_shutdowns.py          fill gaps for demand-only JobNos
5.  apply_hiring_company.py             enrich roster with hire_company
6.  apply_personnel_calendar.py         generate data/personnel_calendar.json
7.  apply_rejected_shutdowns.py         append rejected/declined events to calendar
8.  merge_macro_roster_additions.py     add live macro workers missing from RosterCut
9.  apply_shutdown_display_labels.py    apply display headings
10. normalise_dashboard_data.py         safety normalisation pass
11. validate_dashboard_data.py          fail fast if shape is invalid
12. export_tronox_client_dashboard.py   emit sanitised Tronox client feed
13. export_csbp_client_dashboard.py     emit sanitised CSBP client feed
```

## Normal refresh flow

```text
SharePoint workbook saved
  -> Power Automate commits data/raw/Rapidcrews Macro Data.xlsx to main
  -> GitHub Action runs refresh-data.yml
  -> scripts/sync_sharepoint.py pulls from SharePoint (if secrets configured)
  -> scripts/build_dashboard_data.py runs all 13 pipeline steps
  -> generated data/*.json committed if changed
  -> index.html cache-buster bumped
  -> GitHub Pages redeploys
```

The workflow skips its own bot commits to prevent loops while still triggering Pages deployment.

## Running locally

```sh
pip install openpyxl
python3 scripts/build_dashboard_data.py
python3 -m http.server 8000
```

Open `http://localhost:8000/`

## Adding a shutdown

1. Open `data/raw/Rapidcrews Macro Data.xlsx` in SharePoint.
2. Add the RapidCrews **JobNo** to `ACTIVE_SHUTDOWNS`.
3. Confirm the JobNo has rows in `xpbi02 JobPlanningView` and `xpbi02 PersonnelRosterView`.
4. Save the workbook — Power Automate will push it and trigger the refresh.

If a shutdown has planning demand but no RosterCut export, `ensure_active_shutdowns.py` creates a safe card from the macro workbook alone.

## Worker matrix — availability overlay

The matrix cells have three states:

| Symbol | Meaning | Source |
|--------|---------|--------|
| ✓ green | Worker is rostered on that shutdown | `app.js` from roster data |
| ✗ red (highlighted) | Worker is unavailable — another SRG booking, booked time off, or rejected/declined that shutdown | `matrix-availability.js` reading `data/personnel_calendar.json` |
| blank | No clash identified | — |

The availability overlay runs asynchronously after the base table renders. The per-column filter cycles **Any → ✓ only → ✗ only** — the ✗ filter is DOM-based and applies in repeated passes as the overlay settles.

`matrix-cross-filter.js` is an older companion script that is currently **inert** — it targets `<select>` elements but `app.js` uses `<button>` elements.

## Client dashboards

Sanitised per-client JSON feeds are exported to `data/client/` for use by the Tronox and CSBP per-site dashboard repositories. These feeds:

- Show same-client retention and SRG carry-over.
- Expose new-hire flags for buddy-system planning.
- Strip mobile numbers, personnel IDs and labour-hire details.

The feeds are updated automatically on every refresh. Per-site dashboards pull them via GitHub Pages.

## Naming and headings

Display headings follow `<JobNo> – <description>`. Known fallback labels live in `JOB_DESCRIPTION_OVERRIDES` inside `scripts/apply_shutdown_display_labels.py`. Add overrides there if the workbook does not expose a clean description.

## Hiring Company

Applied in two layers:

1. `scripts/apply_hiring_company.py` writes `hire_company` onto roster entries in `data/*.json`.
2. `assets/matrix-hiring-company-safe.js` decorates the worker retention matrix after render.

The enhancement is non-blocking — if it fails the core dashboard still loads.

## Data validation

`scripts/validate_dashboard_data.py` checks:

- JSON parses cleanly.
- Each company file has `company` and `shutdowns`.
- Each shutdown has ID, name, site, dates and status.
- `required_by_role` and `filled_by_role` are role-to-integer maps with matching keys.
- `roster` is an array of objects with name and role.

## Manual refresh

**GitHub → Actions → Refresh dashboard data → Run workflow**

Use after script changes or when Power Automate has pushed data but the dashboard has not updated.

## Troubleshooting

See `docs/troubleshooting.md`. Common causes:

- Power Automate pushed to the wrong branch.
- GitHub Pages has not redeployed yet (allow ~2 min).
- The workbook saved but no data changed under `data/*.json`.
- A new JobNo is missing from `ACTIVE_SHUTDOWNS`.
- A new JobNo has unmapped Client/Site text — check `apply_shutdown_display_labels.py`.
- JSON validation failed — check the Actions log.
- `xpbi02 PersonnelCalendarView` or `xpbi02 DailyPersonnelSchedule` column names changed — check `data/audit/rapidcrews_workbook_schema.md` and update the alias lists in the relevant scripts.

## Maintenance notes

- Keep `main` as the live branch; GitHub Pages must point at `main / root`.
- Source workbook pushes should only touch `data/raw/**` and `Resumes.xlsx`.
- Avoid committing Office lock files (`~$*.xlsx`).
- Do not re-enable `assets/matrix-hiring-company.js`; the safe version is `assets/matrix-hiring-company-safe.js`.
- Periodically review `data/raw/`, `data/imports/` and `data/history/` for stale files.
- `data/enrichment/` is hand-maintained — update when worker CVs or experience summaries change.
