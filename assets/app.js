/* Unified Southwest Shutdowns dashboard.
 *
 * Pipeline on page load:
 *   1. fetch() the three canonical company JSONs from /data
 *   2. Normalise roster keys (name + role) for retention matching
 *   3. Compute fulfillment + retention metrics in one pass
 *   4. Render KPIs, charts, tables, timeline, and data-quality warnings
 *   5. Re-render on company-filter change (no re-fetch needed)
 *
 * Data files are the single source of truth: overwrite any of them and
 * reload — no code change required.
 */

// Per-company colours. Brand red (#E30613) is reserved for SRG Global accents
// (active chip, today marker, card top-rule), so company colours sit clearly
// outside the red family. Kleenheat is a historical client used here purely
// to seed retention/carry-over stats.
const COMPANIES = [
  { key: "covalent",  file: "data/covalent.json",  color: "#3A7849" }, // forest green
  { key: "tronox",    file: "data/tronox.json",    color: "#3D4250" }, // graphite slate
  { key: "csbp",      file: "data/csbp.json",      color: "#1F4E79" }, // navy blue — CSBP umbrella covers the NAAN2 + KPF LNG (Kleenheat) plants
];

const state = {
  raw: {},                 // company-name -> file payload
  shutdowns: [],           // flat, chronological list across all companies
  tab: "dashboard",        // "dashboard" | "roster"
  filter: "all",           // "all" | company display name
  statusFilter: "all",     // "all" | "booked" | "in_progress" | "completed"
  charts: {},              // Chart.js handles, so we can destroy() on re-render
  matrixFilters: {},       // shutdownId -> "present" | "absent" | "blank" (DOM-filtered)
  matrixSearch: "",        // live text filter for the matrix
  matrixRenderGen: 0,      // incremented each render; stale DOM-filter passes check this
  opsSearch: "",           // live text filter for the ops-roster tab
  opsOnsiteTodayOnly: true, // default: show workers on site today
  opsRosterData: null,     // lazy-loaded data/operations_roster.json; null = not yet fetched
  resumes: new Map(),      // normalised-name -> {resume_url, updated, notes}
};

// -------------------- helpers --------------------

function normaliseName(n) {
  return n.toLowerCase().trim().replace(/[^a-z\s]/g, "").replace(/\s+/g, " ");
}
/** Identity key for retention + matrix matching. Deliberately name-only — a
 * worker who changes role between shutdowns is still the same person. The
 * "latest role" is tracked separately and displayed in the matrix. */
function workerKey(w) {
  return normaliseName(w.name);
}

/** Convert whatever casing the source data carries into a consistent
 * "Firstname [Middle] SURNAME" display form:
 *   - Strips emoji / symbols / stray punctuation
 *   - First + middle names → Title Case (preserves Mc- / Mac- prefixes)
 *   - Surname (the trailing run of originally-uppercase tokens, or the last
 *     single token as fallback) → UPPER CASE
 * Examples:
 *   "adam riley"              → "Adam RILEY"
 *   "Julian VAN DER ZANDEN"   → "Julian VAN DER ZANDEN"
 *   "Christopher McLennan"    → "Christopher MCLENNAN"
 *   "Benjamin 🔷 CHOPPING"    → "Benjamin CHOPPING"
 *   "OMKAR UTTAM DORUGADE"    → "Omkar Uttam DORUGADE"
 */
function standardiseName(raw) {
  if (!raw) return "";
  const cleaned = raw
    .replace(/[^\p{L}\s'\-]/gu, " ")
    .trim()
    .replace(/\s+/g, " ");
  if (!cleaned) return "";
  const parts = cleaned.split(" ");
  if (parts.length === 1) return toTitleCase(parts[0]);

  // Walk back through the tail collecting originally-uppercase tokens; that
  // run is the surname. If nothing at the tail was uppercase, take the last
  // single word as the surname.
  let splitIdx = parts.length;
  while (splitIdx > 1
      && parts[splitIdx - 1].length >= 2
      && parts[splitIdx - 1] === parts[splitIdx - 1].toUpperCase()) {
    splitIdx--;
  }
  if (splitIdx === parts.length) splitIdx = parts.length - 1;

  const firstNames = parts.slice(0, splitIdx).map(toTitleCase).join(" ");
  const surname    = parts.slice(splitIdx).join(" ").toUpperCase();
  return `${firstNames} ${surname}`.trim();
}
function toTitleCase(s) {
  if (!s) return s;
  if (/^mc/i.test(s) && s.length > 2) return "Mc" + s.charAt(2).toUpperCase() + s.slice(3).toLowerCase();
  if (/^mac/i.test(s) && s.length > 3) return "Mac" + s.charAt(3).toUpperCase() + s.slice(4).toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
}
function fmtInt(n) { return n.toLocaleString(); }
function fmtPct(n) { return (n * 100).toFixed(0) + "%"; }
function fmtDate(iso) {
  const d = new Date(iso + "T00:00:00Z");
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "2-digit", timeZone: "UTC" });
}
function companyColor(name) {
  const co = COMPANIES.find(c => c.key === name.toLowerCase());
  return co ? co.color : "#888";
}

/** Render a resume-link badge for the given worker name. Empty string when
 * we don't have a URL on file — callers just concatenate the output, so an
 * absent resume keeps the layout stable. */
function resumeBadge(name) {
  const rec = state.resumes.get(normaliseName(name || ""));
  if (!rec || !rec.resume_url) return "";
  const upd = rec.updated ? ` · updated ${rec.updated}` : "";
  return `<a class="resume-badge" href="${rec.resume_url}" target="_blank" rel="noopener"
            title="Open CV in a new tab${upd}"
            onclick="event.stopPropagation()">CV</a>`;
}

// -------------------- load --------------------

async function load() {
  const results = await Promise.all(COMPANIES.map(async c => {
    const r = await fetch(c.file, { cache: "no-store" });
    if (!r.ok) throw new Error(`Failed to load ${c.file}: ${r.status}`);
    return r.json();
  }));
  for (const payload of results) {
    if (payload && payload.company) state.raw[payload.company] = payload;
  }

  // Resumes are decorative — a 404 here shouldn't block the dashboard.
  try {
    const rr = await fetch("data/resumes.json", { cache: "no-store" });
    if (rr.ok) {
      const doc = await rr.json();
      for (const r of doc.resumes || []) {
        const k = normaliseName(r.name || "");
        if (!k) continue;
        state.resumes.set(k, {
          resume_url: r.resume_url || "",
          updated:    r.updated || "",
          notes:      r.notes || "",
        });
      }
    }
  } catch (e) {
    console.warn("Failed to load data/resumes.json:", e);
  }

  // Flatten and sort chronologically. Infer status from dates when missing.
  // A shutdown is only "completed" once every scheduled worker has
  // demobilised (end_date strictly before today). Between start and end it's
  // "in_progress". Before start it's "booked".
  const todayIso = new Date().toISOString().slice(0, 10);
  const inferStatus = (sd, ed) => ed < todayIso ? "completed"
                                : sd <= todayIso ? "in_progress"
                                : "booked";
  state.shutdowns = [];
  for (const payload of results) {
    for (const s of payload.shutdowns) {
      // Re-infer if the file says "completed" but end_date is still in the
      // future — protects against old files from before the three-way status
      // change.
      let status = s.status || inferStatus(s.start_date, s.end_date);
      if (status === "completed" && s.end_date >= todayIso) {
        status = inferStatus(s.start_date, s.end_date);
      }
      // Standardise every roster entry's display name up-front so matrix /
      // retention table / summary cards all see the same canonical form.
      const cleanRoster = s.roster.map(w => ({ ...w, name: standardiseName(w.name) }));
      state.shutdowns.push({
        ...s,
        roster: cleanRoster,
        status,
        company: payload.company,
        rosterKeys: new Set(cleanRoster.map(workerKey)),
      });
    }
  }
  state.shutdowns.sort((a, b) => a.start_date.localeCompare(b.start_date));

  setupFilter();
  setupTabs();
  render();
}

function setupFilter() {
  document.getElementById("filterbar").addEventListener("click", e => {
    const btn = e.target.closest(".chip");
    if (!btn) return;
    // Each filter group (company / status) toggles independently.
    const group = btn.closest(".filter-group");
    if (group) {
      group.querySelectorAll(".chip").forEach(c => c.classList.remove("active"));
    }
    btn.classList.add("active");
    if (btn.dataset.co) state.filter = btn.dataset.co;
    if (btn.dataset.status) state.statusFilter = btn.dataset.status;
    render();
  });
}

/** Tab switching — also hides the Status filter group on the ops-roster tab,
 * where "completed / booked" filtering doesn't make sense (a worker is either
 * on site on a given day or they aren't). Company filter stays active on both. */
function setupTabs() {
  const tabbar       = document.getElementById("tabbar");
  const panels       = document.querySelectorAll(".tab-panel");
  const statusGroup  = document.getElementById("status-filter-group");
  tabbar.addEventListener("click", e => {
    const btn = e.target.closest(".tab");
    if (!btn) return;
    const tab = btn.dataset.tab;
    if (tab === state.tab) return;
    state.tab = tab;
    tabbar.querySelectorAll(".tab").forEach(t => {
      const active = t.dataset.tab === tab;
      t.classList.toggle("active", active);
      t.setAttribute("aria-selected", active ? "true" : "false");
    });
    panels.forEach(p => {
      const active = p.id === `tab-${tab}`;
      p.classList.toggle("active", active);
      p.hidden = !active;
    });
    if (statusGroup) statusGroup.hidden = (tab === "roster");
    // Gantt and ops roster both measure clientWidth on render — re-render
    // when the tab becomes visible so widths are correct.
    render();
  });

  // Ops-roster toolbar: search + "on site today" toggle.
  const search = document.getElementById("roster-search");
  if (search) {
    search.addEventListener("input", e => {
      state.opsSearch = e.target.value;
      if (state.tab === "roster") renderOpsRoster();
    });
  }
  const onsite = document.getElementById("roster-onsite-today");
  if (onsite) {
    onsite.checked = state.opsOnsiteTodayOnly;
    onsite.addEventListener("change", e => {
      state.opsOnsiteTodayOnly = e.target.checked;
      if (state.tab === "roster") renderOpsRoster();
    });
  }
}

// -------------------- compute --------------------

function filtered() {
  return state.shutdowns.filter(s => {
    if (state.filter !== "all" && s.company !== state.filter) return false;
    if (state.statusFilter !== "all" && s.status !== state.statusFilter) return false;
    return true;
  });
}

function fulfillmentRollup(shutdowns) {
  let required = 0, filled = 0;
  const byCompany = {}, byRole = {};
  for (const s of shutdowns) {
    for (const [role, n] of Object.entries(s.required_by_role)) {
      required += n;
      byCompany[s.company] = byCompany[s.company] || { required: 0, filled: 0 };
      byCompany[s.company].required += n;
      byRole[role] = byRole[role] || { required: 0, filled: 0 };
      byRole[role].required += n;
    }
    for (const [role, n] of Object.entries(s.filled_by_role)) {
      filled += n;
      // filled_by_role can list roles that required_by_role doesn't — the
      // SQL-sourced filled counts sometimes include on-site workers in
      // roles (Superintendent, Site Coordinator, …) the per-site dashboard's
      // required view doesn't plan for. Defensive init so the page doesn't
      // crash when a role is present only on the filled side.
      byCompany[s.company] = byCompany[s.company] || { required: 0, filled: 0 };
      byCompany[s.company].filled += n;
      byRole[role] = byRole[role] || { required: 0, filled: 0 };
      byRole[role].filled += n;
    }
  }
  return { required, filled, byCompany, byRole };
}

/**
 * For each shutdown, compute:
 *   - sameCompanyReturning: roster keys that also appeared in that company's previous shutdown
 *   - crossCompanyReturning: roster keys that appeared in ANY prior shutdown (across all 3 cos) — superset of same-company
 * Returns shutdowns annotated in-place with .metrics.
 */
function retentionRollup(allShutdowns) {
  // Walk in chronological order, maintaining cumulative key sets.
  const priorPerCompany = {};        // company -> Set of keys seen in that company's earlier shutdowns
  const priorAny = new Set();        // union across all prior shutdowns anywhere

  for (const s of allShutdowns) {
    const prevCo = priorPerCompany[s.company] || new Set();
    let sameRet = 0, crossRet = 0;
    for (const k of s.rosterKeys) {
      if (prevCo.has(k)) sameRet++;
      if (priorAny.has(k)) crossRet++;
    }
    const rosterSize = s.rosterKeys.size;
    s.metrics = {
      rosterSize,
      sameRet, crossRet,
      sameRetPct: rosterSize ? sameRet / rosterSize : 0,
      crossRetPct: rosterSize ? crossRet / rosterSize : 0,
      newHires: rosterSize - crossRet,
      isFirstForCompany: prevCo.size === 0,
    };
    // advance
    priorPerCompany[s.company] = new Set([...prevCo, ...s.rosterKeys]);
    for (const k of s.rosterKeys) priorAny.add(k);
  }
}

/** Build list of ambiguous name+role collisions (same key seen at ≥2 companies with overlapping date ranges). */
function ambiguousMatches(shutdowns) {
  const byKey = new Map();   // key -> [{company, shutdown}]
  for (const s of shutdowns) {
    for (const w of s.roster) {
      const k = workerKey(w);
      if (!byKey.has(k)) byKey.set(k, []);
      byKey.get(k).push({ company: s.company, shutdown: s });
    }
  }
  const out = [];
  for (const [k, apps] of byKey) {
    const companies = new Set(apps.map(a => a.company));
    if (companies.size < 2) continue;
    // Pairwise date-overlap check
    for (let i = 0; i < apps.length; i++) {
      for (let j = i + 1; j < apps.length; j++) {
        const a = apps[i], b = apps[j];
        if (a.company === b.company) continue;
        if (a.shutdown.start_date <= b.shutdown.end_date && b.shutdown.start_date <= a.shutdown.end_date) {
          out.push({
            key: k,
            name: a.shutdown.roster.find(w => workerKey(w) === k).name,
            role: a.shutdown.roster.find(w => workerKey(w) === k).role,
            a: { company: a.company, shutdown: a.shutdown.name, dates: `${a.shutdown.start_date} → ${a.shutdown.end_date}` },
            b: { company: b.company, shutdown: b.shutdown.name, dates: `${b.shutdown.start_date} → ${b.shutdown.end_date}` },
          });
        }
      }
    }
  }
  return out;
}

// -------------------- render --------------------

function render() {
  // Retention is always computed across the full chronology — filtering happens on display only.
  retentionRollup(state.shutdowns);

  const view = filtered();

  const totalRoll = fulfillmentRollup(view);

  // Detect placeholder-target shutdowns (Rapid Crews roster only — no real
  // headcount target supplied yet). When present, fill rate trivially reads
  // 100%; we mark the KPIs and surface a banner so it isn't misleading.
  const placeholderShutdowns = view.filter(s =>
    s._source && s._source.required_target_source === "PLACEHOLDER_FROM_ROSTER");
  const allPlaceholder = view.length > 0 && placeholderShutdowns.length === view.length;
  togglePlaceholderBanner(placeholderShutdowns, view);

  const star = (cond) => cond ? '<span class="kpi-star" title="No real target supplied — value derived from confirmed roster">*</span>' : "";

  // 1. Requested / Confirmed positions (demand first, supply second). When
  //    confirmed exceeds requested — the shutdown grew past its planned roster
  //    — flag the surplus inline so the number isn't misread as "only" 100%.
  const overRoll  = totalRoll.filled > totalRoll.required;
  const surplusN  = totalRoll.filled - totalRoll.required;
  const positionsEl = document.getElementById("kpi-positions");
  positionsEl.className = "kpi-value" + (overRoll ? " positive" : "");
  positionsEl.innerHTML = totalRoll.required
    ? `${fmtInt(totalRoll.required)} <span class="kpi-sep">/</span> ${fmtInt(totalRoll.filled)}${star(allPlaceholder)}` +
      (overRoll ? ` <span class="kpi-surplus" title="Confirmed exceeds the plan by ${fmtInt(surplusN)}">+${fmtInt(surplusN)}</span>` : "")
    : "—";

  // 2. Overall fill rate — coloured green at ≥100%. Surpluses read above
  //    100% (e.g. 106%) rather than clamping to 100 — the delta matters.
  const fillRateEl = document.getElementById("kpi-fillrate");
  fillRateEl.className = "kpi-value";
  if (totalRoll.required) {
    const ratio = totalRoll.filled / totalRoll.required;
    if (ratio >= 1) fillRateEl.classList.add("positive");
    fillRateEl.innerHTML = fmtPct(ratio) + star(allPlaceholder);
  } else {
    fillRateEl.innerHTML = "—";
  }

  // 3. Average cross-company retention rate across shutdowns that have any
  //    prior shutdown in the full chronology (the seed shutdown at t=0 has
  //    no priors and would always register 0 — excluding it).
  const sortedAll = [...state.shutdowns].sort((a, b) => a.start_date.localeCompare(b.start_date));
  const seedStart = sortedAll[0]?.start_date;
  const retentionSample = view.filter(s => s.start_date > seedStart && s.metrics);
  if (retentionSample.length > 0) {
    const avg = retentionSample.reduce((a, s) => a + s.metrics.crossRetPct, 0) / retentionSample.length;
    document.getElementById("kpi-retention").textContent = fmtPct(avg);
  } else {
    document.getElementById("kpi-retention").textContent = "—";
  }

  // 4. Next shutdown — the soonest-starting booked or in-progress job.
  const todayIsoNow = new Date().toISOString().slice(0, 10);
  const upcoming = view
    .filter(s => s.status !== "completed")
    .sort((a, b) => a.start_date.localeCompare(b.start_date));
  const next = upcoming[0];
  state.nextShutdownId = next ? next.id : null;  // cross-referenced by renderShutdownSummary
  const nextValEl = document.getElementById("kpi-next");
  const nextSubEl = document.getElementById("kpi-next-sub");
  if (next) {
    const shortName = next.name.replace(/^Kwinana\s+/, "");
    nextValEl.innerHTML = `<span class="kpi-next-co" style="color:${companyColor(next.company)}">${next.company}</span> <span class="kpi-next-name">${shortName}</span>`;
    const daysTo = Math.round((new Date(next.start_date + "T00:00:00Z") - new Date(todayIsoNow + "T00:00:00Z")) / 86400000);
    const when = next.status === "in_progress"
      ? `In progress · ends ${fmtDate(next.end_date)}`
      : daysTo <= 0
        ? `Starts today · ${fmtDate(next.start_date)}`
        : `Starts in ${daysTo} day${daysTo === 1 ? "" : "s"} · ${fmtDate(next.start_date)}`;
    nextSubEl.textContent = when;
  } else {
    nextValEl.textContent = "—";
    nextSubEl.textContent = "No upcoming shutdowns";
  }

  // Each render step is isolated — one failure shouldn't black out the rest
  // of the page. totalRoll from above is reused (fulfillmentRollup is
  // deterministic — no need to rerun it).
  const steps = [
    ["company chart",    () => renderCompanyChart(totalRoll)],
    ["trade chart",      () => renderTradeChart(totalRoll)],
    ["gantt",            () => renderGantt(view)],
    ["shutdown summary", () => renderShutdownSummary(view)],
    ["retention chart",  () => renderRetentionChart(view)],
    ["retention table",  () => renderRetentionTable(view)],
    ["worker matrix",    () => renderWorkerMatrix(view)],
    ["ops roster",       () => renderOpsRoster()],
    ["warnings",         () => renderWarnings()],
  ];
  for (const [name, fn] of steps) {
    try { fn(); } catch (e) { console.error(`[render] ${name} failed:`, e); }
  }
}

function makeChart(id, config) {
  const canvas = document.getElementById(id);
  if (!canvas) return;
  if (typeof Chart === "undefined") {
    // Chart.js CDN didn't load (offline / blocked). Show a graceful placeholder
    // instead of throwing and aborting the rest of the render pipeline.
    const parent = canvas.parentElement;
    if (parent && !parent.querySelector(".chart-offline")) {
      const note = document.createElement("div");
      note.className = "chart-offline";
      note.textContent = "Chart unavailable — Chart.js failed to load.";
      parent.appendChild(note);
    }
    return;
  }
  if (state.charts[id]) state.charts[id].destroy();
  state.charts[id] = new Chart(canvas.getContext("2d"), config);
}

// Each live shutdown has its own per-site dashboard repo with a GitHub Pages
// deployment. Used on the per-shutdown detail card heading so you can jump
// straight to the site's own workforce dashboard. Kleenheat is historical —
// no per-site dashboard — so there's nothing to link.
const SHUTDOWN_LINKS = {
  "covalent-2026-04": "https://n01dea5.github.io/Covalent-Mt-Holland---April-2026/",
  "tronox-2026-05":   "https://n01dea5.github.io/tronox-major-shutdown-may-2026/",
  "csbp-2026-05":     "https://n01dea5.github.io/csbp-naan2-shutdown-workforce-dashboard/",
};

// SRG brand tokens used by the charts — kept in sync with :root in styles.css.
const BRAND = {
  red:       "#E30613",
  dark:      "#1A1A1A",
  grey:      "#595959",
  grey2:     "#8C8C8C",
  light:     "#F5F5F5",
  border:    "#E5E5E5",
  required:  "#D9DCE1",   // muted neutral for "Required" bars — sits behind the brand-coloured "Filled"
};

function renderCompanyChart(roll) {
  // Preserve the canonical company order (Kleenheat → CSBP) so filter
  // changes don't reshuffle the rows.
  const labels = COMPANIES.map(c => Object.keys(roll.byCompany).find(k => k.toLowerCase() === c.key))
    .filter(Boolean);
  const required = labels.map(l => roll.byCompany[l].required);
  const filled   = labels.map(l => roll.byCompany[l].filled);

  makeChart("chart-company", {
    type: "bar",
    data: {
      labels,
      datasets: [
        { label: "Required", data: required, backgroundColor: BRAND.required, borderColor: BRAND.border, borderWidth: 1 },
        { label: "Filled",   data: filled,   backgroundColor: BRAND.red, borderWidth: 0 },
      ],
    },
    options: {
      indexAxis: "y",
      responsive: true, maintainAspectRatio: false,
      scales: {
        x: { beginAtZero: true, grid: { color: BRAND.border }, ticks: { color: BRAND.grey } },
        y: { grid: { display: false }, ticks: { color: BRAND.dark, font: { weight: "700" } } },
      },
      plugins: {
        legend: { labels: { color: BRAND.dark, font: { weight: "600" } } },
        tooltip: {
          backgroundColor: BRAND.dark,
          titleColor: "#fff",
          bodyColor: "#fff",
          borderColor: BRAND.red,
          borderWidth: 1,
          callbacks: {
            afterLabel: (ctx) => {
              const co = labels[ctx.dataIndex];
              const r = roll.byCompany[co];
              return `Fill rate: ${fmtPct(r.filled / r.required)}`;
            },
          },
        },
      },
    },
  });
}

function renderTradeChart(roll) {
  // Sort roles by required size (descending) so the biggest demands lead —
  // more useful than alphabetical when roles vary wildly in size.
  const roles = Object.keys(roll.byRole)
    .sort((a, b) => (roll.byRole[b].required - roll.byRole[a].required) || a.localeCompare(b));
  const required = roles.map(r => roll.byRole[r].required);
  const filled   = roles.map(r => roll.byRole[r].filled);

  makeChart("chart-trade", {
    type: "bar",
    data: {
      labels: roles,
      datasets: [
        { label: "Required", data: required, backgroundColor: BRAND.required, borderColor: BRAND.border, borderWidth: 1 },
        { label: "Filled",   data: filled,   backgroundColor: BRAND.red },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: {
        y: { beginAtZero: true, grid: { color: BRAND.border }, ticks: { color: BRAND.grey } },
        x: { grid: { display: false }, ticks: { color: BRAND.dark, font: { weight: "600" }, maxRotation: 50, minRotation: 30 } },
      },
      plugins: {
        legend: { labels: { color: BRAND.dark, font: { weight: "600" } } },
        tooltip: {
          backgroundColor: BRAND.dark,
          titleColor: "#fff",
          bodyColor: "#fff",
          borderColor: BRAND.red,
          borderWidth: 1,
          callbacks: {
            afterLabel: (ctx) => {
              const r = roll.byRole[roles[ctx.dataIndex]];
              return `Fill rate: ${fmtPct(r.filled / r.required)}`;
            },
          },
        },
      },
    },
  });
}

function renderRetentionChart(view) {
  // Horizontal stacked bar, one bar per shutdown, chronological. Each bar is
  // partitioned into three bands: workers returning from the SAME company,
  // workers returning from a DIFFERENT SRG-pool company, and NEW hires. The
  // bar totals are the shutdown's roster size; the band widths are absolute
  // headcounts (tooltips show the % of roster).
  //
  // This replaces the earlier multi-line chart: lines were sparse (each
  // company only had values on its own shutdowns), overlapped the cross-
  // company dashed line, and the category axis ran out of room once there
  // were 6+ shutdowns. A stacked bar per shutdown reads at a glance and
  // fills the card width regardless of shutdown count.
  const ordered = [...view].sort((a, b) => a.start_date.localeCompare(b.start_date));
  const labels  = ordered.map(s => {
    const monthYear = new Date(s.start_date + "T00:00:00Z")
      .toLocaleDateString(undefined, { month: "short", year: "2-digit", timeZone: "UTC" });
    return `${s.company} · ${monthYear}`;
  });

  // Propagate the row count so the card auto-grows — see .chart-wrap.retention.
  const canvas = document.getElementById("chart-retention");
  if (canvas && canvas.parentElement) {
    canvas.parentElement.style.setProperty("--retention-rows", String(Math.max(1, ordered.length)));
  }

  // Derive band sizes (headcounts) per shutdown. `sameRet` is a subset of
  // `crossRet`, so pure cross-company = crossRet - sameRet.
  const sameCo   = ordered.map(s => s.metrics.sameRet);
  const xCompany = ordered.map(s => Math.max(0, s.metrics.crossRet - s.metrics.sameRet));
  const newHires = ordered.map(s => s.metrics.newHires);
  const totals   = ordered.map(s => s.metrics.rosterSize);

  // Build per-bar border colour so each bar is visually anchored to its
  // company (echoes the company dot in the table below) without obscuring
  // the three-band fill inside.
  const coBorders = ordered.map(s => companyColor(s.company));

  const pct = (n, total) => total ? ` (${Math.round(100 * n / total)}%)` : "";

  makeChart("chart-retention", {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: "Returning – same company",
          data: sameCo,
          backgroundColor: "#16A34A",       // strong green — loyalty
          borderColor: coBorders,
          borderWidth: { top: 0, bottom: 0, left: 1, right: 0 },
          stack: "roster",
        },
        {
          label: "Returning – cross-company",
          data: xCompany,
          backgroundColor: "#7FC89A",       // softer green — still returning, different site
          borderColor: coBorders,
          borderWidth: { top: 0, bottom: 0, left: 0, right: 0 },
          stack: "roster",
        },
        {
          label: "New hires",
          data: newHires,
          backgroundColor: BRAND.required,  // neutral grey — fresh intake
          borderColor: coBorders,
          borderWidth: { top: 0, bottom: 0, left: 0, right: 1 },
          stack: "roster",
        },
      ],
    },
    options: {
      indexAxis: "y",
      responsive: true, maintainAspectRatio: false,
      // Fatter bars so the three-band fill is legible; clamped so short
      // rosters don't disappear in the padding.
      barPercentage: 0.82,
      categoryPercentage: 0.88,
      scales: {
        x: {
          beginAtZero: true,
          stacked: true,
          grid: { color: BRAND.border },
          ticks: { color: BRAND.grey, precision: 0 },
          title: { display: true, text: "Roster headcount",
                   color: BRAND.grey, font: { size: 11, weight: "600" } },
        },
        y: {
          stacked: true,
          grid: { display: false },
          ticks: { color: BRAND.dark, font: { weight: "700" } },
        },
      },
      plugins: {
        legend: { position: "bottom",
                  labels: { color: BRAND.dark, font: { weight: "600" }, boxWidth: 14 } },
        tooltip: {
          backgroundColor: BRAND.dark,
          titleColor: "#fff",
          bodyColor: "#fff",
          borderColor: BRAND.red,
          borderWidth: 1,
          callbacks: {
            title: (items) => {
              if (!items.length) return "";
              const s = ordered[items[0].dataIndex];
              return `${s.company} – ${s.name}`;
            },
            label: (ctx) => {
              const total = totals[ctx.dataIndex];
              const v = ctx.parsed.x;
              return `${ctx.dataset.label}: ${v}${pct(v, total)}`;
            },
            afterBody: (items) => {
              if (!items.length) return "";
              const total = totals[items[0].dataIndex];
              return `Total roster: ${total}`;
            },
          },
        },
      },
    },
  });
}

function renderRetentionTable(view) {
  const tbody = document.querySelector("#retention-table tbody");
  tbody.innerHTML = "";
  const rows = [...view].sort((a, b) => a.start_date.localeCompare(b.start_date));
  for (const s of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${s.name}</td>
      <td><span class="co-dot" style="background:${companyColor(s.company)}"></span>${s.company}</td>
      <td>${fmtDate(s.start_date)}</td>
      <td class="num">${s.metrics.rosterSize}</td>
      <td class="num">${s.metrics.sameRet} <span class="muted">(${fmtPct(s.metrics.sameRetPct)})</span></td>
      <td class="num">${s.metrics.crossRet} <span class="muted">(${fmtPct(s.metrics.crossRetPct)})</span></td>
      <td class="num">${s.metrics.newHires}</td>
    `;
    tbody.appendChild(tr);
  }
}

/**
 * Swimlane Gantt of all shutdowns in the filtered view.
 *   - one lane per company; lane label is sticky-left so it stays visible
 *     while the chart scrolls horizontally
 *   - x-axis has two tiers: month labels (upper) and ISO-week ticks (lower)
 *   - each week is a fixed pixel width (WEEK_PX) so long spans scroll cleanly
 *   - each shutdown is a positioned bar, shaded darker as fill% → 100%
 *   - booked shutdowns get a dashed outline
 *   - vertical "today" marker shown if it falls within the span
 */
function renderGantt(view) {
  const host = document.getElementById("gantt");
  host.innerHTML = "";
  if (view.length === 0) { host.textContent = "No shutdowns for this filter."; return; }

  // Determine lanes in canonical COMPANIES order (so the y-axis stays stable across filters).
  const presentCompanies = new Set(view.map(s => s.company));
  const lanes = Object.keys(state.raw)
    .sort((a, b) => {
      const ai = COMPANIES.findIndex(c => c.key === a.toLowerCase());
      const bi = COMPANIES.findIndex(c => c.key === b.toLowerCase());
      return ai - bi;
    })
    .filter(name => presentCompanies.has(name));

  // --- Span: pad to Monday-of-start-week → Sunday-of-end-week so week ticks align.
  const MIN_WEEK_PX  = 44;                 // minimum column width before we start scrolling
  const LANE_LABEL_W = 120;                // matches CSS --lane-label-w
  const mondayOf = (d) => {
    const nd = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
    const dow = nd.getUTCDay();             // 0=Sun..6=Sat
    const offset = (dow + 6) % 7;           // Mon=0
    nd.setUTCDate(nd.getUTCDate() - offset);
    return nd;
  };
  const minStart  = view.reduce((m, s) => s.start_date < m ? s.start_date : m, view[0].start_date);
  const maxEnd    = view.reduce((m, s) => s.end_date   > m ? s.end_date   : m, view[0].end_date);
  const spanStart = mondayOf(new Date(minStart + "T00:00:00Z"));
  const spanEndRaw = new Date(maxEnd + "T00:00:00Z");
  const spanEnd   = mondayOf(spanEndRaw);
  spanEnd.setUTCDate(spanEnd.getUTCDate() + 7);      // include full week of end
  const totalMs    = spanEnd - spanStart;
  const totalWeeks = Math.round(totalMs / (7 * 86400 * 1000));

  // Fit the Gantt to the container's current width so the whole span is
  // visible by default — only fall back to horizontal scroll when the
  // weeks would get squashed below MIN_WEEK_PX.
  const containerW = host.clientWidth
                  || host.parentElement?.clientWidth
                  || 1200;
  const fitWeekPx  = (containerW - LANE_LABEL_W) / totalWeeks;
  const WEEK_PX    = Math.max(MIN_WEEK_PX, fitWeekPx);
  const innerW     = Math.round(totalWeeks * WEEK_PX);
  const px = (d) => ((d - spanStart) / totalMs) * innerW;

  // --- Inner scroll container (width = weeks * column width) ---
  const inner = document.createElement("div");
  inner.className = "gantt-inner";
  inner.style.width = (LANE_LABEL_W + innerW) + "px";

  // --- Axis: two tiers (month label row on top, week ticks below) ---
  const axis = document.createElement("div");
  axis.className = "gantt-axis";

  // Month tier: one block per calendar month
  const months = document.createElement("div");
  months.className = "gantt-axis-months";
  const mCursor = new Date(Date.UTC(spanStart.getUTCFullYear(), spanStart.getUTCMonth(), 1));
  while (mCursor < spanEnd) {
    const next = new Date(Date.UTC(mCursor.getUTCFullYear(), mCursor.getUTCMonth() + 1, 1));
    const left  = Math.max(0, px(mCursor));
    const right = Math.min(innerW, px(next));
    const width = right - left;
    if (width > 0) {
      const tick = document.createElement("div");
      tick.className = "gantt-month-tick";
      tick.style.left  = left + "px";
      tick.style.width = width + "px";
      const mo = mCursor.toLocaleDateString(undefined, { month: "short", timeZone: "UTC" });
      const yr = mCursor.getUTCMonth() === 0 ? " " + mCursor.getUTCFullYear() : "";
      tick.innerHTML = `<span>${mo}${yr}</span>`;
      months.appendChild(tick);
    }
    mCursor.setUTCMonth(mCursor.getUTCMonth() + 1);
  }
  axis.appendChild(months);

  // Week tier: one block per ISO week, labelled by Monday's day-of-month
  const weeks = document.createElement("div");
  weeks.className = "gantt-axis-weeks";
  const wCursor = new Date(spanStart);
  while (wCursor < spanEnd) {
    const next = new Date(wCursor);
    next.setUTCDate(next.getUTCDate() + 7);
    const left = px(wCursor);
    const tick = document.createElement("div");
    tick.className = "gantt-week-tick";
    tick.style.left  = left + "px";
    tick.style.width = WEEK_PX + "px";
    const monthLetter = wCursor.toLocaleDateString(undefined, { month: "short", timeZone: "UTC" });
    const dom = wCursor.getUTCDate();
    tick.innerHTML = `<span class="dom">${dom}</span><span class="mo">${monthLetter}</span>`;
    weeks.appendChild(tick);
    wCursor.setUTCDate(wCursor.getUTCDate() + 7);
  }
  axis.appendChild(weeks);
  inner.appendChild(axis);

  // --- Body: one swimlane per company ---
  const body = document.createElement("div");
  body.className = "gantt-body";

  // Weekly gridlines — drawn once behind all lanes.
  const grid = document.createElement("div");
  grid.className = "gantt-grid";
  const g = new Date(spanStart);
  while (g < spanEnd) {
    const line = document.createElement("div");
    line.className = "gantt-gridline";
    line.style.left = px(g) + "px";
    grid.appendChild(line);
    g.setUTCDate(g.getUTCDate() + 7);
  }
  body.appendChild(grid);

  // Today marker — inside .gantt-grid so its positioning aligns with the tracks.
  const today = new Date();
  if (today >= spanStart && today <= spanEnd) {
    const todayLine = document.createElement("div");
    todayLine.className = "gantt-today";
    todayLine.style.left = px(today) + "px";
    todayLine.title = "Today";
    grid.appendChild(todayLine);
  }

  // Pixel height per bar/track within a lane. Overlapping shutdowns in the
  // same lane stack onto separate tracks using greedy interval scheduling,
  // so e.g. Tianqi's Construction Ramp-Up and Scaffold Shutdown don't
  // visually collide when they run in parallel.
  const TRACK_H  = 34;
  const TRACK_PAD = 4;

  function assignTracks(shutdownsInLane) {
    const sorted = [...shutdownsInLane].sort((a, b) => a.start_date.localeCompare(b.start_date));
    const trackEnds = [];          // max end_date on each track so far
    const tracks    = new Map();   // shutdown.id -> track index
    for (const s of sorted) {
      let placed = false;
      for (let t = 0; t < trackEnds.length; t++) {
        if (trackEnds[t] < s.start_date) {
          trackEnds[t] = s.end_date;
          tracks.set(s.id, t);
          placed = true;
          break;
        }
      }
      if (!placed) {
        tracks.set(s.id, trackEnds.length);
        trackEnds.push(s.end_date);
      }
    }
    return { tracks, trackCount: Math.max(1, trackEnds.length) };
  }

  for (const lane of lanes) {
    const laneShutdowns      = view.filter(x => x.company === lane);
    const { tracks, trackCount } = assignTracks(laneShutdowns);
    const laneH = trackCount * TRACK_H + TRACK_PAD * 2;

    const row = document.createElement("div");
    row.className = "gantt-row";
    row.style.height = laneH + "px";

    const label = document.createElement("div");
    label.className = "gantt-row-label";
    label.innerHTML = `<span class="co-dot" style="background:${companyColor(lane)}"></span>${lane}`;
    row.appendChild(label);

    const track = document.createElement("div");
    track.className = "gantt-track";

    for (const s of laneShutdowns) {
      const sd = new Date(s.start_date + "T00:00:00Z");
      const ed = new Date(s.end_date + "T00:00:00Z");
      const filled = Object.values(s.filled_by_role).reduce((a, b) => a + b, 0);
      const req    = Object.values(s.required_by_role).reduce((a, b) => a + b, 0);
      const fillPct = req ? filled / req : 0;
      const tIdx = tracks.get(s.id);

      const bar = document.createElement("div");
      bar.className = "gantt-bar status-" + s.status + (s.status === "booked" ? " booked" : "");
      bar.style.left   = px(sd) + "px";
      bar.style.width  = Math.max(4, px(ed) - px(sd)) + "px";
      bar.style.top    = (TRACK_PAD + tIdx * TRACK_H) + "px";
      bar.style.height = (TRACK_H - 4) + "px";
      bar.style.setProperty("--co", companyColor(lane));
      bar.style.setProperty("--fill-opacity", (0.35 + 0.6 * fillPct).toFixed(2));
      bar.title = [
        `${s.company} – ${s.name}`,
        `${fmtDate(s.start_date)} → ${fmtDate(s.end_date)}`,
        `Status: ${statusLabel(s.status)}`,
        `${s.status === "completed" ? "Filled" : "Confirmed"}: ${filled}/${req} (${fmtPct(fillPct)})`,
      ].join("\n");
      bar.innerHTML = `<span>${s.name.replace(/^Kwinana /, "")} &middot; ${fmtPct(fillPct)}</span>`;
      track.appendChild(bar);
    }

    row.appendChild(track);
    body.appendChild(row);
  }

  inner.appendChild(body);
  host.appendChild(inner);

  // Scroll so "today" (or the earliest in-progress/booked shutdown) is
  // visible on first render — for booked work months ahead, this lands the
  // viewport on the relevant week instead of the empty pre-shutdown padding.
  const focus = today >= spanStart && today <= spanEnd
              ? px(today) - 80
              : px(new Date(view[0].start_date + "T00:00:00Z")) - 80;
  host.scrollLeft = Math.max(0, focus);
}

function statusLabel(st) {
  return st === "in_progress" ? "In progress"
       : st === "completed"   ? "Completed"
       : "Booked";
}

/**
 * Consolidated ops roster — the second tab. Each unique worker gets one row
 * spanning the full time axis; their assignments render as coloured bars
 * positioned by per-worker start/end dates (per-shutdown dates as fallback
 * when a legacy file has no per-row dates). Answers questions like "is Joe
 * on site next Tuesday?" and "who's available the week of 18 May?".
 *
 * Matches the Gantt's visual language (month + week axis, today line,
 * container-width fitting with horizontal scroll fallback) so moving between
 * the two tabs feels consistent.
 */
async function renderOpsRoster() {
  const host = document.getElementById("ops-roster");
  if (!host) return;

  // Lazy-load data/operations_roster.json on first visit to this tab.
  if (state.opsRosterData === null) {
    host.innerHTML = '<p style="padding:1rem;color:var(--srg-grey-2)">Loading roster data…</p>';
    try {
      const r = await fetch(`data/operations_roster.json?_=${Date.now()}`, { cache: "no-store" });
      state.opsRosterData = r.ok ? await r.json() : { workers: [] };
    } catch (e) {
      console.warn("renderOpsRoster: could not load operations_roster.json", e);
      state.opsRosterData = { workers: [] };
    }
    renderOpsRoster();
    return;
  }

  host.innerHTML = "";

  const todayIso = new Date().toISOString().slice(0, 10);

  // Derive assignment status from dates.
  const deriveStatus = (start, end) => {
    if (end < todayIso) return "completed";
    if (start <= todayIso && todayIso <= end) return "in_progress";
    return "booked";
  };

  // Map site name to a company colour consistent with the Gantt palette.
  const siteColor = (site) => {
    const s = (site || "").toLowerCase();
    if (s.includes("tronox"))                            return companyColor("tronox");
    if (s.includes("covalent") || s.includes("tianqi")) return companyColor("covalent");
    if (s.includes("csbp") || s.includes("kwinana"))    return companyColor("csbp");
    return "#888";
  };

  // -- 1. Collect workers + their assignments --
  const rawWorkers = state.opsRosterData.workers || [];
  const workers = new Map();
  for (const w of rawWorkers) {
    const name = standardiseName(w.name);
    if (!name) continue;
    const key = workerKey({ name });
    if (!workers.has(key)) {
      workers.set(key, {
        key, name, role: w.role || "", mobile: w.mobile || "",
        hireCompany: w.hire_company || "",
        assignments: [],
      });
    }
    const rec = workers.get(key);
    for (const a of w.assignments || []) {
      if (!a.start || !a.end) continue;
      rec.assignments.push({
        start: a.start, end: a.end,
        site: a.site || "", client: a.client || "",
        jobNo: a.job_no || "",
        scheduleType: a.schedule_type || "",
        isOnLocation: a.is_on_location !== false,
        status: deriveStatus(a.start, a.end),
        hireCompany: w.hire_company || "",
      });
    }
    // Keep role/mobile from the worker record (most recent data from Personnel sheet).
    if (w.role)         rec.role        = w.role;
    if (w.mobile)       rec.mobile      = w.mobile;
    if (w.hire_company) rec.hireCompany = w.hire_company;
  }

  if (workers.size === 0) {
    host.textContent = "No Kwinana roster data available. Run the data pipeline to generate data/operations_roster.json.";
    return;
  }

  // -- 2. Filter: search (name/role/mobile/hire company) + "on site today only" --
  const search     = state.opsSearch.trim().toLowerCase();
  const onsiteOnly = state.opsOnsiteTodayOnly;
  const rows = [...workers.values()].filter(rec => {
    if (search) {
      const hay = `${rec.name} ${rec.role} ${rec.mobile} ${rec.hireCompany}`.toLowerCase();
      if (!hay.includes(search)) return false;
    }
    if (onsiteOnly) {
      const onsite = rec.assignments.some(a =>
        a.start <= todayIso && todayIso <= a.end && a.isOnLocation
      );
      if (!onsite) return false;
    }
    return true;
  });

  // Sort by site → name. Primary site = the site of today's active assignment,
  // or the most-recent assignment when "on site today" is off.
  const primarySite = rec => {
    const today = rec.assignments.find(a => a.start <= todayIso && todayIso <= a.end);
    if (today) return today.site || "Unknown";
    const sorted = [...rec.assignments].sort((a, b) => b.start.localeCompare(a.start));
    return sorted[0]?.site || "Unknown";
  };
  rows.sort((a, b) => {
    const sa = primarySite(a), sb = primarySite(b);
    if (sa !== sb) return sa.localeCompare(sb);
    return a.name.localeCompare(b.name);
  });

  // -- 3. Time axis (Monday-aligned, matches Gantt) --
  const MIN_WEEK_PX  = 44;
  const LANE_LABEL_W = 320;   // room for name + role + hire company
  const mondayOf = (d) => {
    const nd = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
    const offset = (nd.getUTCDay() + 6) % 7;
    nd.setUTCDate(nd.getUTCDate() - offset);
    return nd;
  };
  const allAssignments = rows.flatMap(r => r.assignments);
  const minStart = allAssignments.reduce((m, a) => a.start < m ? a.start : m, allAssignments[0]?.start || todayIso);
  const maxEnd   = allAssignments.reduce((m, a) => a.end   > m ? a.end   : m, allAssignments[0]?.end   || todayIso);
  const spanStart = mondayOf(new Date(minStart + "T00:00:00Z"));
  const spanEnd   = mondayOf(new Date(maxEnd   + "T00:00:00Z"));
  spanEnd.setUTCDate(spanEnd.getUTCDate() + 7);
  const totalMs    = spanEnd - spanStart;
  const totalWeeks = Math.round(totalMs / (7 * 86400 * 1000));
  const containerW = host.clientWidth
                  || host.parentElement?.clientWidth
                  || 1200;
  const fitWeekPx  = (containerW - LANE_LABEL_W) / totalWeeks;
  const WEEK_PX    = Math.max(MIN_WEEK_PX, fitWeekPx);
  const innerW     = Math.round(totalWeeks * WEEK_PX);
  const px = (d) => ((d - spanStart) / totalMs) * innerW;

  // -- 4. Header summary: #workers + #on site today --
  const onsiteNow = rows.filter(rec =>
    rec.assignments.some(a =>
      a.start <= todayIso && todayIso <= a.end && a.isOnLocation
    )).length;
  const countEl = document.getElementById("roster-count");
  if (countEl) {
    countEl.textContent = onsiteOnly
      ? `${rows.length} worker${rows.length === 1 ? "" : "s"} on site today`
      : `${rows.length} worker${rows.length === 1 ? "" : "s"} · ${onsiteNow} on site today`;
  }

  // -- 5. Inner scroll container --
  const inner = document.createElement("div");
  inner.className = "ops-roster-inner";
  inner.style.width = (LANE_LABEL_W + innerW) + "px";
  inner.style.setProperty("--lane-label-w", LANE_LABEL_W + "px");

  // -- 6. Axis (month + week tiers) --
  const axis = document.createElement("div");
  axis.className = "ops-roster-axis";

  const months = document.createElement("div");
  months.className = "ops-roster-months";
  const mCursor = new Date(Date.UTC(spanStart.getUTCFullYear(), spanStart.getUTCMonth(), 1));
  while (mCursor < spanEnd) {
    const next  = new Date(Date.UTC(mCursor.getUTCFullYear(), mCursor.getUTCMonth() + 1, 1));
    const left  = Math.max(0, px(mCursor));
    const right = Math.min(innerW, px(next));
    const width = right - left;
    if (width > 0) {
      const tick = document.createElement("div");
      tick.className = "ops-roster-month-tick";
      tick.style.left  = left + "px";
      tick.style.width = width + "px";
      const mo = mCursor.toLocaleDateString(undefined, { month: "short", timeZone: "UTC" });
      const yr = mCursor.getUTCMonth() === 0 ? " " + mCursor.getUTCFullYear() : "";
      tick.innerHTML = `<span>${mo}${yr}</span>`;
      months.appendChild(tick);
    }
    mCursor.setUTCMonth(mCursor.getUTCMonth() + 1);
  }
  axis.appendChild(months);

  const weeks = document.createElement("div");
  weeks.className = "ops-roster-weeks";
  const wCursor = new Date(spanStart);
  while (wCursor < spanEnd) {
    const tick = document.createElement("div");
    tick.className = "ops-roster-week-tick";
    tick.style.left  = px(wCursor) + "px";
    tick.style.width = WEEK_PX + "px";
    const monthLetter = wCursor.toLocaleDateString(undefined, { month: "short", timeZone: "UTC" });
    const dom = wCursor.getUTCDate();
    tick.innerHTML = `<span class="dom">${dom}</span><span class="mo">${monthLetter}</span>`;
    weeks.appendChild(tick);
    wCursor.setUTCDate(wCursor.getUTCDate() + 7);
  }
  axis.appendChild(weeks);
  inner.appendChild(axis);

  // -- 7. Body: one row per worker, grouped by primary company --
  const body = document.createElement("div");
  body.className = "ops-roster-body";

  // Weekly gridlines + today marker (shared across all rows).
  const grid = document.createElement("div");
  grid.className = "ops-roster-grid";
  const g = new Date(spanStart);
  while (g < spanEnd) {
    const line = document.createElement("div");
    line.className = "ops-roster-gridline";
    line.style.left = px(g) + "px";
    grid.appendChild(line);
    g.setUTCDate(g.getUTCDate() + 7);
  }
  const todayDate = new Date();
  if (todayDate >= spanStart && todayDate <= spanEnd) {
    const todayLine = document.createElement("div");
    todayLine.className = "ops-roster-today";
    todayLine.style.left = px(todayDate) + "px";
    todayLine.title = "Today";
    grid.appendChild(todayLine);
  }
  body.appendChild(grid);

  let lastGroup = null;
  for (const rec of rows) {
    const site = primarySite(rec);
    if (site !== lastGroup) {
      const hdr = document.createElement("div");
      hdr.className = "ops-roster-group";
      hdr.textContent = site;
      body.appendChild(hdr);
      lastGroup = site;
    }

    const row = document.createElement("div");
    row.className = "ops-roster-row";

    const label = document.createElement("div");
    label.className = "ops-roster-row-label";
    const telHref = rec.mobile ? `tel:${rec.mobile.replace(/\s+/g, "")}` : null;
    const mobileHtml = telHref
      ? `<a class="mobile-link" href="${telHref}">${rec.mobile}</a>`
      : `<span class="muted">—</span>`;
    label.innerHTML = `
      <div class="ops-roster-name">${rec.name} ${resumeBadge(rec.name)}</div>
      <div class="ops-roster-meta"><span class="ops-roster-role">${rec.role}</span> · ${mobileHtml}</div>
      ${rec.hireCompany ? `<div class="ops-roster-hire">${rec.hireCompany}</div>` : ""}`;
    row.appendChild(label);

    const track = document.createElement("div");
    track.className = "ops-roster-track";

    // Only render on-location assignments — off-site segments (R&R, demob,
    // future unconfirmed) are excluded to keep the Gantt readable.
    const sortedAssignments = [...rec.assignments]
      .filter(a => a.isOnLocation)
      .sort((a, b) => a.start.localeCompare(b.start));
    for (const a of sortedAssignments) {
      const sd = new Date(a.start + "T00:00:00Z");
      // End date is inclusive — stretch the bar to the end of its last day so
      // a one-day assignment still shows as a visible block.
      const ed = new Date(a.end   + "T00:00:00Z");
      ed.setUTCDate(ed.getUTCDate() + 1);
      const bar = document.createElement("div");
      bar.className = "ops-roster-bar status-" + a.status + (a.status === "booked" ? " booked" : "");
      bar.style.left  = px(sd) + "px";
      bar.style.width = Math.max(6, px(ed) - px(sd)) + "px";
      bar.style.setProperty("--co", siteColor(a.site));
      bar.title = [
        `${rec.name} — ${rec.role}${a.scheduleType ? " (" + a.scheduleType + ")" : ""}`,
        `${a.site}${a.jobNo ? " · Job " + a.jobNo : ""}`,
        rec.hireCompany ? `Hired via: ${rec.hireCompany}` : null,
        `${fmtDate(a.start)} → ${fmtDate(a.end)}`,
        `Status: ${statusLabel(a.status)}`,
      ].filter(Boolean).join("\n");
      bar.innerHTML = `<span>${a.site}</span>`;
      track.appendChild(bar);
    }
    row.appendChild(track);
    body.appendChild(row);
  }

  inner.appendChild(body);
  host.appendChild(inner);

  // Scroll to today (or earliest assignment) on first render, same pattern
  // as the Gantt.
  const focus = todayDate >= spanStart && todayDate <= spanEnd
              ? px(todayDate) - 80
              : px(new Date(minStart + "T00:00:00Z")) - 80;
  host.scrollLeft = Math.max(0, focus);
}

/**
 * One summary card per shutdown. Mirrors the per-site dashboards' trade-group
 * table: for each role show required vs filled, the gap, and the per-role
 * fill rate. Over-fills (filled > required) render with a negative gap — this
 * is truthful: e.g. the live Covalent shutdown grew beyond its original plan.
 */
function renderShutdownSummary(view) {
  const host = document.getElementById("shutdown-summary");
  host.innerHTML = "";
  if (view.length === 0) {
    host.innerHTML = `<p class="muted">No shutdowns for this filter.</p>`;
    return;
  }

  for (const s of view) {
    const req = s.required_by_role || {};
    const fil = s.filled_by_role   || {};
    const roles = [...new Set([...Object.keys(req), ...Object.keys(fil)])]
      .sort((a, b) => (req[b] || 0) - (req[a] || 0) || a.localeCompare(b));

    const totalReq    = Object.values(req).reduce((a, b) => a + b, 0);
    const totalFilled = Object.values(fil).reduce((a, b) => a + b, 0);
    const totalGap    = totalReq - totalFilled;
    const fillRate    = totalReq ? totalFilled / totalReq : 0;
    const isPlaceholder = s._source?.required_target_source === "PLACEHOLDER_FROM_ROSTER";
    const isOverstaffed = totalReq > 0 && totalFilled > totalReq;
    const overstaffedPill = isOverstaffed
      ? `<span class="sd-over-pill" title="Confirmed roster is ${fmtInt(-totalGap)} above the requested plan">+${fmtInt(-totalGap)} over plan</span>`
      : "";
    // "Archived" pill when the live SQL view no longer has this JobNo — the
    // parser re-hydrated it from data/history/, so numbers are frozen to the
    // last successful refresh rather than the current live state.
    const isArchived = s._source?.restored_from_archive === true;
    const archivedPill = isArchived
      ? `<span class="sd-archive-pill" title="Rapid Crews' live SQL view no longer lists this JobNo — values frozen to the last snapshot in data/history/">Archived</span>`
      : "";

    const body = roles.map(r => {
      const rq   = req[r] || 0;
      const fl   = fil[r] || 0;
      const gap  = rq - fl;
      const rate = rq ? fl / rq : 0;
      const gapCls  = gap > 0 ? "gap-short" : gap < 0 ? "gap-over" : "gap-even";
      const fillCls = !rq                 ? "fill-empty"
                   : rate >= 1            ? "fill-ok"
                   : rate >= 0.8          ? "fill-warn"
                                          : "fill-bad";
      const fillLbl = rq ? `<span class="fill-cell ${fillCls}">${fmtPct(rate)}</span>` : '<span class="fill-empty">—</span>';
      return `
        <tr>
          <td>${r}</td>
          <td class="num">${fmtInt(rq)}</td>
          <td class="num">${fmtInt(fl)}</td>
          <td class="num ${gapCls}">${fmtInt(gap)}</td>
          <td class="num">${fillLbl}</td>
        </tr>`;
    }).join("");

    // <details> makes each card natively collapsible. Default behaviour:
    // collapse most cards (there's a compact quick-stat on the head); keep
    // the "next shutdown" card and any in-progress ones expanded so the
    // current action is one glance.
    const card = document.createElement("details");
    const isNext = state.nextShutdownId === s.id;
    card.className = "sd-card" + (isNext ? " sd-card-next" : "");
    card.open = isNext || s.status === "in_progress";
    const nextPill = isNext ? '<span class="sd-card-next-pill">Up next</span>' : "";
    // Per-site dashboard link (Kleenheat has none — it's a historical roster).
    // stopPropagation on click so hitting the link doesn't also toggle the
    // enclosing <details> open/closed.
    const siteUrl = SHUTDOWN_LINKS[s.id];
    const siteLink = siteUrl
      ? `<a class="sd-open" href="${siteUrl}" target="_blank" rel="noopener"
           onclick="event.stopPropagation()"
           title="Open ${s.company}'s workforce dashboard in a new tab"
        >Open site dashboard <span aria-hidden="true">↗</span></a>`
      : "";
    card.innerHTML = `
      <summary class="sd-head">
        <div class="sd-title">
          <span class="co-dot" style="background:${companyColor(s.company)}"></span>
          <span class="sd-co">${s.company}</span>
          <span class="sd-sep">&middot;</span>
          <span class="sd-name">${s.name}</span>${nextPill}
          ${siteLink}
        </div>
        <div class="sd-meta">
          <span class="sd-status status-${s.status}">${statusLabel(s.status)}</span>
          ${overstaffedPill}${archivedPill}
          <span class="sd-dates">${fmtDate(s.start_date)} &rarr; ${fmtDate(s.end_date)}</span>
          <span class="sd-site">${s.site || ""}</span>
          <span class="sd-quick">${fmtInt(totalReq)} / ${fmtInt(totalFilled)}${isPlaceholder ? '<span class="kpi-star">*</span>' : ""} &middot; ${totalReq ? fmtPct(fillRate) : "—"}</span>
          <span class="sd-chevron" aria-hidden="true">&#9662;</span>
        </div>
      </summary>
      <div class="sd-body">
        <div class="sd-kpis">
          <div class="sd-kpi"><span class="sd-kpi-lbl">Planned</span><span class="sd-kpi-val">${fmtInt(totalReq)}${isPlaceholder ? '<span class="kpi-star">*</span>' : ""}</span></div>
          <div class="sd-kpi"><span class="sd-kpi-lbl">Confirmed</span><span class="sd-kpi-val">${fmtInt(totalFilled)}</span></div>
          <div class="sd-kpi"><span class="sd-kpi-lbl">Gap</span><span class="sd-kpi-val ${totalGap > 0 ? "gap-short" : totalGap < 0 ? "gap-over" : "gap-even"}">${fmtInt(totalGap)}</span></div>
          <div class="sd-kpi"><span class="sd-kpi-lbl">Fill rate</span><span class="sd-kpi-val">${totalReq ? fmtPct(fillRate) : "—"}${isPlaceholder ? '<span class="kpi-star">*</span>' : ""}</span></div>
        </div>
        <div class="table-wrap sd-table-wrap">
          <table class="sd-table">
            <thead><tr>
              <th>Role</th>
              <th class="num">Required</th>
              <th class="num">Filled</th>
              <th class="num">Gap</th>
              <th class="num">Fill rate</th>
            </tr></thead>
            <tbody>${body}
              <tr class="sd-total">
                <td>Total</td>
                <td class="num">${fmtInt(totalReq)}</td>
                <td class="num">${fmtInt(totalFilled)}</td>
                <td class="num ${totalGap > 0 ? "gap-short" : totalGap < 0 ? "gap-over" : "gap-even"}">${fmtInt(totalGap)}</td>
                <td class="num">${totalReq ? `<span class="fill-cell ${fillRate >= 1 ? "fill-ok" : fillRate >= 0.8 ? "fill-warn" : "fill-bad"}">${fmtPct(fillRate)}</span>` : '<span class="fill-empty">—</span>'}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    `;
    host.appendChild(card);
  }
}

/**
 * Worker retention matrix — one row per unique worker (normalised name + role)
 * across ALL shutdowns (not just the filtered view, so cross-company stickiness
 * stays visible even when filtered down to one company). Tick marks identify
 * the shutdowns each worker was rostered on, sorted by total count descending.
 *
 * Param is intentionally ignored: the matrix reads `state.shutdowns` directly
 * so the filter chip above can whittle the KPI view without also collapsing
 * the return history. Kept on the signature so `render()`'s dispatch table
 * stays uniform.
 */
function updateMatrixConflictCounts() {
  const table = document.getElementById("worker-matrix");
  if (!table || !table.tHead || !table.tBodies[0]) return;
  const headerRow = table.tHead.querySelector("tr");
  if (!headerRow) return;
  for (const th of headerRow.cells) {
    const sid = th.dataset.shutdownId;
    if (!sid) continue;
    const count = table.tBodies[0]
      .querySelectorAll(`td[data-shutdown-id="${sid}"].availability-conflict`).length;
    let badge = th.querySelector(".conflict-count-badge");
    if (count > 0) {
      if (!badge) {
        badge = document.createElement("span");
        badge.className = "conflict-count-badge";
        const btn = th.querySelector(".matrix-col-filter");
        th.insertBefore(badge, btn || null);
      }
      badge.textContent = count + "✕";
    } else if (badge) {
      badge.remove();
    }
  }
}

function renderWorkerMatrix(_viewShutdowns) {
  const table = document.getElementById("worker-matrix");
  if (!table) return;

  // Always show all shutdowns in chronological order as the columns, so
  // switching filters doesn't hide the context needed to spot returners.
  const shutdowns = [...state.shutdowns].sort((a, b) => a.start_date.localeCompare(b.start_date));

  // Build one record per unique worker (keyed by normalised name only, so a
  // worker who changes role between shutdowns is still the same row). Track
  // their role at every shutdown so we can display the LATEST as the "current
  // role" while still exposing role history on hover.
  const workers = new Map();
  for (const s of shutdowns) {
    for (const w of s.roster) {
      const k = workerKey(w);
      if (!workers.has(k)) {
        workers.set(k, {
          key: k,
          displayName: w.name,      // standardised at load
          rolesByShutdown: {},      // shutdownId -> role
          appearances: new Set(),
          mobile: "",               // latest non-empty wins
        });
      }
      const rec = workers.get(k);
      rec.rolesByShutdown[s.id] = w.role;
      rec.appearances.add(s.id);
      // Keep the display name in sync if a later shutdown has a fuller form
      // (e.g. standardised "Julian VAN DER ZANDEN" vs bare "Julian").
      if (w.name.length > rec.displayName.length) rec.displayName = w.name;
      // Track the most-recent known mobile number. Iterating shutdowns in
      // chronological order means a later roster's number overrides an
      // earlier one — workers do sometimes change numbers.
      if (w.mobile && String(w.mobile).trim()) rec.mobile = String(w.mobile).trim();
    }
  }
  // Resolve each worker's "latest role" = role at their most recent shutdown.
  const rows = [...workers.values()].map(w => {
    const latest = shutdowns.filter(s => w.appearances.has(s.id)).slice(-1)[0];
    const latestRole = latest ? w.rolesByShutdown[latest.id] : "";
    const priorRoles = [...new Set(Object.values(w.rolesByShutdown))].filter(r => r !== latestRole);
    return {
      ...w,
      total:       w.appearances.size,
      role:        latestRole,
      priorRoles,                       // non-empty only when the worker changed role
    };
  }).sort((a, b) => b.total - a.total || a.displayName.localeCompare(b.displayName));

  // Header — company dot + short name per shutdown, plus a tri-state
  // click-to-cycle filter chip (Any → Present → Absent → Any). When a
  // company has more than one shutdown (Tianqi's Construction + Scaffold
  // scopes, for instance) we also show a compact project tag so the
  // columns don't just read as "Tianqi / Tianqi".
  const companyCounts = shutdowns.reduce((a, s) => { a[s.company] = (a[s.company] || 0) + 1; return a; }, {});
  const shortProject = (name) => name.replace(/ Project$/, "").replace(/ Shutdown.*$/, "").replace(/\s+\d{4}$/, "").split(" ").slice(0, 2).join(" ");
  const thead = table.querySelector("thead");
  thead.innerHTML = `<tr>
    <th>Worker</th>
    <th>Role</th>
    <th>Mobile</th>
    ${shutdowns.map(s => {
      const fstate = state.matrixFilters[s.id] || "any";
      const flabel = fstate === "present" ? "✓ only"
                   : fstate === "absent"  ? "✗ only"
                   : fstate === "blank"   ? "· blank"
                   : "any";
      const projectLine = companyCounts[s.company] > 1
        ? `<span class="matrix-col-sub">${shortProject(s.name)}</span>`
        : "";
      return `<th class="num matrix-col" data-shutdown-id="${s.id}">
        <span class="co-dot" style="background:${companyColor(s.company)}"></span>
        ${s.company}<br>
        ${projectLine}
        <span class="matrix-col-sub">${fmtDate(s.start_date)}</span>
        <button type="button"
                class="matrix-col-filter"
                data-shutdown-id="${s.id}"
                data-state="${fstate}"
                title="Click to cycle: any → ✓ present only → ✗ rejected/unavailable only">${flabel}</button>
      </th>`;
    }).join("")}
    <th class="num">Shutdowns</th>
  </tr>`;

  // Apply the active per-column filters. "present" and "blank" can be partially
  // data-driven; "absent" and "blank" are also DOM-driven (availability overlay
  // runs async), so we schedule DOM passes below.
  const filteredRows = rows.filter(w => {
    for (const [sid, st] of Object.entries(state.matrixFilters)) {
      if (st === "present" && !w.appearances.has(sid)) return false;
      // Rostered workers can never be blank — exclude them immediately.
      if (st === "blank" && w.appearances.has(sid)) return false;
    }
    return true;
  });

  const tbody = table.querySelector("tbody");
  const html = filteredRows.map(w => {
    const roleCell = w.priorRoles.length
      ? `<td title="Previously: ${w.priorRoles.join(", ")}">${w.role} <span class="role-shift" aria-hidden="true">↗</span></td>`
      : `<td>${w.role}</td>`;
    // tel: link — strip spaces for the href so iOS/Android dialers accept it.
    const mobileCell = w.mobile
      ? `<td><a class="mobile-link" href="tel:${w.mobile.replace(/\s+/g, "")}">${w.mobile}</a></td>`
      : `<td class="fill-empty">—</td>`;
    return `<tr data-key="${w.key}">
      <td>${w.displayName} ${resumeBadge(w.displayName)}</td>
      ${roleCell}
      ${mobileCell}
      ${shutdowns.map(s => {
        const r = w.rolesByShutdown[s.id];
        return `<td class="num" data-shutdown-id="${s.id}">${r
          ? `<span class="tick" title="${r}" aria-label="${r}">&#10003;</span>`
          : '<span class="tick-empty" aria-label="Not rostered"></span>'}</td>`;
      }).join("")}
      <td class="num ${w.total > 1 ? "returner-count" : ""}">${w.total}</td>
    </tr>`;
  }).join("");
  tbody.innerHTML = html;
  // Bump render generation so any DOM-filter passes from a previous render
  // know they're stale and should bail without touching the new rows.
  const renderGen = ++state.matrixRenderGen;
  // Tell the availability overlay the table was rebuilt so it re-applies marks.
  document.dispatchEvent(new CustomEvent('matrixrendered'));
  // Schedule conflict-count badge updates (runs after overlay settles).
  [400, 800, 1600, 2500].forEach(ms => setTimeout(updateMatrixConflictCounts, ms));

  // Schedule DOM-based passes for "✗ only" (absent) and "· blank" columns.
  // Cells are looked up by data-shutdown-id so column insertions don't shift indices.
  const conflictShutdownIds = Object.entries(state.matrixFilters)
    .filter(([, st]) => st === "absent").map(([sid]) => sid);
  const blankShutdownIds = Object.entries(state.matrixFilters)
    .filter(([, st]) => st === "blank").map(([sid]) => sid);
  if (conflictShutdownIds.length || blankShutdownIds.length) {
    const applyDomFilter = () => {
      // Bail if the table has been rebuilt since this pass was scheduled, or if
      // the user has cycled the filter to a different state.
      if (state.matrixRenderGen !== renderGen) return;
      if (!conflictShutdownIds.every(sid => state.matrixFilters[sid] === "absent")) return;
      if (!blankShutdownIds.every(sid => state.matrixFilters[sid] === "blank")) return;
      const q = state.matrixSearch;
      tbody.querySelectorAll("tr").forEach(tr => {
        const ok =
          conflictShutdownIds.every(sid => {
            const cell = tr.querySelector(`td[data-shutdown-id="${sid}"]`);
            return cell && cell.classList.contains("availability-conflict");
          }) &&
          blankShutdownIds.every(sid => {
            const cell = tr.querySelector(`td[data-shutdown-id="${sid}"]`);
            return cell && !cell.querySelector(".tick") && !cell.classList.contains("availability-conflict");
          });
        if (!ok) {
          tr.style.display = "none";
        } else {
          tr.style.display = (!q || tr.textContent.toLowerCase().includes(q)) ? "" : "none";
        }
      });
    };
    [100, 400, 900, 1800, 3000].forEach(ms => setTimeout(applyDomFilter, ms));
  }

  // Re-apply any live text search on top of the column filters, so typing
  // "joe" then clicking a column filter keeps the typed query in effect.
  if (state.matrixSearch) {
    tbody.querySelectorAll("tr").forEach(tr => {
      if (!tr.textContent.toLowerCase().includes(state.matrixSearch)) tr.style.display = "none";
    });
  }

  // Click handlers — cycle each column filter Any → Present → Absent → Any.
  // Re-wired per render; old handler clears with innerHTML replacement above.
  thead.addEventListener("click", onMatrixHeaderClick);

  // Count + search + active-filter summary.
  const countEl = document.getElementById("matrix-count");
  const total   = rows.length;
  const returners = rows.filter(w => w.total > 1).length;
  const activeFilters = Object.entries(state.matrixFilters);
  const baseSummary = `<strong>${fmtInt(total)}</strong> unique workers &middot; <strong>${fmtInt(returners)}</strong> returner${returners === 1 ? "" : "s"}`;

  // Build a "Clear filters" link that appears when at least one column or
  // search is active.
  const clearLink = (activeFilters.length || state.matrixSearch)
    ? ` <button type="button" id="matrix-clear" class="matrix-clear">Clear filters</button>`
    : "";

  if (countEl) {
    countEl.innerHTML = (filteredRows.length === rows.length
      ? baseSummary
      : `<strong>${fmtInt(filteredRows.length)}</strong> of ${fmtInt(total)} shown`) + clearLink;
  }

  // Wire search input (debounced via state so re-renders keep the query).
  const search = document.getElementById("matrix-search");
  if (search) {
    if (search.value !== state.matrixSearch) search.value = state.matrixSearch;
    search.oninput = () => {
      state.matrixSearch = search.value.trim().toLowerCase();
      // Apply purely via DOM so we don't tear the whole matrix down on every keystroke.
      const q = state.matrixSearch;
      const trs = tbody.querySelectorAll("tr");
      let visible = 0;
      trs.forEach(tr => {
        const match = !q || tr.textContent.toLowerCase().includes(q);
        tr.style.display = match ? "" : "none";
        if (match) visible++;
      });
      countEl.innerHTML = q
        ? `<strong>${fmtInt(visible)}</strong> of ${fmtInt(filteredRows.length)} shown` +
          ` <button type="button" id="matrix-clear" class="matrix-clear">Clear filters</button>`
        : (filteredRows.length === rows.length ? baseSummary : `<strong>${fmtInt(filteredRows.length)}</strong> of ${fmtInt(total)} shown`) + clearLink;
    };
  }

  const clearBtn = document.getElementById("matrix-clear");
  if (clearBtn) {
    clearBtn.onclick = () => {
      state.matrixFilters = {};
      state.matrixSearch  = "";
      if (search) search.value = "";
      renderWorkerMatrix();
    };
  }
}

// Column-header click handler for tri-state filter cycling. Kept as a module-
// level function so re-renders don't stack listeners (the innerHTML replacement
// already drops them, but this is tidier).
function onMatrixHeaderClick(e) {
  const btn = e.target.closest(".matrix-col-filter");
  if (!btn) return;
  const sid = btn.dataset.shutdownId;
  const cur = state.matrixFilters[sid] || "any";
  const next = cur === "any"     ? "present"
             : cur === "present" ? "absent"
             : cur === "absent"  ? "blank"
                                 : "any";
  if (next === "any") delete state.matrixFilters[sid];
  else state.matrixFilters[sid] = next;
  renderWorkerMatrix();
}

/**
 * Show / hide the placeholder-target banner. The Rapid Crews roster export
 * doesn't carry the original requested headcount, so when a shutdown's
 * `_source.required_target_source === "PLACEHOLDER_FROM_ROSTER"` the dashboard
 * is using `required = filled`, which makes fill-rate trivially 100%. This
 * banner makes that obvious and tells the user how to override.
 */
function togglePlaceholderBanner(placeholderShutdowns, allInView) {
  const host = document.getElementById("placeholder-banner");
  if (!host) return;
  if (placeholderShutdowns.length === 0) { host.hidden = true; return; }
  const ids = placeholderShutdowns.map(s => s.id).join(", ");
  host.hidden = false;
  host.innerHTML =
    `<strong>Heads up:</strong> ${placeholderShutdowns.length} of ${allInView.length} shutdown(s) ` +
    `have no Required figure in Rapid Crews — fill-rate KPIs marked <span class="kpi-star">*</span> ` +
    `default to 100% of the confirmed roster. Add the JobNo to Rapid Crews' JobPlanningView ` +
    `or drop a manual override at <code>data/targets/&lt;shutdown_id&gt;.json</code> ` +
    `(affected: <code>${ids}</code>).`;
}

function renderWarnings() {
  const host = document.getElementById("warnings");
  host.innerHTML = "";
  const matches = ambiguousMatches(state.shutdowns);
  if (matches.length === 0) {
    host.innerHTML = `<p class="muted">No ambiguous matches detected.</p>`;
    return;
  }
  const ul = document.createElement("ul");
  for (const m of matches) {
    const li = document.createElement("li");
    li.innerHTML = `<strong>${m.name}</strong> (${m.role}) &mdash; ${m.a.company} <em>${m.a.shutdown}</em> (${m.a.dates}) vs. ${m.b.company} <em>${m.b.shutdown}</em> (${m.b.dates})`;
    ul.appendChild(li);
  }
  host.appendChild(ul);
}

// -------------------- boot --------------------

window.addEventListener("DOMContentLoaded", () => {
  load().catch(err => {
    const banner = document.getElementById("placeholder-banner");
    if (banner) {
      banner.hidden = false;
      banner.innerHTML = `<strong>Failed to load data:</strong> ${err.message}`;
    }
    console.error(err);
  });
});

// Reflow the Gantt when the viewport resizes so the bars keep filling the
// card width. Debounced to a single trailing call per burst.
let _resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(_resizeTimer);
  _resizeTimer = setTimeout(() => {
    if (!state.shutdowns.length) return;
    const v = filtered();
    try { renderGantt(v); }      catch (e) { console.error("[resize] gantt failed:", e); }
    try { renderOpsRoster(v); }  catch (e) { console.error("[resize] ops roster failed:", e); }
  }, 120);
});
