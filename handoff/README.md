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
