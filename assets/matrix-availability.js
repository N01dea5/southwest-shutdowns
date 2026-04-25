/* Adds red crosses to the worker retention matrix for calendar conflicts.
 *
 * Data source: data/personnel_calendar.json from xpbi02 PersonnelCalendarView.
 * Behaviour:
 *   - leave existing green tick/check cells untouched when worker is on shutdown
 *   - if blank and calendar event overlaps shutdown date range, show red cross
 *   - otherwise leave blank
 */
(function () {
  'use strict';

  const DATA_FILES = ['data/covalent.json', 'data/tronox.json', 'data/csbp.json'];
  const CALENDAR_FILE = 'data/personnel_calendar.json';
  let shutdownsByLabel = new Map();
  let shutdownsById = new Map();
  let eventsByName = new Map();
  let attempts = 0;
  let timer = null;

  function normText(value) {
    return String(value || '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function normName(value) {
    return String(value || '')
      .replace(/\bCV\b/g, '')
      .toLowerCase()
      .replace(/[^a-z\s]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
      .replace(/\s+/g, '');
  }

  function date(value) {
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return null;
    d.setHours(0, 0, 0, 0);
    return d;
  }

  function overlaps(aStart, aEnd, bStart, bEnd) {
    const as = date(aStart);
    const ae = date(aEnd || aStart);
    const bs = date(bStart);
    const be = date(bEnd || bStart);
    if (!as || !ae || !bs || !be) return false;
    return as <= be && bs <= ae;
  }

  function shutdownKeys(shutdown) {
    const name = String(shutdown.name || shutdown.id || '').trim();
    const id = String(shutdown.id || '').trim();
    const keys = new Set([normText(name), normText(id)]);
    const jobMatch = name.match(/^(\d+)/) || id.match(/(\d{3,})/);
    if (jobMatch) keys.add(jobMatch[1]);
    return [...keys].filter(Boolean);
  }

  async function loadShutdowns() {
    const map = new Map();
    const byId = new Map();
    for (const file of DATA_FILES) {
      try {
        const response = await fetch(file, { cache: 'no-store' });
        if (!response.ok) continue;
        const payload = await response.json();
        for (const shutdown of payload.shutdowns || []) {
          const record = {
            id: shutdown.id || '',
            name: shutdown.name || '',
            start: shutdown.start_date || '',
            end: shutdown.end_date || shutdown.start_date || '',
            status: shutdown.status || ''
          };
          for (const key of shutdownKeys(shutdown)) map.set(key, record);
          if (record.id) byId.set(record.id, record);
        }
      } catch (error) {
        console.warn('[matrix-availability] skipped shutdown data', file, error);
      }
    }
    shutdownsByLabel = map;
    shutdownsById = byId;
  }

  async function loadCalendar() {
    const map = new Map();
    try {
      const response = await fetch(CALENDAR_FILE, { cache: 'no-store' });
      if (!response.ok) return;
      const payload = await response.json();
      for (const event of payload.events || []) {
        const key = normName(event.name_key || event.name);
        if (!key) continue;
        if (!map.has(key)) map.set(key, []);
        map.get(key).push(event);
      }
    } catch (error) {
      console.warn('[matrix-availability] calendar unavailable', error);
    }
    eventsByName = map;
  }

  function findShutdownForHeader(text) {
    const raw = String(text || '').trim();
    const direct = shutdownsByLabel.get(normText(raw));
    if (direct) return direct;
    const jobMatch = raw.match(/\b\d{3,}\b/);
    if (jobMatch && shutdownsByLabel.has(jobMatch[0])) return shutdownsByLabel.get(jobMatch[0]);
    const normal = normText(raw);
    for (const [key, shutdown] of shutdownsByLabel.entries()) {
      if (normal.includes(key) || key.includes(normal)) return shutdown;
    }
    return null;
  }

  function isTickCell(cell) {
    const text = String(cell.textContent || '').trim();
    if (!text) return false;
    return /✓|✔|check|yes|1/i.test(text) || cell.classList.contains('present') || cell.classList.contains('tick');
  }

  function describeEvent(event) {
    const label = event.type === 'time_off' ? 'Booked off' : 'Working elsewhere';
    const desc = [event.description, event.company_or_job, event.job_no].filter(Boolean).join(' · ');
    return desc ? `${label}: ${desc}` : label;
  }

  function applyAvailability() {
    const table = document.getElementById('worker-matrix');
    if (!table || !table.tHead || !table.tBodies.length) return false;

    const headerRow = table.tHead.querySelector('tr');
    if (!headerRow || !headerRow.cells.length) return false;

    const headerCells = [...headerRow.cells];
    const nameIdx = headerCells.findIndex(cell => /worker|name/i.test(cell.textContent || ''));
    if (nameIdx < 0) return false;

    // Identify shutdown columns: use data-shutdown-id when present (robust to
    // extra columns inserted by other scripts), fall back to header text matching.
    const shutdownColumns = headerCells.map((cell, idx) => {
      const sid = cell.dataset && cell.dataset.shutdownId;
      const shutdown = sid ? shutdownsById.get(sid) : findShutdownForHeader(cell.textContent);
      return shutdown ? { idx, sid: sid || shutdown.id, shutdown } : null;
    }).filter(Boolean);

    if (!shutdownColumns.length) return false;

    for (const row of table.tBodies[0].rows) {
      const nameCell = row.cells[nameIdx];
      if (!nameCell) continue;
      const key = normName(nameCell.textContent);
      const events = eventsByName.get(key) || [];
      if (!events.length) continue;

      for (const { idx, sid, shutdown } of shutdownColumns) {
        if (shutdown.status === 'completed') continue;
        // Prefer data-attribute lookup so column insertions don't break the index.
        const cell = sid
          ? (row.querySelector(`td[data-shutdown-id="${sid}"]`) || row.cells[idx])
          : row.cells[idx];
        if (!cell || isTickCell(cell)) continue;

        const conflict = events.find(event => overlaps(event.start, event.end, shutdown.start, shutdown.end));
        if (!conflict) continue;

        cell.textContent = '✕';
        cell.classList.add('availability-conflict');
        cell.title = `${describeEvent(conflict)} (${conflict.start} to ${conflict.end || conflict.start})`;
        cell.setAttribute('aria-label', cell.title);
      }
    }

    table.dataset.availabilityOverlay = 'true';
    return true;
  }

  async function start() {
    await Promise.all([loadShutdowns(), loadCalendar()]);
    timer = window.setInterval(() => {
      attempts += 1;
      applyAvailability();
      if (attempts >= 40) window.clearInterval(timer);
    }, 500);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
