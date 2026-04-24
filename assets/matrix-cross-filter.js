/* Adds red-cross filtering to the existing worker matrix column filters.
 *
 * app.js owns the base matrix and its present/blank filters. matrix-availability.js
 * overlays red-cross cells after render. This script extends each shutdown
 * column filter with a red-cross option and corrects the blank filter so red
 * crosses are not treated as blanks.
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

  function columnKey(select) {
    const th = select.closest('th');
    if (!th) return '';
    const colIndex = th.cellIndex;
    const headerText = th.textContent
      .replace(CROSS_LABEL, '')
      .replace(/All|Any|Present|Absent|Blanks|Blank|Tick|✓|✔|Cross|✕|×/gi, '')
      .replace(/\s+/g, ' ')
      .trim();
    return `${colIndex}:${headerText}`;
  }

  function hasCrossOption(select) {
    return Array.from(select.options).some(opt => opt.value === CROSS_VALUE);
  }

  function looksLikeMatrixStatusFilter(select) {
    const optionText = Array.from(select.options).map(o => `${o.value} ${o.textContent}`.toLowerCase()).join(' ');
    return optionText.includes('present') || optionText.includes('absent') || optionText.includes('blank') || optionText.includes('✓') || optionText.includes('tick') || optionText.includes('any');
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

  function cellHasTick(cell) {
    if (!cell) return false;
    const text = String(cell.textContent || '').trim();
    return text === '✓' || text === '✔' || cell.classList.contains('present') || cell.classList.contains('matrix-present');
  }

  function cellIsGenuinelyBlank(cell) {
    if (!cell) return false;
    return !cellHasCross(cell) && !cellHasTick(cell);
  }

  function selectMode(select) {
    const value = String(select.value || '').toLowerCase();
    const selectedText = String(select.options[select.selectedIndex]?.textContent || '').toLowerCase();
    const joined = `${value} ${selectedText}`;
    if (value === CROSS_VALUE) return CROSS_VALUE;
    if (joined.includes('blank') || joined.includes('absent')) return 'blank';
    if (joined.includes('present') || joined.includes('tick') || joined.includes('✓')) return 'present';
    return 'other';
  }

  function activeSpecialFilters(t) {
    const filters = [];
    if (!t || !t.tHead) return filters;
    for (const select of Array.from(t.tHead.querySelectorAll('select'))) {
      const mode = selectMode(select);
      if (mode !== CROSS_VALUE && mode !== 'blank') continue;
      const th = select.closest('th');
      if (!th) continue;
      filters.push({ index: th.cellIndex, mode });
    }
    return filters;
  }

  function activeBlankOrCrossFilterCount(t) {
    return activeSpecialFilters(t).length;
  }

  function applyCrossFilters() {
    if (applying) return;
    applying = true;
    try {
      const t = table();
      if (!t || !t.tBodies.length) return;
      injectCrossOptions();
      const filters = activeSpecialFilters(t);
      const rows = Array.from(t.tBodies[0].rows);

      for (const row of rows) {
        if (row.dataset.crossFilterHidden === 'true') {
          row.style.display = '';
          delete row.dataset.crossFilterHidden;
        }
      }

      if (!filters.length) return;

      for (const row of rows) {
        // Work from the current app.js result. If app.js has hidden it for
        // search or another column, keep it hidden. If this script hid it on
        // the previous pass, allow it to be reconsidered.
        const visibleFromBase = row.style.display !== 'none' || row.dataset.crossFilterHidden === 'true';
        if (!visibleFromBase) continue;

        const matches = filters.every(filter => {
          const cell = row.cells[filter.index];
          if (filter.mode === CROSS_VALUE) return cellHasCross(cell);
          if (filter.mode === 'blank') return cellIsGenuinelyBlank(cell);
          return true;
        });
        if (!matches) {
          row.dataset.crossFilterHidden = 'true';
          row.style.display = 'none';
        }
      }
    } finally {
      applying = false;
    }
  }

  function triggerRepeatedApply() {
    // Red crosses are overlaid after the base table render. Apply repeatedly
    // through that render window so Blanks is corrected after the cross cells
    // actually exist.
    [0, 50, 150, 350, 800, 1400].forEach(ms => window.setTimeout(applyCrossFilters, ms));
  }

  function installListeners() {
    document.addEventListener('change', event => {
      const select = event.target && event.target.closest ? event.target.closest(`#${TABLE_ID} thead select`) : null;
      if (!select) return;
      const key = columnKey(select);
      if (select.value === CROSS_VALUE) selectedByColumnKey.set(key, CROSS_VALUE);
      else selectedByColumnKey.delete(key);
      triggerRepeatedApply();
    }, true);

    const search = document.getElementById('matrix-search');
    if (search) search.addEventListener('input', triggerRepeatedApply);

    document.addEventListener('click', event => {
      if (event.target && event.target.closest && event.target.closest('.matrix-card')) {
        triggerRepeatedApply();
      }
    }, true);
  }

  function start() {
    installListeners();
    window.setInterval(applyCrossFilters, 500);
    const root = document.querySelector('.matrix-card') || document.body;
    if (root && 'MutationObserver' in window) {
      const observer = new MutationObserver(() => window.setTimeout(applyCrossFilters, 50));
      observer.observe(root, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['class', 'style'] });
    }
    triggerRepeatedApply();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
