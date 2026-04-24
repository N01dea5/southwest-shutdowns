/* Shows the latest generated_at timestamp from the dashboard JSON payloads. */
(function () {
  'use strict';

  const files = ['data/covalent.json', 'data/tronox.json', 'data/csbp.json'];

  function formatTimestamp(value) {
    if (!value) return null;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return null;
    return date.toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit'
    });
  }

  async function load() {
    const target = document.getElementById('refresh-status');
    if (!target) return;

    const timestamps = [];
    for (const file of files) {
      try {
        const response = await fetch(file, { cache: 'no-store' });
        if (!response.ok) continue;
        const payload = await response.json();
        if (payload.generated_at) timestamps.push(payload.generated_at);
      } catch (error) {
        console.warn('[refresh-status] skipped', file, error);
      }
    }

    const latest = timestamps.sort().pop();
    target.textContent = latest ? `Last refreshed: ${formatTimestamp(latest) || latest}` : 'Last refreshed: unavailable';
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', load, { once: true });
  } else {
    load();
  }
})();
