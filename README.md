# Southwest Shutdowns Dashboard

Static GitHub Pages dashboard for SRG Global Southwest shutdown fulfilment, retention and operations roster visibility.

The live branch is **`main`** and GitHub Pages should serve from **`main / root`**. Power Automate pushes the latest SharePoint workbook into this branch and the GitHub Action regenerates the dashboard JSON.

> Repository setting still to confirm manually: change the default branch to `main` if it is still pointing at an old Claude branch.

## What the dashboard shows

- **Required / Filled positions** by shutdown, company and trade.
- **Retention and carry-over** across the Southwest labour pool.
- **Shutdown schedule** as a Gantt-style timeline.
- **Shutdown detail** cards with required, filled, gap and over-plan flags.
- **Worker retention matrix**, including Hiring Company where available.
- **Operations roster** showing where each worker is scheduled by day.
- **Last refreshed** timestamp from the generated JSON payloads.

## Source of truth

Primary source is:

```text
 data/raw/Rapidcrews Macro Data.xlsx
```

Key sheets:

```text
ACTIVE_SHUTDOWNS              control list of JobNo values to show
xpbi02 JobPlanningView        required / filled planning demand
xpbi02 PersonnelRosterView    worker schedule, client, site, dates
xpbi02 DisciplineTrade        trade lookup
xll01 Personnel               personnel master, mobile, hiring company
```

Supporting source:

```text
Resumes.xlsx                  optional resume link index
```

Generated outputs:

```text
data/covalent.json
data/tronox.json
data/csbp.json
data/resumes.json
data/history/*.json
```

## Current repository layout

```text
index.html
assets/
  app.js                              main dashboard renderer
  styles.css                          base SRG styling
  dashboard-polish.css                final presentation polish
  matrix-hiring-company-safe.js       safe matrix Hiring Company enhancer
  refresh-status.js                   last refreshed display
scripts/
  build_dashboard_data.py             single supported build entrypoint
  parse_rapidcrews.py                 core RapidCrews and roster parser
  parse_macro_data.py                 macro workbook reader
  ensure_active_shutdowns.py          emits active JobNo cards if parser misses them
  apply_hiring_company.py             adds hire_company to roster entries
  apply_shutdown_display_labels.py    applies JobNo - description labels
  normalise_dashboard_data.py         normalises JSON shape for browser safety
  validate_dashboard_data.py          fails refresh if JSON shape is invalid
  sync_sharepoint.py                  optional Graph-based SharePoint pull
data/
  raw/                                source workbooks pushed by Power Automate
  targets/                            fallback required-headcount overrides
  imports/                            provenance snapshots only
  history/                            archived shutdown snapshots
.github/workflows/
  refresh-data.yml                    automated refresh workflow
```

## Normal refresh flow

```text
SharePoint workbook saved
  -> Power Automate commits data/raw/Rapidcrews Macro Data.xlsx to main
  -> GitHub Action runs refresh-data.yml
  -> scripts/build_dashboard_data.py runs the full data build
  -> generated data/*.json is committed if changed
  -> index.html cache-buster is bumped
  -> GitHub Pages redeploys
```

The workflow intentionally does **not** use `[skip ci]`; the job itself ignores its own bot commits to prevent loops while still allowing Pages to deploy.

## Running locally

```sh
pip install openpyxl
python3 scripts/build_dashboard_data.py
python3 -m http.server 8000
```

Open:

```text
http://localhost:8000/
```

## Adding a shutdown

1. Open `data/raw/Rapidcrews Macro Data.xlsx` in SharePoint.
2. Add the RapidCrews **JobNo** to `ACTIVE_SHUTDOWNS`.
3. Confirm the JobNo has rows in `xpbi02 JobPlanningView` and `xpbi02 PersonnelRosterView`.
4. Save the workbook.
5. Power Automate pushes the workbook to GitHub.
6. The dashboard refresh workflow regenerates JSON.

If a shutdown has planning demand but no rich RosterCut export, `ensure_active_shutdowns.py` creates a safe dashboard card from the macro workbook.

## Naming and headings

Dashboard headings are applied by `scripts/apply_shutdown_display_labels.py` and should follow:

```text
<RapidCrews JobNo> – <RapidCrews description>
```

Known fallback labels are stored in `JOB_DESCRIPTION_OVERRIDES` inside that script. Add new overrides there if the workbook does not expose a clean description.

## Hiring Company

Hiring Company is applied in two layers:

1. `scripts/apply_hiring_company.py` writes `hire_company` onto roster entries in `data/*.json`.
2. `assets/matrix-hiring-company-safe.js` decorates the worker retention matrix after the main dashboard has rendered.

The enhancement is deliberately non-blocking. If it fails, the core dashboard still loads.

## Data validation

The workflow runs:

```sh
python3 scripts/validate_dashboard_data.py
```

It validates:

- JSON parses cleanly.
- Each company file has `company` and `shutdowns`.
- Each shutdown has ID, name, site, dates and status.
- `required_by_role` and `filled_by_role` are role-to-integer maps.
- Role keys match between required and filled maps.
- `roster` is an array.
- Worker rows have name and role.

## Manual refresh

Use:

```text
GitHub -> Actions -> Refresh dashboard data -> Run workflow
```

Use this after script changes or when Power Automate has pushed data but the dashboard does not yet show it.

## Troubleshooting

See:

```text
docs/troubleshooting.md
```

Most failures are one of:

- Power Automate pushed to the wrong branch.
- GitHub Pages has not redeployed yet.
- The workbook saved but no data changed under `data/*.json`.
- A new JobNo is missing from `ACTIVE_SHUTDOWNS`.
- A new JobNo has unmapped Client/Site text.
- JSON validation failed.

## Maintenance notes

- Keep `main` as the live branch.
- Keep GitHub Pages pointed at `main / root`.
- Keep source workbook pushes limited to `data/raw/**` and `Resumes.xlsx`.
- Avoid committing Office lock files such as `~$*.xlsx`.
- Do not re-enable the old unsafe `assets/matrix-hiring-company.js`; the safe version is `assets/matrix-hiring-company-safe.js`.
- The repo still contains generated data and workbook history. Periodically review `data/raw/`, `data/imports/` and `data/history/` for stale files.
