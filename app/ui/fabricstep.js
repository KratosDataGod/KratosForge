// The Fabric step (fabric_step.py): a notebook's last code cell, marked Run on Fabric, runs on Fabric
// Spark after the cells above it ran on this PC. Tables written from this PC carry no V-Order, the
// Parquet layout Direct Lake reads fastest, and only Fabric Spark writes it, so the step is where
// OPTIMIZE ... VORDER goes. It is published as a one-cell "<name> (Fabric step)" Spark notebook in
// the lakehouse's workspace and started as a Fabric job, after the risk check and a confirm; its
// status, printed output and each table's V-Order check come back into the cell.

function isFabricStep(cell) {
  return !!cell?.meta?.fabric_step;
}

// The cell "..." menu (notebook.js cellMenu).
function fabricStepMenuItems(cell) {
  if (cell.type !== "code" || !previewShown()) return [];
  const items = [{ label: isFabricStep(cell) ? "Run on this PC, not Fabric" : "Run on Fabric (Spark)", icon: "cloud",
    action: () => toggleFabricStep(cell) }];
  if (isFabricStep(cell)) items.push({ label: "Insert V-Order cell", icon: "flash", action: () => insertVOrderCell(cell) });
  return items;
}

// The cell header's badge (nbcells.js cellHeadExtras).
function fabricStepBadge(cell) {
  if (!isFabricStep(cell)) return null;
  return previewMarked("fabricStep", h("span", { class: "tag fabric-step-tag", title: "This cell runs on Fabric Spark after the cells above it run on this PC" },
    icon("cloud", 12), "Runs on Fabric · Spark"));
}

function lastCodeCell(nb) {
  return [...nb.cells].reverse().find((c) => c.type === "code" && c.source.trim()) || null;
}

function toggleFabricStep(cell) {
  const nb = state.notebook;
  if (!isFabricStep(cell)) {
    const last = lastCodeCell(nb);
    if (cell.source.trim() && last !== cell) {
      toast("Only the last code cell can run on Fabric. Move this cell to the end, or mark the last cell.", true);
      return;
    }
    nb.cells.forEach((c) => { if (c !== cell && isFabricStep(c)) { delete c.meta.fabric_step; updateCell(c); } });
  }
  setMeta(cell, "fabric_step", !isFabricStep(cell));
}

function stepCells(nb) {
  return nb.cells.map((c) => ({ type: c.type, source: c.source, meta: c.meta, cell_id: c.cell_id }));
}

// A dialog of the lakehouse's tables, the ones the notebook names ticked, then the cell's code.
async function insertVOrderCell(cell) {
  const nb = state.notebook;
  const lh = nbLakehouse(nb);
  if (!lh) { toast("Attach a OneLake lakehouse first. The Fabric step V-Orders its tables.", true); return; }
  await loadTableNames(lh);  // notebook.js
  const tables = state.nbTables[lh.lakehouse_id] || [];
  if (!tables.length) { toast(`No tables found in ${lh.name}. Sign in, or run the cells that write them first.`, true); return; }
  const found = await attempt(() => rpc("notebook_vorder_cell", { cells: stepCells(nb), tables }), "Could not read the notebook's tables");
  if (!found) return;
  const boxes = tables.map((t) => h("label", { class: "check-row" },
    h("input", { type: "checkbox", value: t, checked: found.candidates.includes(t) || null }), t));
  const picked = await new Promise((resolve) => {
    showDialog("Insert V-Order cell", h("div", {},
      h("p", { class: "text" }, `OPTIMIZE ... VORDER rewrites each ticked table's files with V-Order on Fabric Spark. Tables this notebook names are ticked. The cell's code is replaced.`),
      h("div", { class: "check-list" }, boxes)), [
      btn("Cancel", null, () => { resolve(null); closeDialog(); }, "tool outline"),
      btn("Insert", null, () => { resolve(boxes.map((b) => b.querySelector("input")).filter((i) => i.checked).map((i) => i.value)); closeDialog(); }, "tool primary"),
    ], () => resolve(null));
  });
  if (!picked) return;
  if (!picked.length) { toast("Tick at least one table.", true); return; }
  await setVOrderSource(cell, picked, false);
}

async function setVOrderSource(cell, tables, rewriteAll) {
  const res = await attempt(() => rpc("notebook_vorder_cell", { cells: [], tables: [], picked: tables, rewrite_all: rewriteAll }), "Could not write the V-Order cell");
  if (!res) return;
  setSource(cell, res.source);  // notebook.js
  markDirty();
  updateCell(cell);
}

// Run all and Run above/below: refuse before any cell runs when the mark can't run.
async function fabricStepPrecheck(nb, cells) {
  if (!cells.some(isFabricStep)) return true;
  const check = await rpc("notebook_fabric_step_check", { name: nb.name, cells: stepCells(nb), lakehouse_ref: nbLakehouse(nb) }).catch((e) => ({ ok: false, problem: String(e) }));
  if (check.ok) return true;
  toast(check.problem, true);
  return false;
}

// Each button resolves before closeDialog, whose onClose answers "no".
function stepConfirm(nb, check) {
  const row = (label, value) => [h("span", { class: "muted-text" }, label), h("span", {}, value)];
  const tables = check.tables.length ? check.tables.join(", ") : "None named in an OPTIMIZE; the cell's own writes are V-Ordered";
  return new Promise((resolve) => {
    showDialog(`Run the last cell of ${nb.name} on Fabric?`, h("div", {},
      h("p", { class: "text" }, `It runs as a Spark job on ${check.workspace_name || "the lakehouse's workspace"}'s Fabric capacity, which uses capacity units. The cells above it ran on this PC.`),
      h("div", { class: "kv-grid" }, row("Workspace", check.workspace_name || check.workspace_id), row("Lakehouse", check.lakehouse),
        row("Runs as", `${check.item_name}, a Spark notebook`), row("Tables", tables)),
      h("p", { class: "muted-text small" }, `Writes: replaces the ${check.item_name} notebook in the workspace; each OPTIMIZE adds a Delta version to its table; the cell's output goes to Files/_fabric_desktop/results in ${check.lakehouse}.`)), [
      btn("Stop here", null, () => { resolve(false); closeDialog(); }, "tool outline"),
      btn("Run on Fabric", null, () => { resolve(true); closeDialog(); }, "tool primary"),
    ], () => resolve(false));
  });
}

function stepError(cell, text) {
  cell.status = "error";
  cell.outputs = [{ type: "error", ename: "Fabric step", evalue: text }];
  if (state.notebook?.cells.includes(cell)) updateResults(cell);
  return false;
}

// notebook.js runCell hands the marked cell here. Resolves true when the step completed.
async function runFabricStep(cell, nb) {
  const check = await rpc("notebook_fabric_step_check", { name: nb.name, cells: stepCells(nb), lakehouse_ref: nbLakehouse(nb) }).catch((e) => ({ ok: false, problem: String(e) }));
  if (!check.ok) return stepError(cell, check.problem);
  if (!(await ensureSignedIn())) return stepError(cell, "Sign in to run this cell on Fabric. Nothing was sent.");
  if (!(await riskGate({ id: `notebook:${nb.name}`, action: "run_fabric_step", title: `Run the last cell of ${nb.name} on Fabric?`, runLabel: "Continue" }))) {
    return stepError(cell, "Not sent to Fabric: the risk check stopped it.");
  }
  if (!(await stepConfirm(nb, check))) {
    cell.status = "cancelled";
    cell.outputs = [{ type: "text", text: "Not sent to Fabric." }];
    if (state.notebook?.cells.includes(cell)) updateResults(cell);
    return false;
  }
  cell.status = "running";
  cell.outputs = [{ type: "fabric_step", status: "Publishing", text: `Publishing ${check.item_name} to ${check.workspace_name || "the workspace"}...` }];
  if (state.notebook?.cells.includes(cell)) updateResults(cell);
  const run = await rpc("run_notebook_fabric_step", { name: nb.name, cells: stepCells(nb), lakehouse_ref: nbLakehouse(nb) }).catch((e) => ({ error: String(e) }));
  if (run.error) return stepError(cell, run.error);
  logActivity({ id: `fabric:${run.job_id}`, status: "running", title: `${nb.name} · Fabric step`, text: `On Fabric in ${run.workspace_name || "the workspace"}`, url: run.url, monitor: true });
  trackFabricJobs();
  const started = performance.now();
  let res = { status: run.status, url: run.url };
  for (let i = 0; i < 720 && !FABRIC_DONE.has(res.status); i++) {  // up to an hour while the app is open
    cell.seconds = (performance.now() - started) / 1000;
    cell.outputs = [{ type: "fabric_step", status: res.status === "NotStarted" ? "Queued" : "Running", url: run.url, seconds: cell.seconds, run }];
    if (state.notebook?.cells.includes(cell)) updateResults(cell);
    await new Promise((r) => setTimeout(r, 5000));
    res = await rpc("notebook_fabric_step_result", { request_id: run.request_id }).catch(() => res);
  }
  if (!FABRIC_DONE.has(res.status)) {
    return stepError(cell, "Still running on Fabric after an hour. Monitor > Runs follows it; the cell doesn't.");
  }
  cell.seconds = (performance.now() - started) / 1000;
  cell.status = res.status === "Completed" ? "ok" : "error";
  const out = [{ type: "fabric_step", status: res.status, url: res.url, seconds: cell.seconds, tables: res.tables || [], run }];
  if (res.output?.stdout) out.push({ type: "stream", text: res.output.stdout });
  if (res.output?.error) out.push({ type: "error", ename: "Error on Fabric", evalue: res.output.error.trim().split("\n").pop(), traceback: res.output.error });
  else if (res.status !== "Completed") out.push({ type: "error", ename: `Fabric step ${res.status.toLowerCase()}`, evalue: res.message || "Open the run in Fabric to see why." });
  cell.outputs = out;
  cell.finishedAt = timeText(new Date());
  if (state.notebook?.cells.includes(cell)) {
    updateResults(cell);
    // Fabric's word on the step: the label glows once when it completed, the ember when it failed;
    // cancelled plays nothing (motion.js).
    const label = document.querySelector(`[data-cell="${cell.id}"] .cell-status > span`);
    if (res.status === "Completed") forgeEvent("fabric.run.succeeded", label);
    else if (res.status === "Failed") forgeEvent("fabric.run.failed", label);
  }
  return cell.status === "ok";
}

// The step's line in the cell's output (notebook.js renderOutput): where it is, a link to the run,
// Cancel while it runs, and after a completed run each table's V-Order check.
function fabricStepOutput(o, cell) {
  const time = o.seconds != null ? ` · ${Math.round(o.seconds)} s` : "";
  const label = { Publishing: "Publishing", Queued: "Queued on Fabric", Running: "Running on Fabric", Completed: "Completed on Fabric",
    Failed: "Failed on Fabric", Cancelled: "Cancelled on Fabric", Deduped: "Skipped by Fabric" }[o.status] || o.status;
  const running = o.status === "Queued" || o.status === "Running";
  // While Fabric runs it there is no measure: the navy wolf trots in place beside what Fabric says
  // (motion.js remoteProgress), with no fill or percentage.
  const head = h("div", { class: "fabric-step-line" },
    running && !o.text ? remoteProgress("Fabric step", `${label}${time}`) : h("span", {}, o.text || `${label}${time}`),
    o.url ? h("a", { href: "#", onclick: (e) => { e.preventDefault(); openInFabric(o.url); } }, "Open the run in Fabric") : null,
    running && o.run ? btn("Cancel", null, () => attempt(() => rpc("cancel_fabric_run", { workspace_id: o.run.workspace_id, item_id: o.run.item_id, job_id: o.run.job_id }), "Could not cancel"), "tool small outline") : null);
  const counted = (t) => t.checked != null ? `${t.v_ordered_files} of ${t.checked}${t.checked < t.files ? ` newest (of ${t.files})` : ""} files V-Ordered` : "";
  const tables = (o.tables || []).map((t) => h("span", { class: `vorder-chip ${t.v_ordered ? "ok" : t.v_ordered === false ? "warn" : ""}`,
    title: t.message || counted(t) },
  t.v_ordered ? icon("ok", 12) : null, `${t.table} ${t.v_ordered ? "V-Ordered" : t.v_ordered === false ? "not V-Ordered" : "not checked"}`));
  const missed = (o.tables || []).filter((t) => t.v_ordered === false).map((t) => t.table);
  return h("div", { class: "output fabric-step-output" }, head,
    tables.length ? h("div", { class: "vorder-chips" }, tables, h("span", { class: "muted-text small" }, "Checked from each file's Parquet footer after the run.")) : null,
    missed.length && cell ? h("div", { class: "fabric-step-line" },
      h("span", {}, "OPTIMIZE leaves alone a file it has nothing to compact with. Rewrite all files writes each table back with V-Order: the whole table, so a large table takes longer and uses more capacity."),
      btn("Rewrite all files", null, () => setVOrderSource(cell, missed, true), "tool small outline")) : null);
}
