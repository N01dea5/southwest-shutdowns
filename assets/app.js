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

const COMPANIES = [
  { key: "covalent", file: "data/covalent.json", color: "#1f77b4" },
  { key: "tronox",   file: "data/tronox.json",   color: "#d6731b" },
  { key: "csbp",     file: "data/csbp.json",     color: "#2ca02c" },
];

const state = {
  raw: {},            // company-name -> file payload
  shutdowns: [],      // flat, chronological list across all companies
  filter: "all",      // "all" | company display name
  charts: {},         // Chart.js handles, so we can destroy() on re-render
};

// -------------------- helpers --------------------

function normaliseName(n) {
  return n.toLowerCase().trim().replace(/[^a-z\s]/g, "").replace(/\s+/g, " ");
}
function workerKey(w) {
  return normaliseName(w.name) + "|" + w.role.toLowerCase().trim();
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

// -------------------- load --------------------

async function load() {
  const results = await Promise.all(COMPANIES.map(async c => {
    const r = await fetch(c.file, { cache: "no-store" });
    if (!r.ok) throw new Error(`Failed to load ${c.file}: ${r.status}`);
    return r.json();
  }));
  for (const payload of results) state.raw[payload.company] = payload;

  // Flatten and sort chronologically
  state.shutdowns = [];
  for (const payload of results) {
    for (const s of payload.shutdowns) {
      state.shutdowns.push({
        ...s,
        company: payload.company,
        rosterKeys: new Set(s.roster.map(workerKey)),
      });
    }
  }
  state.shutdowns.sort((a, b) => a.start_date.localeCompare(b.start_date));

  renderFreshness(results);
  setupFilter();
  render();
}

function renderFreshness(payloads) {
  const parts = payloads.map(p => {
    const ts = new Date(p.generated_at);
    return `${p.company} &middot; ${ts.toLocaleDateString()} ${ts.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}`;
  });
  document.getElementById("freshness").innerHTML = "Data as of<br>" + parts.join(" &nbsp;|&nbsp; ");
}

function setupFilter() {
  document.getElementById("filterbar").addEventListener("click", e => {
    const btn = e.target.closest(".chip");
    if (!btn) return;
    document.querySelectorAll(".filterbar .chip").forEach(c => c.classList.remove("active"));
    btn.classList.add("active");
    state.filter = btn.dataset.co;
    render();
  });
}

// -------------------- compute --------------------

function filtered() {
  if (state.filter === "all") return state.shutdowns;
  return state.shutdowns.filter(s => s.company === state.filter);
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
      byCompany[s.company].filled += n;
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
  const roll = fulfillmentRollup(view);

  // KPIs
  document.getElementById("kpi-required").textContent = fmtInt(roll.required);
  document.getElementById("kpi-filled").textContent = fmtInt(roll.filled);
  document.getElementById("kpi-fillrate").textContent = roll.required ? fmtPct(roll.filled / roll.required) : "—";
  document.getElementById("kpi-shutdowns").textContent = fmtInt(view.length);

  renderCompanyChart(roll);
  renderTradeChart(roll);
  renderRetentionChart(view);
  renderRetentionTable(view);
  renderTimeline(view);
  renderWarnings();
}

function makeChart(id, config) {
  if (state.charts[id]) state.charts[id].destroy();
  const ctx = document.getElementById(id).getContext("2d");
  state.charts[id] = new Chart(ctx, config);
}

function renderCompanyChart(roll) {
  const labels = Object.keys(roll.byCompany);
  const required = labels.map(l => roll.byCompany[l].required);
  const filled   = labels.map(l => roll.byCompany[l].filled);

  makeChart("chart-company", {
    type: "bar",
    data: {
      labels,
      datasets: [
        { label: "Required", data: required, backgroundColor: "#cfd8e3" },
        { label: "Filled",   data: filled,   backgroundColor: labels.map(companyColor) },
      ],
    },
    options: {
      indexAxis: "y",
      responsive: true, maintainAspectRatio: false,
      scales: { x: { beginAtZero: true } },
      plugins: {
        tooltip: {
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
  const roles = Object.keys(roll.byRole).sort();
  const required = roles.map(r => roll.byRole[r].required);
  const filled   = roles.map(r => roll.byRole[r].filled);

  makeChart("chart-trade", {
    type: "bar",
    data: {
      labels: roles,
      datasets: [
        { label: "Required", data: required, backgroundColor: "#cfd8e3" },
        { label: "Filled",   data: filled,   backgroundColor: "#1f77b4" },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: { y: { beginAtZero: true } },
      plugins: {
        tooltip: {
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
  // One line per company for same-company retention, plus one line for cross-company across the whole view.
  const byCompany = {};
  for (const s of view) {
    (byCompany[s.company] = byCompany[s.company] || []).push(s);
  }
  Object.values(byCompany).forEach(arr => arr.sort((a, b) => a.start_date.localeCompare(b.start_date)));

  // Build a shared x-axis of all shutdowns in the view, chronological.
  const ordered = [...view].sort((a, b) => a.start_date.localeCompare(b.start_date));
  const labels = ordered.map(s => `${s.company} ${s.name}`);

  const datasets = [];
  for (const [co, arr] of Object.entries(byCompany)) {
    datasets.push({
      label: `${co} – same company`,
      data: ordered.map(s => s.company === co ? +(s.metrics.sameRetPct * 100).toFixed(1) : null),
      borderColor: companyColor(co),
      backgroundColor: companyColor(co),
      spanGaps: true,
      tension: 0.25,
      borderWidth: 2,
    });
  }
  datasets.push({
    label: "Any company – cross-company carry-over",
    data: ordered.map(s => +(s.metrics.crossRetPct * 100).toFixed(1)),
    borderColor: "#555",
    backgroundColor: "#555",
    borderDash: [6, 4],
    tension: 0.25,
    borderWidth: 2,
  });

  makeChart("chart-retention", {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: {
        y: { beginAtZero: true, max: 100, ticks: { callback: v => v + "%" } },
        x: { ticks: { maxRotation: 60, minRotation: 30, autoSkip: false } },
      },
      plugins: {
        legend: { position: "bottom" },
        tooltip: { callbacks: { label: ctx => ctx.dataset.label + ": " + ctx.parsed.y + "%" } },
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

function renderTimeline(view) {
  const host = document.getElementById("timeline");
  host.innerHTML = "";
  const rows = [...view].sort((a, b) => a.start_date.localeCompare(b.start_date));
  if (rows.length === 0) { host.textContent = "No shutdowns for this filter."; return; }

  const minDate = new Date(rows[0].start_date);
  const maxDate = new Date(rows[rows.length - 1].end_date);
  const span = Math.max(1, maxDate - minDate);

  const track = document.createElement("div");
  track.className = "timeline-track";
  for (const s of rows) {
    const left = ((new Date(s.start_date) - minDate) / span) * 100;
    const width = Math.max(2, ((new Date(s.end_date) - new Date(s.start_date)) / span) * 100);
    const filled = Object.values(s.filled_by_role).reduce((a, b) => a + b, 0);
    const req    = Object.values(s.required_by_role).reduce((a, b) => a + b, 0);
    const pct = req ? filled / req : 0;

    const block = document.createElement("div");
    block.className = "timeline-block";
    block.style.left = left + "%";
    block.style.width = width + "%";
    block.style.background = companyColor(s.company);
    block.style.opacity = (0.4 + 0.6 * pct).toFixed(2);
    block.title = `${s.company} – ${s.name}\n${s.start_date} → ${s.end_date}\nFill: ${filled}/${req} (${fmtPct(pct)})`;
    block.innerHTML = `<span>${s.company} ${fmtPct(pct)}</span>`;
    track.appendChild(block);
  }
  host.appendChild(track);

  const axis = document.createElement("div");
  axis.className = "timeline-axis";
  axis.innerHTML = `<span>${fmtDate(rows[0].start_date)}</span><span>${fmtDate(rows[rows.length - 1].end_date)}</span>`;
  host.appendChild(axis);
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
    document.getElementById("freshness").textContent = "Failed to load data: " + err.message;
    console.error(err);
  });
});
