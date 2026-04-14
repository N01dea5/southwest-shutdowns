# Company Shutdown Data Schema

Each of the three source dashboards (Covalent, Tronox, CSBP) writes to its own JSON file in this directory:

- `covalent.json`
- `tronox.json`
- `csbp.json`

The unified dashboard (`/index.html`) reads all three on load. Overwriting any file and re-serving the page is the only step required to refresh the roll-up view — no rebuild, no code change.

## Shape

```jsonc
{
  "company": "Covalent",               // string: display name
  "generated_at": "2026-04-13T09:00:00Z", // ISO 8601: source dashboard export time (shown as freshness)

  "shutdowns": [
    {
      "id":         "covalent-2025-11",   // stable string, unique within the file
      "name":       "Kwinana Nov 2025",   // display label
      "site":       "Kwinana",            // physical site
      "start_date": "2025-11-01",         // ISO date
      "end_date":   "2025-11-20",         // ISO date
      "status":     "completed",          // "completed" | "booked" (optional; inferred from dates if absent)

      // Role-level headcount. Keys are trade names; values are integer counts.
      // Keys MUST match between required_by_role and filled_by_role for the same shutdown.
      // For booked (future) shutdowns, required_by_role is the target; filled_by_role tracks confirmed mobilisations so far.
      "required_by_role": { "Boilermaker": 40, "Scaffolder": 25, "Rigger": 12 },
      "filled_by_role":   { "Boilermaker": 38, "Scaffolder": 22, "Rigger": 12 },

      // Every worker actually mobilised for this shutdown, one object per head.
      // No stable employee ID — the dashboard normalizes `name + role` to build a key.
      "roster": [
        { "name": "John Smith",  "role": "Boilermaker" },
        { "name": "Alex O'Neil", "role": "Scaffolder"  }
      ]
    }
  ]
}
```

## Rules

- `shutdowns` must be an array; order doesn't matter (the dashboard sorts chronologically by `start_date`).
- `roster.length` should roughly equal `sum(filled_by_role)`; small mismatches are tolerated but flagged in the data-quality panel.
- Role names should be consistent across companies (e.g. always `Boilermaker`, not `boilermaker` or `BM`) so cross-site trade rollups work. The dashboard also normalizes casing.
- Names in `roster` should be the full display name as known to the company. The dashboard lowercases, strips punctuation, and collapses whitespace before matching.
- `status` is optional. If absent, the dashboard infers `booked` when `start_date` is in the future and `completed` otherwise. Booked shutdowns are included in the Gantt and in the booked-positions KPI, but excluded from completed fill-rate KPIs.

## Retention semantics

The unified dashboard computes two retention metrics per shutdown:

1. **Same-company retention** — share of this shutdown's roster who also appeared on the *previous* shutdown at the same company (by `start_date` order).
2. **Cross-company carry-over** — share of this shutdown's roster who appeared on *any* prior shutdown across all three companies.

Both use the normalized `name + role` key. Workers who change trade between shutdowns appear as new hires (by design — we're measuring role-specific retention).
