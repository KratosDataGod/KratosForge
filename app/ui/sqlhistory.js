// Query history and Fabric query insights on the Warehouse page (WH-07): the explorer's History tab.

// This PC's own runs on a warehouse, newest first, kept in the app's storage on this PC with their
// SQL so they can be opened again. The engine never keeps the SQL (sql_executions has states and
// timings only), so this is the only copy; Clear history removes it.
const SQL_HISTORY_MAX = 200;        // runs kept per warehouse
const SQL_HISTORY_SQL = 20000;      // characters of SQL kept per run
const SQL_HISTORY_BUDGET = 500000;  // characters across every warehouse's history (about 1 MB), so open tabs keep room

const SQL_HISTORY_PREFIX = "fabric-desktop.sqlhistory.";
const sqlHistoryKey = (ref) => `${SQL_HISTORY_PREFIX}${sqlRefKey(ref)}`;

function sqlHistoryRead(key) {
  try {
    const list = JSON.parse(localStorage.getItem(key));
    return Array.isArray(list) ? list.filter((e) => e && typeof e.sql === "string") : [];
  } catch { return []; }
}

const sqlHistory = (ref) => sqlHistoryRead(sqlHistoryKey(ref));

// An engine error can echo values from the rows, so history keeps only its first line, with quoted
// text and numbers taken out.
const sqlHistoryError = (err) => String(err || "").split("\n")[0]
  .replace(/'(?:[^']|'')*'|"[^"]*"|N?'[^']*$/g, "'…'").replace(/\d[\d.,:-]*/g, "#").slice(0, 120) || undefined;

// Adds a run, newest first. Every warehouse's history shares one budget: over it, or when the
// browser's storage is full, the oldest runs anywhere go first, so history keeps recording and
// never crowds out the open tabs.
function sqlHistoryAdd(ref, entry) {
  const lists = new Map();
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key?.startsWith(SQL_HISTORY_PREFIX)) lists.set(key, sqlHistoryRead(key));
    }
  } catch { return; /* storage unavailable: no history this session */ }
  const own = sqlHistoryKey(ref);
  lists.set(own, [{ ...entry, sql: entry.sql.slice(0, SQL_HISTORY_SQL), cut: entry.sql.length > SQL_HISTORY_SQL || undefined },
    ...(lists.get(own) || [])].slice(0, SQL_HISTORY_MAX));
  const changed = new Set([own]);
  const size = () => [...lists.values()].reduce((n, l) => n + JSON.stringify(l).length, 0);
  const dropOldest = () => {
    let at = null;
    for (const [key, l] of lists) if (l.length > (key === own ? 1 : 0) && (at === null || (Number(l.at(-1).at) || 0) < (Number(lists.get(at).at(-1).at) || 0))) at = key;
    if (at === null) return false;
    lists.get(at).pop();
    changed.add(at);
    return true;
  };
  while (size() > SQL_HISTORY_BUDGET && dropOldest()) { /* trimmed to the budget */ }
  for (let tries = 0; tries < 500; tries++) {
    try {
      // The lists that shrank are written first, so they make room for the new run.
      for (const key of [...changed].sort((a, b) => (a === own) - (b === own))) {
        const l = lists.get(key);
        if (l.length) localStorage.setItem(key, JSON.stringify(l)); else localStorage.removeItem(key);
      }
      return;
    } catch (err) {
      if (!/quota/i.test(err?.name || "") || !dropOldest()) return;
    }
  }
}

function sqlHistoryClear(ref) {
  try { localStorage.removeItem(sqlHistoryKey(ref)); } catch { /* nothing kept */ }
}

// Fabric's own record of every query on the warehouse, by anyone, from its queryinsights views.
// Each opens in a new tab and runs there, after the usual confirm: it is a query in Fabric.
const SQL_INSIGHTS = [
  ["Long running", "Queries that took longest, by their median time", "SELECT TOP (50) *\nFROM queryinsights.long_running_queries\nORDER BY median_total_elapsed_time_ms DESC;"],
  ["Frequently run", "Queries run most often", "SELECT TOP (50) *\nFROM queryinsights.frequently_run_queries\nORDER BY number_of_runs DESC;"],
  ["Recent requests", "Every request in the last 30 days, newest first", "SELECT TOP (100) *\nFROM queryinsights.exec_requests_history\nORDER BY start_time DESC;"],
];

// The History tab. actions.open(name, sql) opens SQL in a new tab without running it;
// actions.insight(name, sql) opens and runs one of the insights queries.
function whHistory(ref, actions) {
  const box = h("div", { class: "explorer-content wh-queries wh-history" });
  const filter = h("input", { type: "search", class: "wh-filter", placeholder: "Filter by text or table", "aria-label": "Filter history",
    value: sqlPage.whHistoryFilter || "", oninput: (e) => { sqlPage.whHistoryFilter = e.target.value; draw(); } });
  const when = (at) => {
    const d = new Date(at);
    return d.toDateString() === new Date().toDateString() ? timeText(d) : dateText(d);
  };
  // The heading and filter stay in the page; only the list under them is redrawn, so a redraw
  // (each letter typed, a run finishing, the catalog arriving) never takes the focus from the filter.
  const list = h("div");
  box.append(h("div", { class: "wh-q-head" }, "This PC's runs"), filter, list);
  function draw() {
    const term = (sqlPage.whHistoryFilter || "").trim().toLowerCase();
    const all = sqlHistory(ref);
    const shown = all.filter((e) => !term || e.sql.toLowerCase().includes(term));
    const rows = [];
    if (!shown.length) rows.push(h("p", { class: "muted-text wh-note" }, term ? "Nothing matches." : "No runs yet. Each query you run here is kept, newest first."));
    for (const e of shown) {
      const state = SQL_STATE[e.state]?.[1] || e.state;
      const first = e.sql.trim().split("\n").find((l) => l.trim() && !l.trim().startsWith("--")) || e.sql.trim();
      rows.push(h("button", { class: "tree-row wh-h-row", title: e.error ? `${state}: ${e.error}` : "Open in a new tab (doesn't run it)",
        onclick: () => actions.open(`Run at ${when(e.at)}`, e.sql) },
      h("span", { class: "wh-h-sql" }, first.trim()),
      h("span", { class: `wh-h-meta${["failed", "timed_out"].includes(e.state) ? " bad" : ""}` },
        [when(e.at), state, e.seconds != null ? took(e.seconds) : null,
          e.state === "completed" ? `${(e.rows || 0).toLocaleString()} row${e.rows === 1 ? "" : "s"}` : null].filter(Boolean).join(" · "),
        e.change ? h("span", { class: "wh-h-change" }, ` · Change${e.affected != null && e.state === "completed" ? `, ${Number(e.affected).toLocaleString()} affected` : ""}`) : null)));
    }
    if (all.length) rows.push(btn("Clear history", null, async () => {
      if (!(await confirmDialog("Clear history", `Remove the ${all.length} run${all.length === 1 ? "" : "s"} kept for ${ref.name} on this PC? Saved queries and open tabs stay.`, "Clear", { danger: true }))) return;
      sqlHistoryClear(ref);
      sqlPage.whRedraw?.();
    }, "tool outline small wh-h-clear"));
    rows.push(h("p", { class: "muted-text wh-note" }, `Up to ${SQL_HISTORY_MAX} runs, with their SQL, kept in the app on this PC only.`),
      h("div", { class: "wh-q-head" }, "Query insights from Fabric"),
      h("p", { class: "muted-text wh-note" }, `Everyone's queries on ${ref.name}, from its queryinsights views. Each opens in a new tab and runs a query in Fabric (uses capacity).`),
      ...SQL_INSIGHTS.map(([name, about, sql]) => h("button", { class: "tree-row", title: about, onclick: () => actions.insight(name, sql) },
        icon("history", 14), h("span", { class: "label" }, name))));
    list.replaceChildren(...rows);
  }
  draw();
  return { el: box, draw };
}
