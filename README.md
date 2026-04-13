# Southwest Shutdowns — Unified Dashboard

Internal-only roll-up of the three site dashboards (Covalent, Tronox, CSBP). Shows:

- **Fulfillment** — positions required vs. filled, overall and by trade.
- **Retention / carry-over** — how many workers return on the next shutdown, both at the **same company** and **across any of the three companies**.

Static site, no server. Each source dashboard pushes its canonical data into this repo as JSON; this page re-reads on load.

## Layout

```
index.html                 unified dashboard
assets/app.js              load → normalise → compute → render
assets/styles.css
data/
  covalent.json            source-of-truth data per company
  tronox.json
  csbp.json
  schema.md                JSON contract each company dashboard must honour
  _gen.py                  throwaway sample-data generator (delete once real feeds are wired)
```

## Run locally

```sh
python3 -m http.server 8000
# browse http://localhost:8000/
```

Chart.js is loaded from a CDN; no build step required.

## Updating data

1. Export the canonical shutdown data from the company dashboard in the shape described in [`data/schema.md`](data/schema.md).
2. Overwrite `data/<company>.json` on the `claude/unified-company-dashboard-GUq5N` branch (or wherever this site is served from).
3. Commit. The dashboard re-reads the JSONs on every page load, so the next refresh picks up the change — no code deploy needed.

The `generated_at` ISO timestamp in each file drives the freshness indicator in the top-right of the page.

## Retention semantics

No stable employee IDs exist in the source data, so matching is on a normalised `name + role` key (lowercased, punctuation stripped, whitespace collapsed). Two retention views are shown side-by-side:

- **Same-company retention** — for each shutdown, share of its roster who were also on that company's previous shutdown (chronological). Measures site loyalty.
- **Cross-company carry-over** — for each shutdown, share of its roster who appeared on *any* prior shutdown at *any* of the three companies. Measures regional workforce stickiness and is mathematically ≥ same-company retention.

A "new hires" column on the retention table equals `roster − cross-company returning`.

A data-quality panel flags cases where the same normalised name+role is on two companies' rosters with overlapping dates — almost certainly two different people, surfaced for ops review.

## Sample data

`data/_gen.py` produces deterministic, plausible data (32 workers per shutdown, mix of permanents / floats / casuals) so retention shows meaningful variation out of the box. Delete it (and re-run against real exports) once the three source dashboards start writing their own JSON here.
