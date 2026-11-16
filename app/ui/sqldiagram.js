// SQL page > Explorer > Diagram (parity item 15): the tables of one schema and how they link, read only.
// Solid lines are the warehouse's own keys (sys.foreign_keys, read with one catalog query in Fabric);
// dashed lines are guessed from column names like customer_id, and say so. It never writes keys.
// A lakehouse has no keys, so every line there is a guess. Export as SVG writes a new file in Downloads.

const DIAGRAM_MAX_TABLES = 40;
const DIAGRAM_MAX_COLUMNS = 12;
// Primary and foreign keys from the warehouse's catalog, one row per key column.
const WH_KEYS_SQL = `SELECT 'F' AS kind, SCHEMA_NAME(pt.schema_id) AS table_schema, pt.name AS table_name, pc.name AS column_name,
  SCHEMA_NAME(rt.schema_id) AS ref_schema, rt.name AS ref_table, rc.name AS ref_column
FROM sys.foreign_key_columns AS fkc
JOIN sys.tables AS pt ON pt.object_id = fkc.parent_object_id
JOIN sys.columns AS pc ON pc.object_id = fkc.parent_object_id AND pc.column_id = fkc.parent_column_id
JOIN sys.tables AS rt ON rt.object_id = fkc.referenced_object_id
JOIN sys.columns AS rc ON rc.object_id = fkc.referenced_object_id AND rc.column_id = fkc.referenced_column_id
UNION ALL
SELECT 'P', SCHEMA_NAME(t.schema_id), t.name, c.name, NULL, NULL, NULL
FROM sys.key_constraints AS kc
JOIN sys.tables AS t ON t.object_id = kc.parent_object_id
JOIN sys.index_columns AS ic ON ic.object_id = kc.parent_object_id AND ic.index_id = kc.unique_index_id
JOIN sys.columns AS c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE kc.type = 'PK';`;

// The keys as { pks: {"schema.table": [column]}, fks: [{ from, fromCol, to, toCol }] } from WH_KEYS_SQL's rows.
function diagramKeys(rows) {
  const pks = {};
  const fks = [];
  for (const [kind, schema, table, col, refSchema, refTable, refCol] of rows || []) {
    if (kind === "P") (pks[`${schema}.${table}`] ||= []).push(col);
    else if (kind === "F") fks.push({ from: `${schema}.${table}`, fromCol: col, to: `${refSchema}.${refTable}`, toCol: refCol });
  }
  return { pks, fks };
}

// A table name as a column stem would name it: dim_customer, DimCustomers and customers are all "customer".
function diagramStem(name) {
  return String(name).toLowerCase().replace(/^(dim|fact|fct|tbl|d|f)_/, "").replace(/^dim(?=[a-z])/, "").replace(/s$/, "");
}

// The lines between `tables` ([{ id: "schema.table", name, columns: [{ name, type }] }]): every key whose
// two ends are both drawn, then a guess for each column named <stem>_id (or <stem>id) that has no key: to
// the one other table named for the stem that has that column, or an id column, or has it as its primary key.
function diagramEdges(tables, keys = { pks: {}, fks: [] }) {
  const byId = new Map(tables.map((t) => [t.id, t]));
  const lower = (s) => String(s).toLowerCase();
  const has = (t, col) => t.columns.find((c) => lower(c.name) === lower(col));
  const edges = keys.fks.filter((k) => byId.has(k.from) && byId.has(k.to)).map((k) => ({ ...k, kind: "key" }));
  const keyed = new Set(edges.map((e) => `${e.from}.${lower(e.fromCol)}`));
  for (const t of tables) {
    for (const c of t.columns) {
      const m = lower(c.name).match(/^([a-z][a-z0-9_]*?)_?id$/);
      if (!m || m[1].length < 2 || keyed.has(`${t.id}.${lower(c.name)}`) || (keys.pks[t.id] || []).map(lower).includes(lower(c.name))) continue;
      const stem = m[1].replace(/_$/, "");
      if (diagramStem(t.name) === stem) continue;  // the table's own id (dim_region.region_id), not a link
      const found = tables.filter((o) => o !== t).map((o) => {
        const pk = (keys.pks[o.id] || []).map(lower);
        if (pk.length === 1 && pk[0] === lower(c.name)) return { o, col: has(o, c.name).name, rank: 0 };
        if (diagramStem(o.name) !== stem) return null;
        if (has(o, c.name)) return { o, col: has(o, c.name).name, rank: 1 };
        if (has(o, "id")) return { o, col: has(o, "id").name, rank: 2 };
        return null;
      }).filter(Boolean).sort((a, b) => a.rank - b.rank);
      if (found.length && (found.length === 1 || found[0].rank < found[1].rank)) {
        edges.push({ from: t.id, fromCol: c.name, to: found[0].o.id, toCol: found[0].col, kind: "guess" });
      }
    }
  }
  return edges;
}

// Where each table's box goes: tables with lines out (facts) in the middle, the tables they point to on
// either side, and the rest after them; with no lines, a grid. Returns { boxes: Map(id -> {x, y, w, h, rows}), width, height }.
function diagramLayout(tables, edges, keys = { pks: {} }) {
  const W = 220, GAP_X = 90, GAP_Y = 28, ROW = 20, HEAD = 28;
  const lower = (s) => String(s).toLowerCase();
  const rowsOf = (t) => {
    const pk = new Set((keys.pks[t.id] || []).map(lower));
    const linked = new Set(edges.flatMap((e) => [e.from === t.id ? lower(e.fromCol) : null, e.to === t.id ? lower(e.toCol) : null]).filter(Boolean));
    const tag = (c) => (pk.has(lower(c.name)) ? "PK" : edges.some((e) => e.from === t.id && lower(e.fromCol) === lower(c.name)) ? "FK" : "");
    const ordered = [...t.columns.filter((c) => pk.has(lower(c.name))), ...t.columns.filter((c) => !pk.has(lower(c.name)))];
    const must = ordered.filter((c) => pk.has(lower(c.name)) || linked.has(lower(c.name)));
    const shown = ordered.filter((c) => must.includes(c) || ordered.indexOf(c) < DIAGRAM_MAX_COLUMNS).slice(0, Math.max(DIAGRAM_MAX_COLUMNS, must.length));
    const rows = shown.map((c) => ({ name: c.name, tag: tag(c) }));
    if (t.columns.length > shown.length) rows.push({ more: t.columns.length - shown.length });
    return rows;
  };
  const facts = tables.filter((t) => edges.some((e) => e.from === t.id && e.to !== t.id));
  const columns = facts.length ? [[], facts, []] : [[], [], [], []];
  if (facts.length) {
    for (const t of tables.filter((x) => !facts.includes(x))) {
      const side = columns[0].length <= columns[2].length ? 0 : 2;
      columns[side].push(t);
    }
  } else {
    tables.forEach((t, i) => columns[i % 4].push(t));
  }
  const boxes = new Map();
  let height = 0;
  columns.forEach((col, i) => {
    let y = 20;
    for (const t of col) {
      const rows = rowsOf(t);
      const box = { x: 20 + i * (W + GAP_X), y, w: W, h: HEAD + rows.length * ROW + 8, rows };
      boxes.set(t.id, box);
      y += box.h + GAP_Y;
    }
    height = Math.max(height, y);
  });
  const used = columns.filter((c) => c.length).length || 1;
  const width = 40 + columns.length * W + (columns.length - 1) * GAP_X;
  return { boxes, width: columns[columns.length - 1].length ? width : 40 + (used) * (W + GAP_X) - GAP_X, height: Math.max(height, 120) };
}

// The diagram as an <svg> string, with its colors written in (so a saved file looks the same anywhere).
// Names are escaped and only ever text, never an attribute, except on the page (interactive), where each
// table's box carries its id and a label to click; the saved file leaves those out.
function diagramSvg(tables, edges, keys, { dark = false, interactive = true } = {}) {
  const { boxes, width, height } = diagramLayout(tables, edges, keys);
  const c = dark ? { bg: "#1d2128", box: "#252a33", head: "#223352", line: "#343a45", text: "#e9ebef", muted: "#9ba2ad", key: "#8fb4f0", guess: "#9ba2ad" }
    : { bg: "#ffffff", box: "#ffffff", head: "#e9eef7", line: "#c8ccd2", text: "#242424", muted: "#616161", key: "#1b5fb4", guess: "#616161" };
  const esc = (s) => String(s).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const rowY = (box, col) => {
    const i = box.rows.findIndex((r) => r.name && r.name.toLowerCase() === String(col).toLowerCase());
    return box.y + 28 + (i < 0 ? 0 : i) * 20 + 14;
  };
  const paths = edges.map((e) => {
    const a = boxes.get(e.from), b = boxes.get(e.to);
    if (!a || !b) return "";
    const right = b.x > a.x;
    const x1 = right ? a.x + a.w : a.x, x2 = right ? b.x : b.x + b.w;
    const y1 = rowY(a, e.fromCol), y2 = rowY(b, e.toCol);
    const bend = Math.max(40, Math.abs(x2 - x1) / 2) * (right ? 1 : -1);
    const same = a === b || a.x === b.x;
    const d = same ? `M${a.x + a.w} ${y1} C ${a.x + a.w + 60} ${y1}, ${b.x + b.w + 60} ${y2}, ${b.x + b.w} ${y2}`
      : `M${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`;
    const title = `${e.from}.${e.fromCol} → ${e.to}.${e.toCol} (${e.kind === "key" ? "from the warehouse's keys" : "guessed from column names"})`;
    return `<path d="${d}" fill="none" stroke="${e.kind === "key" ? c.key : c.guess}" stroke-width="1.5"${e.kind === "key" ? "" : ' stroke-dasharray="5 4"'}><title>${esc(title)}</title></path>`;
  }).join("");
  const box = (t) => {
    const b = boxes.get(t.id);
    const rows = b.rows.map((r, i) => {
      const y = b.y + 28 + i * 20 + 18;
      if (r.more) return `<text x="${b.x + 10}" y="${y}" fill="${c.muted}">+ ${r.more} more column${r.more === 1 ? "" : "s"}</text>`;
      return `${r.tag ? `<text x="${b.x + 10}" y="${y}" fill="${c.muted}" font-size="10">${r.tag}</text>` : ""}<text x="${b.x + 34}" y="${y}" fill="${c.text}">${esc(r.name)}</text>`;
    }).join("");
    return (interactive ? `<g class="diagram-table" data-table="${esc(t.id)}" tabindex="0" role="button" aria-label="${esc(t.name)}: open it in the Explorer">` : "<g>") +
      `<rect x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" rx="6" fill="${c.box}" stroke="${c.line}"/>` +
      `<rect x="${b.x + 1}" y="${b.y + 1}" width="${b.w - 2}" height="26" rx="5" fill="${c.head}"/>` +
      `<text x="${b.x + 10}" y="${b.y + 18}" fill="${c.text}" font-weight="700">${esc(t.name)}</text>${rows}</g>`;
  };
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" ` +
    `font-family="Segoe UI, system-ui, sans-serif" font-size="12" role="${interactive ? "group" : "img"}" aria-label="Diagram of ${tables.length} table${tables.length === 1 ? "" : "s"}">` +
    `<rect width="100%" height="100%" fill="${c.bg}"/>${paths}${tables.map(box).join("")}</svg>`;
}

// One line on what the diagram shows.
function diagramSummary(tables, edges, shownOf, total, kind = "warehouse") {
  const keyed = edges.filter((e) => e.kind === "key").length;
  const guessed = edges.length - keyed;
  return [`${tables.length}${total > shownOf ? ` of ${total}` : ""} table${tables.length === 1 && total <= shownOf ? "" : "s"}`,
    ...(kind === "lakehouse" ? [] : [`${keyed} from keys`]), `${guessed} guessed from names`].join(" · ");
}

// Opens the diagram. source: { kind: "warehouse" | "lakehouse", name, schemas: [schema], tables(schema) ->
// Promise<[{ schema, name, columns }]>, keys() -> Promise<{ pks, fks }> (warehouse only), reveal({ schema, name }) }.
async function openSqlDiagram(source) {
  const view = { schema: source.schemas[0], fit: true, svg: "", tables: [], edges: [], keys: { pks: {}, fks: [] }, total: 0 };
  const status = h("span", { class: "muted-text small", role: "status" }, "Reading tables...");
  const holder = h("div", { class: "sql-diagram" });
  const problem = h("p", { class: "fail-text small", role: "alert" });
  const pick = source.schemas.length > 1 ? h("select", { "aria-label": "Schema", onchange: (e) => { view.schema = e.target.value; load(); } },
    source.schemas.map((s) => h("option", { value: s }, s))) : null;
  const fit = btn("Fit", null, () => { view.fit = !view.fit; fit.setAttribute("aria-pressed", String(view.fit)); draw(); }, "tool outline small",
    { "aria-pressed": "true", title: "Fit the diagram to the window, or show it at full size" });
  const save = btn("Export as SVG", "save", exportSvg, "tool outline small", { disabled: true });
  let keysRead = null;
  async function load() {
    status.textContent = "Reading tables...";
    problem.textContent = "";
    save.disabled = true;
    try {
      if (source.keys && !keysRead) keysRead = source.keys().then((k) => k, (err) => ({ error: err }));
      const all = await source.tables(view.schema);
      const keys = keysRead ? await keysRead : { pks: {}, fks: [] };
      if (keys.error) {
        problem.textContent = `Couldn't read the warehouse's keys, so every line is guessed from names: ${friendlyError(keys.error).text}`;
      }
      view.keys = keys.error ? { pks: {}, fks: [] } : keys;
      view.total = all.length;
      view.tables = all.slice(0, DIAGRAM_MAX_TABLES).map((t) => ({ id: `${t.schema}.${t.name}`, name: t.name, columns: t.columns || [] }));
      view.edges = diagramEdges(view.tables, view.keys);
      draw();
    } catch (err) {
      status.textContent = "";
      problem.textContent = `Couldn't read the tables: ${friendlyError(err).text}`;
    }
  }
  function draw() {
    if (!view.tables.length) {
      status.textContent = `${view.schema} has no tables.`;
      holder.replaceChildren();
      return;
    }
    status.textContent = diagramSummary(view.tables, view.edges, DIAGRAM_MAX_TABLES, view.total, source.kind)
      + (view.total > DIAGRAM_MAX_TABLES ? `. Showing the first ${DIAGRAM_MAX_TABLES}.` : "");
    const dark = document.documentElement.dataset.colorMode === "dark";
    holder.innerHTML = diagramSvg(view.tables, view.edges, view.keys, { dark });
    const svg = holder.querySelector("svg");
    if (view.fit) { svg.removeAttribute("width"); svg.removeAttribute("height"); svg.style.width = "100%"; svg.style.height = "auto"; }
    for (const g of holder.querySelectorAll(".diagram-table")) {
      const open = () => {
        const [schema, ...rest] = g.dataset.table.split(".");
        closeDialog();
        source.reveal({ schema, name: rest.join(".") });
      };
      g.addEventListener("click", open);
      g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
    }
    save.disabled = false;
  }
  async function exportSvg() {
    const out = await attempt(() => rpc("sql_diagram_export", { name: `${source.name} ${view.schema} diagram`,
      svg: diagramSvg(view.tables, view.edges, view.keys, { interactive: false }) }), "Could not save the diagram");
    if (out) toast(`Saved to ${out.path}`);
  }
  const overlay = showDialog(`Diagram: ${source.name}`, h("div", { class: "sql-diagram-body" },
    h("div", { class: "row wrap" }, previewTag(), pick, status, h("span", { class: "spacer" }), fit, save),
    problem, holder,
    h("p", { class: "muted-text small sql-diagram-legend" },
      ...(source.kind === "warehouse" ? [h("span", { class: "sql-diagram-solid", "aria-hidden": "true" }), " From the warehouse's keys "] : []),
      h("span", { class: "sql-diagram-dashed", "aria-hidden": "true" }),
      source.kind === "warehouse" ? " Guessed from column names." : " Guessed from column names (a lakehouse has no keys).",
      " Click a table to open it in the Explorer. Read only: nothing here changes a key. ", learnMore("table-diagram-preview"))),
  [btn("Close", null, closeDialog, "tool primary")]);
  overlay.querySelector(".dialog").classList.add("wide");
  await load();
}
