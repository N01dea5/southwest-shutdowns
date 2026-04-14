# Southwest Shutdowns — Unified Dashboard

Internal-only roll-up of the three site dashboards (Covalent, Tronox, CSBP). Shows:

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
  covalent.json                  source-of-truth data per client
  tronox.json
  csbp.json
  schema.md                      JSON contract documented
  raw/                           Rapid Crews "RosterCut" XLSX exports (one per shutdown)
  targets/                       optional per-shutdown {role: required_headcount} overrides
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

### Real headcount targets

The Rapid Crews roster export only carries *confirmed* heads, not the
*requested* target. Until you supply real targets, the dashboard runs with
`required_by_role = filled_by_role` (so fill rate trivially reads 100%) and
shows a banner saying as much.

Override per-shutdown by dropping a target file at:

```
data/targets/<shutdown_id>.json
```

Example (`data/targets/tronox-2026-05.json`):

```json
{
  "Mechanical Fitter": 28,
  "Boilermaker": 12,
  "Trade Assistant": 8,
  "Advanced Rigger": 4,
  "Supervisor - Mechanical": 2
}
```

Re-run the parser. The banner disappears for that shutdown and fill-rate
reflects the gap to target.

## Retention semantics

No stable employee IDs exist in the source data, so matching is on a normalised `name + role` key (lowercased, punctuation stripped, whitespace collapsed). Two retention views are shown side-by-side:

- **Same-company retention** — for each shutdown, share of its roster who were also on that company's previous shutdown (chronological). Measures site loyalty.
- **Cross-company carry-over** — for each shutdown, share of its roster who appeared on *any* prior shutdown at *any* of the three companies. Measures regional workforce stickiness and is mathematically ≥ same-company retention.

A "new hires" column on the retention table equals `roster − cross-company returning`.

A data-quality panel flags cases where the same normalised name+role is on two companies' rosters with overlapping dates — almost certainly two different people, surfaced for ops review.
