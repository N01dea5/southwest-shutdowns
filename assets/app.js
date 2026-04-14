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

// Per-company colours. Brand red (#CC0000) is reserved for SRG Global accents
// (active chip, today marker, KPI rule), so company colours sit clearly outside
// the red family.
const COMPANIES = [
  { key: "covalent", file: "data/covalent.json", color: "#3A7849" }, // forest green
  { key: "tronox",   file: "data/tronox.json",   color: "#3D4250" }, // graphite slate
  { key: "csbp",     file: "data/csbp.json",     color: "#1F4E79" }, // navy blue
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
      state.shutdowns.push({
        ...s,
        status,
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

  const view       = filtered();
  const completed  = view.filter(s => s.status === "completed");
  const inProgress = view.filter(s => s.status === "in_progress");
  const booked     = view.filter(s => s.status === "booked");
  // "Open" = not yet completed. In-progress and booked shutdowns both have
  // positions left to fill and are tracked together in the headline KPI.
  const openSds    = view.filter(s => s.status !== "completed");

  const totalRoll = fulfillmentRollup(view);
  const openRoll  = fulfillmentRollup(openSds);

  // Detect placeholder-target shutdowns (Rapid Crews roster only — no real
  // headcount target supplied yet). When present, fill rate trivially reads
  // 100%; we mark the KPIs and surface a banner so it isn't misleading.
  const placeholderShutdowns = view.filter(s =>
    s._source && s._source.required_target_source === "PLACEHOLDER_FROM_ROSTER");
  const allPlaceholder = view.length > 0 && placeholderShutdowns.length === view.length;
  togglePlaceholderBanner(placeholderShutdowns, view);

  const star = (cond) => cond ? '<span class="kpi-star" title="No real target supplied — value derived from confirmed roster">*</span>' : "";

  document.getElementById("kpi-required").innerHTML = fmtInt(totalRoll.required) + star(allPlaceholder);
  document.getElementById("kpi-filled").textContent = fmtInt(totalRoll.filled);
  document.getElementById("kpi-fillrate").innerHTML = (totalRoll.required
    ? fmtPct(totalRoll.filled / totalRoll.required)
    : "—") + star(allPlaceholder);
  document.getElementById("kpi-booked").innerHTML   = (openRoll.required
    ? `${fmtInt(openRoll.filled)} / ${fmtInt(openRoll.required)}`
    : "—") + star(openSds.length > 0 && openSds.every(s => s._source?.required_target_source === "PLACEHOLDER_FROM_ROSTER"));
  document.getElementById("kpi-booked-sub").textContent = openRoll.required
    ? `${fmtPct(openRoll.filled / openRoll.required)} confirmed`
    : "booked / in prog / done";
  document.getElementById("kpi-shutdowns").textContent =
    `${fmtInt(booked.length)} / ${fmtInt(inProgress.length)} / ${fmtInt(completed.length)}`;

  // Each render step is isolated — one failure shouldn't black out the rest of the page.
  const chartRoll = fulfillmentRollup(view);
  const steps = [
    ["company chart",    () => renderCompanyChart(chartRoll)],
    ["trade chart",      () => renderTradeChart(chartRoll)],
    ["gantt",            () => renderGantt(view)],
    ["shutdown summary", () => renderShutdownSummary(view)],
    ["retention chart",  () => renderRetentionChart(view)],
    ["retention table",  () => renderRetentionTable(view)],
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

/**
 * Swimlane Gantt of all shutdowns in the filtered view.
 *   - one lane per company
 *   - x-axis: month ticks spanning [earliest start, latest end], padded to month boundaries
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

  // Span: pad to month start/end so month ticks look clean.
  const minStart = view.reduce((m, s) => s.start_date < m ? s.start_date : m, view[0].start_date);
  const maxEnd   = view.reduce((m, s) => s.end_date   > m ? s.end_date   : m, view[0].end_date);
  const spanStart = new Date(Date.UTC(+minStart.slice(0, 4), +minStart.slice(5, 7) - 1, 1));
  const endD = new Date(maxEnd + "T00:00:00Z");
  const spanEnd   = new Date(Date.UTC(endD.getUTCFullYear(), endD.getUTCMonth() + 1, 1));
  const totalMs   = spanEnd - spanStart;

  const pct = (d) => ((d - spanStart) / totalMs) * 100;

  // --- Axis: month ticks ---
  const axis = document.createElement("div");
  axis.className = "gantt-axis";
  const cursor = new Date(spanStart);
  while (cursor < spanEnd) {
    const next = new Date(Date.UTC(cursor.getUTCFullYear(), cursor.getUTCMonth() + 1, 1));
    const left = pct(cursor);
    const width = pct(next) - left;
    const tick = document.createElement("div");
    tick.className = "gantt-tick";
    tick.style.left = left + "%";
    tick.style.width = width + "%";
    const monthLabel = cursor.toLocaleDateString(undefined, { month: "short", timeZone: "UTC" });
    const yearLabel  = cursor.getUTCMonth() === 0 ? cursor.getUTCFullYear() : "";
    tick.innerHTML = `<span class="mo">${monthLabel}</span><span class="yr">${yearLabel}</span>`;
    axis.appendChild(tick);
    cursor.setUTCMonth(cursor.getUTCMonth() + 1);
  }
  host.appendChild(axis);

  // --- Body: one swimlane per company ---
  const body = document.createElement("div");
  body.className = "gantt-body";

  // Gridlines aligned with the month ticks — drawn once behind all lanes.
  const grid = document.createElement("div");
  grid.className = "gantt-grid";
  const g = new Date(spanStart);
  while (g < spanEnd) {
    const line = document.createElement("div");
    line.className = "gantt-gridline";
    line.style.left = pct(g) + "%";
    grid.appendChild(line);
    g.setUTCMonth(g.getUTCMonth() + 1);
  }
  body.appendChild(grid);

  // Today marker — inside .gantt-grid so its % positioning aligns with the tracks.
  const today = new Date();
  if (today >= spanStart && today <= spanEnd) {
    const todayLine = document.createElement("div");
    todayLine.className = "gantt-today";
    todayLine.style.left = pct(today) + "%";
    todayLine.title = "Today";
    grid.appendChild(todayLine);
  }

  for (const lane of lanes) {
    const row = document.createElement("div");
    row.className = "gantt-row";

    const label = document.createElement("div");
    label.className = "gantt-row-label";
    label.innerHTML = `<span class="co-dot" style="background:${companyColor(lane)}"></span>${lane}`;
    row.appendChild(label);

    const track = document.createElement("div");
    track.className = "gantt-track";

    for (const s of view.filter(x => x.company === lane)) {
      const sd = new Date(s.start_date + "T00:00:00Z");
      const ed = new Date(s.end_date + "T00:00:00Z");
      const filled = Object.values(s.filled_by_role).reduce((a, b) => a + b, 0);
      const req    = Object.values(s.required_by_role).reduce((a, b) => a + b, 0);
      const fillPct = req ? filled / req : 0;

      const bar = document.createElement("div");
      bar.className = "gantt-bar status-" + s.status + (s.status === "booked" ? " booked" : "");
      bar.style.left  = pct(sd) + "%";
      bar.style.width = Math.max(0.4, pct(ed) - pct(sd)) + "%";
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

  host.appendChild(body);
}

function statusLabel(st) {
  return st === "in_progress" ? "In progress"
       : st === "completed"   ? "Completed"
       : "Booked";
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

    const body = roles.map(r => {
      const rq   = req[r] || 0;
      const fl   = fil[r] || 0;
      const gap  = rq - fl;
      const rate = rq ? fl / rq : 0;
      const gapCls = gap > 0 ? "gap-short" : gap < 0 ? "gap-over" : "gap-even";
      return `
        <tr>
          <td>${r}</td>
          <td class="num">${fmtInt(rq)}</td>
          <td class="num">${fmtInt(fl)}</td>
          <td class="num ${gapCls}">${gap > 0 ? "+" : ""}${fmtInt(gap)}</td>
          <td class="num">${rq ? fmtPct(rate) : "—"}</td>
        </tr>`;
    }).join("");

    const card = document.createElement("div");
    card.className = "sd-card";
    card.innerHTML = `
      <div class="sd-head">
        <div class="sd-title">
          <span class="co-dot" style="background:${companyColor(s.company)}"></span>
          <span class="sd-co">${s.company}</span>
          <span class="sd-sep">&middot;</span>
          <span class="sd-name">${s.name}</span>
        </div>
        <div class="sd-meta">
          <span class="sd-status status-${s.status}">${statusLabel(s.status)}</span>
          <span class="sd-dates">${fmtDate(s.start_date)} &rarr; ${fmtDate(s.end_date)}</span>
          <span class="sd-site">${s.site || ""}</span>
        </div>
      </div>
      <div class="sd-kpis">
        <div class="sd-kpi"><span class="sd-kpi-lbl">Planned</span><span class="sd-kpi-val">${fmtInt(totalReq)}${isPlaceholder ? '<span class="kpi-star">*</span>' : ""}</span></div>
        <div class="sd-kpi"><span class="sd-kpi-lbl">Confirmed</span><span class="sd-kpi-val">${fmtInt(totalFilled)}</span></div>
        <div class="sd-kpi"><span class="sd-kpi-lbl">Gap</span><span class="sd-kpi-val ${totalGap > 0 ? "gap-short" : totalGap < 0 ? "gap-over" : "gap-even"}">${totalGap > 0 ? "+" : ""}${fmtInt(totalGap)}</span></div>
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
              <td class="num ${totalGap > 0 ? "gap-short" : totalGap < 0 ? "gap-over" : "gap-even"}">${totalGap > 0 ? "+" : ""}${fmtInt(totalGap)}</td>
              <td class="num">${totalReq ? fmtPct(fillRate) : "—"}</td>
            </tr>
          </tbody>
        </table>
      </div>
    `;
    host.appendChild(card);
  }
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
    `are missing a real headcount target — fill-rate KPIs marked <span class="kpi-star">*</span> ` +
    `default to 100% of the confirmed roster. Drop a target file at ` +
    `<code>data/targets/&lt;shutdown_id&gt;.json</code> to override (affected: ` +
    `<code>${ids}</code>).`;
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
