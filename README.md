# Southwest Shutdowns — Unified Dashboard

Internal-only roll-up of the three site dashboards (Covalent, Tronox, CSBP) plus the Kleenheat March 2026 shutdown (historical, seeded purely for cross-shutdown retention stats). Shows:

- **Fulfillment** — positions required vs. filled, overall and by trade, for completed shutdowns.
- **Booked positions** — aggregate confirmed-vs-target headcount for upcoming shutdowns.
- **Retention / carry-over** — how many workers return on the next shutdown, both at the **same company** and **across any of the three companies**.
- **Gantt schedule** — swimlane view of every shutdown (completed and booked) with a "today" marker and fill shading.

Static site, no server. Each source dashboard pushes its canonical data into this repo as JSON; this page re-reads on load.

## Layout

```
index.html                       unified dashboard
assets/app.js                    load → normalise → compute → render
assets/styles.css
data/
  kleenheat.json                 historical shutdown (retention seed only)
  covalent.json                  source-of-truth data per client
  tronox.json
  csbp.json
  schema.md                      JSON contract documented
  raw/                           Rapid Crews "RosterCut" XLSX exports (one per shutdown)
  targets/                       per-shutdown {role: required_headcount} overrides
                                 (synced from each site's source dashboard repo)
  imports/                       raw planned-roster extracts from each site dashboard
                                 (full names, roles, groups, shifts, TBC flags, contingency)
scripts/
  parse_rapidcrews.py            converts data/raw/*.xlsx → data/<company>.json
```

## Run locally

```sh
python3 -m http.server 8000
# browse http://localhost:8000/
```

Chart.js + Google Fonts (Barlow Condensed / Bebas Neue) are loaded via CDN; no build step.

## Updating data

The current source of truth is **Rapid Crews**. Every refresh follows the same
loop:

1. **Export** a roster from Rapid Crews → "RosterCut" → XLSX.
2. **Drop** the file into `data/raw/`. The filename's leading numeric token is
   the Rapid Crews roster id (e.g. `1353`).
3. **Map** that roster id to a client + project + site by adding a line to
   `ROSTER_MAP` in `scripts/parse_rapidcrews.py`.
4. **Run** `python3 scripts/parse_rapidcrews.py` — it regenerates
   `data/<company>.json` from every roster in `data/raw/`.
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

### Real headcount targets

The Rapid Crews roster export only carries *confirmed* heads, not the
*requested* target. If a shutdown has no target file, the dashboard runs with
`required_by_role = filled_by_role` (so fill rate trivially reads 100%) and
shows a banner saying as much.

Targets for the three current shutdowns are now synced from each site's own
SRG Global dashboard repo:

- `data/targets/covalent-2026-04.json` — from
  [N01dea5/Covalent-Mt-Holland---April-2026][covalent-src] (63 planned)
- `data/targets/tronox-2026-05.json` — from
  [N01dea5/tronox-major-shutdown-may-2026][tronox-src] (104 planned)
- `data/targets/csbp-2026-05.json` — from
  [N01dea5/csbp-naan2-shutdown-workforce-dashboard][csbp-src] (36 planned)

[covalent-src]: https://github.com/N01dea5/Covalent-Mt-Holland---April-2026
[tronox-src]:   https://github.com/N01dea5/tronox-major-shutdown-may-2026
[csbp-src]:     https://github.com/N01dea5/csbp-naan2-shutdown-workforce-dashboard

Each target file is `{role: required_headcount}` keyed by the role names the
Rapid Crews roster uses (e.g. `"Mechanical Fitter"`, `"Advanced Rigger"`,
`"Supervisor - Mechanical"`). The parser merges these on top of the counts
derived from the Rapid Crews XLSX, flips the shutdown's
`required_target_source` to `"REAL_TARGET"`, and the dashboard's placeholder
banner clears.

The full planned roster from each source dashboard — names, shifts, trade
groups, shift-days, TBC flags and contingency workforce — is archived raw in
`data/imports/<company>-source.json`. Those files are provenance, not inputs
to the parser. When a source dashboard changes (new headcount, slot added),
re-run `scripts/sync_source_targets.py` (see below) to regenerate both
`data/imports/` and `data/targets/`.

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

### Refreshing from the source dashboards

`scripts/sync_source_targets.py` fetches each source dashboard's `index.html`,
extracts its planned roster, writes `data/imports/<company>-source.json`, and
rewrites `data/targets/<shutdown_id>.json` using a per-company role map from
the source vocabulary (e.g. Covalent's "Fitter - Inspections", Tronox's
"Rigger - Advanced") into the Rapid Crews vocabulary the parser reads. Run it
any time a site dashboard ships new targets, then re-run
`scripts/parse_rapidcrews.py`.

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
2. `python3 scripts/sync_source_targets.py` — pulls planned + confirmed counts
   from each per-site dashboard (`Covalent-Mt-Holland---April-2026`,
   `tronox-major-shutdown-may-2026`, `csbp-naan2-shutdown-workforce-dashboard`)
3. `python3 scripts/parse_rapidcrews.py` — parses every XLSX in `data/raw/`
   and merges the target overrides
4. If any file under `data/` changed, the workflow bumps the `?v=…`
   cache-buster on `index.html` (so iOS Safari refetches the CSS/JS after GH
   Pages redeploys) and commits the lot back with
   `Auto-refresh dashboard data [skip ci]`

The `[skip ci]` in the auto-commit message stops the workflow from
re-triggering itself. `concurrency: refresh-data-${ref}` lets a newer push
queue up behind the current run rather than stepping on it.

### What's still manual

1. Uploading a RosterCut XLSX to `data/raw/` when a new Rapid Crews snapshot
   is available (drag into the GitHub web UI, or commit via git).
2. Editing `data/targets/*.json` or `data/imports/*.json` by hand when the
   per-site dashboard role mapping needs a tweak — everything else is picked
   up by the sync script.

Everything after those two things is automatic.

### Logs and manual runs

Actions tab → "Refresh dashboard data" workflow. Click a run to see the
per-step logs and the summary (which site dashboards were polled, whether
anything changed). Hit "Run workflow" in the top-right for a manual refresh.

## Retention semantics

No stable employee IDs exist in the source data, so matching is on a normalised `name + role` key (lowercased, punctuation stripped, whitespace collapsed). Two retention views are shown side-by-side:

- **Same-company retention** — for each shutdown, share of its roster who were also on that company's previous shutdown (chronological). Measures site loyalty.
- **Cross-company carry-over** — for each shutdown, share of its roster who appeared on *any* prior shutdown at *any* of the three companies. Measures regional workforce stickiness and is mathematically ≥ same-company retention.

A "new hires" column on the retention table equals `roster − cross-company returning`.

A data-quality panel flags cases where the same normalised name+role is on two companies' rosters with overlapping dates — almost certainly two different people, surfaced for ops review.
