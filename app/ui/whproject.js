// The warehouse as files in Git (WH-11), on the Warehouse page's Project tab: the <name>.Warehouse
// folder Fabric's Git integration writes, found in a repository opened in Source control
// (warehouse_project_service.py). A file opens in a tab of its own (tab.proj), and Save writes it
// back. Import from the warehouse scripts its objects from the catalog (catalog queries in Fabric)
// and writes them as files; New object writes one file from a starting script. Nothing is
// committed: the files show as changes in Source control.

const whProjects = { state: "idle", repos: [], projects: [], error: "" };

async function whLoadProjects() {
  whProjects.state = "loading";
  try {
    const r = await rpc("wh_projects_list");
    Object.assign(whProjects, { state: "done", repos: r.repos, projects: r.projects, error: "" });
  } catch (err) {
    Object.assign(whProjects, { state: "failed", error: whWhy(err) });
  }
}

// A failed call says so; one the person cancelled (a sign-in window closed) says nothing.
function whFailed(what, err) {
  const f = friendlyError(err);
  if (!f.cancelled) toast(`${what}: ${f.text}`, true);
}

// Why a call left something not loaded or not run, for the line that says so: friendlyError has no
// text for a closed sign-in window, so it says that instead of nothing.
function whWhy(err) {
  const f = friendlyError(err);
  return f.cancelled ? "The sign-in window was closed." : f.text;
}

const WH_KIND_LABELS = { schema: "Schema", table: "Table", view: "View", procedure: "Stored procedure", function: "Function" };
const WH_KIND_ICONS = { schema: "folder", table: "table", view: "windowDatabase", procedure: "flash", function: "braces" };
const WH_KIND_OF = { U: "table", V: "view", P: "procedure", FN: "function", IF: "function", TF: "function" };

const WH_MODULES_SQL = `SELECT s.name AS schema_name, o.name AS object_name, RTRIM(o.type) AS object_type, m.definition
FROM sys.sql_modules AS m
JOIN sys.objects AS o ON o.object_id = m.object_id
JOIN sys.schemas AS s ON s.schema_id = o.schema_id
WHERE o.type IN ('V', 'P', 'FN', 'IF', 'TF') AND o.is_ms_shipped = 0
ORDER BY s.name, o.name;`;

// User schemas: dbo, guest, INFORMATION_SCHEMA and sys are 1 to 4, database roles from 16384.
const WH_SCHEMAS_SQL = `SELECT s.name FROM sys.schemas AS s WHERE s.schema_id BETWEEN 5 AND 16383 ORDER BY s.name;`;

// Schemas Fabric makes and keeps itself in a warehouse (queryinsights, _rsc): they
// aren't the project's, so Import, Compare and Deploy leave them out.
const WH_FABRIC_SCHEMAS = new Set(["queryinsights", "_rsc", "sys", "information_schema"]);
const whFabricSchema = (schema) => WH_FABRIC_SCHEMAS.has(String(schema || "").toLowerCase());

// The script a new object's file starts with.
function whNewObjectText(kind, schema, name) {
  const full = whName(schema, name);
  return {
    table: `CREATE TABLE ${full}\n(\n    [Id] int NOT NULL\n);\nGO\n`,
    view: `CREATE VIEW ${full}\nAS\nSELECT 1 AS [Value];\nGO\n`,
    procedure: `CREATE PROCEDURE ${full}\nAS\nBEGIN\n    SET NOCOUNT ON;\nEND;\nGO\n`,
    function: `CREATE FUNCTION ${full} ()\nRETURNS int\nAS\nBEGIN\n    RETURN 1;\nEND;\nGO\n`,
    schema: `CREATE SCHEMA ${whQuote(name)};\nGO\n`,
  }[kind];
}

// The projects for this warehouse first (by name), then the others in the open repositories.
function whProjectChoices(ref) {
  const mine = whProjects.projects.filter((p) => p.name.toLowerCase() === ref.name.toLowerCase());
  return { mine, others: whProjects.projects.filter((p) => !mine.includes(p)) };
}

const whProjectId = (p) => `${p.repo}|${p.folder}`;

// The Project tab. actions.open(file, project) opens a project file in a tab.
function whProject(ref, actions) {
  const box = h("div", { class: "explorer-content wh-project" });
  const pick = sqlPage.whProjectPick ||= {};
  const files = sqlPage.whProjectFiles ||= {};  // project id -> { state, data, error }

  const current = () => {
    const { mine, others } = whProjectChoices(ref);
    const all = [...mine, ...others];
    return all.find((p) => whProjectId(p) === pick[sqlRefKey(ref)]) || mine[0] || null;
  };
  async function loadFiles(project) {
    const id = whProjectId(project);
    files[id] = { state: "loading" };
    draw();
    try {
      files[id] = { state: "done", data: await rpc("wh_project_files", { repo: project.repo, folder: project.folder }) };
    } catch (err) {
      files[id] = { state: "failed", error: whWhy(err) };
    }
    sqlPage.whRedraw?.();
  }
  const refresh = async () => {
    await whLoadProjects();
    const p = current();
    if (p) await loadFiles(p); else sqlPage.whRedraw?.();
  };

  function tree(project, data) {
    const rows = [];
    const byDir = new Map();
    for (const f of data.files) {
      const at = f.path.lastIndexOf("/");
      const dir = at < 0 ? "" : f.path.slice(0, at);
      if (!byDir.has(dir)) byDir.set(dir, []);
      byDir.get(dir).push(f);
    }
    const shown = new Set();
    for (const dir of [...byDir.keys()].sort()) {
      const parts = dir ? dir.split("/") : [];
      parts.forEach((part, i) => {
        const key = parts.slice(0, i + 1).join("/");
        if (shown.has(key)) return;
        shown.add(key);
        rows.push(h("div", { class: "tree-row muted wh-p-dir", style: `padding-left:${8 + i * 14}px` }, icon("folder", 14), h("span", { class: "label" }, part)));
      });
      for (const f of byDir.get(dir)) {
        const name = f.path.slice(f.path.lastIndexOf("/") + 1);
        const openTab = sqlPage.tabs?.[sqlPage.active]?.proj;
        const on = openTab && openTab.repo === project.repo && openTab.folder === project.folder && openTab.path === f.path;
        rows.push(h("button", { class: `tree-row wh-p-file${on ? " on" : ""}`, style: `padding-left:${8 + parts.length * 14}px`,
          title: f.problem || `${WH_KIND_LABELS[f.kind] || "File"} ${f.kind === "schema" ? f.name : `${f.schema}.${f.name}`} · ${f.path}`,
          disabled: f.problem && !f.version ? true : null, onclick: () => actions.open(f, project) },
        icon(WH_KIND_ICONS[f.kind] || "document", 14), h("span", { class: "label" }, name),
        f.git ? h("span", { class: `wh-p-git ${f.git === "A" ? "added" : "changed"}`, title: f.git === "A" ? "New, not committed yet" : "Changed, not committed yet" }, f.git) : null,
        f.problem ? h("span", { class: "error-text wh-q-from", title: f.problem }, "not an object") : null));
      }
    }
    if (!data.files.length) rows.push(h("p", { class: "muted-text wh-note" }, "No .sql files yet. Import from the warehouse, or add a New object."));
    return rows;
  }

  function draw() {
    if (ref.kind === "endpoint") {
      box.replaceChildren(h("p", { class: "muted-text wh-note" },
        "A SQL analytics endpoint isn't kept in Git: its lakehouse is. Open a warehouse to work with its project."));
      return;
    }
    if (whProjects.state === "idle" || whProjects.state === "loading") {
      box.replaceChildren(h("div", { class: "tree-row muted" }, "Looking for warehouse projects..."));
      return;
    }
    if (whProjects.state === "failed") {
      box.replaceChildren(h("div", { class: "output error wh-note" }, `Could not look for projects: ${whProjects.error}`),
        btn("Try again", "sync", refresh, "tool outline small"));
      return;
    }
    const project = current();
    const rows = [h("p", { class: "muted-text wh-note" }, "Files in Fabric's Git layout. Edit them here or in VS Code; commit in Source control.")];
    if (!project) {
      rows.push(h("p", { class: "wh-note" }, whProjects.repos.length
        ? `No ${ref.name}.Warehouse folder in the repositories opened in Source control. Import from ${ref.name} makes one.`
        : "Open or clone a Git repository in Source control first: the project's files go there."));
      if (whProjects.repos.length) rows.push(btn(`Import from ${ref.name}`, "arrowDownload", () => whProjectImport(ref, null, refresh), "tool outline small"));
      const { others } = whProjectChoices(ref);
      if (others.length) {
        rows.push(h("label", { class: "field wh-note" }, "Or use another project",
          h("select", { "aria-label": "Project", onchange: (e) => { pick[sqlRefKey(ref)] = e.target.value; const p = current(); if (p) loadFiles(p); } },
            h("option", { value: "" }, "Choose..."), others.map((p) => h("option", { value: whProjectId(p) }, `${p.folder} · ${p.repo_label}`)))));
      }
      box.replaceChildren(...rows);
      return;
    }
    const id = whProjectId(project);
    const { mine, others } = whProjectChoices(ref);
    if (mine.length + others.length > 1) {
      rows.push(h("select", { class: "wh-p-pick", "aria-label": "Project", onchange: (e) => { pick[sqlRefKey(ref)] = e.target.value; loadFiles(current()); } },
        [...mine, ...others].map((p) => h("option", { value: whProjectId(p), selected: whProjectId(p) === id || null }, `${p.folder} · ${p.repo_label}`))));
    }
    const f = files[id];
    if (!f) { loadFiles(project); return; }
    rows.push(h("div", { class: "wh-p-where" }, h("b", {}, project.folder), ` · ${project.repo_label}`,
      f.data?.branch ? h("span", { class: "muted-text" }, ` · ${f.data.branch}`) : null));
    if (f.state === "loading") rows.push(h("div", { class: "tree-row muted" }, "Reading the project's files..."));
    else if (f.state === "failed") rows.push(h("div", { class: "output error wh-note" }, `Could not read the project: ${f.error}`));
    else {
      if (!f.data.sqlproj) rows.push(h("p", { class: "muted-text wh-note" }, "This folder has no .sqlproj, so Fabric may not read it as a warehouse project."));
      rows.push(...tree(project, f.data));
      if (f.data.note) rows.push(h("p", { class: "muted-text wh-note" }, f.data.note));
    }
    rows.push(h("div", { class: "wh-p-actions" },
      btn(`Compare with ${ref.name}`, "branchCompare", () => whProjectCompare(ref, project), "tool outline small",
        { title: "Compare the project's files with the warehouse, object by object. Runs catalog queries in Fabric (uses capacity); nothing changes." }),
      btn("New object", "add", () => whProjectNewObject(ref, project, actions, refresh), "tool outline small"),
      btn(`Import from ${ref.name}`, "arrowDownload", () => whProjectImport(ref, project, refresh), "tool outline small",
        { title: "Script the warehouse's objects into the project. Runs catalog queries in Fabric (uses capacity)." }),
      btn("Refresh", "sync", refresh, "tool small")));
    box.replaceChildren(...rows);
  }
  if (whProjects.state === "idle" && ref.kind !== "endpoint") whLoadProjects().then(() => sqlPage.whRedraw?.());
  draw();
  return { el: box, draw, refresh };
}

// Every object in the warehouse as {kind, schema, name, text}: tables from the explorer's catalog,
// views, procedures and functions from sys.sql_modules, and the schemas, without Fabric's own
// (WH_FABRIC_SCHEMAS). Catalog queries in Fabric.
async function whScriptWarehouse(ref) {
  if (sqlPage.catalog?.key !== sqlRefKey(ref) || sqlPage.catalog.state !== "done") {
    await whLoadCatalog(ref, () => sqlPage.whRedraw?.());
    if (sqlPage.catalog?.state !== "done") throw new Error(sqlPage.catalog?.error || "The objects couldn't be listed.");
  }
  const cat = sqlPage.catalog;
  const modules = await whCatalogQuery(ref, WH_MODULES_SQL);
  const schemas = await whCatalogQuery(ref, WH_SCHEMAS_SQL);
  const out = schemas.rows.map(([s]) => ({ kind: "schema", schema: s, name: s, text: `CREATE SCHEMA ${whQuote(s)};\nGO\n` }));
  for (const o of cat.objects.filter((x) => x.type === "U")) {
    out.push({ kind: "table", schema: o.schema, name: o.name, text: `${whCreateTable(o, cat.cols[`${o.schema}.${o.name}`] || [])}GO\n` });
  }
  for (const [schema, name, type, def] of modules.rows) {
    if (typeof def === "string" && def.trim()) out.push({ kind: WH_KIND_OF[type] || "function", schema, name, text: `${def.trim()}\nGO\n` });
  }
  return out.filter((o) => !whFabricSchema(o.schema));
}

// Import from the warehouse: scripts it, shows what would change in the project, then writes.
// project is null when there's none yet: the person picks the repository and <name>.Warehouse is made.
async function whProjectImport(ref, project, done) {
  if (!(await warehouseRunOk(ref))) return;
  toast(`Scripting ${ref.name} from its catalog...`);
  let objects;
  try { objects = await whScriptWarehouse(ref); } catch (err) {
    whFailed(`Could not script ${ref.name}`, err);
    return;
  }
  const repoPick = project ? null : h("select", { "aria-label": "Repository" }, whProjects.repos.map((r) => h("option", { value: r.repo }, r.label)));
  const target = () => (project ? { repo: project.repo, folder: project.folder } : { repo: repoPick.value, create: ref.name });
  let plan;
  try { plan = await rpc("wh_project_write", { ...target(), objects, dry_run: true }); } catch (err) {
    whFailed("Could not read the project", err);
    return;
  }
  const replace = h("input", { type: "checkbox" });
  const summary = h("div", {});
  const n = (k) => plan.counts[k];
  const drawSummary = () => summary.replaceChildren(
    h("ul", { class: "wh-p-plan" },
      h("li", {}, `${n("added")} new file${n("added") === 1 ? "" : "s"}`),
      h("li", {}, `${n("changed")} file${n("changed") === 1 ? "" : "s"} that differ from the warehouse`),
      h("li", { class: "muted-text" }, `${n("same")} already the same`),
      n("skipped") ? h("li", { class: "error-text" }, `${n("skipped")} skipped: ${plan.skipped.slice(0, 3).map((s) => `${s.name} (${s.why})`).join("; ")}`) : null),
    n("changed") ? h("label", { class: "check" }, replace, ` Replace the ${n("changed")} that differ with the warehouse's version`) : null,
    n("changed") ? h("p", { class: "muted-text small" }, `Differ: ${plan.changed.slice(0, 6).map((c) => c.name).join(", ")}${n("changed") > 6 ? ", ..." : ""}`) : null);
  drawSummary();
  if (repoPick) {
    repoPick.onchange = async () => {
      try { plan = await rpc("wh_project_write", { ...target(), objects, dry_run: true }); drawSummary(); } catch { /* the write says why */ }
    };
  }
  const ok = await new Promise((resolve) => {
    let fin = false;
    const finish = (v) => { if (!fin) { fin = true; resolve(v); } };
    showDialog(`Import ${ref.name} into ${project ? project.folder : "a new project"}`, h("div", { class: "wh-p-import" },
      h("p", {}, `${objects.length} object${objects.length === 1 ? "" : "s"} scripted from ${ref.name}'s catalog.`),
      repoPick ? h("label", { class: "field" }, `Make ${ref.name}.Warehouse in`, repoPick) : null,
      summary,
      h("p", { class: "muted-text small" }, "Files change on this PC only. Nothing is committed and no file is removed: review the change in Source control. " +
        "A table is scripted with its columns, types and NULL or NOT NULL.")),
    [btn("Cancel", null, () => { finish(false); closeDialog(); }, "tool outline"),
      btn("Import", null, () => { finish(true); closeDialog(); }, "tool primary")], () => finish(false));
  });
  if (!ok) return;
  try {
    const out = await rpc("wh_project_write", { ...target(), objects, replace: replace.checked });
    const c = out.counts;
    toast(`Imported ${ref.name}: ${c.added} new, ${replace.checked ? c.changed : 0} replaced, ${c.same} the same${c.skipped ? `, ${c.skipped} skipped` : ""}.`);
    if (out.created) sqlPage.whProjectPick[sqlRefKey(ref)] = `${out.repo}|${out.folder}`;
  } catch (err) {
    whFailed("Could not import", err);
  }
  await done();
}

// New object: a kind, a schema and a name; its file starts from a short script and opens in a tab.
async function whProjectNewObject(ref, project, actions, done) {
  const kind = h("select", { "aria-label": "Kind" }, Object.entries(WH_KIND_LABELS).map(([k, l]) => h("option", { value: k }, l)));
  const schema = h("input", { value: "dbo", "aria-label": "Schema", spellcheck: "false" });
  const name = h("input", { "aria-label": "Name", spellcheck: "false", placeholder: "DimProduct" });
  const why = h("p", { class: "error-text", role: "alert" });
  kind.onchange = () => { schema.disabled = kind.value === "schema"; };
  const create = btn("Create file", null, async () => {
    const k = kind.value;
    const s = k === "schema" ? name.value.trim() : schema.value.trim();
    const nm = name.value.trim();
    if (!nm || !s) { why.textContent = "Give it a schema and a name."; return; }
    create.disabled = true;
    try {
      const out = await rpc("wh_project_write", { repo: project.repo, folder: project.folder,
        objects: [{ kind: k, schema: s, name: nm, text: whNewObjectText(k, s, nm) }] });
      if (out.skipped.length) throw new Error(out.skipped[0].why);
      const row = out.added[0] || out.changed[0] || out.same[0];
      if (!out.added.length) throw new Error(`${row.name} is already in the project, in ${row.path}.`);
      closeDialog();
      await done();
      const file = sqlPage.whProjectFiles?.[whProjectId(project)]?.data?.files.find((f) => f.path === row.path);
      if (file) actions.open(file, project);
    } catch (err) { why.textContent = friendlyError(err).text; create.disabled = false; }
  }, "tool primary");
  showDialog("New object", h("div", {},
    h("label", { class: "field" }, "Kind", kind),
    h("label", { class: "field" }, "Schema", schema),
    h("label", { class: "field" }, "Name", name),
    h("p", { class: "muted-text" }, `A .sql file in ${project.folder}, where Fabric's Git layout puts it. Nothing runs and nothing is committed.`),
    why), [btn("Cancel", null, closeDialog, "tool outline"), create]);
  name.focus();
}

// Save on a tab opened from a project file: writes it back, refusing a file changed since it was opened.
async function whProjectSave(tab) {
  const f = tab.proj;
  try {
    const out = await rpc("wh_project_save", { repo: f.repo, folder: f.folder, path: f.path, text: tab.sql, expect: f.version || "" });
    tab.proj = { ...f, version: out.version };
    toast(`Saved ${f.path} in ${f.folder}.`);
    return true;
  } catch (err) {
    whFailed("Could not save the file", err);
    return false;
  }
}

// ---- Compare (WH-12): the project's files beside the warehouse, object by object.

const WH_STATES = [["all", "All"], ["changed", "Changed"], ["only_project", "Only in project"], ["only_warehouse", "Only in warehouse"], ["same", "Same"]];
const WH_STATE_LABEL = { changed: "Changed", only_project: "Only in project", only_warehouse: "Only in warehouse", same: "Same" };

// Scripts the warehouse and compares it with the project, then shows the compare view.
async function whProjectCompare(ref, project) {
  if (!(await warehouseRunOk(ref))) return;
  toast(`Reading ${ref.name}'s catalog to compare...`);
  try {
    const objects = await whScriptWarehouse(ref);
    const result = await rpc("wh_project_compare", { repo: project.repo, folder: project.folder, objects });
    const first = result.objects.findIndex((o) => o.state !== "same");
    sqlPage.whCompare = { key: sqlRefKey(ref), project, result, at: new Date(), filter: "all", picked: Math.max(0, first), objects };
    sqlPage.whCompareShown = true;
    sqlPage.whMonitorShown = false;
    renderQuery();
  } catch (err) {
    whFailed("Could not compare", err);
  }
}

const whObjLabel = (o) => (o.kind === "schema" ? o.name : `${o.schema}.${o.name}`);

// The compare view, in place of the editor. actions.open(file, project) opens a project file.
function whCompareView(ref, actions) {
  const c = sqlPage.whCompare;
  const r = c.result;
  const env = whEnv(ref);
  const shown = r.objects.map((o, i) => [o, i]).filter(([o]) => c.filter === "all" || o.state === c.filter);
  const count = (s) => (s === "all" ? r.objects.length : r.counts[s]);
  const pane = h("div", { class: "wh-cmp-pane" });
  const branch = sqlPage.whProjectFiles?.[whProjectId(c.project)]?.data?.branch;

  async function drawPane() {
    const o = r.objects[c.picked];
    if (!o) { pane.replaceChildren(h("p", { class: "muted-text wh-note" }, "Nothing to show.")); return; }
    const head = h("div", { class: "wh-cmp-cols" }, h("div", {}, `In ${ref.name} now`),
      h("div", {}, `In the project${branch ? ` (${branch})` : ""}`));
    let rows;
    if (o.state === "same") {
      pane.replaceChildren(head, h("p", { class: "muted-text wh-note" }, `${whObjLabel(o)} reads the same on both sides (formatting, comments and the case of keywords aside).`), paneActions(o));
      return;
    }
    if (o.state === "changed") {
      o.rows ||= (await attempt(() => rpc("wh_project_diff", { warehouse_text: o.warehouse_text, project_text: o.project_text }), "Could not show the difference"))?.rows || [];
      rows = o.rows;
    } else {
      const text = (o.state === "only_project" ? o.project_text : o.warehouse_text).replace(/\n+$/, "").split("\n");
      rows = text.map((line) => (o.state === "only_project" ? { op: "added", left: null, right: line } : { op: "removed", left: line, right: null }));
    }
    if (sqlPage.whCompare !== c || r.objects[c.picked] !== o) return;
    const cell = (text, op) => h("div", { class: `wh-cmp-line ${text === null ? "none" : op}` }, text ?? "");
    pane.replaceChildren(...[head,
      o.detail?.length ? h("ul", { class: "wh-cmp-detail" }, o.detail.map((d) => h("li", {}, d))) : null,
      h("div", { class: "wh-cmp-diff", role: "table", "aria-label": `Difference for ${whObjLabel(o)}` },
        rows.map((x) => h("div", { class: "wh-cmp-row", role: "row" }, cell(x.left, x.op), cell(x.right, x.op)))),
      paneActions(o)].filter(Boolean));
  }

  function paneActions(o) {
    const p = c.project;
    return h("div", { class: "wh-cmp-actions" },
      o.path ? btn("Open file", null, () => actions.open({ path: o.path }, p), "tool small") : null,
      o.state === "changed" || o.state === "only_warehouse" ? btn("Take the warehouse's version", null, () => takeWarehouse(o), "tool small",
        { title: o.state === "changed" ? `Replace ${o.path} with ${ref.name}'s version` : `Write ${whObjLabel(o)} into the project` }) : null,
      o.state === "only_project" ? h("span", { class: "muted-text" }, `Deploy to ${ref.name} creates it in the warehouse.`) : null);
  }

  async function takeWarehouse(o) {
    if (o.state === "changed" && !(await confirmDialog(`Replace ${o.path}?`,
      `It gets ${whObjLabel(o)} as it is in ${ref.name}. Your version stays in Git history only if it was committed.`, "Replace", { danger: true }))) return;
    const out = await attempt(() => rpc("wh_project_write", { repo: c.project.repo, folder: c.project.folder, replace: o.state === "changed",
      objects: [{ kind: o.kind, schema: o.schema, name: o.name, text: o.warehouse_text }] }), "Could not write the file");
    if (!out) return;
    const row = out.added[0] || out.changed[0] || out.same[0];
    if (!row) { toast(`Could not write ${whObjLabel(o)}: ${out.skipped[0]?.why || "it was skipped"}`, true); return; }
    if (row.replaced === "no") {
      toast(`${row.path} already holds another script, so ${whObjLabel(o)} wasn't written. Open the file to see what's there.`, true);
      return;
    }
    r.counts[o.state] -= 1;
    r.counts.same += 1;
    Object.assign(o, { state: "same", path: row.path, project_text: o.warehouse_text, rows: null, detail: [] });
    toast(`Wrote ${row.path} from ${ref.name}. Commit it in Source control.`);
    await sqlPage.whProjectView?.refresh();
    renderQuery();
  }

  const list = h("div", { class: "wh-cmp-list", role: "group", "aria-label": "Objects" },
    h("div", { class: "wh-cmp-list-head" }, h("span", {}, "Object"), h("span", {}, "State")),
    shown.length ? shown.map(([o, i]) => h("button", { class: `wh-cmp-item${i === c.picked ? " on" : ""}`, "aria-pressed": String(i === c.picked),
      onclick: () => { c.picked = i; renderQuery(); } },
    h("span", { class: "label", title: WH_KIND_LABELS[o.kind] }, icon(WH_KIND_ICONS[o.kind] || "document", 14), whObjLabel(o)),
    h("span", { class: `wh-cmp-state ${o.state}` }, o.state === "changed" && o.detail?.length && o.kind === "table" ? "Changed: columns" : WH_STATE_LABEL[o.state])))
      : h("p", { class: "muted-text wh-note" }, "Nothing in this group."));

  drawPane();
  return h("div", { class: "wh-cmp" },
    h("div", { class: "wh-cmp-head" },
      h("h2", {}, `Compare the project with ${ref.name}${env !== "none" ? ` (${ENV_NAMES[env] || env})` : ""}`),
      h("span", { class: "muted-text" }, `${c.project.folder} · read from the warehouse catalog at ${c.at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · nothing changes`),
      h("span", { class: "spacer" }),
      btn("Compare again", "sync", () => whProjectCompare(ref, c.project), "tool outline small"),
      ref.kind === "warehouse" ? btn(`Deploy to ${ref.name}…`, null, () => whProjectDeploy(ref, c.project), "tool primary small",
        { title: "Preview the changes that make the warehouse match the project, then run the ones you pick" }) : null),
    h("div", { class: "wh-cmp-chips", role: "group", "aria-label": "Show" }, WH_STATES.map(([s, label]) =>
      h("button", { class: `wh-cmp-chip ${s}${c.filter === s ? " on" : ""}`, "aria-pressed": String(c.filter === s),
        onclick: () => { c.filter = s; renderQuery(); } }, `${label} ${count(s)}`))),
    r.unread.length ? h("p", { class: "muted-text wh-note" }, `Not compared: ${r.unread.slice(0, 3).map((u) => `${u.path} (${u.why})`).join("; ")}${r.unread.length > 3 ? `, and ${r.unread.length - 3} more` : ""}.`) : null,
    h("div", { class: "wh-cmp-body" }, list, pane));
}

// ---- Deploy (WH-13): make the warehouse match the project, change by change, after a preview.

const WH_ACTION_WORD = { create: "CREATE", alter: "CREATE OR ALTER", add_columns: "ALTER TABLE", drop: "DROP" };
const WH_KIND_WORD = { schema: "SCHEMA", table: "TABLE", view: "VIEW", procedure: "PROCEDURE", function: "FUNCTION" };

// One confirmed warehouse change, start to finish: the final status.
async function whRunChange(ref, sql) {
  const whRef = { workspace_id: ref.workspace_id, item_id: ref.item_id, kind: ref.kind };
  let status = await rpc("sql_start", { query: sql, target: "warehouse", dialect: "tsql", warehouse_ref: whRef,
    allow: ["warehouse_write"], confirm: true, confirm_name: ref.name });
  let wait = 150;
  while (["queued", "running", "cancelling"].includes(status.state)) {
    await new Promise((r) => setTimeout(r, wait));
    wait = Math.min(1000, wait * 1.5);
    status = await rpc("sql_status", { execution: status.id });
  }
  return status;
}

// Test and Prod deploy only what Git has: the engine holds back (held) each change whose file isn't
// committed, and every change while the branch isn't pushed. This says what to do about it.
function whDeployGate(env, plan) {
  if (!plan.gated || !plan.changes.some((c) => c.held)) return "";
  const where = ENV_NAMES[env] || "This environment";
  const dirty = plan.changes.filter((c) => c.held && !c.committed).map((c) => c.path);
  if (dirty.length) return `${where} deploys only what's committed. Commit ${dirty.length === 1 ? dirty[0] : `${dirty.length} files`} in Source control first.`;
  if (!plan.upstream) return `${where} deploys only what's pushed, and ${plan.branch || "this branch"} has no remote branch yet. Push it in Source control first.`;
  return `${where} deploys only what's pushed. Push ${plan.ahead} commit${plan.ahead === 1 ? "" : "s"} in Source control first.`;
}

async function whProjectDeploy(ref, project) {
  if (!whChangesAllowed()) { toast("Warehouse changes are turned off in Settings > Advanced > SQL queries.", true); return; }
  if (!(await warehouseRunOk(ref))) return;
  toast(`Reading ${ref.name}'s catalog to plan the deploy...`);
  let plan;
  try {
    const objects = await whScriptWarehouse(ref);
    await whEnvRead();
    plan = await rpc("wh_project_deploy_plan", { repo: project.repo, folder: project.folder, objects, environment: whEnv(ref),
      workspace_id: ref.workspace_id });
    // The engine's check on each change, before anything is sent: one it would refuse isn't offered.
    const whRef = { workspace_id: ref.workspace_id, item_id: ref.item_id, kind: ref.kind };
    for (const c of plan.changes.filter((x) => x.offered)) {
      const dry = await rpc("sql_start", { query: c.sql, target: "warehouse", dialect: "tsql", warehouse_ref: whRef, allow: ["warehouse_write"], dry_run: true });
      if (dry.refused) Object.assign(c, { offered: false, ticked: false, why: `the engine refuses it: ${dry.refused.reason}` });
    }
  } catch (err) {
    whFailed("Could not plan the deploy", err);
    return;
  }
  if (!plan.changes.length) { toast(`${ref.name} already matches ${project.folder}: nothing to deploy.`); return; }
  // The engine's environment for this deploy (the stricter of the page's and the project's), so the label
  // and the typed-name rule match the gate it applied.
  const env = plan.environment || whEnv(ref);
  const named = ["prod", "none"].includes(env);
  const where = env === "none" ? "no environment, so it's treated like Prod" : ENV_NAMES[env] || env;
  let picked = plan.changes.findIndex((c) => c.offered);
  if (picked < 0) picked = 0;
  const preview = h("div", { class: "wh-dep-preview" });
  const gateNote = h("p", { class: "error-text wh-dep-gate", role: "alert" });
  const devNote = h("p", { class: "muted-text small" });
  const typed = h("input", { "aria-label": "Warehouse name", spellcheck: "false", autocomplete: "off", oninput: () => update() });
  const go = btn("Deploy", null, () => { finish(true); closeDialog(); }, "tool danger-solid");
  const cancel = btn("Cancel", null, () => { finish(false); closeDialog(); }, "tool outline");
  const boxes = [];
  const label = (c) => `${WH_ACTION_WORD[c.action]}${c.action === "add_columns" ? "" : ` ${WH_KIND_WORD[c.kind]}`} ${whObjLabel(c)}`;
  const list = h("div", { class: "wh-dep-list" }, h("div", { class: "wh-dep-head" }, `${plan.changes.length} change${plan.changes.length === 1 ? "" : "s"}, in this order`),
    plan.changes.map((c, i) => {
      const box = h("input", { type: "checkbox", checked: c.ticked || null, disabled: c.offered ? null : true,
        "aria-label": label(c), onchange: () => { c.ticked = box.checked; update(); } });
      boxes.push(box);
      return h("div", { class: `wh-dep-item${i === picked ? " on" : ""}${c.offered ? "" : " off"}` }, box,
        h("button", { class: "wh-dep-what", "aria-pressed": String(i === picked), title: "Show what runs",
          onclick: () => {
            picked = i;
            drawPreview();
            list.querySelectorAll(".wh-dep-item").forEach((el, j) => {
              el.classList.toggle("on", j === i);
              el.querySelector(".wh-dep-what").setAttribute("aria-pressed", String(j === i));
            });
          } },
          h("span", { class: `wh-dep-verb${c.action === "drop" ? " drop" : ""}` }, `${i + 1}. ${label(c)}`),
          h("span", { class: "muted-text" }, c.why), c.git ? h("span", { class: "wh-dep-git" }, "not committed") : null));
    }));
  function drawPreview() {
    const c = plan.changes[picked];
    preview.replaceChildren(...[h("div", { class: "wh-dep-preview-head" }, c.offered ? "What runs" : "Not offered"),
      c.detail?.length ? h("ul", { class: "wh-cmp-detail" }, c.detail.map((d) => h("li", {}, d))) : null,
      c.offered ? h("pre", { class: "wh-dep-sql" }, c.sql) : h("p", { class: "wh-note" }, c.why)].filter(Boolean));
  }
  function update() {
    const ticked = plan.changes.filter((c) => c.offered && c.ticked);
    const gate = whDeployGate(env, plan);
    gateNote.textContent = gate;
    gateNote.hidden = !gate;
    const dirty = ticked.filter((c) => c.git).length;
    devNote.textContent = !gate && dirty ? `${dirty} of these file${dirty === 1 ? " isn't" : "s aren't"} committed: ${ENV_NAMES[env] || env} deploys them as they are on this PC. Test and Prod deploy only what's committed and pushed.` : "";
    devNote.hidden = !devNote.textContent;
    go.textContent = `Deploy ${ticked.length} change${ticked.length === 1 ? "" : "s"}${env === "prod" ? " to Prod" : ""}`;
    go.disabled = !ticked.length || !!gate || (named && typed.value !== ref.name);
  }
  let resolve;
  let fin = false;
  const finish = (v) => { if (!fin) { fin = true; resolve(v); } };
  const ok = new Promise((r) => { resolve = r; });
  drawPreview();
  update();
  const commit = plan.commit ? plan.commit.slice(0, 7) : "no commit yet";
  showDialog(`Deploy ${project.folder}`, h("div", { class: "wh-dep" },
    h("p", {}, `From ${project.repo_label}, branch ${plan.branch || "(none)"} at ${commit}, to ${ref.name} in ${ref.workspace_name || ref.workspace_id} (${where}). It runs in Fabric as you and uses capacity.`),
    h("div", { class: "wh-dep-body" }, list, preview),
    plan.changes.some((c) => c.action === "add_columns" && !c.offered) ? h("p", { class: "wh-dep-warn" }, plan.changes.find((c) => c.action === "add_columns" && !c.offered).why) : null,
    gateNote,
    devNote,
    h("p", { class: "muted-text small" }, "Each change runs on its own, in this order, and the deploy stops at the first that fails, saying which went in. " +
      "Each change is recorded in the audit log like any warehouse change: who, which warehouse and how many rows, never the SQL."),
    named ? h("label", { class: "field" }, h("span", {}, "Type ", h("code", {}, ref.name), ` to deploy to ${env === "prod" ? "Prod" : "it"}`), typed) : null),
  [cancel, go], () => finish(false));
  (named ? typed : cancel).focus();
  if (!(await ok)) return;

  const run = plan.changes.filter((c) => c.offered && c.ticked);
  const results = [];
  for (const c of run) {
    toast(`Deploying ${results.length + 1} of ${run.length}: ${label(c)}...`);
    let status;
    try { status = await whRunChange(ref, c.sql); } catch (err) { status = { state: "failed", error: whWhy(err) }; }
    results.push({ c, status });
    if (status.state !== "completed") break;
  }
  const done = results.filter((r) => r.status.state === "completed").length;
  const failed = results.find((r) => r.status.state !== "completed");
  showDialog(failed ? `Deployed ${done} of ${run.length} changes` : `Deployed ${done} change${done === 1 ? "" : "s"} to ${ref.name}`, h("div", { class: "wh-dep" },
    h("ul", { class: "wh-dep-results" }, run.map((c) => {
      const r = results.find((x) => x.c === c);
      return h("li", { class: !r ? "muted-text" : r.status.state === "completed" ? "ok" : "error-text" },
        `${!r ? "Not run" : r.status.state === "completed" ? "Done" : "Failed"}: ${label(c)}${r && r.status.state !== "completed" ? ` (${r.status.error || r.status.state})` : ""}`);
    })),
    failed ? h("p", { class: "muted-text small" }, "The change that failed kept nothing, and the ones after it didn't run. Fix it in the project and deploy again.") : null),
  [btn("Close", null, closeDialog, "tool primary")]);
  await whLoadCatalog(ref, () => sqlPage.whRedraw?.());
  if (sqlPage.whCompare?.key === sqlRefKey(ref)) await whProjectCompare(ref, project);
}
