// Compare data (editor parity S34, Preview), from the approved mockup: Explorer > Compare data, or a
// table's menu > Compare with. A and B are each a table (or a warehouse's view) or a query. Compare runs each
// one as an ordinary run that only reads (its queue, Stop and time limit): on this PC for a lakehouse, in
// Fabric for a warehouse, after a confirm. Then sql_compare reads the two results on this PC with the row
// diff Verify uses. Changing the key, a tolerance or the tile shown compares the same two results again;
// nothing runs again until A or B changes.

// The query a side runs: a table is all of its rows; a query is run as written, less a trailing ;.
function sqlCompareSideQuery(side) {
  if (side.kind === "table") return side.name ? `SELECT * FROM ${side.name}` : "";
  return String(side.sql || "").trim().replace(/;\s*$/, "").trim();
}

function sqlCompareSideLabel(side, letter) {
  return side.kind === "table" && side.name ? `${letter}: ${side.name}` : `${letter}: query`;
}

// A warehouse's tables and views for Compare, from the Explorer's catalog: each one's name as nameOf
// writes it ([schema].[name]), its columns, and the primary key the catalog knows (cat.pks, read by the
// Diagram), else no key. A key is never guessed.
function sqlCompareWarehouseTables(cat, nameOf) {
  return (cat?.objects || []).filter((o) => o.type === "U" || o.type === "V").map((o) => ({
    name: nameOf(o.schema, o.name),
    columns: cat.cols?.[`${o.schema}.${o.name}`] || [],
    keys: o.type === "U" ? [...(cat.pks?.[`${o.schema}.${o.name}`] || [])] : [],
  }));
}

// The confirm before a warehouse's Compare runs A and B in Fabric.
function sqlCompareFabricConfirm(ref) {
  return { title: `Run A and B in Fabric on ${ref.name}?`,
    text: `Compare runs A, then B, on the ${ref.kind === "endpoint" ? "SQL analytics endpoint" : "warehouse"}. ` +
      "Both only read, and both use capacity. Nothing is changed." };
}

const SQL_COMPARE_TILES = [["same", "Same"], ["only_a", "Only in A"], ["only_b", "Only in B"], ["changed", "Changed"]];

// The tiles to show: Changed only when rows were matched on a key (a whole-row compare can't say).
function sqlCompareTiles(out) {
  if (!out?.counts) return [];
  return SQL_COMPARE_TILES.filter(([k]) => out.counts[k] !== null && out.counts[k] !== undefined)
    .map(([k, label]) => ({ key: k, label, count: out.counts[k] }));
}

const sqlCompareRows = (n) => `${Number(n).toLocaleString("en-US")} row${n === 1 ? "" : "s"}`;

// The verdict line: Same or Differs, the rows on each side, and that nothing changed.
function sqlCompareVerdict(out) {
  const word = out.result === "same" ? "Same" : "Differs";
  const secs = typeof out.seconds === "number" ? ` in ${out.seconds < 10 ? out.seconds.toFixed(1) : Math.round(out.seconds)} s` : "";
  const rows = `${sqlCompareRows(out.rows.a)} in A, ${Number(out.rows.b).toLocaleString("en-US")} in B.`;
  const how = out.level === "fingerprint" ? " Too many rows to list which differ, so only checksums were compared." : "";
  return { word, text: `${rows} Compared on this PC${secs}.${how} Read only: nothing was changed.` };
}

const SQL_COMPARE_KIND = { only_a: "only in A", only_b: "only in B", changed: "changed" };

// "Showing 3 of 15 changed rows." for the rows listed below the tiles.
const SQL_COMPARE_NOUN = { only_a: ["row only in A", "rows only in A"], only_b: ["row only in B", "rows only in B"],
  changed: ["changed row", "changed rows"] };
function sqlCompareShowing(out) {
  const n = out.examples?.length || 0;
  const total = out.listed || 0;
  const [one, many] = SQL_COMPARE_NOUN[out.status] || ["row that differs", "rows that differ"];
  if (!total) return out.status ? `No ${many}.` : "";
  const at = out.offset || 0;
  const f = (x) => x.toLocaleString("en-US");
  const range = n === total ? f(n) : `${f(at + 1)}–${f(at + n)}`;
  return `Showing ${range} of ${f(total)} ${total === 1 ? one : many}.`;
}

// The table of differing rows as a model: the key columns, then A's and B's values of the columns shown.
// Only the columns that differ on this page are shown, unless showAll; a whole-row compare shows the row.
function sqlCompareTable(out, showAll) {
  const ex = out.examples || [];
  if (out.method !== "keyed") {
    const names = (out.common || []).map((c) => c.name);
    return { keyed: false, names, rows: ex.map((e) => ({ status: e.status, times: e.times || 1, values: names.map((n) => e.row?.[n]) })) };
  }
  const keys = out.keys || [];
  const differ = new Set(ex.flatMap((e) => Object.keys(e.columns || {})));
  const all = (out.common || []).map((c) => c.name).filter((n) => !keys.some((k) => k.toLowerCase() === n.toLowerCase()));
  const cols = showAll ? all : all.filter((n) => differ.has(n));
  const rows = ex.map((e) => ({
    status: e.status, key: keys.map((k) => e.key?.[k]),
    cells: cols.map((n) => {
      const d = e.columns?.[n];
      const s = e.same?.[n];
      return d ? { a: d.a, b: d.b, changed: true } : s ? { a: s.a, b: s.b, changed: false } : { a: undefined, b: undefined, changed: false };
    }),
  }));
  return { keyed: true, keys, cols, rows, hidden: all.length - cols.length };
}

const sqlCompareCell = (v) => v === null ? "NULL" : v === undefined ? "" : typeof v === "number" ? v.toLocaleString("en-US", { maximumFractionDigits: 20 }) : String(v);

function sqlCompareDialog(ctx) {
  // ctx: { ref, dialect, tables: [names], a: preset table name, columns(name) -> Promise<[{name, type}]> }.
  // A warehouse adds start(query) -> sql_start's status, confirm() -> Promise<bool> before A and B run,
  // keys(name) -> the table's known key, and where ("in Fabric"). A lakehouse leaves them out.
  const first = ctx.tables[0] || "";
  const where = ctx.where || "on this PC";
  const knownKey = (side) => (side.kind === "table" && side.name && ctx.keys ? ctx.keys(side.name) : []);
  const s = {
    a: { kind: "table", name: ctx.a || first, sql: "" },
    b: { kind: ctx.tables.length > 1 || !ctx.a ? "table" : "query", name: ctx.tables.find((t) => t !== (ctx.a || first)) || "", sql: "" },
    keys: [], tol: {}, ran: null, runs: null, out: null, status: null, offset: 0, showAll: false, busy: "", job: null, error: "",
    columns: [],
  };
  s.keys = knownKey(s.a);
  let closed = false;
  let asking = false;  // the confirm is showing in place of this dialog, which comes back after it
  const box = h("div", { class: "sql-compare" });
  const sig = () => JSON.stringify([sqlCompareSideQuery(s.a), sqlCompareSideQuery(s.b), ctx.dialect]);

  async function fillColumns() {
    if (s.out?.common) { s.columns = s.out.common; return; }
    if (s.a.kind === "table" && s.a.name && ctx.columns) {
      const cols = await ctx.columns(s.a.name).catch(() => []);
      s.columns = (cols || []).map((c) => ({ name: c.name, number: /int|double|float|decimal|numeric|real|long|short|byte/i.test(c.type || "") }));
    } else s.columns = [];
  }

  function sidePicker(letter, side) {
    const id = `sql-compare-${letter.toLowerCase()}`;
    const kind = h("select", { "aria-label": `${letter} is a`, onchange: (e) => { side.kind = e.target.value; s.error = ""; draw(); fillColumns().then(draw); } },
      h("option", { value: "table", selected: side.kind === "table", disabled: !ctx.tables.length }, "Table"),
      h("option", { value: "query", selected: side.kind === "query" }, "Query"));
    const value = side.kind === "table"
      ? h("select", { id, "aria-label": `${letter}: table`, onchange: (e) => { side.name = e.target.value; s.keys = letter === "A" ? knownKey(side) : s.keys; draw(); fillColumns().then(draw); } },
        ctx.tables.map((t) => h("option", { value: t, selected: t === side.name }, t)))
      : h("textarea", { id, rows: 3, spellcheck: "false", class: "sql-compare-query", "aria-label": `${letter}: query`,
        placeholder: "SELECT * FROM …", oninput: (e) => { side.sql = e.target.value; } }, side.sql);
    return h("div", { class: "sql-compare-pick" }, h("span", { class: "sql-compare-label" }, letter), kind, value,
      h("span", { class: "muted-text small" }, side.kind === "table" ? "Every row of the table" : "One query that only reads"));
  }

  function keyPicker() {
    const free = s.columns.filter((c) => !s.keys.some((k) => k.toLowerCase() === c.name.toLowerCase()));
    return h("div", { class: "sql-compare-pick" }, h("span", { class: "sql-compare-label", id: "sql-compare-keys" }, "Match rows on"),
      h("div", { class: "sql-compare-chips", role: "group", "aria-labelledby": "sql-compare-keys" },
        s.keys.map((k) => h("button", { type: "button", class: "chip", "aria-label": `Stop matching on ${k}`, onclick: () => { s.keys = s.keys.filter((x) => x !== k); again(); } }, k, " ✕")),
        free.length ? h("select", { "aria-label": "Add a key column", onchange: (e) => { if (e.target.value) { s.keys.push(e.target.value); again(); } } },
          h("option", { value: "" }, "+ Add a key"), free.map((c) => h("option", { value: c.name }, c.name))) : null),
      h("span", { class: "muted-text small" }, s.keys.length ? "A repeated key stops the compare." : s.columns.length
        ? "With no key, rows compare as whole rows, and only Only in A and Only in B are counted."
        : "Compare once to pick the key columns from the results."));
  }

  function tolerancePicker() {
    const numbers = s.columns.filter((c) => c.number && !(c.name in s.tol) && !s.keys.some((k) => k.toLowerCase() === c.name.toLowerCase()));
    const col = h("select", { "aria-label": "Number column" }, numbers.map((c) => h("option", { value: c.name }, c.name)));
    const amount = h("input", { type: "number", min: "0", step: "any", value: "0.01", "aria-label": "Within", class: "sql-compare-within" });
    const add = () => {
      const v = Number(amount.value);
      if (!col.value || !(v >= 0)) { toast("Type a tolerance of 0 or more, like 0.01, then press Add.", true); return; }
      s.tol[col.value] = v;
      again();
    };
    return h("div", { class: "sql-compare-pick" }, h("span", { class: "sql-compare-label", id: "sql-compare-tol" }, "Tolerance"),
      h("div", { class: "sql-compare-chips", role: "group", "aria-labelledby": "sql-compare-tol" },
        Object.entries(s.tol).map(([n, v]) => h("button", { type: "button", class: "chip", "aria-label": `Remove the tolerance on ${n}`,
          onclick: () => { delete s.tol[n]; again(); } }, `${n} ± ${v}`, " ✕")),
        numbers.length ? h("span", { class: "sql-compare-add" }, col, "±", amount, btn("Add", null, add, "tool outline small")) : null),
      h("span", { class: "muted-text small" }, "Other columns must be equal. A NULL equals only another NULL."));
  }

  function results() {
    if (s.busy) return h("p", { class: "muted-text", role: "status" }, s.busy);
    if (s.error) return h("p", { class: "sql-compare-error", role: "alert" }, s.error);
    const out = s.out;
    if (!out) return h("p", { class: "muted-text" }, "Pick A and B, then Compare. Both only read, so nothing is changed.");
    if (out.stopped) return h("p", { class: "sql-compare-stopped", role: "alert" }, out.stopped);
    const v = sqlCompareVerdict(out);
    const t = sqlCompareTable(out, s.showAll);
    const notes = [...(out.columns?.only_a?.length ? [`Only in A: ${out.columns.only_a.join(", ")}.`] : []),
      ...(out.columns?.only_b?.length ? [`Only in B: ${out.columns.only_b.join(", ")}.`] : []),
      ...(out.columns?.types?.length ? [`Different types: ${out.columns.types.join(", ")}.`] : []),
      ...(out.note ? [out.note] : []), ...(out.limitations || [])];
    const page = (d) => { s.offset = Math.max(0, s.offset + d * 50); again(); };
    return h("div", { class: "sql-compare-results" },
      h("div", { class: `sql-compare-verdict ${out.result}`, role: "status" }, h("b", {}, v.word), h("span", {}, v.text)),
      notes.length ? h("ul", { class: "sql-compare-notes" }, notes.map((n) => h("li", {}, n))) : null,
      h("div", { class: "sql-compare-tiles", role: "group", "aria-label": "Show rows" }, sqlCompareTiles(out).map((tile) => {
        const on = s.status === tile.key;
        const can = tile.key !== "same";
        return h("button", { type: "button", class: `sql-compare-tile${on ? " on" : ""}`, disabled: !can, "aria-pressed": can ? String(on) : null,
          title: can ? (on ? "Show every row that differs" : `Show only the rows ${tile.label.toLowerCase()}`) : "Rows that are the same aren't listed",
          onclick: () => { s.status = on ? null : tile.key; s.offset = 0; again(); } },
        h("b", {}, tile.count.toLocaleString("en-US")), h("span", {}, tile.label));
      })),
      t.rows.length ? h("div", { class: "sql-compare-table-wrap" }, diffTable(t)) : null,
      h("div", { class: "sql-compare-foot" },
        h("span", { class: "muted-text small" }, sqlCompareShowing(out),
          t.keyed && t.hidden && !s.showAll ? " Only the columns that differ are shown." : ""),
        t.keyed && (t.hidden || s.showAll) ? btn(s.showAll ? "Show only differing columns" : "Show all columns", null, () => { s.showAll = !s.showAll; draw(); }, "tool outline small") : null,
        s.offset > 0 ? btn("Previous", null, () => page(-1), "tool outline small") : null,
        (s.offset + (out.examples?.length || 0)) < (out.listed || 0) ? btn("Next", null, () => page(1), "tool outline small") : null,
        out.result === "differs" && out.counts ? btn("Export", "arrowDownload", exportRows, "tool outline small",
          { title: "Save every row that differs to a CSV file in Downloads" }) : null));
  }

  function diffTable(t) {
    if (!t.keyed) {
      return h("table", { class: "sql-compare-table", "aria-label": "Rows that differ" },
        h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Where"), t.names.map((n) => h("th", { scope: "col" }, n)))),
        h("tbody", {}, t.rows.map((r) => h("tr", {}, h("td", {}, `${SQL_COMPARE_KIND[r.status]}${r.times > 1 ? ` ×${r.times}` : ""}`),
          r.values.map((v) => h("td", {}, sqlCompareCell(v)))))));
    }
    const la = sqlCompareSideLabel(s.a, "A");
    const lb = sqlCompareSideLabel(s.b, "B");
    return h("table", { class: "sql-compare-table", "aria-label": "Rows that differ, A beside B" },
      h("thead", {},
        h("tr", {}, t.keys.map(() => h("td", {})), h("td", {}), t.cols.length ? [h("th", { scope: "colgroup", colspan: t.cols.length, class: "grp" }, la),
          h("th", { scope: "colgroup", colspan: t.cols.length, class: "grp" }, lb)] : null),
        h("tr", {}, t.keys.map((k) => h("th", { scope: "col" }, k)), h("th", { scope: "col" }, "Where"),
          t.cols.map((n, i) => h("th", { scope: "col", class: i === 0 ? "grp" : "" }, n)), t.cols.map((n, i) => h("th", { scope: "col", class: i === 0 ? "grp" : "" }, n)))),
      h("tbody", {}, t.rows.map((r) => h("tr", {},
        r.key.map((v) => h("td", {}, sqlCompareCell(v))), h("td", {}, SQL_COMPARE_KIND[r.status]),
        r.status === "changed" ? [
          r.cells.map((c, i) => h("td", { class: `${i === 0 ? "grp" : ""}${c.changed ? " chg" : ""}` }, sqlCompareCell(c.a))),
          r.cells.map((c, i) => h("td", { class: `${i === 0 ? "grp" : ""}${c.changed ? " chg" : ""}` }, sqlCompareCell(c.b)))]
          : t.cols.length ? h("td", { colspan: t.cols.length * 2, class: "muted-text grp" }, r.status === "only_a" ? "No row in B with this key" : "No row in A with this key") : null))));
  }

  // One side's run: an ordinary SQL page run that only reads, on this lakehouse or in Fabric. Stop ends it.
  async function runSide(query, letter) {
    const job = s.job;
    let st = await (ctx.start ? ctx.start(query) : rpc("sql_start", { query, lakehouse_ref: ctx.ref, dialect: ctx.dialect }));
    job.id = st.id;
    let wait = 150;
    while (["queued", "running", "cancelling"].includes(st.state)) {
      if (job.stopped || closed) { rpc("sql_cancel", { execution: st.id }).catch(() => {}); return null; }
      await new Promise((r) => setTimeout(r, wait));
      wait = Math.min(1000, wait * 1.5);
      st = await rpc("sql_status", { execution: st.id });
    }
    if (job.stopped || closed) return null;
    if (st.refused) throw new Error(`${letter} changes something, and Compare data only reads. Pick a table or a query that only reads.`);
    if (st.state !== "completed") throw new Error(`${letter}: ${st.error || `the query ${String(st.state).replace("_", " ")}.`}`);
    return st.id;
  }

  async function compare() {
    const qa = sqlCompareSideQuery(s.a);
    const qb = sqlCompareSideQuery(s.b);
    if (!qa || !qb) { s.error = `Pick a table, or a query that only reads, for ${!qa ? "A" : "B"}.`; draw(); return; }
    s.error = "";
    if (s.ran !== sig()) {
      if (ctx.confirm) {
        // The confirm replaces this dialog while it shows; this dialog comes back as it was.
        asking = true;
        const ok = await ctx.confirm();
        asking = false;
        if (closed) return;
        open();
        if (!ok) return;
      }
      s.job = { id: null, stopped: false };
      s.out = null; s.runs = null; s.status = null; s.offset = 0;
      try {
        s.busy = `Running A ${where}…`; draw();
        const a = await runSide(qa, "A");
        if (!a) { s.busy = ""; s.job = null; if (!closed) draw(); return; }
        s.busy = `Running B ${where}…`; draw();
        const b = await runSide(qb, "B");
        if (!b) { s.busy = ""; s.job = null; if (!closed) draw(); return; }
        s.runs = { a, b }; s.ran = sig();
      } catch (err) {
        s.busy = ""; s.job = null; s.error = friendlyError(err).text;
        if (!closed) draw();
        return;
      }
      s.job = null;
    }
    await again();
  }

  // The same two results compared again: a new key, tolerance, tile or page. Nothing runs.
  async function again() {
    if (!s.runs) { draw(); return; }
    s.busy = "Comparing on this PC…"; s.error = ""; draw();
    try {
      const out = await rpc("sql_compare", { a: s.runs.a, b: s.runs.b, keys: s.keys, tolerance: s.tol, status: s.status, offset: s.offset });
      if (out.rerun_needed) { s.ran = null; s.runs = null; s.out = null; s.error = `${out.message} Press Compare to run A and B again.`; }
      else { s.out = out; s.columns = out.common || s.columns; }
    } catch (err) {
      s.error = friendlyError(err).text;
    }
    s.busy = "";
    if (!closed) draw();
  }

  async function exportRows() {
    const out = await attempt(() => rpc("sql_compare_export", { a: s.runs.a, b: s.runs.b, keys: s.keys, tolerance: s.tol, name: ctx.ref.name }),
      "Couldn't export the rows that differ");
    if (!out) return;
    if (out.rerun_needed) { toast(out.message, true); return; }
    toast(`Saved ${sqlCompareRows(out.rows)} that differ to ${out.name} in Downloads.${out.notes?.length ? ` ${out.notes.join(" ")}` : ""}`);
  }

  function stop() {
    if (!s.job || s.job.stopped) return;
    s.job.stopped = true;
    if (s.job.id) rpc("sql_cancel", { execution: s.job.id }).catch(() => {});
  }

  const swap = btn("Swap A and B", null, () => { [s.a, s.b] = [s.b, s.a]; s.keys = knownKey(s.a); s.tol = {}; draw(); fillColumns().then(draw); }, "tool outline");
  const go = btn("Compare", null, () => compare(), "tool primary");
  const stopBtn = btn("Stop", null, stop, "tool outline");
  function draw() {
    if (closed) return;
    const focused = box.contains(document.activeElement) ? document.activeElement.getAttribute("aria-label") || document.activeElement.textContent : null;
    box.replaceChildren(
      h("p", { class: "muted-text small" }, `${ctx.ref.name}. `, previewTag()),
      h("div", { class: "sql-compare-bar" }, sidePicker("A", s.a), sidePicker("B", s.b), keyPicker(), tolerancePicker()),
      results());
    go.disabled = !!s.busy;
    stopBtn.hidden = !s.job;
    swap.disabled = !!s.busy;
    if (focused) [...box.querySelectorAll("button, select, input, textarea")].find((el) => (el.getAttribute("aria-label") || el.textContent) === focused)?.focus();
  }
  const closeBtn = btn("Close", null, closeDialog, "tool outline");
  function open() {
    showDialog("Compare data", box, [swap, stopBtn, closeBtn, go], () => { if (!asking) { closed = true; stop(); } });
    box.closest(".dialog")?.classList.add("wide", "sql-compare-dialog");
  }
  fillColumns().then(draw);
  draw();
  open();
}
