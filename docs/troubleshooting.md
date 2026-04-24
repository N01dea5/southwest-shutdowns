# Troubleshooting

## Public dashboard is blank or not loading

1. Check the browser hard refresh first.
   - Windows: `Ctrl + F5`
   - macOS: `Cmd + Shift + R`

2. Check GitHub Pages source.
   - Repository → Settings → Pages
   - Source should be `Deploy from a branch`
   - Branch should be `main`
   - Folder should be `/root`

3. Check whether the latest commit changed `index.html` cache-busters.
   - `assets/app.js?v=...`
   - `assets/styles.css?v=...`

4. Check JSON validity.
   - Run `python3 scripts/validate_dashboard_data.py`
   - If validation fails, fix the data issue before checking the browser.

5. Temporarily disable non-core enhancements if needed.
   - Core app: `assets/app.js`
   - Safe optional enhancers: `assets/matrix-hiring-company-safe.js`, `assets/refresh-status.js`
   - The dashboard should still render if optional enhancers fail.

## GitHub updates but dashboard does not change

1. Confirm the update landed on `main`.
2. Confirm Pages is serving `main / root`.
3. Confirm the refresh workflow made an `Auto-refresh dashboard data` commit.
4. Confirm the relevant generated file changed:
   - `data/covalent.json`
   - `data/tronox.json`
   - `data/csbp.json`
5. Confirm the shutdown exists inside the relevant JSON.
6. Wait for GitHub Pages deployment, then hard refresh.

## New shutdown does not appear

Check these in order:

1. `ACTIVE_SHUTDOWNS` contains the JobNo.
2. `xpbi02 JobPlanningView` has planning rows for the JobNo.
3. `xpbi02 PersonnelRosterView` has roster/schedule rows for the JobNo.
4. The Client/Site values are mapped by the parser.
5. `scripts/ensure_active_shutdowns.py` ran in the workflow.
6. The output file contains the new shutdown ID.

For unmapped Client/Site values, update the client mapping logic in:

```text
scripts/parse_macro_data.py
scripts/ensure_active_shutdowns.py
```

## Hiring Company column is missing

1. Confirm `hire_company` is present in `data/*.json` roster entries.
2. Confirm `assets/matrix-hiring-company-safe.js` is loaded in `index.html`.
3. Confirm the worker matrix has rendered before checking the column.
4. Hard refresh the browser.

The Hiring Company column is intentionally added after the main render so it cannot block the dashboard from loading.

## Workflow fails

1. Open GitHub → Actions → Refresh dashboard data.
2. Open the failed run.
3. Check the failed step.
4. Common failure points:
   - Workbook unreadable or corrupted.
   - Missing/renamed RapidCrews worksheet.
   - Invalid JSON generated.
   - Role key mismatch not fixed by normalisation.

The workflow validates generated dashboard data before committing. A validation failure is preferable to deploying a blank dashboard.

## Power Automate push is working but workflow does not run

Check the Power Automate commit path. The GitHub Action only runs when these paths change:

```text
data/raw/**
data/targets/**
data/imports/**
Resumes.xlsx
```

If Power Automate pushes somewhere else, the workflow will not trigger.

## Default branch warning

The live branch should be `main`. If the repository default branch still points to an old Claude branch, change it manually:

```text
Repository → Settings → Branches → Default branch → main
```

This avoids future PRs, searches and GitHub UI actions targeting the wrong branch.
