// The Warehouse page's object explorer (WH-02): schemas, tables, views, stored procedures and
// functions, read from the warehouse's own catalog views (sys.objects, sys.columns) with the same
// read-only managed run as a query (sql_start target "warehouse"). It loads before app.js and uses
// app.js and query.js helpers only when its functions are called.

const WH_OBJECT_GROUPS = [["U", "Tables"], ["V", "Views"], ["P", "Stored procedures"], ["FN", "Functions"]];
const WH_ICONS = { U: "table", V: "windowDatabase", P: "flash", FN: "braces" };
const WH_GROUP_OF = { U: "U", V: "V", P: "P", FN: "FN", IF: "FN", TF: "FN" };

const WH_OBJECTS_SQL = `SELECT s.name AS schema_name, o.name AS object_name, RTRIM(o.type) AS object_type
FROM sys.objects AS o
JOIN sys.schemas AS s ON s.schema_id = o.schema_id
WHERE o.type IN ('U', 'V', 'P', 'FN', 'IF', 'TF') AND o.is_ms_shipped = 0
ORDER BY s.name, o.name;`;

const WH_COLUMNS_SQL = `SELECT s.name AS schema_name, o.name AS object_name, c.name AS column_name, t.name AS type_name,
  c.max_length, c.precision, c.scale, c.is_nullable
FROM sys.columns AS c
JOIN sys.objects AS o ON o.object_id = c.object_id
JOIN sys.schemas AS s ON s.schema_id = o.schema_id
JOIN sys.types AS t ON t.user_type_id = c.user_type_id
WHERE o.type IN ('U', 'V') AND o.is_ms_shipped = 0
ORDER BY s.name, o.name, c.column_id;`;

// A column's type as T-SQL writes it: varchar(50), nvarchar(max), decimal(18,2), datetime2(6).
function whTypeLabel(type, maxLength, precision, scale) {
  const t = String(type || "").toLowerCase();
  if (["varchar", "char", "varbinary", "binary"].includes(t)) return `${t}(${maxLength === -1 ? "max" : maxLength})`;
  if (["nvarchar", "nchar"].includes(t)) return `${t}(${maxLength === -1 ? "max" : maxLength / 2})`;
  if (["decimal", "numeric"].includes(t)) return `${t}(${precision},${scale})`;
  if (["datetime2", "time", "datetimeoffset"].includes(t)) return `${t}(${scale})`;
  return t;
}

// A [schema].[name] reference, escaping ] as T-SQL does.
function whQuote(s) {
  return `[${String(s).replace(/]/g, "]]")}]`;
}

function whName(schema, name) {
  return `${whQuote(schema)}.${whQuote(name)}`;
}

// ---- Script as (WH-03): T-SQL for an object, opened in a new tab and never run.

const WH_DROP_KIND = { U: "TABLE", V: "VIEW", P: "PROCEDURE", FN: "FUNCTION" };

// The first lines of every script: where it came from, and that opening it ran nothing.
function whScriptHeader(ref, verb) {
  return `-- ${verb} script from ${ref.name}, ${new Date().toISOString().slice(0, 16).replace("T", " ")} UTC.\n` +
    "-- Opened, not run.\n";
}

// CREATE TABLE from the explorer's columns (names, T-SQL types, NULL or NOT NULL).
function whCreateTable(o, cols) {
  const lines = cols.map((c) => `    ${whQuote(c.name)} ${c.type}${c.nullable === false ? " NOT NULL" : " NULL"}`);
  return `CREATE TABLE ${whName(o.schema, o.name)}\n(\n${lines.join(",\n")}\n);\n`;
}

// A view, procedure or function's definition as ALTER: its leading CREATE (or CREATE OR ALTER) becomes ALTER.
function whAsAlter(definition) {
  return definition.replace(/^(\s*(?:--[^\n]*\n\s*|\/\*[\s\S]*?\*\/\s*)*)CREATE\s+(?:OR\s+ALTER\s+)?(VIEW|PROC|PROCEDURE|FUNCTION)\b/i,
    (_, lead, kind) => `${lead}ALTER ${kind.toUpperCase()}`);
}

// The definition of a view, procedure or function, from sys.sql_modules (a catalog query in Fabric).
async function whDefinition(ref, o) {
  const id = whName(o.schema, o.name).replace(/'/g, "''");
  const { rows } = await whCatalogQuery(ref, `SELECT m.definition\nFROM sys.sql_modules AS m\nWHERE m.object_id = OBJECT_ID(N'${id}');`);
  const def = rows[0]?.[0];
  if (typeof def !== "string" || !def.trim()) throw new Error(`Fabric returned no definition for ${o.name}.`);
  return def.trim();
}

// The Script as menu items for an object. open(title, text) opens a script tab; query(name) runs SELECT TOP (100).
// Fabric's own schemas (queryinsights, _rsc, sys) can't be changed, so their objects have no ALTER or DROP.
function whScriptItems(ref, o, cols, { open, query }) {
  const full = whName(o.schema, o.name);
  const drop = () => open(`${o.name} (DROP)`, `${whScriptHeader(ref, "DROP")}DROP ${WH_DROP_KIND[o.type]} ${full};\n`);
  const module = (verb) => async () => {
    toast(`Reading the definition of ${o.name}...`);
    try {
      const def = await whDefinition(ref, o);
      open(`${o.name} (${verb})`, `${whScriptHeader(ref, verb)}${verb === "ALTER" ? whAsAlter(def) : def}\n`);
    } catch (err) {
      toast(`Could not script ${o.name}: ${String(err).replace(/^Error: /, "")}`, true);
    }
  };
  const items = [];
  if (o.type === "U" || o.type === "V") items.push({ label: "Select top 100 rows", icon: "play", action: () => query(full) });
  const fabric = whFabricSchema(o.schema);
  if (o.type === "U") {
    items.push({ label: "Script as CREATE", icon: "code", action: () => open(`${o.name} (CREATE)`, `${whScriptHeader(ref, "CREATE")}${whCreateTable(o, cols || [])}`) });
  } else {
    items.push({ label: "Script as CREATE", icon: "code", action: module("CREATE") });
    if (!fabric) items.push({ label: "Script as ALTER", icon: "code", action: module("ALTER") });
  }
  if (!fabric) items.push({ label: "Script as DROP", icon: "code", action: drop });
  items.push({ info: fabric ? `${o.schema} is Fabric's own schema, so it can't be changed` : "Scripts open in a new tab and don't run" });
  return items;
}

// One catalog query, start to last page: { columns, rows }. Throws with the run's own error.
async function whCatalogQuery(ref, query) {
  let status = await rpc("sql_start", { query, target: "warehouse", dialect: "tsql",
    warehouse_ref: { workspace_id: ref.workspace_id, item_id: ref.item_id, kind: ref.kind } });
  let wait = 150;
  while (["queued", "running", "cancelling"].includes(status.state)) {
    await new Promise((r) => setTimeout(r, wait));
    wait = Math.min(1000, wait * 1.5);
    status = await rpc("sql_status", { execution: status.id });
  }
  if (status.state !== "completed") throw new Error(status.error || `The catalog query ${status.state}.`);
  const rows = [];
  let columns = [];
  for (let offset = 0; ;) {
    const page = await rpc("sql_page", { execution: status.id, offset });
    if (page.rerun_needed) throw new Error(page.message);
    columns = page.columns;
    rows.push(...page.rows);
    if (!page.has_next) break;
    offset += page.rows.length;
  }
  return { columns, rows };
}

// The catalog of the warehouse on sqlPage.catalog: { key, state: idle|loading|done|failed, objects:
// [{schema, name, type}], cols: {"schema.name": [{name, type}]}, at, error }. Two queries run in
// Fabric, one after the other; onChange is called as the state moves.
async function whLoadCatalog(ref, onChange) {
  const key = sqlRefKey(ref);
  const cat = sqlPage.catalog = { key, state: "loading", objects: [], cols: {}, at: null, error: null };
  onChange();
  try {
    const objs = await whCatalogQuery(ref, WH_OBJECTS_SQL);
    const cols = await whCatalogQuery(ref, WH_COLUMNS_SQL);
    if (sqlPage.catalog !== cat) return;
    cat.objects = objs.rows.map(([schema, name, type]) => ({ schema, name, type: WH_GROUP_OF[type] || type }));
    for (const [schema, name, col, type, len, prec, scale, nullable] of cols.rows) {
      (cat.cols[`${schema}.${name}`] ||= []).push({ name: col, type: whTypeLabel(type, len, prec, scale), nullable: !!nullable });
    }
    Object.assign(cat, { state: "done", at: new Date() });
  } catch (err) {
    if (sqlPage.catalog !== cat) return;
    Object.assign(cat, { state: "failed", error: String(err).replace(/^Error: /, "") });
  }
  onChange();
}

// The completion provider for a warehouse, from the explorer's catalog: tables and views, and their columns.
function warehouseSqlProvider(ref) {
  const cat = () => (sqlPage.catalog?.key === sqlRefKey(ref) ? sqlPage.catalog : null);
  return {
    id: `${ref.kind}:${ref.item_id}`,
    label: ref.name,
    tables: () => (cat()?.objects || []).filter((o) => o.type === "U" || o.type === "V").map((o) => ({ schema: o.schema, name: o.name })),
    cachedColumns: (t) => cat()?.cols[`${t.schema}.${t.name}`],
    columns: (t) => Promise.resolve(cat()?.cols[`${t.schema}.${t.name}`] || []),
  };
}

// The explorer box. actions: load() lists the objects (asking first, since it runs in Fabric),
// query(name) opens SELECT TOP (100) on a table or view, insert(text) types a name in the editor,
// open(title, text) opens a script in a new tab without running it. Each object's ⋯ button (or a
// right-click, or Shift+F10) has Script as. The objects are a tree: one row takes Tab, the arrow keys
// move and open, Home and End jump, and Enter does what a click does.
function whExplorer(ref, actions) {
  const box = h("div", { class: "explorer-content" });
  const tree = h("div", { role: "tree", "aria-label": `Objects in ${ref.name}`, class: "wh-tree", onkeydown: treeKeys,
    onfocusin: (e) => { const it = treeItemOf(e.target); if (it) sqlPage.whFocus = it.dataset.tid; } });
  const open = sqlPage.whOpen ||= new Set();
  const filter = h("input", { type: "search", class: "wh-filter", placeholder: "Filter objects", "aria-label": "Filter objects",
    value: sqlPage.whFilter || "", oninput: (e) => { sqlPage.whFilter = e.target.value; draw(); } });
  const toggle = (id) => { if (open.has(id)) open.delete(id); else open.add(id); draw(); };
  const row = (depth, label, attrs = {}, ...rest) => h("button", { class: "tree-row", style: `padding-left:${6 + depth * 16}px`,
    role: "treeitem", "aria-level": String(depth + 1), tabindex: "-1", ...attrs }, ...rest, h("span", { class: "label" }, label));
  // The tree row a key or focus came from: an object's chevron and ⋯ buttons count as its row.
  const treeItemOf = (el) => el.closest?.("[role=treeitem]") || el.closest?.(".wh-object")?.querySelector("[role=treeitem]");

  function treeKeys(e) {
    const cur = treeItemOf(e.target);
    if (!cur || e.altKey || e.ctrlKey || e.metaKey) return;
    const items = [...tree.querySelectorAll("[role=treeitem]")];
    const at = items.indexOf(cur);
    const level = Number(cur.getAttribute("aria-level"));
    const expanded = cur.getAttribute("aria-expanded");
    const move = (it) => { if (it) { items.forEach((x) => { x.tabIndex = -1; }); it.tabIndex = 0; it.focus(); } };
    if (e.key === "ArrowDown") move(items[at + 1]);
    else if (e.key === "ArrowUp") move(items[at - 1]);
    else if (e.key === "Home") move(items[0]);
    else if (e.key === "End") move(items[items.length - 1]);
    else if (e.key === "ArrowRight") {
      if (expanded === "false") toggle(cur.dataset.tid);
      else if (expanded === "true") move(items[at + 1]);
      else return;
    } else if (e.key === "ArrowLeft") {
      if (expanded === "true") toggle(cur.dataset.tid);
      else move(items.slice(0, at).reverse().find((x) => Number(x.getAttribute("aria-level")) < level));
    } else if ((e.key === "F10" && e.shiftKey) || e.key === "ContextMenu") {
      if (!cur.closest(".wh-object")) return;
      cur.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true }));
    } else return;
    e.preventDefault();
  }

  function draw() {
    const cat = sqlPage.catalog?.key === sqlRefKey(ref) ? sqlPage.catalog : null;
    filter.hidden = cat?.state !== "done";
    const hadFocus = tree.contains(document.activeElement);
    if (!cat || cat.state === "idle") {
      box.replaceChildren(h("p", { class: "muted-text wh-note" }, "Lists schemas, tables, views, procedures and functions from the warehouse's catalog."),
        btn("List objects", "sync", actions.load, "tool outline small", { title: "Runs two catalog queries in Fabric (uses capacity)" }));
      return;
    }
    if (cat.state === "loading") { box.replaceChildren(h("div", { class: "tree-row muted" }, "Listing objects in Fabric...")); return; }
    if (cat.state === "failed") {
      box.replaceChildren(h("div", { class: "output error wh-note" }, `Could not list the objects: ${cat.error}`),
        btn("Try again", "sync", actions.load, "tool outline small"));
      return;
    }
    const term = (sqlPage.whFilter || "").trim().toLowerCase();
    const shown = cat.objects.filter((o) => !term || o.name.toLowerCase().includes(term) || o.schema.toLowerCase().includes(term));
    const rows = [];
    const schemas = [...new Set(shown.map((o) => o.schema))];
    const empty = !schemas.length && h("div", { class: "tree-row muted" }, term ? "Nothing matches." : "This warehouse has no tables, views, procedures or functions yet.");
    for (const schema of schemas) {
      const sid = `s:${schema}`;
      const sOpen = term || !open.has(sid);  // schemas start open
      rows.push(row(0, schema, { class: "tree-row chev", "aria-expanded": String(!!sOpen), "data-tid": sid, onclick: () => toggle(sid) }, chevron(sOpen), icon("folder", 15)));
      if (!sOpen) continue;
      for (const [type, label] of WH_OBJECT_GROUPS) {
        const objs = shown.filter((o) => o.schema === schema && o.type === type);
        if (!objs.length) continue;
        const gid = `g:${schema}.${type}`;
        const gOpen = term || (type === "U" ? !open.has(gid) : open.has(gid));  // tables start open
        rows.push(row(1, `${label} (${objs.length})`, { class: "tree-row chev", "aria-expanded": String(!!gOpen), "data-tid": gid, onclick: () => toggle(gid) }, chevron(gOpen)));
        if (!gOpen) continue;
        for (const o of objs) {
          const full = whName(o.schema, o.name);
          const cols = cat.cols[`${o.schema}.${o.name}`];
          const oid = `o:${o.schema}.${o.name}`;
          const readable = o.type === "U" || o.type === "V";
          const menu = (e) => {
            e.preventDefault();
            showMenu(e.currentTarget.closest(".sql-table-row").querySelector(".wh-more"), whScriptItems(ref, o, cols, actions));
          };
          // The name is the tree row; the chevron and ⋯ are for the mouse (the arrow keys and Shift+F10 do the same).
          rows.push(h("div", { class: "sql-table-row wh-object", role: "none", oncontextmenu: menu },
            readable ? h("button", { class: "tree-row chev", "aria-label": open.has(oid) ? `Hide columns of ${o.name}` : `Show columns of ${o.name}`,
              "aria-expanded": String(open.has(oid)), tabindex: "-1",
              style: "padding-left:38px", onclick: () => toggle(oid) }, chevron(open.has(oid))) : h("span", { class: "wh-indent" }),
            h("button", { class: "tree-row", role: "treeitem", "aria-level": "3", tabindex: "-1", "data-tid": oid,
              ...(readable ? { "aria-expanded": String(open.has(oid)) } : {}),
              title: readable ? `Query ${full}` : `Insert ${full}`,
              onclick: () => (readable ? actions.query(full) : actions.insert(full)) },
            icon(WH_ICONS[o.type] || "code", 15), h("span", { class: "label" }, o.name)),
            h("button", { class: "tree-row wh-more", "aria-label": `Script ${o.name}`, "aria-haspopup": "menu", tabindex: "-1",
              title: "Script as... (Shift+F10)", onclick: menu }, "⋯")));
          if (readable && open.has(oid)) {
            for (const c of cols || []) {
              rows.push(h("button", { class: "tree-row sql-col", style: "padding-left:70px", title: `Insert ${c.name}`,
                role: "treeitem", "aria-level": "4", tabindex: "-1", "data-tid": `c:${o.schema}.${o.name}.${c.name}`,
                onclick: () => actions.insert(sqlQuoteIdent(c.name, "warehouse")) },
              h("span", { class: "label" }, c.name), h("span", { class: "hint" }, c.type)));
            }
          }
        }
      }
    }
    tree.replaceChildren(...rows);
    // One row takes Tab: the one last focused, else the first.
    const items = [...tree.querySelectorAll("[role=treeitem]")];
    const cur = items.find((x) => x.dataset.tid === sqlPage.whFocus) || items[0];
    if (cur) cur.tabIndex = 0;
    box.replaceChildren(...(empty ? [empty] : [tree]),
      h("div", { class: "muted-text wh-note" }, `Listed from the warehouse catalog at ${timeText(cat.at)}. `,
        h("a", { href: "#", onclick: (e) => { e.preventDefault(); actions.load(); } }, "Refresh")));
    if (cur && hadFocus) cur.focus();  // after the tree is back in the page, or the focus is lost
  }
  draw();
  return { el: h("div", { class: "wh-explorer" }, filter, box), draw };
}
