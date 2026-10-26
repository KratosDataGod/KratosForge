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
    Object.assign(whProjects, { state: "failed", error: String(err).replace(/^Error: /, "") });
  }
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
      files[id] = { state: "failed", error: String(err).replace(/^Error: /, "") };
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
// views, procedures and functions from sys.sql_modules, and the schemas. Catalog queries in Fabric.
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
  return out;
}

// Import from the warehouse: scripts it, shows what would change in the project, then writes.
// project is null when there's none yet: the person picks the repository and <name>.Warehouse is made.
async function whProjectImport(ref, project, done) {
  if (!(await warehouseRunOk(ref))) return;
  toast(`Scripting ${ref.name} from its catalog...`);
  let objects;
  try { objects = await whScriptWarehouse(ref); } catch (err) {
    toast(`Could not script ${ref.name}: ${String(err).replace(/^Error: /, "")}`, true);
    return;
  }
  const repoPick = project ? null : h("select", { "aria-label": "Repository" }, whProjects.repos.map((r) => h("option", { value: r.repo }, r.label)));
  const target = () => (project ? { repo: project.repo, folder: project.folder } : { repo: repoPick.value, create: ref.name });
  let plan;
  try { plan = await rpc("wh_project_write", { ...target(), objects, dry_run: true }); } catch (err) {
    toast(`Could not read the project: ${String(err).replace(/^Error: /, "")}`, true);
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
    toast(`Could not import: ${String(err).replace(/^Error: /, "")}`, true);
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
    } catch (err) { why.textContent = String(err).replace(/^Error: /, ""); create.disabled = false; }
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
    toast(`Couldn't save the file: ${String(err).replace(/^Error: /, "")}`, true);
    return false;
  }
}
