/* Adds red-cross filtering to the existing worker matrix column filters.
 *
 * app.js owns the base matrix and its present/blank filters. matrix-availability.js
 * overlays red-cross cells after render. This script extends each shutdown
 * column filter with a red-cross option and applies that filter after the
 * availability overlay has marked cells.
 */
(function () {
  'use strict';

  const TABLE_ID = 'worker-matrix';
  const CROSS_VALUE = 'conflict';
  const CROSS_LABEL = '✕ Cross';
  const selectedByColumnKey = new Map();
  let applying = false;

  function table() {
    return document.getElementById(TABLE_ID);
  }

  function headerRows(t) {
    return t && t.tHead ? Array.from(t.tHead.rows) : [];
  }

  function columnKey(select) {
    const th = select.closest('th');
    if (!th) return '';
    const colIndex = th.cellIndex;
    const headerText = th.textContent
      .replace(CROSS_LABEL, '')
      .replace(/All|Present|Absent|Blank|Tick|✓|✔|Cross|✕|×/gi, '')
      .replace(/\s+/g, ' ')
      .trim();
    return `${colIndex}:${headerText}`;
  }

  function hasCrossOption(select) {
    return Array.from(select.options).some(opt => opt.value === CROSS_VALUE);
  }

  function looksLikeMatrixStatusFilter(select) {
    const optionText = Array.from(select.options).map(o => `${o.value} ${o.textContent}`.toLowerCase()).join(' ');
    return optionText.includes('present') || optionText.includes('absent') || optionText.includes('blank') || optionText.includes('✓') || optionText.includes('tick');
  }

  function injectCrossOptions() {
    const t = table();
    if (!t) return;
    const selects = Array.from(t.tHead ? t.tHead.querySelectorAll('select') : []);
    for (const select of selects) {
      if (!looksLikeMatrixStatusFilter(select)) continue;
      const key = columnKey(select);
      if (!hasCrossOption(select)) {
        const option = document.createElement('option');
        option.value = CROSS_VALUE;
        option.textContent = CROSS_LABEL;
        select.appendChild(option);
      }
      if (selectedByColumnKey.get(key) === CROSS_VALUE) {
        select.value = CROSS_VALUE;
      }
    }
  }

  function cellHasCross(cell) {
    if (!cell) return false;
    const text = String(cell.textContent || '').trim();
    return cell.classList.contains('availability-conflict') || text === '✕' || text === '×' || text.toLowerCase() === 'x';
  }

  function activeCrossFilters(t) {
    const filters = [];
    if (!t || !t.tHead) return filters;
    for (const select of Array.from(t.tHead.querySelectorAll('select'))) {
      if (select.value !== CROSS_VALUE) continue;
      const th = select.closest('th');
      if (!th) continue;
      filters.push(th.cellIndex);
    }
    return filters;
  }

  function baseVisible(row) {
    // Preserve any hide behaviour already applied by app.js or other scripts.
    return row.style.display !== 'none' || row.dataset.crossFilterHidden === 'true';
  }

  function applyCrossFilters() {
    if (applying) return;
    applying = true;
    try {
      const t = table();
      if (!t || !t.tBodies.length) return;
      injectCrossOptions();
      const filters = activeCrossFilters(t);
      const rows = Array.from(t.tBodies[0].rows);

      for (const row of rows) {
        if (row.dataset.crossFilterHidden === 'true') {
          row.style.display = '';
          delete row.dataset.crossFilterHidden;
        }
      }

      if (!filters.length) return;

      for (const row of rows) {
        if (!baseVisible(row)) continue;
        const matches = filters.every(idx => cellHasCross(row.cells[idx]));
        if (!matches) {
          row.dataset.crossFilterHidden = 'true';
          row.style.display = 'none';
        }
      }
    } finally {
      applying = false;
    }
  }

  function installListeners() {
    document.addEventListener('change', event => {
      const select = event.target && event.target.closest ? event.target.closest(`#${TABLE_ID} thead select`) : null;
      if (!select) return;
      const key = columnKey(select);
      if (select.value === CROSS_VALUE) selectedByColumnKey.set(key, CROSS_VALUE);
      else selectedByColumnKey.delete(key);
      window.setTimeout(applyCrossFilters, 0);
      window.setTimeout(applyCrossFilters, 650);
    }, true);
  }

  function start() {
    installListeners();
    window.setInterval(applyCrossFilters, 700);
    const root = document.querySelector('.matrix-card') || document.body;
    if (root && 'MutationObserver' in window) {
      const observer = new MutationObserver(() => window.setTimeout(applyCrossFilters, 50));
      observer.observe(root, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['class', 'style'] });
    }
    applyCrossFilters();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
