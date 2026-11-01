// Estate X-ray on Home: every lakehouse you can reach, read once, with what needs attention
// ranked first and a fix on each line. "Adopt" turns a lakehouse's tables into metadata entities.

const xrayPage = { data: null, timer: null, kind: "all", showAll: false, folded: false, watching: null };

const XRAY_KINDS = {
  small_files: "Small files", unidentified: "Unidentified", access: "Access", cost: "Cost",
  stale: "Not changing", vacuum: "Needs vacuum", empty: "Empty lakehouses",
};

// Where each kind of finding is explained, with how to fix it by hand (docs/xray-fixes.md).
const XRAY_DOCS = {
  small_files: "small-files", unidentified: "unidentified-tables", access: "access", cost: "cost",
  stale: "not-changing", vacuum: "needs-vacuum", empty: "empty-lakehouses",
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
    else if (res.result) forgeEvent("xray.scan.finished", box.querySelector(".xray-stats"));
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
    open.length ? h("div", { class: "xray-list" }, shown.slice(0, limit).map((f) => xrayFinding(f, box, res)))
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
      + "the names of people or cost estimates. Three buttons write, and each asks first. Optimize names the table, says it runs in "
      + "Fabric and uses capacity, and shows the risk check, then starts a Fabric table maintenance job that compacts it "
      + "and removes old files past the history the table keeps. Vacuum first shows how many old files it would delete, and deletes them in "
      + "Fabric only when you confirm, keeping the history the table promises. Adopt shows the entities it would create "
      + "and, when you confirm, writes them as files in your metadata project on this PC; the table itself isn't changed.",
    results: "Findings are kept on this PC. Lakehouse and table findings added to the central lakehouse show in "
      + "Optimize > Opportunities, and anyone who can read that lakehouse can read them, including the workspace, "
      + "lakehouse and table names of every workspace this scan reached; the app hides items you can't open, but "
      + "that is a courtesy, not security. Optimize and Vacuum run as jobs in Fabric; follow them in Monitor.",
    anchor: "estate-x-ray-preview",
  });
}

// Where the scan's findings went besides this PC, when it is connected to a central lakehouse.
function xrayShared(shared) {
  if (!shared) return null;
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

function xrayFinding(f, box, res) {
  const sev = { high: ["fail", "Fix first"], medium: ["wrn", "Worth fixing"], low: ["neu", "Good to know"] }[f.severity];
  return h("div", { class: "xray-item" },
    h("span", { class: `pill-s ${sev[0]}` }, sev[1]),
    h("div", { class: "grow" },
      h("div", {}, h("b", {}, f.title)),
      h("div", { class: "muted-text small" }, f.detail, " ",
        h("a", { href: "#", onclick: (e) => { e.preventDefault(); openXrayDoc(f.kind); } }, "How to fix this")),
      xrayWhere(f) ? h("div", { class: "muted-text small where" }, xrayWhere(f)) : null,
      xrayFix(f)),
    f.fix?.state === "submitted" ? null : xrayAction(f, box, res));
}

// A fix shows as submitted until its Fabric job finishes; only a completed job marks the finding fixed.
function xrayFix(f) {
  if (f.fix?.state === "submitted") return h("div", { class: "muted-text small" }, "Fix submitted to Fabric, not finished yet. Follow it in Monitor.");
  if (f.fix?.state === "failed") return h("div", { class: "reason small" }, `The fix didn't finish in Fabric (${f.fix.status})${f.fix.message ? `: ${f.fix.message}` : ""}.`);
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

// Vacuum one table: a dry run first says what it would delete and how much history it keeps.
async function vacuumDialog(f, box, res) {
  const label = f.schema ? `${f.schema}.${f.table}` : f.table;
  const body = h("div", {}, h("p", { class: "muted-text" }, `Checking what Vacuum would delete from ${label}...`));
  const vacuumBtn = btn("Vacuum", "delete", async () => {
    vacuumBtn.disabled = true;
    const r = await attempt(() => rpc("xray_vacuum", vacuumArgs(f)), "Vacuum not started");
    if (!r) { vacuumBtn.disabled = false; return; }
    closeDialog();
    f.fix = { state: "submitted", job_id: r.job_id };
    toast(`Vacuuming ${label} in Fabric, keeping ${r.keep_days} days of history. Follow it in Monitor.`, false, { log: true });
    redrawXray(box, res);
  }, "tool danger-solid");
  vacuumBtn.disabled = true;
  showDialog(`Vacuum ${label}`, body, [btn("Cancel", null, closeDialog, "tool outline"), vacuumBtn]);
  const { workspace_id, lakehouse_id, table, schema, lakehouse_name } = vacuumArgs(f);
  const p = await attempt(() => rpc("xray_vacuum_preview", { workspace_id, lakehouse_id, table, schema, lakehouse_name }),
    "Could not check the table");
  if (!p) { closeDialog(); return; }
  const what = p.files
    ? `Vacuum deletes ${p.files.toLocaleString()} old file${p.files === 1 ? "" : "s"}${p.bytes != null ? ` (${sizeText(p.bytes)})` : ""} that no version in the last ${p.keep_days} days reads.`
    : `Nothing to delete yet: every old file is still inside the ${p.keep_days} days of history this table keeps.`;
  body.replaceChildren(
    h("p", { class: "text" }, what),
    h("p", { class: "muted-text small" }, `It keeps ${p.keep_days} days of history (${p.why}), so Restore and time travel still reach back that far. `,
      "It runs in Fabric as table maintenance, VACUUM only, and does not compact files."),
  );
  vacuumBtn.disabled = !p.files;
}

// Every shown "Needs vacuum" finding, one Fabric job each, each keeping its own table's history.
function vacuumAll(findings, box, res) {
  const run = async () => {
    closeDialog();
    let started = 0;
    const failed = [];
    for (const f of findings) {
      try { const r = await rpc("xray_vacuum", vacuumArgs(f)); f.fix = { state: "submitted", job_id: r.job_id }; started++; } catch { failed.push(f.table); }
    }
    toast(failed.length ? `Started ${started} vacuums. Not started: ${failed.join(", ")}` : `Started ${started} vacuums in Fabric. Follow them in Monitor.`,
      !!failed.length, { log: true });
    redrawXray(box, res);
  };
  showDialog(`Vacuum ${findings.length} tables`, h("p", { class: "text" },
    "Each table is vacuumed in Fabric as its own table maintenance job. Each keeps the history it promises: at least 7 days, ",
    "or its layer's Keep history days, or its own retention setting, whichever is longest."),
  [btn("Cancel", null, closeDialog, "tool outline"), btn(`Vacuum ${findings.length}`, "delete", run, "tool danger-solid")]);
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
