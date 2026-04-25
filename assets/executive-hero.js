/* Populates executive hero metadata from generated dashboard JSON. */
(function () {
  'use strict';

  const files = ['data/covalent.json', 'data/tronox.json', 'data/csbp.json'];

  function parseDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function fmtDate(date) {
    if (!date) return '—';
    return date.toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' });
  }

  function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  async function loadPayloads() {
    const payloads = [];
    for (const file of files) {
      try {
        const response = await fetch(file, { cache: 'no-store' });
        if (!response.ok) continue;
        payloads.push(await response.json());
      } catch (error) {
        console.warn('[executive-hero] skipped', file, error);
      }
    }
    return payloads;
  }

  async function render() {
    const payloads = await loadPayloads();
    const shutdowns = payloads.flatMap(payload => (payload.shutdowns || []).map(s => ({ ...s, company: payload.company || s.company || '' })));
    if (!shutdowns.length) return;

    const dated = shutdowns
      .map(s => ({ ...s, start: parseDate(s.start_date), end: parseDate(s.end_date) }))
      .filter(s => s.start && s.end)
      .sort((a, b) => a.start - b.start);

    const minStart = dated[0] && dated[0].start;
    const maxEnd = dated.reduce((latest, s) => !latest || s.end > latest ? s.end : latest, null);
    setText('hero-reporting-period', `${fmtDate(minStart)} — ${fmtDate(maxEnd)}`);

    const clients = [...new Set(shutdowns.map(s => s.company).filter(Boolean))];
    if (clients.length) setText('hero-clients', clients.join(' · '));

    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const active = dated.filter(s => s.start <= today && s.end >= today).length;
    const upcoming = dated.filter(s => s.start > today);
    const next = upcoming[0] || dated[dated.length - 1];

    const activeText = active === 1 ? '1 on site' : `${active} on site`;
    setText('hero-active-shutdowns', activeText);

    if (next) {
      const name = String(next.name || next.id || 'Next shutdown').replace(/^\d+\s*[–-]\s*/, '');
      setText('hero-next-mobilisation', `${name} · ${fmtDate(next.start)}`);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', render, { once: true });
  } else {
    render();
  }
})();
