/* Executive retention table formatter.
 *
 * Rebuilds #retention-table after the base app renders it. This keeps app.js
 * stable while presenting retention as an operational signal table:
 *   Shutdown | Client | Start | Roster | Same client | SRG carry-over | New | Labour hire | Signal
 */
(function () {
  'use strict';

  const DATA_FILES = ['data/covalent.json', 'data/tronox.json', 'data/csbp.json'];
  let shutdownIndex = new Map();
  let attempts = 0;
  let timer = null;

  function normalise(value) {
    return String(value || '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function parseCount(value) {
    const match = String(value || '').match(/-?\d+/);
    return match ? Number(match[0]) : 0;
  }

  function pct(count, total) {
    if (!total) return 0;
    return Math.round((count / total) * 100);
  }

  function fmtDate(value) {
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value || '—');
    return d.toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' });
  }

  function signalFor(srgPct, freshPct, labourHirePct) {
    if (labourHirePct >= 50) return { label: 'High labour hire', tone: 'warn' };
    if (freshPct >= 50) return { label: 'High new load', tone: 'warn' };
    if (srgPct >= 70) return { label: 'Strong SRG carry-over', tone: 'good' };
    if (srgPct >= 50) return { label: 'Healthy SRG carry-over', tone: 'ok' };
    if (srgPct < 35) return { label: 'Low SRG carry-over', tone: 'bad' };
    return { label: 'Watch', tone: 'watch' };
  }

  function barHTML(percent) {
    const safe = Math.max(0, Math.min(100, Number(percent) || 0));
    return `<span class="retention-bar" aria-hidden="true"><span style="width:${safe}%"></span></span>`;
  }

  function metricHTML(count, total, emphasise) {
    const percent = pct(count, total);
    return `<div class="retention-metric ${emphasise ? 'primary' : ''}">
      <div><strong>${percent}%</strong><span>${count}/${total}</span></div>
      ${barHTML(percent)}
    </div>`;
  }

  function compactMetricHTML(count, total, className) {
    const percent = pct(count, total);
    return `<div class="new-hire-load ${className || ''}"><strong>${count}</strong><span>${percent}%</span></div>`;
  }

  function nameKey(value) {
    return String(value || '')
      .toLowerCase()
      .replace(/[^a-z\s]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function isLabourHire(worker) {
    const company = String(worker && (worker.hire_company || worker.hiring_company || '') || '').trim();
    return company && !/\bSRG\b/i.test(company);
  }

  async function loadShutdownIndex() {
    const index = new Map();
    for (const file of DATA_FILES) {
      try {
        const response = await fetch(file, { cache: 'no-store' });
        if (!response.ok) continue;
        const payload = await response.json();
        for (const shutdown of payload.shutdowns || []) {
          const name = String(shutdown.name || shutdown.id || '');
          if (!name) continue;
          const key = normalise(name);
          const roster = Array.isArray(shutdown.roster) ? shutdown.roster : [];
          // De-dupe by normalised name so the labour-hire count and the
          // roster count use the same denominator the worker matrix shows
          // (one row per unique worker) and the retention metrics are
          // computed against (rosterKeys is name-keyed).
          const seen = new Set();
          let labourHire = 0;
          for (const worker of roster) {
            const k = nameKey(worker.name);
            if (!k || seen.has(k)) continue;
            seen.add(k);
            if (isLabourHire(worker)) labourHire += 1;
          }
          index.set(key, {
            name,
            company: payload.company || shutdown.company || '',
            start_date: shutdown.start_date || '',
            roster: seen.size,
            labourHire
          });
        }
      } catch (error) {
        console.warn('[retention-table-executive] skipped', file, error);
      }
    }
    shutdownIndex = index;
  }

  function decorate() {
    const table = document.getElementById('retention-table');
    if (!table || !table.tBodies.length) return false;
    if (table.dataset.executiveRetention === 'true') return true;

    const sourceRows = [...table.tBodies[0].rows];
    if (!sourceRows.length) return false;

    const rows = sourceRows.map(row => {
      const cells = [...row.cells].map(cell => cell.textContent.trim());
      const shutdown = cells[0] || '';
      const indexed = shutdownIndex.get(normalise(shutdown)) || {};
      const company = cells[1] || indexed.company || '';
      const start = cells[2] || indexed.start_date || '';
      // Roster size = unique named workers in this shutdown's roster. This is
      // the denominator the upstream retention metrics (sameRet / crossRet /
      // newHires) were computed against, and it matches the worker matrix's
      // tick count for this shutdown's column. cells[3] carries
      // s.metrics.rosterSize from app.js; the indexed value is a fallback.
      const roster = parseCount(cells[3]) || indexed.roster || 0;
      const same = parseCount(cells[4]);
      // cells[5] is crossRet (all SRG returning, including same-client workers).
      // Executive definition of "SRG carry-over" uses this full returning SRG
      // cohort, so it should never be below "Same client".
      const crossRetRaw = parseCount(cells[5]);
      const srgCarry = Math.max(0, crossRetRaw);
      // "New" = workers in this roster who hadn't appeared in any prior
      // shutdown anywhere. Pull straight from cells[6] (s.metrics.newHires)
      // so same + (crossRet - same) + new always sums back to roster.
      const fresh = parseCount(cells[6]);
      const labourHireRaw = indexed.labourHire || 0;
      const labourHire = Math.min(labourHireRaw, roster || labourHireRaw);
      const srgPct = pct(srgCarry, roster);
      const freshPct = pct(fresh, roster);
      const labourHirePct = pct(labourHire, roster);
      return { shutdown, company, start, roster, same, srgCarry, fresh, labourHire, srgPct, freshPct, labourHirePct };
    });

    table.classList.add('retention-executive-table');
    table.dataset.executiveRetention = 'true';
    table.tHead.innerHTML = `<tr>
      <th>Shutdown</th>
      <th>Client</th>
      <th>Start</th>
      <th class="num">Roster</th>
      <th>Same client</th>
      <th>SRG carry-over</th>
      <th>New</th>
      <th>Labour hire</th>
      <th>Signal</th>
    </tr>`;

    table.tBodies[0].innerHTML = rows.map(row => {
      const signal = signalFor(row.srgPct, row.freshPct, row.labourHirePct);
      return `<tr>
        <td class="ret-shutdown"><strong>${row.shutdown}</strong></td>
        <td><span class="client-pill">${row.company}</span></td>
        <td class="ret-date">${fmtDate(row.start)}</td>
        <td class="num ret-roster">${row.roster}</td>
        <td>${metricHTML(row.same, row.roster, false)}</td>
        <td>${metricHTML(row.srgCarry, row.roster, true)}</td>
        <td>${compactMetricHTML(row.fresh, row.roster, 'new-load')}</td>
        <td>${compactMetricHTML(row.labourHire, row.roster, 'labour-hire-load')}</td>
        <td><span class="signal-pill signal-${signal.tone}">${signal.label}</span></td>
      </tr>`;
    }).join('');

    return true;
  }

  async function start() {
    await loadShutdownIndex();
    timer = window.setInterval(() => {
      attempts += 1;
      decorate();
      if (attempts >= 40 || document.getElementById('retention-table')?.dataset.executiveRetention === 'true') {
        window.clearInterval(timer);
      }
    }, 500);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
