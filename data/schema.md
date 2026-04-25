# Company Shutdown Data Schema

Each client (Covalent, Tronox, CSBP) has its own JSON in this directory:

- `covalent.json`
- `tronox.json`
- `csbp.json`

The unified dashboard (`/index.html`) reads all three on load. **The canonical
data flow today is**:

1. Export a roster from Rapid Crews (RosterCut XLSX)
2. Drop the file in `data/raw/`
3. Run `python3 scripts/parse_rapidcrews.py`
4. Commit the regenerated JSONs

The script handles the schema below, including the optional fields. You can
also hand-edit a JSON if needed — the dashboard is forgiving about extra fields.

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
      "status":     "completed",          // "booked" | "in_progress" | "completed" (optional; inferred from dates)

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
      ],

      // ---- Optional fields produced by scripts/parse_rapidcrews.py ----
      "crew_split":        { "Day": 25, "Night": 22, "Contingency": 8 },
      "mobilised_by_role": { "Boilermaker": 38, "Scaffolder": 22 },
      "labour_hire_split": { "SRG - South West": 47, "MMFS - Labour Hire": 6 },

      // Provenance / data-quality block.
      "_source": {
        "rapid_crews_roster_id":   "1353",
        "rapid_crews_export_file": "1353 (RosterCut) 2026-04-14_15-16-54.xlsx",
        // Where required_by_role came from:
        //   "RAPID_CREWS_JOB_PLANNING" — xpbi02 JobPlanningView (preferred)
        //   "TARGET_FILE"              — manual override at data/targets/<id>.json
        //   "PLACEHOLDER_FROM_ROSTER"  — no target known, required = filled
        "required_target_source": "RAPID_CREWS_JOB_PLANNING",
        // Set true when the shutdown was restored from data/history/ because
        // Rapid Crews' live SQL view no longer has the JobNo. Dashboard shows
        // an "Archived" pill so users know the numbers are frozen.
        "restored_from_archive":  false
      }
    }
  ]
}
```

## `required_by_role` — where it comes from

1. **Rapid Crews `xpbi02 JobPlanningView`** (source of truth). When a JobNo
   is present in the live macro workbook, `required_by_role` is aggregated
   from its `Required` column (grouped by CompetencyId → Trade name). The
   shutdown is tagged `required_target_source: "RAPID_CREWS_JOB_PLANNING"`.
2. **`data/targets/<shutdown_id>.json`** (fallback). Only consulted when
   Rapid Crews has nothing for this JobNo — typically historic Pegasus
   rosters or carry-over snapshots like Kleenheat. Keyed by Rapid Crews role
   names. Tagged `required_target_source: "TARGET_FILE"`.
3. **Placeholder** (last resort). No RC data and no target file →
   `required_by_role = filled_by_role`, dashboard surfaces a banner and a
   `*` next to the affected fill-rate KPIs. Tagged
   `required_target_source: "PLACEHOLDER_FROM_ROSTER"`.

`filled_by_role` is always derived from Rapid Crews' roster (either the
RosterCut export or `xpbi02 PersonnelRosterView`) — no target file
overrides it. When filled > required, the dashboard reads above 100% fill
rate and surfaces an "Over plan +N" pill on the shutdown card.

## Rules

- `shutdowns` must be an array; order doesn't matter (the dashboard sorts chronologically by `start_date`).
- `roster.length` should roughly equal `sum(filled_by_role)`; small mismatches are tolerated but flagged in the data-quality panel.
- Role names should be consistent across companies (e.g. always `Boilermaker`, not `boilermaker` or `BM`) so cross-site trade rollups work. The dashboard also normalizes casing.
- Names in `roster` should be the full display name as known to the company. The dashboard lowercases, strips punctuation, and collapses whitespace before matching.
- `status` is optional. If absent, the dashboard infers it from the dates: `booked` when `start_date` is still in the future, `completed` when `end_date` has strictly passed, and `in_progress` in between (roster is on site, nobody has demobilised yet). Completed counts include only truly-finished shutdowns; `in_progress` and `booked` are both counted as "open" in the headline KPIs.

## Retention semantics

The unified dashboard computes two retention metrics per shutdown:

1. **Same-company retention** — share of this shutdown's roster who also appeared on the *previous* shutdown at the same company (by `start_date` order).
2. **Cross-company carry-over** — share of this shutdown's roster who appeared on *any* prior shutdown across all three companies.

Both use the normalized `name + role` key. Workers who change trade between shutdowns appear as new hires (by design — we're measuring role-specific retention).
