const cfg = window.CLIENT_DASHBOARD_CONFIG || {};
const ticketLabels = { cse:'CSE', wah:'WAH', ewp:'EWP', ba:'BA', fork:'Fork', hr:'HR', dog:'Dog', rig:'Rig', gta:'GTA', fa:'FA', hrwl:'HRWL' };
let feed = null;
const $ = id => document.getElementById(id);
const pct = (n, d) => d ? Math.round(n / d * 100) : 0;
const fmtDate = s => s ? new Date(s).toLocaleDateString(undefined, { day:'2-digit', month:'short', year:'numeric' }) : '—';
const fmtDateTime = s => s ? new Date(s).toLocaleString(undefined, { day:'2-digit', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit' }) : '—';
const isoDate = d => d.toISOString().slice(0,10);
function setText(id, value) { const el = $(id); if (el) el.textContent = value; }
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
  setText('updated', `Last updated ${fmtDateTime(data.generated_at)}`);
  setText('requiredTotal', s.required_total);
  setText('confirmedTotal', s.confirmed_total);
  setText('gapTotal', s.gap_total);
  setText('sameClient', `${s.same_client_retention} (${pct(s.same_client_retention, s.confirmed_total)}%)`);
  setText('srgCarry', `${s.srg_carry_over} (${pct(s.srg_carry_over, s.confirmed_total)}%)`);
  setText('buddyRequired', s.buddy_required);
  setText('sourceNote', '');
}
function chartRows(items, max, fillClass = '') {
  return items.map(([label, value]) => {
    const p = max ? Math.round((value / max) * 100) : 0;
    return `<div class="chart-row"><div class="chart-label" title="${label}">${label}</div><div class="chart-track"><span class="chart-fill ${fillClass}" style="width:${p}%"></span></div><div class="chart-value">${value}</div></div>`;
  }).join('');
}
function renderCharts(data) {
  const discipline = data.roles.map(r => [r.role, r.confirmed || 0]).sort((a,b) => b[1]-a[1]);
  const maxDisc = Math.max(1, ...discipline.map(x => x[1]));
  setText('disciplineChartTitle', 'Confirmed by discipline');
  if ($('disciplineChart')) $('disciplineChart').innerHTML = chartRows(discipline, maxDisc);

  const s = data.summary;
  const retention = [['Same client', s.same_client_retention || 0], ['SRG carry-over', s.srg_carry_over || 0], ['New', s.new_hires || 0]];
  const maxRetention = Math.max(1, ...retention.map(x => x[1]));
  if ($('retentionChart')) $('retentionChart').innerHTML = chartRows(retention, maxRetention, 'blue');

  const shifts = Object.entries(s.shift_split || {}).sort((a,b) => b[1]-a[1]);
  const maxShift = Math.max(1, ...shifts.map(x => x[1]));
  if ($('shiftChart')) $('shiftChart').innerHTML = chartRows(shifts, maxShift, 'good');
}
function renderDiscipline(data) {
  $('disciplineTable').querySelector('tbody').innerHTML = data.roles.map(r => {
    const p = pct(r.confirmed, r.required);
    const open = Math.max(r.gap || 0, 0);
    return `<tr><td><strong>${r.role}</strong></td><td class="num">${r.required}</td><td class="num">${r.confirmed}</td><td class="num">${open}</td><td><div class="progress"><div class="bar"><span style="width:${Math.min(p,100)}%"></span></div><strong>${p}%</strong></div></td><td>${pill(open > 0 ? 'Open' : 'Covered', open > 0 ? 'bad' : 'good')}</td></tr>`;
  }).join('');
  $('shiftTable').querySelector('tbody').innerHTML = Object.entries(data.summary.shift_split || {}).map(([shift, count]) => `<tr><td><strong>${shift}</strong></td><td class="num">${count}</td></tr>`).join('');
  const roleSelect = $('roleFilter');
  if (roleSelect && roleSelect.options.length <= 1) [...new Set(data.workers.map(w => w.role).filter(Boolean))].sort().forEach(role => roleSelect.insertAdjacentHTML('beforeend', `<option>${role}</option>`));
}
function filteredWorkers() {
  const q = $('search') ? $('search').value.trim().toLowerCase() : '';
  const role = $('roleFilter') ? $('roleFilter').value : 'All';
  const shift = $('shiftFilter') ? $('shiftFilter').value : 'All';
  const status = $('statusFilter') ? $('statusFilter').value : 'All';
  return feed.workers.filter(w => {
    if (q && !`${w.name} ${w.role}`.toLowerCase().includes(q)) return false;
    if (role !== 'All' && w.role !== role) return false;
    if (shift !== 'All' && w.shift !== shift) return false;
    if (status === 'new' && !w.new_hire) return false;
    if (status === 'same' && !w.same_client_retention) return false;
    if (status === 'carry' && !w.srg_carry_over) return false;
    return true;
  });
}
function renderWorkers() {
  const rows = filteredWorkers();
  const html = rows.map(w => `<tr><td><strong>${w.name}</strong></td><td>${w.role}</td><td>${w.shift || '—'}</td><td>${retentionHTML(w)}</td><td>${w.buddy_required ? pill('Buddy required','warn') : '<span class="muted">—</span>'}</td><td>${ticketsHTML(w.tickets)}</td></tr>`).join('');
  if ($('workerTable')) $('workerTable').querySelector('tbody').innerHTML = html;
  if ($('summaryWorkerTable')) $('summaryWorkerTable').querySelector('tbody').innerHTML = html;
  renderRosterMatrix(rows);
}
function dateRange(start, end) {
  const out = [];
  const s = new Date(start);
  const e = new Date(end);
  if (Number.isNaN(s.getTime()) || Number.isNaN(e.getTime())) return out;
  s.setHours(0,0,0,0); e.setHours(0,0,0,0);
  for (let d = new Date(s); d <= e; d.setDate(d.getDate()+1)) out.push(new Date(d));
  return out;
}
function workerOnDate(worker, dayIso) {
  const s = worker.start || feed.shutdown.start_date;
  const e = worker.end || feed.shutdown.end_date;
  return dayIso >= s && dayIso <= e;
}
function renderRosterMatrix(workers) {
  const table = $('rosterMatrix');
  if (!table || !feed) return;
  const days = dateRange(feed.shutdown.start_date, feed.shutdown.end_date);
  table.querySelector('thead').innerHTML = `<tr><th class="worker-cell">Employee</th><th>Role</th><th>Shift</th>${days.map(d => `<th class="date-head"><strong>${d.toLocaleDateString(undefined,{day:'2-digit'})}</strong><span>${d.toLocaleDateString(undefined,{weekday:'short'})}</span></th>`).join('')}</tr>`;
  table.querySelector('tbody').innerHTML = workers.map(w => `<tr><td class="worker-cell"><strong>${w.name}</strong></td><td>${w.role}</td><td>${w.shift || '—'}</td>${days.map(d => workerOnDate(w, isoDate(d)) ? '<td class="onsite-cell">✓</td>' : '<td class="blank-cell"></td>').join('')}</tr>`).join('');
}
function setupTabs() {
  document.querySelectorAll('.tab').forEach(btn => btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    $(btn.dataset.panel).classList.add('active');
    renderRosterMatrix(filteredWorkers());
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
    renderCharts(feed);
    renderWorkers();
    ['search','roleFilter','shiftFilter','statusFilter'].forEach(id => { if ($(id)) $(id).addEventListener('input', renderWorkers); });
    ['roleFilter','shiftFilter','statusFilter'].forEach(id => { if ($(id)) $(id).addEventListener('change', renderWorkers); });
  } catch (err) {
    $('error').style.display = 'block';
    $('error').textContent = 'Unable to load sanitised Southwest source feed: ' + err.message;
    console.error(err);
  }
}
boot();
