const cfg = window.CLIENT_DASHBOARD_CONFIG || {};
const ticketLabels = { cse:'CSE', wah:'WAH', ewp:'EWP', ba:'BA', fork:'Fork', hr:'HR', dog:'Dog', rig:'Rig', gta:'GTA', fa:'FA', hrwl:'HRWL' };
let feed = null;
const $ = id => document.getElementById(id);
const pct = (n, d) => d ? Math.round(n / d * 100) : 0;
const fmtDate = s => s ? new Date(s).toLocaleDateString(undefined, { day:'2-digit', month:'short', year:'numeric' }) : '—';
function setText(id, value) { $(id).textContent = value; }
function pill(text, tone) { return `<span class="pill ${tone}">${text}</span>`; }
function retentionHTML(w) {
  if (w.same_client_retention) return pill('Same client', 'good');
  if (w.srg_carry_over) return pill('SRG carry-over', 'blue');
  if (w.new_hire) return pill('New', 'warn');
  return '<span class="muted">—</span>';
}
function ticketsHTML(tickets) {
  const keys = Object.keys(tickets || {});
  if (!keys.length) return '<span class="muted">Not recorded</span>';
  return `<div class="tickets">${keys.map(k => `<span class="ticket ${(tickets[k].status || '').replace(/[^a-z_]/g,'')}">${ticketLabels[k] || k.toUpperCase()}</span>`).join('')}</div>`;
}
function renderSummary(data) {
  const s = data.summary;
  const sh = data.shutdown;
  setText('subtitle', `${sh.name} · ${fmtDate(sh.start_date)} – ${fmtDate(sh.end_date)} · ${s.confirmed_total} confirmed`);
  setText('updated', `Generated ${fmtDate(data.generated_at)} · Source: ${data.source_of_truth}`);
  setText('requiredTotal', s.required_total);
  setText('confirmedTotal', s.confirmed_total);
  setText('gapTotal', s.gap_total);
  setText('sameClient', `${s.same_client_retention} (${pct(s.same_client_retention, s.confirmed_total)}%)`);
  setText('srgCarry', `${s.srg_carry_over} (${pct(s.srg_carry_over, s.confirmed_total)}%)`);
  setText('buddyRequired', s.buddy_required);
  setText('sourceNote', 'Client-facing feed is sanitised from the internal Southwest dashboard source of truth. Mobile numbers, personnel IDs, hiring company names and SharePoint document links are not exposed.');
}
function renderDiscipline(data) {
  $('disciplineTable').querySelector('tbody').innerHTML = data.roles.map(r => {
    const p = pct(r.confirmed, r.required);
    const open = Math.max(r.gap || 0, 0);
    return `<tr><td><strong>${r.role}</strong></td><td class="num">${r.required}</td><td class="num">${r.confirmed}</td><td class="num">${open}</td><td><div class="progress"><div class="bar"><span style="width:${Math.min(p,100)}%"></span></div><strong>${p}%</strong></div></td><td>${pill(open > 0 ? 'Open' : 'Covered', open > 0 ? 'bad' : 'good')}</td></tr>`;
  }).join('');
  $('shiftTable').querySelector('tbody').innerHTML = Object.entries(data.summary.shift_split || {}).map(([shift, count]) => `<tr><td><strong>${shift}</strong></td><td class="num">${count}</td></tr>`).join('');
  const roleSelect = $('roleFilter');
  [...new Set(data.workers.map(w => w.role).filter(Boolean))].sort().forEach(role => roleSelect.insertAdjacentHTML('beforeend', `<option>${role}</option>`));
}
function renderWorkers() {
  const q = $('search').value.trim().toLowerCase();
  const role = $('roleFilter').value;
  const shift = $('shiftFilter').value;
  const status = $('statusFilter').value;
  const rows = feed.workers.filter(w => {
    if (q && !`${w.name} ${w.role}`.toLowerCase().includes(q)) return false;
    if (role !== 'All' && w.role !== role) return false;
    if (shift !== 'All' && w.shift !== shift) return false;
    if (status === 'new' && !w.new_hire) return false;
    if (status === 'same' && !w.same_client_retention) return false;
    if (status === 'carry' && !w.srg_carry_over) return false;
    return true;
  });
  $('workerTable').querySelector('tbody').innerHTML = rows.map(w => `<tr><td><strong>${w.name}</strong></td><td>${w.role}</td><td>${w.shift || '—'}</td><td>${retentionHTML(w)}</td><td>${w.buddy_required ? pill('Buddy required','warn') : '<span class="muted">—</span>'}</td><td>${ticketsHTML(w.tickets)}</td></tr>`).join('');
}
function setupTabs() {
  document.querySelectorAll('.tab').forEach(btn => btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    $(btn.dataset.panel).classList.add('active');
  }));
}
async function boot() {
  setupTabs();
  try {
    const res = await fetch(cfg.dataUrl, { cache:'no-store' });
    if (!res.ok) throw new Error(`Feed request failed: ${res.status}`);
    feed = await res.json();
    renderSummary(feed);
    renderDiscipline(feed);
    renderWorkers();
    ['search','roleFilter','shiftFilter','statusFilter'].forEach(id => $(id).addEventListener('input', renderWorkers));
    ['roleFilter','shiftFilter','statusFilter'].forEach(id => $(id).addEventListener('change', renderWorkers));
  } catch (err) {
    $('error').style.display = 'block';
    $('error').textContent = 'Unable to load sanitised Southwest source feed: ' + err.message;
    console.error(err);
  }
}
boot();
