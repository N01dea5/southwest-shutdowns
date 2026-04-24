/* Executive retention chart formatter.
 *
 * Replaces the base retention chart with a clearer operational readout:
 *   - bar: any-client carry-over rate
 *   - line: same-client retention rate
 *   - point: new-hire load
 *
 * This runs after app.js and is non-blocking.
 */
(function () {
  'use strict';

  const DATA_FILES = ['data/covalent.json', 'data/tronox.json', 'data/csbp.json'];
  let attempts = 0;
  let timer = null;

  function workerKey(worker) {
    return String(worker && worker.name || '')
      .toLowerCase()
      .replace(/[^a-z\s]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function shutdownJobLabel(name) {
    const text = String(name || 'Shutdown').trim();
    const match = text.match(/^(\d+)\s*[–-]\s*(.+)$/);
    if (match) return `${match[1]} — ${match[2].replace(/^CSBP\s*-\s*/i, '')}`;
    return text;
  }

  function pct(count, total) {
    return total ? Math.round((count / total) * 100) : 0;
  }

  function parseDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? new Date(0) : date;
  }

  async function loadShutdowns() {
    const shutdowns = [];
    for (const file of DATA_FILES) {
      try {
        const response = await fetch(file, { cache: 'no-store' });
        if (!response.ok) continue;
        const payload = await response.json();
        for (const shutdown of payload.shutdowns || []) {
          shutdowns.push({ ...shutdown, company: payload.company || shutdown.company || '' });
        }
      } catch (error) {
        console.warn('[retention-chart-executive] skipped', file, error);
      }
    }
    return shutdowns.sort((a, b) => parseDate(a.start_date) - parseDate(b.start_date));
  }

  function buildRetentionRows(shutdowns) {
    const seenAny = new Set();
    const seenByCompany = new Map();

    return shutdowns.map(shutdown => {
      const roster = Array.isArray(shutdown.roster) ? shutdown.roster : [];
      const company = shutdown.company || '';
      if (!seenByCompany.has(company)) seenByCompany.set(company, new Set());
      const seenCompany = seenByCompany.get(company);

      let same = 0;
      let carry = 0;
      let newHires = 0;

      const uniqueWorkers = new Set();
      for (const worker of roster) {
        const key = workerKey(worker);
        if (!key || uniqueWorkers.has(key)) continue;
        uniqueWorkers.add(key);

        const wasSame = seenCompany.has(key);
        const wasAny = seenAny.has(key);
        if (wasSame) same += 1;
        if (wasAny) carry += 1;
        if (!wasAny) newHires += 1;
      }

      for (const key of uniqueWorkers) {
        seenAny.add(key);
        seenCompany.add(key);
      }

      const total = uniqueWorkers.size;
      return {
        id: shutdown.id,
        name: shutdownJobLabel(shutdown.name),
        company,
        total,
        same,
        carry,
        newHires,
        samePct: pct(same, total),
        carryPct: pct(carry, total),
        newHirePct: pct(newHires, total)
      };
    }).filter(row => row.total > 0);
  }

  function destroyExistingChart(canvas) {
    try {
      if (window.Chart && typeof Chart.getChart === 'function') {
        const existing = Chart.getChart(canvas);
        if (existing) existing.destroy();
      }
    } catch (error) {
      console.warn('[retention-chart-executive] chart destroy skipped', error);
    }
  }

  function renderChart(rows) {
    const canvas = document.getElementById('chart-retention');
    if (!canvas || !window.Chart || !rows.length) return false;

    destroyExistingChart(canvas);
    canvas.dataset.executiveRetentionChart = 'true';

    const labels = rows.map(row => row.name);
    const chart = new Chart(canvas, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          {
            type: 'bar',
            label: 'Any-client carry-over',
            data: rows.map(row => row.carryPct),
            borderWidth: 0,
            borderRadius: 8,
            barThickness: 24,
            maxBarThickness: 30
          },
          {
            type: 'line',
            label: 'Same-client retention',
            data: rows.map(row => row.samePct),
            borderWidth: 2,
            tension: 0.28,
            pointRadius: 4,
            pointHoverRadius: 6,
            fill: false
          },
          {
            type: 'line',
            label: 'New-hire load',
            data: rows.map(row => row.newHirePct),
            borderWidth: 0,
            pointRadius: 5,
            pointHoverRadius: 7,
            showLine: false
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        indexAxis: 'y',
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            position: 'bottom',
            labels: {
              boxWidth: 10,
              boxHeight: 10,
              usePointStyle: true,
              font: { size: 11, weight: '700' }
            }
          },
          tooltip: {
            callbacks: {
              afterTitle(items) {
                const row = rows[items[0].dataIndex];
                return `${row.company} · Roster ${row.total}`;
              },
              label(context) {
                const row = rows[context.dataIndex];
                const value = context.parsed.x;
                if (context.dataset.label === 'Any-client carry-over') return `Carry-over: ${value}% (${row.carry}/${row.total})`;
                if (context.dataset.label === 'Same-client retention') return `Same-client: ${value}% (${row.same}/${row.total})`;
                return `New hires: ${value}% (${row.newHires}/${row.total})`;
              }
            }
          }
        },
        scales: {
          x: {
            min: 0,
            max: 100,
            grid: { color: '#eef1f4' },
            ticks: {
              callback: value => `${value}%`,
              font: { size: 11, weight: '700' }
            },
            title: {
              display: true,
              text: 'Share of roster',
              font: { size: 11, weight: '800' }
            }
          },
          y: {
            grid: { display: false },
            ticks: {
              autoSkip: false,
              font: { size: 11, weight: '700' }
            }
          }
        }
      }
    });

    window.__executiveRetentionChart = chart;
    document.documentElement.style.setProperty('--retention-rows', String(rows.length));
    return true;
  }

  async function start() {
    const rows = buildRetentionRows(await loadShutdowns());
    timer = window.setInterval(() => {
      attempts += 1;
      const canvas = document.getElementById('chart-retention');
      if (canvas && window.Chart && renderChart(rows)) window.clearInterval(timer);
      if (attempts >= 30) window.clearInterval(timer);
    }, 500);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
