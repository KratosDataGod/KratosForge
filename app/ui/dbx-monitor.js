// Monitor > Databricks (Preview): recent job runs and Lakeflow pipeline updates in a Databricks
// workspace, and one run's tasks with the failed task's error. Reading them is GET requests only
// (engine databricks_runs and databricks_run), so nothing starts and nothing bills DBUs. DBUs per
// run come from billing, which Optimize > Cost reads after asking first.
// Mockup: https://claude.ai/artifact/XgpknxChxChHfHTKHKHugD (Jobs)

const dbxMon = { connection: "", mine: true, days: 1, kind: "all", selected: null, detail: null };

function dbxTook(s) {
  if (!s) return "";
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`;
}

function dbxOutcomeClass(o) {
  return o === "failed" ? "fail" : o === "ok" ? "pass" : o === "running" ? "info" : "neu";
}

function databricksRunsPanel() {
  const box = h("div", { class: "dbx-mon" }, loading("Reading Databricks connections…"));
  dbxMonLoad(box);
  return box;
}

async function dbxMonLoad(box) {
  const found = await rpc("engine_connections").catch((err) => ({ connections: [], error: friendlyError(err).text }));
  if (state.view !== "monitor" || monitorPage.panel !== "databricks") return;
  const conns = (found.connections || []).filter((c) => c.host);
  if (!conns.length) {
    box.replaceChildren(h("div", { class: "pad" },
      h("p", {}, found.error || "There's no Databricks connection on this PC yet."),
      h("p", { class: "muted-text small" }, "Sign in with the Databricks CLI, then add it on Settings > Connections."),
      btn("Open Settings > Connections", null, () => { state.settingsTab = "connections"; go("settings"); }, "tool outline")));
    return;
  }
  if (!conns.some((c) => c.key === dbxMon.connection)) dbxMon.connection = conns[0].key;
  const seg = (key, label, options) => h("div", { class: "seg", role: "group", "aria-label": label }, options.map(([value, text]) =>
    h("button", { class: dbxMon[key] === value ? "on" : null, "aria-pressed": String(dbxMon[key] === value),
      onclick: () => { dbxMon[key] = value; dbxMon.selected = null; render(); } }, text)));
  monitorSlots.views?.replaceChildren(
    h("select", { class: "group-select", "aria-label": "Databricks connection", onchange: (e) => { dbxMon.connection = e.target.value; dbxMon.selected = null; render(); } },
      conns.map((c) => h("option", { value: c.key, selected: c.key === dbxMon.connection }, c.label))),
    seg("kind", "Show", [["all", "All"], ["job", "Jobs"], ["pipeline", "Lakeflow pipelines"]]),
    seg("mine", "Whose", [[true, "Mine"], [false, "Everyone"]]),
    seg("days", "Last", [[1, "24 hours"], [7, "7 days"]]));
  box.replaceChildren(loading("Reading runs… nothing starts and nothing bills DBUs."));
  const out = await attempt(() => rpc("databricks_runs", { connection: dbxMon.connection, days: dbxMon.days, mine: dbxMon.mine }),
    "Couldn't read Databricks runs");
  if (state.view !== "monitor" || monitorPage.panel !== "databricks") return;
  if (!out) { box.replaceChildren(h("p", { class: "pad muted-text" }, "The runs couldn't be read.")); return; }
  dbxMonDraw(box, out);
}

function dbxMonDraw(box, out) {
  if (out.signed_in === false || (out.error && !out.rows?.length)) {
    box.replaceChildren(h("div", { class: "callout git-warn pad", role: "alert" }, out.error || "You aren't signed in to this workspace."));
    return;
  }
  const notes = [["jobs", "Job runs"], ["pipelines", "Lakeflow pipelines"]].filter(([k]) => out[k] && !out[k].ok)
    .map(([k, label]) => h("p", { class: "muted-text small dbx-note" }, `${label} not shown: ${out[k].error}`));
  const rows = (out.rows || []).filter((r) => dbxMon.kind === "all" || r.kind === dbxMon.kind);
  const table = rows.length ? h("div", { class: "table-wrap" }, h("table", { class: "list dbx-runs" },
    h("thead", {}, h("tr", {}, ["Name", "Started", "Result", "Took", "Compute", "Trigger"].map((t) => h("th", {}, t)))),
    h("tbody", {}, rows.map((r) => {
      const open = r.kind === "job" ? () => dbxMonOpen(box, out, r) : null;
      return h("tr", { class: `${open ? "click" : ""}${dbxMon.selected === r.id ? " selected" : ""}` },
        h("td", {}, open ? h("button", { class: "tool link", onclick: open }, r.name) : r.name,
          r.kind === "pipeline" ? h("span", { class: "muted-text small" }, " · Lakeflow") : null),
        h("td", {}, r.started ? relTime(r.started) : ""),
        h("td", {}, h("span", { class: `pill-s ${dbxOutcomeClass(r.outcome)}` }, r.result)),
        h("td", {}, dbxTook(r.took_s)), h("td", {}, r.compute), h("td", {}, r.trigger));
    }))))
    : h("p", { class: "pad muted-text" }, `No runs in the last ${dbxMon.days === 1 ? "24 hours" : "7 days"}${dbxMon.mine ? " started by you" : ""}.`);
  box.replaceChildren(h("div", { class: "dbx-cols" },
    h("section", { class: "dbx-list", "aria-label": "Runs" }, notes, table,
      h("p", { class: "muted-text small" }, "DBUs billed per run come from billing, which Optimize > Cost reads after asking first.")),
    dbxMon.detail ? dbxDetail(dbxMon.detail) : null));
}

async function dbxMonOpen(box, out, r) {
  dbxMon.selected = r.id;
  dbxMon.detail = { loading: true, name: r.name };
  dbxMonDraw(box, out);
  const detail = await attempt(() => rpc("databricks_run", { connection: dbxMon.connection, run_id: r.id }), "Couldn't read the run");
  if (dbxMon.selected !== r.id) return;
  dbxMon.detail = detail || null;
  dbxMonDraw(box, out);
}

function dbxDetail(d) {
  if (d.loading) return h("section", { class: "card pad dbx-detail", "aria-label": "Run detail" }, loading(`Reading ${d.name}…`));
  return h("section", { class: "card pad dbx-detail", "aria-label": "Run detail" },
    h("div", { class: "row" }, h("h2", { class: "grow" }, d.name),
      btn("Close", null, () => { dbxMon.detail = null; dbxMon.selected = null; render(); }, "tool outline small")),
    h("p", { class: "muted-text small" }, [d.started ? `Started ${relTime(d.started)}` : "", d.result, dbxTook(d.took_s)].filter(Boolean).join(" · ")),
    h("ol", { class: "dbx-tasks" }, (d.tasks || []).map((t) => h("li", { class: t.outcome === "failed" ? "dbx-failed" : "" },
      h("div", {}, h("strong", {}, t.key), `: ${t.result}`, t.took_s ? `, ${dbxTook(t.took_s)}` : ""),
      t.notebook ? h("div", { class: "muted-text small mono" }, t.notebook) : null,
      t.error ? h("pre", { class: "dbx-error small" }, t.error) : null))),
    Object.keys(d.parameters || {}).length ? h("dl", { class: "about small" },
      Object.entries(d.parameters).flatMap(([k, v]) => [h("dt", {}, k), h("dd", { class: "mono" }, v)])) : null,
    d.url ? btn("Open in Databricks", null, () => openLink(d.url), "tool outline") : null,
    h("p", { class: "muted-text small" }, "Repair run starts compute and bills DBUs, so it comes later with a confirm that says which cluster."));
}
