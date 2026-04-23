# Handoff files

Drop-in replacement `index.html` files for the per-site workforce dashboards.
These aren't part of this repo's runtime — they're staged here so you can
copy them across to each per-site repo without needing clone-level access.

## Design (applies to all three)

Each dashboard fetches its roster + ticket data from
`southwest-shutdowns/data/<company>.json` on page load and merges it
with any hand-curated context still kept inline in the file:

- **Live feed wins for:** names, role, shift assignment, day/night pie,
  required-vs-filled math, and every ticket boolean (cse, wah, ewp, ba,
  fork, hr, dog, rig, gta, fa) — those come from the SQL compliance
  sheet via the workbook.
- **Inline data wins for:** resume narrative, trade years / shutdown
  years, "new hire" annotations, "also holds" cert extras, driver's
  licence class. These are things the SQL sheet doesn't carry so they
  stay hand-maintained in the dashboard repo.
- **Name-matched by:** case-insensitive, order-independent first+last
  fingerprinting so "DACK, Joe" in an overlay collides with "Joe DACK"
  in the feed.

No inline resume data is lost. A worker who cycles off a shutdown and
returns to a later one still has their resume overlay ready in the file.

## tronox-index.html

Replaces `index.html` in [`N01dea5/tronox-major-shutdown-may-2026`](https://github.com/N01dea5/tronox-major-shutdown-may-2026).

Keeps the inline `CREW` layout (planned positions with day/night split)
and the `TICKETS_OVERLAY` object (per-person `newhire` flag, `extras`
cert list, `drivers` class). The SQL-sourced feed replaces the rostered
names + ticket booleans; the overlay annotations stay for whichever
workers are still on the current roster.

## covalent-index.html

Replaces `index.html` in [`N01dea5/Covalent-Mt-Holland---April-2026`](https://github.com/N01dea5/Covalent-Mt-Holland---April-2026).

Keeps the existing 64-record `E` array — **renamed to `RESUME_DB`** — as
an in-file resume cache (trade years, shutdown years, full professional
summary prose). The live feed drives who's on the shutdown; for each
rostered worker we look them up in `RESUME_DB` by name and layer on
their resume details. Unmatched workers render as "resume pending" with
their live ticket coverage. The full Gantt (`GANTT_TASKS`) stays inline
— it's engineering work-order data, not workforce data.

## csbp-index.html _(pending)_

Will replace `index.html` in [`N01dea5/csbp-naan2-shutdown-workforce-dashboard`](https://github.com/N01dea5/csbp-naan2-shutdown-workforce-dashboard).
Same pattern; smaller. Not yet staged.

## Deploy

For each dashboard:

1. Open the target repo's `index.html` in GitHub's web editor:
   `https://github.com/<owner>/<repo>/edit/main/index.html`
2. Open the matching raw URL (below) in another tab, `Ctrl+A`, `Ctrl+C`.
3. In the edit tab: `Ctrl+A` (select the existing file), `Ctrl+V` (paste).
4. Commit to `main` with message:
   `Fetch roster + ticket data from southwest-shutdowns feed`
5. GitHub Pages republishes in about a minute — hard-refresh the dashboard.

Raw URLs (feature branch — switch to `main` once merged):

- Tronox:   https://raw.githubusercontent.com/N01dea5/southwest-shutdowns/claude/new-session-Csp1A/handoff/tronox-index.html
- Covalent: https://raw.githubusercontent.com/N01dea5/southwest-shutdowns/claude/new-session-Csp1A/handoff/covalent-index.html
