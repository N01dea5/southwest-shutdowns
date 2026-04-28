/* Safe Hiring Company enhancement for the worker retention matrix.
 *
 * This script is intentionally isolated from app.js. It does not patch app.js
 * functions and cannot block the main dashboard render. It fetches the same
 * JSON files, builds a name -> hire company/mobile lookup, then decorates the
 * worker matrix table if/when it exists.
 */
(function () {
  'use strict';

  const DATA_FILES = ['data/covalent.json', 'data/tronox.json', 'data/csbp.json'];
  let hireByName = new Map();
  let mobileByName = new Map();
  let attempts = 0;
  let timer = null;

  function normaliseName(value) {
    return String(value || '')
      .toLowerCase()
      .replace(/[^a-z\s]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function cleanCellText(value) {
    return String(value || '')
      .replace(/\bCV\b/g, '')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function formatMobile(value) {
    const digits = String(value || '').replace(/\D/g, '');
    if (!digits) return '';
    let local = digits;
    if (digits.length === 11 && digits.startsWith('61') && digits[2] === '4') {
      local = '0' + digits.slice(2);
    } else if (digits.length === 9 && digits.startsWith('4')) {
      local = '0' + digits;
    }
    if (local.length === 10 && local.startsWith('04')) {
      return `${local.slice(0, 4)} ${local.slice(4, 7)} ${local.slice(7)}`;
    }
    return digits;
  }

  async function loadLookups() {
    const hireMap = new Map();
    const mobileMap = new Map();

    for (const file of DATA_FILES) {
      try {
        const response = await fetch(file, { cache: 'no-store' });
        if (!response.ok) continue;
        const payload = await response.json();

        for (const shutdown of payload.shutdowns || []) {
          for (const worker of shutdown.roster || []) {
            const key = normaliseName(worker.name);
            if (!key) continue;

            const hire = String(worker.hire_company || worker.hiring_company || '').trim();
            if (hire) {
              if (!hireMap.has(key)) hireMap.set(key, new Set());
              hireMap.get(key).add(hire);
            }

            const mobile = formatMobile(worker.mobile || worker.phone || worker.contact_number || '');
            if (mobile && !mobileMap.has(key)) mobileMap.set(key, mobile);
          }
        }
      } catch (error) {
        console.warn('[matrix-hiring-company] skipped', file, error);
      }
    }

    hireByName = new Map([...hireMap.entries()].map(([key, values]) => [key, [...values].sort().join(' / ')]));
    mobileByName = mobileMap;
  }

  function headerCells(table) {
    const row = table && table.tHead ? table.tHead.querySelector('tr') : null;
    return row ? [...row.cells] : [];
  }

  function findNameColumn(headers) {
    const idx = headers.findIndex(cell => /worker|name/i.test(cell.textContent || ''));
    return idx >= 0 ? idx : 0;
  }

  function findRoleColumn(headers) {
    return headers.findIndex(cell => /role|trade|position/i.test(cell.textContent || ''));
  }

  function findMobileColumn(headers) {
    return headers.findIndex(cell => /mobile|phone/i.test(cell.textContent || ''));
  }

  function ensureHeader(table) {
    const headers = headerCells(table);
    if (!headers.length) return null;

    const existing = headers.findIndex(cell => (cell.dataset && cell.dataset.hiringCompanyCol === 'true') || /^hiring company$/i.test((cell.textContent || '').trim()));
    if (existing >= 0) return existing;

    const roleIdx = findRoleColumn(headers);
    const insertAt = roleIdx >= 0 ? roleIdx + 1 : Math.min(2, headers.length);
    const th = document.createElement('th');
    th.textContent = 'Hiring company';
    th.dataset.hiringCompanyCol = 'true';

    const headerRow = table.tHead.querySelector('tr');
    headerRow.insertBefore(th, headerRow.cells[insertAt] || null);
    return insertAt;
  }

  function applyHiringCompanyColumn() {
    try {
      const table = document.getElementById('worker-matrix');
      if (!table || !table.tHead || !table.tBodies || !table.tBodies.length) return false;

      const headersBefore = headerCells(table);
      if (!headersBefore.length) return false;
      const nameIdx = findNameColumn(headersBefore);
      const hireIdx = ensureHeader(table);
      if (hireIdx === null || hireIdx < 0) return false;

      const headersAfter = headerCells(table);
      const mobileIdx = findMobileColumn(headersAfter);

      for (const row of table.tBodies[0].rows) {
        let cell = [...row.cells].find(td => td.dataset && td.dataset.hiringCompanyCol === 'true');
        if (!cell) {
          cell = document.createElement('td');
          cell.dataset.hiringCompanyCol = 'true';
          cell.className = 'muted hire-company-cell';
          row.insertBefore(cell, row.cells[hireIdx] || null);
        }

        const nameText = row.cells[nameIdx] ? cleanCellText(row.cells[nameIdx].textContent) : '';
        const key = normaliseName(nameText);
        const hire = hireByName.get(key);
        cell.textContent = hire || '—';

        if (mobileIdx >= 0 && row.cells[mobileIdx]) {
          const mobileCell = row.cells[mobileIdx];
          const cleanMobile = mobileByName.get(key) || formatMobile(mobileCell.textContent);
          if (cleanMobile) mobileCell.textContent = cleanMobile;
          mobileCell.classList.add('matrix-mobile-cell');
        }
      }
      return true;
    } catch (error) {
      console.warn('[matrix-hiring-company] decoration skipped', error);
      return false;
    }
  }

  async function start() {
    try {
      await loadLookups();
    } catch (error) {
      console.warn('[matrix-hiring-company] lookup load failed', error);
    }

    // Decorate when app.js rebuilds the matrix (filter clicks, etc.).
    document.addEventListener('matrixrendered', applyHiringCompanyColumn);

    // Initial decoration: poll briefly while app.js is building the table for
    // the first time. Bail out as soon as decoration succeeds so the timer
    // doesn't keep firing for the full 20 seconds in the happy path.
    timer = window.setInterval(() => {
      attempts += 1;
      const ok = applyHiringCompanyColumn();
      if (ok || attempts >= 40) window.clearInterval(timer);
    }, 500);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
