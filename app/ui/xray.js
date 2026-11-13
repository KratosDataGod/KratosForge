// Estate X-ray on Home: every lakehouse you can reach, read once, with what needs attention
// ranked first and a fix on each line. "Adopt" turns a lakehouse's tables into metadata entities.

// picked: the ids of findings ticked for Optimize; batch: the Optimize batch running or last run (optimizeBatch).
const xrayPage = { data: null, timer: null, kind: "all", showAll: false, folded: false, watching: null,
  box: null, picked: new Set(), batch: null };

const XRAY_KINDS = {
  small_files: "Small files", unidentified: "Unidentified", access: "Access", cost: "Cost",
  stale: "Not changing", vacuum: "Needs vacuum", staging: "Leftover staging", empty: "Empty lakehouses",
};

// Where each kind of finding is explained, with how to fix it by hand (docs/xray-fixes.md).
const XRAY_DOCS = {
  small_files: "small-files", unidentified: "unidentified-tables", access: "access", cost: "cost",
  stale: "not-changing", vacuum: "needs-vacuum", staging: "leftover-staging-lakehouses", empty: "empty-lakehouses",
};

function openXrayDoc(kind) {
  const anchor = XRAY_DOCS[kind];
  attempt(() => invoke("open_doc", { page: `xray-fixes.md${anchor ? `#${anchor}` : ""}` }), "Could not open the guide");
}

// Home calls loadXray once everything else on Home has loaded. The card starts folded or not as
// last left; folded, it still shows the overall numbers.
function xrayCard() {
  let folded = false;
  try { folded = localStorage.getItem("fd.fold.home.xray") === "1"; } catch { /* unfolded */ }
  xrayPage.folded = folded;
  return h("section", { class: `card pad xray${folded ? " folded" : ""}` },
    h("div", { class: "muted-text" }, "The Estate X-ray loads once the rest of Home is in."));
}

async function loadXray(box, refresh) {
  clearTimeout(xrayPage.timer);
  if (!state.user) { drawXray(box, null); return; }
  // auto: the very first scan on this PC starts by itself; after that only the button scans.
  const res = await attempt(() => rpc("xray", { refresh, auto: !refresh }), refresh ? "Scan not started" : "Could not read the X-ray");
  if (!res || state.view !== "home" || !box.isConnected) return;
  xrayPage.data = res;
  drawXray(box, res);
  // A scan this card watched run ends: the numbers glow once, or the ember beside why it stopped. A
  // scan that ended before the card was drawn plays nothing (motion.js).
  if (res.running) xrayPage.watching = box;
  else if (xrayPage.watching === box) {
    xrayPage.watching = null;
    if (res.error) forgeEvent("xray.scan.failed", box.querySelector(".reason"));
    // Findings that reached Optimize's shared lakehouse make it Optimize's analysis: the same effect, under its name.
    else if (res.result) forgeEvent(xrayReachedOptimize(res.result.shared) ? "optimize.analysis.finished" : "xray.scan.finished",
      box.querySelector(".xray-stats"));
  }
  if (res.running && !res.paused) xrayPage.timer = setTimeout(() => loadXray(box, false), 2000);
}

async function pauseXray(box, paused) {
  clearTimeout(xrayPage.timer);
  const res = await attempt(() => rpc("xray_pause", { paused }), paused ? "Could not pause the scan" : "Could not resume the scan");
  if (!res || !box.isConnected) return;
  xrayPage.data = res;
  drawXray(box, res);
  if (res.running && !res.paused) xrayPage.timer = setTimeout(() => loadXray(box, false), 2000);
}

function drawXray(box, res) {
  if (box instanceof Element) xrayPage.box = box;
  const foldBtn = h("button", { class: "tool outline small", "aria-expanded": String(!xrayPage.folded),
    onclick: () => {
      xrayPage.folded = !xrayPage.folded;
      try { localStorage.setItem("fd.fold.home.xray", xrayPage.folded ? "1" : "0"); } catch { /* this session only */ }
      box.classList.toggle("folded", xrayPage.folded);
      drawXray(box, res);
    } }, xrayPage.folded ? "Show details" : "Hide details");
  const head = (extra) => h("div", { class: "xray-head" },
    h("div", { class: "grow" }, preview("xray", h("h2", {}, "Estate X-ray ")),
      h("div", { class: "muted-text small" }, "Every lakehouse you can reach, read once: what slows it down, what is broken, who has access, and what it costs. ",
        h("a", { href: "#", onclick: (e) => { e.preventDefault(); openXrayDoc(null); } }, "How each fix works"))),
    extra);
  if (!state.user) {
    fill(box, head(btn("Sign in and scan", "search", async () => { if (await signIn()) render(); }, "tool primary")),
      h("p", { class: "muted-text" }, "The scan reads table logs and workspace roles only. It changes nothing."));
    return;
  }
  const result = res.result;
  const scanBtn = res.running
    ? btn(res.paused ? "Resume" : "Pause", res.paused ? "play" : "pause", () => pauseXray(box, !res.paused), "tool outline")
    : btn(result ? "Scan again" : "Scan now", "sync", () => loadXray(box, true), "tool outline");
  const progress = res.running ? h("div", { class: "xray-progress" },
    h("div", { class: "row" }, h("b", {}, res.paused ? `Paused: ${(res.progress?.phase || "scanning").toLowerCase()}` : res.progress?.phase || "Scanning"),
      res.progress?.total ? h("span", { class: "muted-text small" }, `${res.progress.done} of ${res.progress.total}`) : null),
    // Before the scan can count what it reads, the navy wolf trots in place (motion.js remoteProgress):
    // it reads Fabric, and has no measure yet. Paused, the grey bar stands still.
    res.progress?.total
      ? h("div", { class: "progress" }, h("div", { class: `bar${res.paused ? " paused" : ""}`, style: `width:${Math.max(4, Math.round(100 * res.progress.done / res.progress.total))}%` }))
      : res.paused ? h("div", { class: "progress" }, h("div", { class: "bar paused" }))
      : remoteProgress("Estate X-ray scan", "Reading Fabric")) : null;
  if (!result) {
    fill(box, head(h("div", { class: "row" }, scanBtn)), progress,
      res.error ? h("p", { class: "reason" }, `The scan stopped: ${friendlyError(res.error).text}`) : null,
      !res.running && !res.error ? h("p", { class: "muted-text" }, "No scan yet. It reads table logs and workspace roles only, and changes nothing.") : null,
      xrayHow());
    return;
  }
  const open = result.findings.filter((f) => !f.fixed);
  const byKind = {};
  for (const f of open) byKind[f.kind] = (byKind[f.kind] || 0) + 1;
  const shown = open.filter((f) => (xrayPage.kind === "all" || f.kind === xrayPage.kind)
    && matches(`${f.title} ${f.detail} ${f.workspace_name || ""} ${f.lakehouse_name || ""}`));
  const chip = (kind, label, n) => h("button", { class: `chip${xrayPage.kind === kind ? " on" : ""}`,
    onclick: () => { xrayPage.kind = kind; xrayPage.showAll = false; drawXray(box, res); } }, label, n !== undefined ? h("span", { class: "count" }, n) : null);
  const limit = xrayPage.showAll ? shown.length : 6;
  // Only findings Optimize fixes can be ticked, and only those shown: a filter or search keeps what it hides out of the batch.
  const pickable = shown.filter(xrayPickable);
  const keep = new Set(pickable.map((f) => f.id));
  for (const id of [...xrayPage.picked]) if (!keep.has(id)) xrayPage.picked.delete(id);
  const picking = pickable.length > 0 && !xrayBatchActive();
  const high = open.filter((f) => f.severity === "high").length;

  const stats = h("div", { class: "xray-stats" },
    stat(result.workspaces, "workspaces"), stat(result.lakehouses, "lakehouses"),
    stat(result.tables, result.partial ? "tables (first 500)" : "tables"), stat(sizeText(result.bytes), "in Delta tables"),
    stat(open.length, open.length === 1 ? "finding" : "findings", open.length ? (high ? "bad" : "warn") : "good"));
  const top = head(h("div", { class: "row" }, h("span", { class: "muted-text small" }, "Scanned ", when(result.scanned)), scanBtn, foldBtn));
  if (xrayPage.folded) {
    fill(box, top, progress, stats);
    return;
  }
  fill(box,
    top,
    progress,
    stats,
    withHowItWorks([
    xrayShared(result.shared),
    result.skipped?.length ? h("p", { class: "muted-text small" }, `Couldn't read: ${result.skipped.join(", ")} (a paused capacity, or no access).`) : null,
    open.length ? h("div", { class: "row wrap xray-chips" }, chip("all", "All", open.length),
      Object.entries(XRAY_KINDS).filter(([k]) => byKind[k]).map(([k, label]) => chip(k, label, byKind[k]))) : null,
    xrayBatchPanel(),
    picking ? xrayPickBar(pickable, shown, box, res) : null,
    open.length ? h("div", { class: "xray-list" }, shown.slice(0, limit).map((f) => xrayFinding(f, box, res, picking)))
      : h("p", { class: "muted-text" }, "Nothing needs attention. Every table is compact, readable and changing."),
    h("div", { class: "row" },
      shown.length > limit ? btn(`Show all ${shown.length}`, null, () => { xrayPage.showAll = true; drawXray(box, res); }, "tool link") : null,
      xrayPage.showAll && shown.length > 6 ? btn("Show fewer", null, () => {
        xrayPage.showAll = false;
        drawXray(box, res);
        box.scrollIntoView({ block: "start", behavior: "smooth" });
      }, "tool link") : null,
      xrayPage.kind === "vacuum" && shown.length > 1 ? btn(`Vacuum all ${shown.length}`, "delete", () => vacuumAll(shown, box, res), "tool link") : null),
    xrayLakehouses(result.lakehouse_rows || [])].filter(Boolean), xrayHow()));
}

// "How this works" beside the findings: the scan only reads; Optimize, Vacuum and Adopt write.
function xrayHow() {
  return howItWorks("home.xray", {
    reads: "Table logs and workspace roles in every lakehouse you can reach, and the runs this app started in Fabric. "
      + "The first scan on this PC starts by itself after you sign in; after that it reads only when you press Scan again.",
    changes: "Scanning changes no lakehouse it reads. When this PC is connected to a central lakehouse you can write to "
      + "(Settings > Optimize lakehouses), each scan also adds its lakehouse and table findings there as new rows, never "
      + "the names of people or cost estimates. Four buttons write, and each asks first. Optimize names the table, says it runs in "
      + "Fabric and uses capacity, and shows the risk check, then starts a Fabric table maintenance job that compacts it "
      + "and removes old files past the history the table keeps. Optimize on ticked tables asks once for all of them, then starts "
      + "the same job for each, at most 3 at a time, and only for tables whose risk check passes; Stop starts nothing more. Vacuum first shows how many old files it would delete, and deletes them in "
      + "Fabric only when you confirm, keeping the history the table promises. Adopt shows the entities it would create "
      + "and, when you confirm, writes them as files in your metadata project on this PC; the table itself isn't changed. "
      + "Remove, on a leftover Dataflow Gen2 staging lakehouse, first checks again that a newer one exists in the workspace and "
      + "that nothing was written to it for 30 days, shows what it holds, and deletes that one lakehouse in Fabric only when you type delete. "
      + "The newest staging lakehouse in a workspace is never offered.",
    results: "Findings are kept on this PC. Lakehouse and table findings added to the central lakehouse show in "
      + "Optimize > Opportunities, and anyone who can read that lakehouse can read them, including the workspace, "
      + "lakehouse and table names of every workspace this scan reached; the app hides items you can't open, but "
      + "that is a courtesy, not security. Optimize and Vacuum run as jobs in Fabric; follow them in Monitor.",
    anchor: "estate-x-ray-preview",
  });
}

// Where the scan's findings went besides this PC, when it is connected to a central lakehouse.
function xrayReachedOptimize(shared) {
  return !!shared && !shared.error && !shared.queued && typeof shared.findings === "number";
}

function xrayShared(shared) {
  if (!shared) return null;
  if (shared.queued) {
    return h("p", { class: "muted-text small" },
      `${shared.lakehouse} couldn't be reached (${shared.error}), so ${shared.queued} finding${shared.queued === 1 ? " waits" : "s wait"} on this PC. They're added with the next scan, or when you connect to it again.`);
  }
  return h("p", { class: "muted-text small" }, shared.error
    ? `These findings weren't added to ${shared.lakehouse}: ${shared.error}`
    : `${shared.findings} lakehouse and table finding${shared.findings === 1 ? " was" : "s were"} added to ${shared.lakehouse} for Optimize > Opportunities.`);
}

function stat(value, label, tone = "") {
  return h("div", { class: `xray-stat ${tone}` }, h("b", {}, typeof value === "number" ? value.toLocaleString() : value), h("span", {}, label));
}

function xrayWhere(f) {
  if (!f.lakehouse_name) return f.kind === "access" ? "Workspace roles" : f.kind === "cost" ? "Runs this app started in Fabric" : "";
  return `${f.workspace_name} / ${f.lakehouse_name}`;
}

// A finding's fix button. box is the X-ray card, or a function that redraws whatever shows the
// finding (Optimize's Overview, AF-5). In a table row the button is small, and only the row asked
// to lead keeps a solid button.
function xrayAction(f, box, res, { small = false, solid = true } = {}) {
  const cls = (base) => `${solid ? base : base.replace(" primary", " outline")}${small ? " small" : ""}`;
  const lakehouseLink = () => openInFabric(fabricItemLink({ type: "Lakehouse", item_id: f.lakehouse_id, workspace_id: f.workspace_id }));
  const make = {
    optimize: () => btn("Optimize", "sync", () => optimizeDialog(f, box, res), cls("tool primary")),
    vacuum: () => btn("Vacuum", "delete", () => vacuumDialog(f, box, res), cls("tool outline")),
    remove_staging: () => btn("Remove", "delete", () => stagingDialog(f, box, res), cls("tool outline")),
    open: () => btn("Open in Fabric", "publish", lakehouseLink, cls("tool outline")),
    access: () => btn("Review access", "person", () => { catalogPage.tab = "access"; go("onelake"); }, cls("tool outline")),
    run_local: () => btn("Open notebook", "notebook", () => {
      openNotebook(f.notebook);
      toast("Set Runs on to This PC in the ribbon, and its runs start no Fabric job (OneLake reads and writes still use capacity).");
    }, cls("tool primary")),
    cost: () => btn("See cost", null, () => { optimizePage.tab = "cost"; go("optimize"); }, cls("tool outline")),
  }[f.action];
  return make ? make() : null;
}

function redrawXray(box, res) {
  if (typeof box === "function") box(); else drawXray(box, res);
}

function xrayFinding(f, box, res, picking = false) {
  const sev = { high: ["fail", "Fix first"], medium: ["wrn", "Worth fixing"], low: ["neu", "Good to know"] }[f.severity];
  const inBatch = xrayBatchItem(f.id);
  const queued = inBatch && !inBatch.ended && !inBatch.job_id;
  const tick = !picking ? null : xrayPickable(f)
    ? h("input", { type: "checkbox", class: "xray-pick", checked: xrayPage.picked.has(f.id) ? true : null,
      "aria-label": `Optimize ${f.schema ? `${f.schema}.${f.table}` : f.table} in ${f.lakehouse_name}`,
      onchange: (e) => { if (e.target.checked) xrayPage.picked.add(f.id); else xrayPage.picked.delete(f.id); xrayPage.drawPickBar?.(); } })
    : h("span", { class: "xray-pick" });
  return h("div", { class: "xray-item" },
    tick,
    h("span", { class: `pill-s ${sev[0]}` }, sev[1]),
    h("div", { class: "grow" },
      h("div", {}, h("b", {}, f.title)),
      h("div", { class: "muted-text small" }, f.detail, " ",
        h("a", { href: "#", onclick: (e) => { e.preventDefault(); openXrayDoc(f.kind); } }, "How to fix this")),
      xrayWhere(f) ? h("div", { class: "muted-text small where" }, xrayWhere(f)) : null,
      queued ? h("div", { class: "muted-text small" }, "Waiting in the Optimize batch above.") : xrayFix(f)),
    f.fix?.state === "submitted" || (inBatch && !inBatch.ended) ? null : xrayAction(f, box, res));
}

// Why Fabric stopped a fix's job, in a sentence, with what to do when the capacity was too busy.
function xrayStopped(status, message) {
  const why = String(message || "").trim().replace(/\.$/, "");
  const said = why ? `Fabric stopped it: ${/^[A-Z][a-z]/.test(why) ? why[0].toLowerCase() + why.slice(1) : why}.` : `Fabric stopped it (${status}).`;
  return /capacity|throttl/i.test(why) ? `${said} Try Optimize again when the capacity is less busy.` : said;
}

// A fix shows as submitted until its Fabric job finishes; only a completed job marks the finding fixed.
function xrayFix(f) {
  if (f.fix?.state === "submitted") return h("div", { class: "muted-text small" }, "Fix submitted to Fabric, not finished yet. Follow it in Monitor.");
  if (f.fix?.state === "failed") return h("div", { class: "reason small" }, xrayStopped(f.fix.status, f.fix.message));
  return null;
}

function vacuumArgs(f) {
  return { workspace_id: f.workspace_id, lakehouse_id: f.lakehouse_id, table: f.table, schema: f.schema,
    lakehouse_name: f.lakehouse_name, workspace_name: f.workspace_name, finding_id: f.id };
}

// Optimize one table: say what runs in Fabric and the risk check (FINOPS-004) first. Blocked has
// no Optimize button; warnings need the box ticked when the settings ask for it. The engine checks
// again on start: it refuses blocked or changed findings and records going ahead past warnings.
async function optimizeDialog(f, box, res, refresh = false) {
  const label = f.schema ? `${f.schema}.${f.table}` : f.table;
  const body = h("div", { class: "wl-confirm" }, h("p", { class: "muted-text" }, `Checking ${label} and its capacity...`));
  const start = btn("Optimize", "sync", async () => {
    if (ack && !ack.querySelector("input").checked) { toast("Tick \"I've looked at these\" first, or cancel.", true); return; }
    start.disabled = true;
    const r = await attempt(() => rpc("xray_optimize", { ...vacuumArgs(f), risk_id: risk?.id || "" }), "Optimize not started");
    if (!r) { start.disabled = false; return; }
    closeDialog();
    f.fix = { state: "submitted", job_id: r.job_id };
    toast(`Optimizing ${label} in Fabric. Follow it in Monitor.`, false, { log: true });
    redrawXray(box, res);
  }, "tool primary");
  let ack = null, risk = null;
  start.disabled = true;
  showDialog(`Optimize ${label}?`, body, [btn("Cancel", null, closeDialog, "tool outline"), start]);
  const { workspace_id, lakehouse_id, table, schema, lakehouse_name, workspace_name } = vacuumArgs(f);
  const p = await attempt(() => rpc("xray_optimize_check", { workspace_id, lakehouse_id, table, schema, lakehouse_name, workspace_name, refresh }),
    "Could not check the table");
  if (!p) { closeDialog(); return; }
  risk = p.risk;
  ack = riskAckBox(p.risk);
  body.replaceChildren(
    h("p", { class: "text" }, `Optimize runs in Fabric as a table maintenance job on ${label}, and uses capacity. `,
      "It compacts small files into larger ones with V-Order, then removes old files that no version in the last ",
      `${p.keep_days} days reads, so Restore and time travel still reach back that far (${p.why}).`),
    riskPanel(p.risk, { onRecheck: () => { closeDialog(); optimizeDialog(f, box, res, true); }, box: ack }));
  start.disabled = p.risk?.result === "blocked";
  if (start.disabled) start.title = "The risk check found something that stops this run";
}

// Vacuum one table: a dry run first says what it would delete and how much history it keeps, with
// the same risk check as Optimize. Blocked has no Vacuum button; warnings need the box ticked when the
// settings ask for it. The engine checks again on start, as it does for Optimize.
async function vacuumDialog(f, box, res, refresh = false) {
  const label = f.schema ? `${f.schema}.${f.table}` : f.table;
  const body = h("div", { class: "wl-confirm" }, h("p", { class: "muted-text" }, `Checking what Vacuum would delete from ${label} and its capacity...`));
  const vacuumBtn = btn("Vacuum", "delete", async () => {
    if (ack && !ack.querySelector("input").checked) { toast("Tick \"I've looked at these\" first, or cancel.", true); return; }
    vacuumBtn.disabled = true;
    const r = await attempt(() => rpc("xray_vacuum", { ...vacuumArgs(f), risk_id: risk?.id || "" }), "Vacuum not started");
    if (!r) { vacuumBtn.disabled = false; return; }
    closeDialog();
    f.fix = { state: "submitted", job_id: r.job_id };
    toast(`Vacuuming ${label} in Fabric, keeping ${r.keep_days} days of history. Follow it in Monitor.`, false, { log: true });
    redrawXray(box, res);
  }, "tool danger-solid");
  let ack = null, risk = null;
  vacuumBtn.disabled = true;
  showDialog(`Vacuum ${label}`, body, [btn("Cancel", null, closeDialog, "tool outline"), vacuumBtn]);
  const { workspace_id, lakehouse_id, table, schema, lakehouse_name, workspace_name } = vacuumArgs(f);
  const p = await attempt(() => rpc("xray_vacuum_preview", { workspace_id, lakehouse_id, table, schema, lakehouse_name, workspace_name, refresh }),
    "Could not check the table");
  if (!p) { closeDialog(); return; }
  risk = p.risk;
  ack = riskAckBox(p.risk);
  const what = p.files
    ? `Vacuum deletes ${p.files.toLocaleString()} old file${p.files === 1 ? "" : "s"}${p.bytes != null ? ` (${sizeText(p.bytes)})` : ""} that no version in the last ${p.keep_days} days reads.`
    : `Nothing to delete yet: every old file is still inside the ${p.keep_days} days of history this table keeps.`;
  body.replaceChildren(
    h("p", { class: "text" }, what),
    h("p", { class: "muted-text small" }, `It keeps ${p.keep_days} days of history (${p.why}), so Restore and time travel still reach back that far. `,
      "It runs in Fabric as table maintenance, VACUUM only, does not compact files, and uses capacity."),
    riskPanel(p.risk, { onRecheck: () => { closeDialog(); vacuumDialog(f, box, res, true); }, box: ack }));
  vacuumBtn.disabled = !p.files || p.risk?.result === "blocked";
  if (p.risk?.result === "blocked") vacuumBtn.title = "The risk check found something that stops this run";
}

// Remove one leftover Dataflow Gen2 staging lakehouse: the engine checks again, live, that a newer
// one exists and nothing was written to this one lately; anything it couldn't confirm keeps it.
async function stagingDialog(f, box, res) {
  const args = { workspace_id: f.workspace_id, lakehouse_id: f.lakehouse_id };
  const body = h("div", {}, h("p", { class: "muted-text" }, `Checking ${f.lakehouse_name}...`));
  let p = null;
  const removeBtn = btn("Remove...", "delete", async () => {
    const ok = await confirmDialog(`Delete ${p.lakehouse_name}?`,
      `This deletes the lakehouse ${p.lakehouse_name} in ${f.workspace_name} with its ${p.files.toLocaleString()} file${p.files === 1 ? "" : "s"} `
      + `(${sizeText(p.bytes)}). Fabric may not let you get it back. ${p.kept} is kept.`, "Delete lakehouse",
      { danger: true, typeToConfirm: "delete" });
    if (!ok) return;
    const r = await attempt(() => rpc("xray_remove_staging", { ...args, lakehouse_name: p.lakehouse_name,
      workspace_name: f.workspace_name, finding_id: f.id }), "Lakehouse not deleted");
    if (!r) return;
    f.fixed = true;
    toast(`Deleted ${p.lakehouse_name}. ${p.kept} is kept.`, false, { log: true });
    redrawXray(box, res);
  }, "tool danger");
  removeBtn.disabled = true;
  showDialog(`Remove ${f.lakehouse_name}`, body, [btn("Cancel", null, closeDialog, "tool outline"), removeBtn]);
  p = await attempt(() => rpc("xray_staging_preview", args), "Could not check the lakehouse");
  if (!p) { closeDialog(); return; }
  const row = (k, v) => h("tr", {}, h("th", {}, k), h("td", {}, v));
  body.replaceChildren(
    h("p", { class: "text" }, p.ok
      ? `${p.lakehouse_name} looks left over: ${p.kept} in the same workspace is newer, nothing has been written to it for ${p.quiet_days} days or more, `
        + "and every dataflow in the workspace has refreshed since without writing to it."
      : `${p.lakehouse_name} stays. Nothing will be deleted:`),
    p.ok ? "" : h("ul", { class: "reason" }, p.why.map((w) => h("li", {}, w))),
    h("table", { class: "list compact" }, h("tbody", {},
      row("Workspace", f.workspace_name),
      row("Made", p.created ? when(p.created) : "Not known"),
      row("Last written", p.last_write ? when(p.last_write) : p.files === 0 ? "Never: it's empty" : "Not known"),
      row("Holds", p.files == null ? "Not known" : `${p.files.toLocaleString()} file${p.files === 1 ? "" : "s"}, ${sizeText(p.bytes)}`),
      row("Kept", p.kept ? `${p.kept} (the newest, always kept)` : "None"),
      row("Dataflows", (p.dataflows || []).length ? h("div", {}, p.dataflows.map((d) => h("div", {},
        d.ok ? "✓ " : "✗ ", h("b", {}, d.name), h("span", { class: "muted-text small" },
          d.names_it ? " names it" : d.last_refresh ? ` refreshed ${relTime(d.last_refresh)}` : " no completed refresh"))))
        : "None found"))),
    h("p", { class: "muted-text small" }, "Fabric doesn't say which staging lakehouse a dataflow uses, so the newest is always kept, "
      + "and an older one goes only when every dataflow's refreshes and definition show it doesn't stage there. Only this lakehouse is deleted."));
  removeBtn.disabled = !p.ok;
}

// Every shown "Needs vacuum" finding, one Fabric job each, each keeping its own table's history.
// Each table's risk check runs first: only tables that pass start here. A table with warnings or a
// block is left for its own Vacuum dialog, which shows the check and asks before going ahead.
async function vacuumAll(findings, box, res) {
  const body = h("div", {}, h("p", { class: "muted-text" }, `Checking ${findings.length} tables and their capacity...`));
  const go = btn(`Vacuum ${findings.length}`, "delete", () => run(), "tool danger-solid");
  go.disabled = true;
  showDialog(`Vacuum ${findings.length} tables`, body, [btn("Cancel", null, closeDialog, "tool outline"), go]);
  const ready = [], held = [];
  for (const f of findings) {
    const { workspace_id, lakehouse_id, table, schema, lakehouse_name, workspace_name } = vacuumArgs(f);
    try {
      const p = await rpc("xray_vacuum_preview", { workspace_id, lakehouse_id, table, schema, lakehouse_name, workspace_name });
      const result = p.risk?.result;
      if (result === "blocked" || result === "warning") held.push({ f, why: result === "blocked" ? "blocked" : "has warnings" });
      else if (p.files) ready.push({ f, risk_id: p.risk?.id || "" });
    } catch { held.push({ f, why: "couldn't be checked" }); }
  }
  const run = async () => {
    closeDialog();
    let started = 0;
    const failed = [];
    for (const { f, risk_id } of ready) {
      try { const r = await rpc("xray_vacuum", { ...vacuumArgs(f), risk_id }); f.fix = { state: "submitted", job_id: r.job_id }; started++; } catch { failed.push(f.table); }
    }
    toast(failed.length ? `Started ${started} vacuums. Not started: ${failed.join(", ")}` : `Started ${started} vacuums in Fabric. Follow them in Monitor.`,
      !!failed.length, { log: true });
    redrawXray(box, res);
  };
  const name = ({ f }) => (f.schema ? `${f.schema}.${f.table}` : f.table);
  body.replaceChildren(
    h("p", { class: "text" },
      "Each table is vacuumed in Fabric as its own table maintenance job, and uses capacity. Each keeps the history it promises: at least 7 days, ",
      "or its layer's Keep history days, or its own retention setting, whichever is longest."),
    held.length ? h("p", { class: "reason small" }, `Not started here, so open each one's Vacuum to see its risk check: `,
      held.map((x) => `${name(x)} (${x.why})`).join(", "), ".") : "");
  go.replaceChildren(icon("delete", 16), `Vacuum ${ready.length}`);
  go.disabled = !ready.length;
}

// ---- Optimize many tables at once

// At most this many of a batch's tables are optimizing in Fabric at once: the next starts only when
// one of them ends, so a long list doesn't land on the capacity all together. How often the batch
// asks Fabric how its running jobs are doing.
const XRAY_BATCH = { atOnce: 3, pollMs: 20000 };
const XRAY_BATCH_ENDED = new Set(["done", "failed", "held", "error", "stopped"]);

function xrayPickable(f) {
  return f.action === "optimize" && !f.fixed && f.fix?.state !== "submitted" && !(xrayBatchItem(f.id)?.ended === false);
}

function xrayBatchActive() {
  return !!xrayPage.batch && !xrayPage.batch.finished;
}

// A finding's line in the batch while the batch is still on the card.
function xrayBatchItem(id) {
  return xrayPage.batch?.items.find((it) => it.f.id === id) || null;
}

function xrayLabel(f) {
  return f.schema ? `${f.schema}.${f.table}` : f.table;
}

// Above the findings: tick all shown, or Optimize all, which ticks every finding shown so you can
// untick the ones to leave out; then Optimize N selected asks before anything starts.
function xrayPickBar(pickable, shown, box, res) {
  const bar = h("div", { class: "row wrap xray-pickbar" });
  const draw = () => {
    const n = xrayPage.picked.size;
    const all = h("input", { type: "checkbox", class: "xray-pick", checked: n && n === pickable.length ? true : null,
      "aria-label": n === pickable.length ? "Clear the selection" : `Select all ${pickable.length} tables Optimize can fix`,
      onchange: (e) => {
        if (e.target.checked) { pickable.forEach((f) => xrayPage.picked.add(f.id)); xrayPage.showAll = true; }
        else xrayPage.picked.clear();
        drawXray(box, res);
        (box instanceof Element ? box : document).querySelector(".xray-pickbar input")?.focus();
      } });
    all.indeterminate = n > 0 && n < pickable.length;
    bar.replaceChildren(...[all,
      h("span", { class: "grow muted-text small" }, n
        ? `${n} of ${pickable.length} table${pickable.length === 1 ? "" : "s"} selected. Untick any you want to leave out.`
        : `${pickable.length} table${pickable.length === 1 ? "" : "s"} here can be optimized. Tick the ones to fix together.`),
      n ? btn("Clear", null, () => { xrayPage.picked.clear(); drawXray(box, res); }, "tool link small") : null,
      n ? btn(`Optimize ${n} selected`, "sync", () => optimizeBatchDialog(shown.filter((f) => xrayPage.picked.has(f.id)), box, res), "tool primary small")
        : btn(`Optimize all ${pickable.length}`, "sync", () => {
          pickable.forEach((f) => xrayPage.picked.add(f.id));
          xrayPage.showAll = true;
          drawXray(box, res);
          (box instanceof Element ? box : document).querySelector(".xray-pickbar button.primary")?.focus();
        }, "tool outline small")].filter(Boolean));
  };
  xrayPage.drawPickBar = () => { if (bar.isConnected) draw(); };
  draw();
  return bar;
}

// Asks once for the whole batch, with the count and every table named. Nothing starts before Optimize here.
function optimizeBatchDialog(findings, box, res) {
  if (!findings.length || xrayBatchActive()) return;
  const n = findings.length;
  const lakehouses = new Set(findings.map((f) => f.lakehouse_id)).size;
  const go = btn(`Optimize ${n}`, "sync", () => { closeDialog(); optimizeBatch(findings); }, "tool primary");
  showDialog(`Optimize ${n} table${n === 1 ? "" : "s"}?`, h("div", { class: "wl-confirm" },
    h("p", { class: "text" }, `Each of the ${n} table${n === 1 ? "" : "s"} in ${lakehouses} lakehouse${lakehouses === 1 ? "" : "s"} runs in Fabric `,
      "as its own table maintenance job, and uses capacity. Each compacts small files with V-Order, then removes old files ",
      "past the history its table keeps, as a single Optimize does."),
    h("ul", { class: "muted-text small" },
      h("li", {}, `At most ${XRAY_BATCH.atOnce} run at once. The next starts when one finishes.`),
      h("li", {}, "Each table's risk check runs just before it starts. A table with warnings, a block, or a check that couldn't run is skipped, "
        + "and stays on the list for its own Optimize, where you can see the check."),
      h("li", {}, "Stop starts nothing more. Jobs already running in Fabric finish; cancel them in Monitor. "
        + "Closing the app stops the batch the same way.")),
    h("div", { class: "xray-batch-names" }, findings.map((f) => h("div", { class: "small" },
      h("b", {}, xrayLabel(f)), h("span", { class: "muted-text" }, ` ${f.workspace_name} / ${f.lakehouse_name}`))))),
  [btn("Cancel", null, closeDialog, "tool outline"), go]);
}

function redrawXrayCard() {
  if (xrayPage.box?.isConnected && xrayPage.data) drawXray(xrayPage.box, xrayPage.data);
}

// Marks the finding on the card as it is now, as well as the one the batch started from (a scan
// since may have drawn new ones).
function xrayBatchMark(item, fix) {
  const now = xrayPage.data?.result?.findings?.find((x) => x.id === item.f.id);
  for (const f of new Set([item.f, now].filter(Boolean))) {
    f.fix = fix;
    if (fix.state === "fixed") f.fixed = true;
  }
}

function xrayBatchWait(batch, ms) {
  return new Promise((resolve) => {
    const t = setTimeout(resolve, ms);
    batch.wake = () => { clearTimeout(t); resolve(); };
  });
}

async function optimizeBatch(findings) {
  const batch = { items: findings.map((f) => ({ f, state: "waiting", why: "", job_id: null, ended: false })),
    stopped: false, finished: false, closed: false, wake: null };
  xrayPage.batch = batch;
  xrayPage.picked.clear();
  const set = (it, state, why = "") => { it.state = state; it.why = why; it.ended = XRAY_BATCH_ENDED.has(state); };
  const running = () => batch.items.filter((it) => it.state === "running");
  const next = () => batch.items.find((it) => it.state === "waiting");
  toast(`Optimizing ${findings.length} table${findings.length === 1 ? "" : "s"} in Fabric, ${XRAY_BATCH.atOnce} at a time.`, false, { log: true });
  redrawXrayCard();
  while (!batch.closed) {
    let it;
    while (!batch.stopped && !batch.closed && running().length < XRAY_BATCH.atOnce && (it = next())) {
      await optimizeBatchStart(batch, it, set);
      redrawXrayCard();
    }
    if (batch.stopped) batch.items.filter((x) => x.state === "waiting").forEach((x) => set(x, "stopped", "You stopped the batch first."));
    if (!running().length && (batch.stopped || !next())) break;
    await xrayBatchWait(batch, XRAY_BATCH.pollMs);
    if (batch.closed) break;
    await optimizeBatchPoll(running(), set);
    redrawXrayCard();
  }
  batch.finished = true;
  if (!batch.closed) {
    const n = (s) => batch.items.filter((x) => x.state === s).length;
    const bad = n("failed") + n("error");
    const parts = [`${n("done")} optimized`, n("failed") && `${n("failed")} failed`, n("error") && `${n("error")} couldn't start`, n("held") && `${n("held")} skipped by the risk check`,
      n("stopped") && `${n("stopped")} not started`].filter(Boolean);
    toast(`Optimize batch finished: ${parts.join(", ")}.`, !!bad, { log: true });
  }
  redrawXrayCard();
}

// One table: its risk check, then its job, as a single Optimize does. Only a pass goes ahead here; the
// engine checks again on start and refuses anything that changed.
async function optimizeBatchStart(batch, it, set) {
  set(it, "checking");
  redrawXrayCard();
  const { f } = it;
  const { workspace_id, lakehouse_id, table, schema, lakehouse_name, workspace_name } = vacuumArgs(f);
  let p;
  try {
    p = await rpc("xray_optimize_check", { workspace_id, lakehouse_id, table, schema, lakehouse_name, workspace_name });
  } catch (e) { set(it, "error", friendlyError(e).text); return; }
  const result = p.risk?.result;
  if (result !== "pass") {
    set(it, "held", result === "blocked" ? `The risk check blocks it: ${p.risk.summary}`
      : result === "warning" ? "The risk check has warnings. Open its own Optimize to see them."
      : "The risk check couldn't run. Open its own Optimize to see why.");
    return;
  }
  if (batch.stopped || batch.closed) { set(it, "stopped", "You stopped the batch first."); return; }
  try {
    const r = await rpc("xray_optimize", { ...vacuumArgs(f), risk_id: p.risk.id || "" });
    it.job_id = r.job_id;
    set(it, "running");
    xrayBatchMark(it, { state: "submitted", job_id: r.job_id });
  } catch (e) { set(it, "error", friendlyError(e).text); }
}

async function optimizeBatchPoll(items, set) {
  if (!items.length) return;
  let runs;
  try {
    runs = await rpc("fabric_runs", { limit: 200, job_ids: items.map((it) => it.job_id) });
  } catch { return; }  // Fabric unreachable for now: ask again next time
  const byJob = new Map((runs || []).map((r) => [r.job_id, r]));
  for (const it of items) {
    const run = byJob.get(it.job_id);
    if (!run || run.stale) continue;
    if (run.status === "Completed") {
      set(it, "done");
      xrayBatchMark(it, { state: "fixed", job_id: it.job_id, status: run.status, message: "" });
    } else if (["Failed", "Cancelled", "Deduped"].includes(run.status)) {
      set(it, "failed", xrayStopped(run.status, run.message));
      xrayBatchMark(it, { state: "failed", job_id: it.job_id, status: run.status, message: run.message || "" });
    }
  }
}

const XRAY_BATCH_STATES = {
  waiting: ["neu", "Waiting"], checking: ["info", "Checking"], running: ["info", "Running in Fabric"],
  done: ["pass", "Optimized"], failed: ["fail", "Failed"], error: ["fail", "Couldn't start"],
  held: ["wrn", "Skipped"], stopped: ["neu", "Not started"],
};

// The batch's progress on the card, a line per table, until you close it.
function xrayBatchPanel() {
  const batch = xrayPage.batch;
  if (!batch) return null;
  const count = (s) => batch.items.filter((x) => x.state === s).length;
  const total = batch.items.length;
  const ended = batch.items.filter((x) => x.ended).length;
  const summary = [count("running") && `${count("running")} running in Fabric`, count("waiting") && `${count("waiting")} waiting`,
    count("done") && `${count("done")} optimized`, count("failed") && `${count("failed")} failed`, count("error") && `${count("error")} couldn't start`,
    count("held") && `${count("held")} skipped`, count("stopped") && `${count("stopped")} not started`].filter(Boolean).join(", ");
  const title = batch.finished ? (batch.stopped ? "Optimize batch stopped" : "Optimize batch finished")
    : batch.stopped ? `Stopping: waiting for ${count("running")} running in Fabric` : `Optimizing ${total} table${total === 1 ? "" : "s"}`;
  const stop = btn("Stop", "dismiss", () => {
    batch.stopped = true;
    batch.wake?.();
    redrawXrayCard();
  }, "tool outline small");
  const close = btn(batch.finished ? "Close" : "Stop watching", null, () => {
    batch.closed = true;
    batch.wake?.();
    xrayPage.batch = null;
    redrawXrayCard();
  }, "tool link small");
  return h("section", { class: "xray-batch", "aria-label": "Optimize batch" },
    h("div", { class: "row wrap" }, h("b", { class: "grow" }, title),
      !batch.finished && !batch.stopped ? stop : null,
      batch.finished || batch.stopped ? close : null),
    h("div", { class: "muted-text small", role: "status" }, `${ended} of ${total} ended. ${summary}.`),
    h("div", { class: "progress" }, h("div", { class: "bar", style: `width:${Math.max(4, Math.round(100 * ended / total))}%` })),
    batch.stopped && !batch.finished ? h("p", { class: "muted-text small" },
      "Nothing more starts. Jobs already running in Fabric finish; cancel them in Monitor, or Stop watching to leave them to it.") : null,
    h("div", { class: "xray-batch-list" }, batch.items.map((it) => {
      const [tone, label] = XRAY_BATCH_STATES[it.state];
      return h("div", { class: "xray-batch-item" },
        h("span", { class: `pill-s ${tone}` }, label),
        h("div", { class: "grow" }, h("b", {}, xrayLabel(it.f)),
          h("span", { class: "muted-text small" }, ` ${it.f.workspace_name} / ${it.f.lakehouse_name}`),
          it.why ? h("div", { class: `small ${it.state === "held" || it.state === "stopped" ? "muted-text" : "reason"}` }, it.why) : null));
    })));
}

function xrayLakehouses(rows) {
  if (!rows.length) return null;
  const shown = rows.filter((r) => matches(`${r.lakehouse_name} ${r.workspace_name}`));
  return h("details", { class: "xray-lakehouses" },
    h("summary", {}, h("b", {}, "Your lakehouses"), h("span", { class: "muted-text small" },
      ` ${rows.length}, with Adopt to bring a lakehouse's tables under metadata`)),
    h("table", { class: "list" },
      h("thead", {}, h("tr", {}, ["Lakehouse", "Workspace", "Tables", "Size", "Findings", ""].map((c, i) => h("th", { class: i > 1 ? "n" : null }, c)))),
      h("tbody", {}, shown.map((r) => h("tr", {},
        h("td", {}, h("span", { class: "item" }, itemIcon("Lakehouse", 16), r.lakehouse_name)),
        h("td", {}, r.workspace_name),
        h("td", { class: "n" }, r.tables), h("td", { class: "n" }, sizeText(r.bytes)),
        h("td", { class: "n" }, r.issues ? h("span", { class: "pill-s wrn" }, r.issues) : h("span", { class: "muted-text" }, "None")),
        h("td", { class: "n" }, r.managed ? h("span", { class: "pill-s pass" }, "Managed by metadata")
          : !r.tables ? null
          : btn(r.adopted ? `Adopt more (${r.adopted} adopted)` : "Adopt", "add", () => adoptDialog(r), "tool link small")))))));
}

async function adoptDialog(r) {
  const project = state.meta.project || await rpc("meta_project").catch(() => null);
  if (!project?.path) {
    showDialog("Adopt needs a metadata project", h("p", { class: "text" },
      "Adopting turns each table into a metadata entity that loads from it. Create or open a metadata project first."),
    [btn("Cancel", null, closeDialog, "tool outline"), btn("Open Data mesh", null, () => { closeDialog(); go("metadata"); }, "tool primary")]);
    return;
  }
  const body = h("div", { class: "scroll" }, h("p", { class: "muted-text" }, `Reading the tables in ${r.lakehouse_name}...`));
  const layerSel = h("select", {});
  let plan = null;
  const adopt = async () => {
    const picked = [...body.querySelectorAll("tr[data-table]")].filter((tr) => tr.querySelector("input[type=checkbox]").checked)
      .map((tr) => ({ schema: tr.dataset.schema || null, table: tr.dataset.table, name: tr.querySelector("input.name").value.trim(),
                      keys: tr.querySelector("input.keys").value.split(",").map((k) => k.trim()).filter(Boolean) }));
    if (!picked.length) { toast("Pick at least one table.", true); return; }
    const res = await attempt(() => rpc("xray_adopt", { workspace_id: r.workspace_id, lakehouse_id: r.lakehouse_id,
      lakehouse_name: r.lakehouse_name, workspace_name: r.workspace_name, tables: picked, layer: layerSel.value }), "Not adopted");
    if (!res) return;
    closeDialog();
    if (res.problems.length) toast(`Adopted ${res.adopted.length}. Skipped: ${res.problems.join("; ")}`, true);
    else toast(`Adopted ${res.adopted.length} table${res.adopted.length === 1 ? "" : "s"} into ${res.layer}. Run them from Data mesh.`, false, { log: true });
    state.meta.project = await rpc("meta_project").catch(() => state.meta.project);
    render();
  };
  showDialog(`Adopt ${r.lakehouse_name}`, body,
    [btn("Cancel", null, closeDialog, "tool outline"), btn("Adopt", "add", adopt, "tool primary")]);
  document.querySelector(".dialog")?.classList.add("roomy");
  plan = await attempt(() => rpc("xray_adopt_plan", { workspace_id: r.workspace_id, lakehouse_id: r.lakehouse_id,
    lakehouse_name: r.lakehouse_name, workspace_name: r.workspace_name }), "Could not read the lakehouse");
  if (!plan) { closeDialog(); return; }
  layerSel.replaceChildren(...plan.layers.map((l) => h("option", { value: l, selected: l === plan.layer ? true : null }, l)));
  body.replaceChildren(
    h("p", { class: "text" }, `Each table you pick becomes an entity in the first layer that loads from it: in full each run, or only new rows when it has a timestamp like updated_at. The original tables in ${r.lakehouse_name} are never changed, so your current loads keep working while you move them over.`),
    h("label", { class: "field inline" }, "Layer", layerSel),
    h("table", { class: "list" },
      h("thead", {}, h("tr", {}, ["", "Table", "Entity", "Key", ""].map((c) => h("th", {}, c)))),
      h("tbody", {}, plan.tables.map((t) => h("tr", { "data-table": t.table, "data-schema": t.schema || "" },
        h("td", {}, h("input", { type: "checkbox", checked: t.ok && !t.exists ? true : null, disabled: t.ok ? null : true,
          "aria-label": `Adopt ${t.schema ? `${t.schema}.${t.table}` : t.table}` })),
        h("td", {}, h("span", { class: "mono" }, t.schema ? `${t.schema}.${t.table}` : t.table),
          t.columns ? h("div", { class: "muted-text small" }, `${t.columns.length} columns`) : null),
        h("td", {}, t.ok ? h("input", { class: "name", value: t.exists ? `${t.name}_adopted` : t.name, "aria-label": "Entity name" }) : null),
        h("td", {}, t.ok ? h("input", { class: "keys", value: (t.keys || []).join(", "), "aria-label": "Key columns", list: `cols-${t.table}` }) : null,
          t.ok ? h("datalist", { id: `cols-${t.table}` }, t.columns.map((c) => h("option", { value: c }))) : null),
        h("td", { class: "muted-text small" }, t.why || (t.exists ? `${t.name} exists, so it gets a new name` : "")))))));
}
