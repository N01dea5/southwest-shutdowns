/* Adds Hiring Company to the worker retention matrix.
 *
 * The base app renders the matrix from state.shutdowns. This lightweight layer
 * runs immediately after each matrix render and injects a stable column using
 * each roster entry's `hire_company` value.
 */
(function () {
  function normName(value) {
    return String(value || "")
      .toLowerCase()
      .replace(/[^a-z\s]/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function workerHireMap(view) {
    const map = new Map();
    const shutdowns = Array.isArray(view)
      ? view
      : (typeof filtered === "function" ? filtered() : (typeof state !== "undefined" ? state.shutdowns : []));

    for (const shutdown of shutdowns || []) {
      for (const worker of shutdown.roster || []) {
        const key = typeof workerKey === "function" ? workerKey(worker) : normName(worker.name);
        if (!key) continue;
        const hire = worker.hire_company || worker.hiring_company || worker.labour_hire || "";
        if (!hire) continue;
        if (!map.has(key)) map.set(key, new Set());
        map.get(key).add(hire);
      }
    }

    const out = new Map();
    for (const [key, companies] of map.entries()) {
      out.set(key, [...companies].sort().join(" / "));
    }
    return out;
  }

  function ensureHiringCompanyColumn(view) {
    const table = document.getElementById("worker-matrix");
    if (!table || !table.tHead || !table.tBodies.length) return;

    const hireByWorker = workerHireMap(view);

    const headerRow = table.tHead.querySelector("tr");
    if (headerRow && !headerRow.querySelector('[data-col="hire-company"]')) {
      const th = document.createElement("th");
      th.dataset.col = "hire-company";
      th.textContent = "Hiring company";
      const roleHeader = [...headerRow.cells].findIndex(cell => /role/i.test(cell.textContent || ""));
      const insertAt = roleHeader >= 0 ? roleHeader + 1 : Math.min(2, headerRow.cells.length);
      headerRow.insertBefore(th, headerRow.cells[insertAt] || null);
    }

    const headers = headerRow ? [...headerRow.cells].map(c => (c.textContent || "").trim().toLowerCase()) : [];
    const hireIdx = headers.findIndex(h => h === "hiring company");
    const roleIdx = headers.findIndex(h => h === "role");
    const nameIdx = Math.max(0, headers.findIndex(h => /worker|name/.test(h)));
    const insertAt = hireIdx >= 0 ? hireIdx : (roleIdx >= 0 ? roleIdx + 1 : Math.min(2, headers.length));

    for (const row of table.tBodies[0].rows) {
      let td = row.querySelector('[data-col="hire-company"]');
      if (!td) {
        td = document.createElement("td");
        td.dataset.col = "hire-company";
        td.className = "muted hire-company-cell";
        row.insertBefore(td, row.cells[insertAt] || null);
      }
      const nameText = row.cells[nameIdx] ? row.cells[nameIdx].textContent : "";
      const key = normName(nameText);
      td.textContent = hireByWorker.get(key) || "—";
    }
  }

  if (typeof renderWorkerMatrix === "function") {
    const originalRenderWorkerMatrix = renderWorkerMatrix;
    renderWorkerMatrix = function patchedRenderWorkerMatrix(view) {
      originalRenderWorkerMatrix(view);
      ensureHiringCompanyColumn(view);
    };
  }

  // Fallback for any direct DOM refresh that bypasses renderWorkerMatrix.
  const observer = new MutationObserver(() => ensureHiringCompanyColumn());
  window.addEventListener("DOMContentLoaded", () => {
    const table = document.getElementById("worker-matrix");
    if (table) observer.observe(table, { childList: true, subtree: true });
    ensureHiringCompanyColumn();
  });
})();
