# Handoff files

Replacement `index.html` files for the per-site workforce dashboards.
These aren't part of this repo's runtime — they're staged here so they
can be copied across to each per-site repo.

## tronox-index.html

Replaces `index.html` in [`N01dea5/tronox-major-shutdown-may-2026`](https://github.com/N01dea5/tronox-major-shutdown-may-2026).

Fetches `data/tronox.json` from this repo on page load and renders the
four-tab dashboard (Profile / Risk / Roster / Print) from the live JSON
— named roster, day/night shift assignment, current tickets (with expiry
tooltips + SharePoint links), risk register, ticket-coverage ranking.

### Deploy

1. Open `N01dea5/tronox-major-shutdown-may-2026` in a browser.
2. Click `index.html` -> pencil/edit icon.
3. Select all contents, paste in the new file, commit to `main` with
   message: "Fetch roster + ticket data from southwest-shutdowns feed".
4. GitHub Pages rebuilds within ~60s. Hard-refresh the dashboard.

## covalent-index.html

Replaces `index.html` in [`N01dea5/Covalent-Mt-Holland---April-2026`](https://github.com/N01dea5/Covalent-Mt-Holland---April-2026).

Preserves the full Covalent dashboard — Workforce Profile, Risk Assessment,
Shutdown Roster, Gantt scheduling, Print Summary. The GANTT_TASKS work-order
data stays inline (it's engineering data, not workforce data) but the `E`
and `ROSTER` constants now fetch from `data/covalent.json` at page load.

Ticket cells link straight to the SharePoint evidence PDFs; tickets expiring
within 30 days get amber highlighting.
