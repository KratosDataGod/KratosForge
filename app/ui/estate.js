// Looking after the data estate: a table's history with restore, what runs cost in Fabric
// capacity, OneLake shortcuts and access (tabs of the OneLake page), the tests that gate a
// promote, and the VL_Sources variable library.
// Loaded after meta.js and dataops.js. The entity page, Monitor, the OneLake page and the
// Metadata tabs call into this file; everything else here is self-contained.

// replaceChildren, minus the nulls that conditional parts leave (the DOM would print "null").
function fill(el, ...kids) {
  if (el) el.replaceChildren(...kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false));
}

// ---------- history and restore ----------

async function historyDialog(name) {
  const env = state.meta.env;
  const body = h("div", { class: "scroll estate" }, h("p", { class: "muted-text" }, "Reading the table's history..."));
  const overlay = showDialog(`History of ${name} in ${ENV_LABEL[env]}`, body, [btn("Close", null, closeDialog, "tool outline")]);
  overlay.querySelector(".dialog").classList.add("wide");
  if (env !== "dev" && !state.user && !(await signIn())) return;
  // A refusal says why in the dialog, which otherwise kept saying it was reading.
  let hist;
  try { hist = await rpc("meta_table_history", { name, env }); } catch (err) {
    if (body.isConnected) fill(body, errorPanel(err, { label: "Could not read the history", retry: () => historyDialog(name) }));
    return;
  }
  if (!body.isConnected) return;
  if (!hist.versions.length) {
    fill(body, h("p", { class: "muted-text" }, `${name} hasn't been written in ${ENV_LABEL[env]} yet.`));
    return;
  }
  const what = (v) => {
    if (v.restored_to !== null && v.restored_to !== undefined) return `Restored to version ${v.restored_to}`;
    const op = { WRITE: "Write", MERGE: "Merge", DELETE: "Delete", RESTORE: "Restore", OPTIMIZE: "Optimize",
      "VACUUM START": "Vacuum started", "VACUUM END": "Vacuum finished", "SET TBLPROPERTIES": "Settings changed",
      "CREATE TABLE": "Created" }[v.operation] || v.operation || "";
    return v.run_id ? `${op} · run ${v.run_id}` : op;
  };
  fill(body,
    h("p", { class: "muted-text" }, `Every run writes a new version. Versions from the last ${hist.retention_days} days can be restored `,
      `(the ${hist.layer} layer's Keep history). Restoring adds a new version, so it can be undone the same way.`),
    h("table", { class: "list" },
      h("thead", {}, h("tr", {}, ["Version", "When", "What happened", "Rows", ""].map((c) => h("th", {}, c)))),
      h("tbody", {}, hist.versions.map((v) => h("tr", { class: v.maintenance ? "quiet" : null },
        h("td", { class: "mono" }, v.version), h("td", {}, when(v.at)), h("td", {}, what(v)),
        h("td", { class: "n" }, v.rows ?? ""),
        h("td", { class: "n" }, v.current ? h("span", { class: "pill-s pass" }, "Current")
          : v.restorable ? btn("Restore", "history", () => restoreDialog(name, env, v, hist), "tool outline small")
            : h("span", { class: "muted-text small", title: v.why_not || "" }, "Files removed")))))),
    hist.restores.length ? [h("h3", {}, "Earlier restores"),
      hist.restores.map((r) => h("div", { class: "muted-text small" },
        `${relTime(r.at)}: back to version ${r.to_version} by ${r.by || "someone"}${r.reason ? ` (${r.reason})` : ""}`))] : null);
}

function restoreDialog(name, env, v, hist) {
  const reason = h("input", { placeholder: "Bad export from CRM on Monday" });
  const restore = async () => {
    closeDialog();
    toast(`Restoring ${name} to version ${v.version}...`);
    const res = await metaCall("meta_restore", { name, env, version: v.version, reason: reason.value.trim() }, "Not restored");
    if (!res) return;
    toast(`${name} in ${ENV_LABEL[env]} is back to version ${v.version}`, false, { log: true });
    const downstream = res.downstream || [];
    showDialog("Restored", h("div", { class: "scroll" },
      h("div", { class: "pass-box" }, icon("ok", 16), `Version ${res.new_version} now holds what version ${v.version} had.`),
      res.incremental ? h("p", { class: "text" }, `Its watermark moved back to ${res.watermark || "the start"}, so the next run loads again what came after.`) : null,
      downstream.length ? [h("p", { class: "text" }, `These tables read it and still hold what they took from the bad load. Run them to pick up the restored data; an incremental one needs Rebuild with downstream on its own page.`),
        h("div", { class: "row wrap" }, downstream.map((d) => h("button", { class: "node", onclick: () => { closeDialog(); openEntity(d); } }, entityName(d))))]
        : h("p", { class: "muted-text" }, "Nothing reads this table.")),
    [btn("Close", null, () => { closeDialog(); render(); }, "tool outline"),
      downstream.length ? btn("Run downstream", "play", () => { closeDialog(); runEntities(downstream); }, "tool primary") : null].filter(Boolean));
  };
  showDialog(`Restore ${name} to version ${v.version}?`, h("div", { class: "scroll" },
    h("p", { class: "text" }, `${name} in ${ENV_LABEL[env]} goes back to how it was ${dateTimeText(v.at, { empty: `at version ${v.version}` })}. `,
      "Later versions stay in its history, so you can restore one of them again."),
    hist.incremental ? h("p", { class: "text" }, "It loads incrementally, so its watermark moves back too and the next run picks up what came after.") : null,
    hist.downstream.length ? h("p", { class: "muted-text" }, `Tables that read it aren't changed: ${hist.downstream.join(", ")}.`) : null,
    h("label", { class: "field" }, "Why (kept with the restore)", reason)),
  [btn("Cancel", null, () => historyDialog(name), "tool outline"), btn("Restore", "history", restore, "tool primary")]);
}

// The entity lineage's file source, when the files come through a OneLake shortcut.
function shortcutLineage(e) {
  if (e.reads.length) return null;
  const slot = h("span", {});
  rpc("meta_entity_shortcuts", { name: e.name, env: state.meta.env }).then((list) => {
    if (!slot.isConnected || !list?.length) return;
    slot.replaceWith(...list.map((s) => h("span", { class: "node plain", title: `${s.full_path} points at ${s.target_type}: ${s.target}` },
      icon("databaseLink", 14), ` via shortcut ${s.name}`, h("span", { class: "muted-text small" }, ` → ${s.target_type}`))));
  }).catch(() => {});
  return slot;
}

// ---------- tests that gate promote ----------

async function renderTestsTab(body) {
  const m = state.meta;
  fill(body, h("p", { class: "muted-text" }, "Reading the tests..."));
  let t = await metaCall("meta_tests", { run: false });
  if (!t || state.view !== "metadata" || m.tab !== "tests") return;
  if (!t.at && t.files.length) t = (await metaCall("meta_tests", { run: true })) || t;
  if (state.view !== "metadata" || m.tab !== "tests") return;
  const run = async () => {
    body.querySelector(".tests-summary")?.replaceChildren(h("span", { class: "muted-text" }, "Running the tests..."));
    const res = await metaCall("meta_tests", { run: true }, "Tests didn't run");
    if (res) draw(res);
  };
  const draw = (r) => {
    const failed = r.tests.filter((x) => x.status !== "passed");
    fill(body,
      h("div", { class: "row tests-summary" },
        r.at ? (failed.length ? h("span", { class: "pill-s fail" }, `${failed.length} failing`) : h("span", { class: "pill-s pass" }, "All passing")) : null,
        h("span", { class: "muted-text" }, r.at ? `${r.passed} passed, ${r.failed} failed · run ${relTime(r.at)} in ${took(r.seconds)}` : "Not run yet"),
        r.stale ? h("span", { class: "pill-s wrn", title: "The project changed since this run" }, "Out of date") : null,
        h("span", { class: "spacer" }), btn("Run tests", "beaker", run, "tool primary")),
      r.missing.length ? h("div", { class: "problems" }, h("b", {}, "Custom code points at tests that don't exist"), gateList(r.missing)) : null,
      r.tests.length ? h("table", { class: "list" },
        h("thead", {}, h("tr", {}, ["", "Test", "Checks the code of", "Took", ""].map((c) => h("th", {}, c)))),
        h("tbody", {}, r.tests.map((x) => h("tr", {},
          h("td", {}, h("span", { class: `pill-s ${x.status === "passed" ? "pass" : "fail"}` }, x.status === "passed" ? "Passed" : x.status === "failed" ? "Failed" : "Error")),
          h("td", {}, h("div", { class: "mono" }, x.name), h("div", { class: "muted-text small mono" }, x.file),
            x.message ? h("div", { class: "reason" }, x.message) : null),
          h("td", {}, x.used_by.length ? x.used_by.map((u) => h("div", { class: "small" }, u)) : h("span", { class: "muted-text small" }, "Shared helpers")),
          h("td", { class: "n" }, took(x.seconds)), h("td", {})))))
        : h("p", { class: "muted-text" }, "No tests yet. Put test_*.py files in the project's tests folder, with test_ functions that assert what your code does."),
      h("div", { class: "info-cards" },
        infoCard("Promote runs them first", "Dev can't be promoted to Test, or Test to Prod, while a test fails. Every hook, custom check and custom step names its test, so code without one is caught at save."),
        infoCard("Plain Python", "A test is a function named test_something that uses assert, as in pytest. It can take tmp_path for a scratch folder; other pytest fixtures aren't available here."),
        infoCard("Your transforms, fresh", "Each run imports transforms/ and tests/ again, so it always tests what you just saved.")));
  };
  draw(t);
}

// ---------- VL_Sources variable library ----------

function varlibCard() {
  const card = h("div", { class: "card pad estate" }, h("b", {}, "Variable library VL_Sources"),
    h("p", { class: "muted-text small" }, "Reading the source systems..."));
  const load = () => rpc("meta_varlib").then((v) => { if (card.isConnected) fill(card, ...varlibBody(v, load)); })
    .catch((err) => fill(card, h("b", {}, "Variable library VL_Sources"), h("p", { class: "muted-text" }, friendlyError(err).text)));
  load();
  return card;
}

function varlibBody(v, reload) {
  const envs = ["dev", "test", "prod"];
  const first = v.variables.find((x) => x.name.includes("_") && x.name !== "ENVIRONMENT" && !x.name.startsWith("KEYS_"));
  const snippet = `lib = notebookutils.variableLibrary.getLibrary("${v.library}")\nlib.${first ? first.name : "CRM_server_host"}`;
  const publish = async (env) => {
    if (!state.user && !(await signIn())) return;
    toast(`Publishing ${v.library} to ${ENV_LABEL[env]}...`);
    const res = await metaCall("meta_publish_varlib", { env }, "Not published");
    if (!res) return;
    toast(`${v.library} ${res.created ? "created" : "updated"} in ${res.workspace_name || ENV_LABEL[env]}, with ${res.value_set} active`, false, { log: true });
    reload();
  };
  const cell = (x, env) => env !== "dev" && x[env] === x.dev ? h("td", { class: "muted-text small" }, "same as Dev")
    : h("td", { class: "mono small" }, x[env] || h("span", { class: "muted-text" }, "empty"));
  return [
    h("div", { class: "row wrap" }, previewMarked("varlib", h("b", { class: "grow" }, `Variable library ${v.library}`)),
      envs.map((env) => {
        const ws = v.environments[env];
        return btn(`Publish to ${ENV_LABEL[env]}`, "publish", () => publish(env), "tool outline small",
          { disabled: ws.workspace_id ? null : true,
            title: ws.workspace_id ? `${ws.workspace_name || ws.workspace_id}, with ${ws.value_set} active${v.published[env] ? ` · last published ${dateTimeText(v.published[env])}` : ""}`
              : `Assign a workspace to ${ENV_LABEL[env]} first` });
      })),
    h("p", { class: "muted-text small" }, "One Fabric variable library per environment workspace, with that environment's connection values active. ",
      "Promote updates it in the target. Notebooks read the same names in Fabric and on this PC:"),
    h("pre", { class: "sql-block" }, snippet),
    h("table", { class: "list compact" },
      h("thead", {}, h("tr", {}, h("th", {}, "Variable"), envs.map((env) => h("th", {}, ENV_LABEL[env])))),
      h("tbody", {}, v.variables.map((x) => h("tr", {},
        h("td", {}, h("div", { class: "mono" }, x.name), h("div", { class: "muted-text small" }, x.note)),
        envs.map((env) => cell(x, env)))))),
  ];
}

// ---------- cost (a panel of the Monitor page) ----------

const costPage = { days: 30 };

function money(value, currency) {
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency: currency || "USD", maximumFractionDigits: value < 10 ? 2 : 0 }).format(value);
  } catch {
    return `${Number(value).toFixed(2)} ${currency || ""}`;
  }
}

function cuHours(n) {
  return `${n < 10 ? Number(n).toFixed(2) : Math.round(n).toLocaleString()} CU h`;
}

function costPanel() {
  const panel = h("div", { class: "monitor-scroll estate cost" }, h("p", { class: "muted-text monitor-empty" }, "Adding up the runs..."));
  rpc("cost_report", { days: costPage.days }).then((r) => { if (panel.isConnected) fill(panel, ...costBody(r)); })
    .catch((err) => fill(panel, h("p", { class: "muted-text monitor-empty" }, friendlyError(err).text)));
  return panel;
}

// Every CU and cost figure is a classified measurement (sidecar measurement.py): measured,
// estimated, scenario or unknown. The badge says which, and clicking the figure shows how it was worked out.
const MEASURE_KIND = {
  measured: ["Measured", "pass", "Read from a source that observed it."],
  estimated: ["Estimated", "wrn", "Worked out from the assumptions below, not read from Fabric's billing."],
  scenario: ["Scenario", "cus", "What it would have used if it had run differently. Nothing was charged or saved for certain."],
  unknown: ["Unknown", "neu", "Not enough reliable information. It is left out of totals, not counted as 0."],
};

function measureBadge(m) {
  const [label, cls, why] = MEASURE_KIND[m?.classification] || MEASURE_KIND.unknown;
  const badge = h("span", { class: `pill-s ${cls}`, title: why }, label);
  return m?.complete === false && m.classification !== "unknown"
    ? [badge, h("span", { class: "pill-s neu", title: m.reason || "Some runs' figures aren't known" }, "Incomplete")] : badge;
}

function measureValue(m, cur) {
  if (!m || m.value === null || m.value === undefined) return "Not known";
  if (m.unit === "currency") return m.value && m.value < 0.01 ? `under ${money(0.01, m.currency || cur)}` : money(m.value, m.currency || cur);
  if (m.unit === "cu_hours") return cuHours(m.value);
  if (m.unit === "cu_seconds") return `${Math.round(m.value).toLocaleString()} CU-s`;
  if (m.unit === "percent") return `${m.value < 0.1 && m.value > 0 ? "<0.1" : Number(m.value).toFixed(1)}%`;
  return String(m.value);
}

// A figure with its badge; click it to see how it was worked out.
function measureFigure(title, m, cur) {
  return h("button", { class: "measure-fig", title: "How this was worked out", onclick: (ev) => { ev.stopPropagation(); measureDialog(title, m, cur); } },
    measureValue(m, cur), " ", measureBadge(m));
}

function measureDialog(title, m, cur) {
  const [label, , why] = MEASURE_KIND[m?.classification] || MEASURE_KIND.unknown;
  const list = (items) => items && items.length ? h("ul", { class: "text" }, items.map((x) => h("li", {}, x))) : null;
  const methods = m.methods || (m.method ? [m.method] : []);
  const sources = m.sources || (m.source ? [m.source] : []);
  const row = (k, v) => v ? h("tr", {}, h("td", { class: "muted-text" }, k), h("td", {}, v)) : null;
  const period = m.period && (m.period.start || m.period.end)
    ? `${dateTimeText(m.period.start, { empty: "?" })} to ${dateTimeText(m.period.end, { empty: "now" })}` : "";
  showDialog(title, h("div", { class: "scroll" },
    h("p", { class: "text" }, h("b", {}, measureValue(m, cur)), " ", measureBadge(m), " ", why),
    h("table", { class: "list compact" }, h("tbody", {},
      row("Classification", label),
      row("Unit", { cu_hours: "CU hours (capacity use)", cu_seconds: "CU-seconds (capacity use)", percent: "Percent of a capacity over the period", currency: m.metric === "billed_cost" ? `Money (${m.currency}), as billed` : `Money (${m.currency || cur}), a price applied to CU use` }[m.unit]),
      row("Worked out as", methods.join("; ")),
      row("Source", sources.join(", ")),
      row("Period", period),
      row("Runs counted", m.parts !== undefined ? `${m.parts - (m.unknown_parts || 0)} of ${m.parts}` : ""),
      row("Not known", m.reason || ""))),
    m.assumptions?.length ? [h("p", { class: "text" }, h("b", {}, "Assumptions")), list(m.assumptions)] : null,
    h("p", { class: "muted-text small" }, String(m.metric || "").startsWith("billed_")
      ? "Azure Cost Management bills a capacity per meter and day, for the whole capacity: it can't be split into single runs."
      : "Fabric's Capacity Metrics app has the CU your capacity recorded. A price is not a charge: on a reserved or already-running capacity the invoice doesn't change with each run.")),
  [btn("Close", null, closeDialog, "tool outline")]);
}

// ---------- capacity of each environment (FINOPS-002, read only) ----------

// Running, not running or unknown; or why it couldn't be read.
function capacityStatus(e) {
  if (e.access !== "ok") return h("span", { class: "pill-s neu", title: e.action || "" }, e.access_text);
  const [label, cls] = { running: ["Running", "pass"], not_running: ["Not running", "fail"] }[e.status] || ["Unknown", "neu"];
  return h("span", { class: `pill-s ${cls}`, title: e.status_text }, label);
}

function capacityName(e) {
  const c = e.capacity;
  if (!c) return e.capacity_id ? h("span", { class: "mono small", title: "Capacity id from the workspace" }, e.capacity_id) : null;
  return h("span", {}, h("b", {}, c.name || c.id), [c.sku, c.region || e.region].filter(Boolean).length
    ? h("span", { class: "muted-text" }, ` · ${[c.sku, c.region || e.region].filter(Boolean).join(" · ")}`) : null);
}

function capacityAge(e) {
  if (!e.observed_at) return null;
  return h("span", { class: "muted-text small" }, "Read ", when(e.observed_at), e.cached ? ` (${e.read_error.toLowerCase()} now)` : "",
    e.stale ? [" ", h("span", { class: "pill-s wrn", title: "Older than an hour: press Refresh" }, "Stale")] : null);
}

// One line for an environment card: the capacity and whether it is running.
function capacityLine(e) {
  if (!e || e.access === "no_workspace") return null;
  return h("div", { class: "capacity-line" }, icon("gauge", 14), h("span", { class: "muted-text small" }, "Capacity"),
    capacityName(e), capacityStatus(e), capacityAge(e));
}

function capacityStrip(doc) {
  const rows = (doc?.environments || []).filter((e) => e.access !== "no_workspace");
  const refresh = async (button) => {
    button.disabled = true;
    const res = await attempt(() => rpc("capacity_health", { refresh: true }), "Capacities not read");
    if (res) render();
    button.disabled = false;
  };
  if (!rows.length) return null;
  return h("div", { class: "card pad capacity-strip" },
    h("div", { class: "row" }, h("b", { class: "grow" }, "Capacities"),
      h("span", { class: "muted-text small" }, "Read only, with your sign-in. Running doesn't mean there's room: how busy a capacity is shows in Fabric's Capacity Metrics app."),
      btn("Refresh", "sync", (ev) => refresh(ev.currentTarget), "tool outline small")),
    h("table", { class: "list compact" }, h("tbody", {}, rows.map((e) => h("tr", {},
      h("td", {}, h("b", {}, ENV_LABEL[e.env] || e.env), h("div", { class: "muted-text small" }, e.workspace_name || "")),
      h("td", {}, capacityName(e) || h("span", { class: "muted-text" }, "Not known")),
      h("td", {}, capacityStatus(e)),
      h("td", { class: "small" }, e.access === "ok" ? e.status_text : e.action, e.cached ? h("div", { class: "muted-text" }, e.action) : null),
      h("td", {}, capacityAge(e)))))));
}

// ---------- measured usage imported from Capacity Metrics app and Azure Cost Management exports (FINOPS-003) ----------

const USAGE_STATE = {
  matched: ["Matched by name and time", "pass", "Imported rows with this run's workspace and item name that started while it ran. Likely this run's use, not proven: names and times are all an export has."],
  no_row: ["No row", "wrn", "An import covers this workspace and time, but no row matched: the export may be sampled or filtered."],
  not_covered: ["Not covered", "neu", "No import covers this run's workspace and time."],
  no_item_name: ["Can't match", "neu", "This kind of run doesn't record the item it ran, so it can't be matched."],
};

function usageCard(u, cur) {
  if (!u) return null;
  const imports = u.imports_list || [];
  const importBtn = btn("Import usage file", "attach", () => usageImportDialog(), "tool outline small");
  const tag = preview("usage", h("span", { class: "usage-tag" }));
  if (!tag) return null;
  const head = h("div", { class: "row" }, h("b", {}, "Measured usage"), tag, h("span", { class: "spacer" }), importBtn);
  if (u.error) return h("div", { class: "card pad usage-card" }, head, h("p", { class: "error-text" }, u.error));
  if (!imports.length) {
    return h("div", { class: "card pad usage-card" }, head,
      h("p", { class: "muted-text small" }, "The figures above are estimates. For measured CU, a capacity admin exports the operations table from the Capacity Metrics app's Timepoint page (More options > Export data > CSV) and gives you the file. For what a capacity was billed, finance can export Azure Cost Management cost details as CSV. Files stay on this PC; no admin rights are needed to import them."));
  }
  const m = u.matched, runs = u.runs;
  if (!u.rows && u.billed?.length) {
    return h("div", { class: "card pad usage-card" }, head, usageBilled(u.billed, cur), usageImportsList(imports));
  }
  const stat = (figure, label, sub) => h("div", { class: "cmp-stat" }, h("span", { class: "v" }, figure), h("span", { class: "k" }, label),
    sub ? h("span", { class: "k" }, sub) : null);
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const reasons = Object.entries(u.unmatched.by_reason || {}).map(([k, n]) => `${k}: ${n}`).join("\n");
  const matchedRuns = (u.by_run || []).filter((b) => b.state === "matched");
  return h("div", { class: "card pad usage-card" }, head,
    h("div", { class: "cmp-stats" },
      stat(u.imported ? measureFigure("Measured CU in your imports", u.imported, cur) : "None", "Measured CU in your imports",
        `${plural(u.rows, "row")} in the last ${(Date.parse(u.period.end) - Date.parse(u.period.start)) / 864e5 | 0} days`),
      stat(m.measured ? measureFigure("This app's runs, measured", m.measured, cur) : "None",
        "This app's runs, measured", `${m.runs} of ${plural(runs.total, "Fabric run")} matched`),
      m.estimated ? stat(measureFigure("The same runs, estimated", m.estimated, cur), "The same runs, estimated",
        m.measured_to_estimate ? `measured is ${Number(m.measured_to_estimate).toFixed(1)}× the estimate` : null) : null,
      stat(u.unmatched.measured ? measureFigure("Other use in the imports", u.unmatched.measured, cur) : "None",
        h("span", { title: reasons }, "Other use in the imports"), `${plural(u.unmatched.rows, "row")} not from this app's runs`)),
    runs.no_row || runs.not_covered || runs.no_item_name ? h("p", { class: "muted-text small" },
      [runs.no_row ? `${plural(runs.no_row, "run")} inside an import had no matching row (exports can be sampled or filtered).` : "",
        runs.not_covered ? `${plural(runs.not_covered, "run")} aren't covered by any import.` : "",
        runs.no_item_name ? `${plural(runs.no_item_name, "run")} can't be matched.` : ""].filter(Boolean).join(" ")) : null,
    matchedRuns.length ? h("table", { class: "list compact" },
      h("thead", {}, h("tr", {}, ["Run", "Started", "Measured", "Estimated"].map((c) => h("th", {}, c)))),
      h("tbody", {}, matchedRuns.slice(0, 20).map((b) => h("tr", {},
        h("td", {}, h("b", {}, b.name || b.item), h("div", { class: "muted-text small" }, `${b.kind} · ${b.workspace || ""}`)),
        h("td", {}, when(b.started_at)),
        h("td", { class: "n" }, measureFigure(`${b.name}: measured`, b.measured, cur)),
        h("td", { class: "n" }, measureFigure(`${b.name}: estimated`, b.estimated, cur)))))) : null,
    usageBilled(u.billed, cur),
    usageImportsList(imports));
}

function usageImportsList(imports) {
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  return h("details", { class: "usage-imports" }, h("summary", { class: "small" }, `${plural(imports.length, "import")} on this PC`),
    h("table", { class: "list compact" }, h("tbody", {}, imports.map((i) => h("tr", {},
      h("td", {}, h("b", {}, i.file_name), h("div", { class: "muted-text small" }, `${i.provider_label} · ${i.layout_label}`)),
      h("td", { class: "small" }, i.period?.start ? `${dateText(i.period.start)} to ${dateText(Math.max(Date.parse(i.period.start), Date.parse(i.period.end || i.period.start) - 1))}` : ""),
      h("td", { class: "n" }, plural(i.rows, "row")),
      h("td", { class: "small muted-text" }, "Imported ", when(i.imported_at)),
      h("td", {}, btn("Remove", "delete", async () => {
        if (!(await confirmDialog(`Remove ${i.file_name}?`, "Its rows are removed from this PC. Your exported file isn't touched, and you can import it again.", "Remove", { danger: true }))) return;
        if (await attempt(() => rpc("usage_import_delete", { import_id: i.id }), "Not removed")) render();
      }, "tool outline small")))))));
}

// What each capacity was billed, from Azure Cost Management exports: for the whole capacity, never per run.
function usageBilled(billed, cur) {
  if (!billed?.length) return null;
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const day = (d) => dateText(`${d}T00:00:00Z`, { utc: true });
  return h("div", { class: "usage-billed" },
    h("p", { class: "text" }, h("b", {}, "Billed per capacity"), h("span", { class: "muted-text small" }, " · from Azure Cost Management, for the whole capacity")),
    h("table", { class: "list compact" },
      h("thead", {}, h("tr", {}, ["Capacity", "Days", "Compute billed", "Cost"].map((c) => h("th", {}, c)))),
      h("tbody", {}, billed.map((b) => h("tr", {},
        h("td", {}, h("b", {}, b.capacity), h("div", { class: "muted-text small" },
          b.environments.length ? `Used by ${b.environments.join(", ")}` : "Not one of your environments' capacities")),
        h("td", { class: "small" }, `${plural(b.days, "day")}`, h("div", { class: "muted-text small" }, b.first_day === b.last_day ? day(b.first_day) : `${day(b.first_day)} to ${day(b.last_day)}`)),
        h("td", { class: "n" }, b.cu ? measureFigure(`${b.capacity}: compute billed`, b.cu, cur) : h("span", { class: "muted-text", title: "No compute meter billed in hours in these rows" }, "None")),
        h("td", { class: "n" }, b.cost.map((c) => h("div", {}, measureFigure(`${b.capacity}: billed cost (${c.currency})`, c, cur)))))))));
}

function usageImportDialog() {
  const state = { name: "", text: "", options: { tz: "UTC" } };
  const out = h("div", { class: "usage-preview" });
  const input = h("input", { type: "file", accept: ".csv,text/csv" });
  const tz = h("input", { value: "UTC", size: 8, title: "The export doesn't say its time zone. UTC, or an offset such as +02:00." });
  let shown = null, seq = 0;  // the options of the preview the dialog shows, which Import sends
  const go = btn("Import", "save", async () => {
    const res = await attempt(() => rpc("usage_import", { file_name: state.name, content: state.text, options: shown }), "Not imported");
    if (res) { closeDialog(); toast(`Imported ${res.rows} row${res.rows === 1 ? "" : "s"} of measured usage`); render(); }
  }, "tool primary");
  go.disabled = true;
  const check = async () => {
    go.disabled = true;
    if (!state.text) return;
    state.options.tz = tz.value.trim() || "UTC";
    fill(out, h("p", { class: "muted-text" }, "Checking the file..."));
    const n = ++seq, options = { ...state.options };
    const p = await attempt(() => rpc("usage_preview", { file_name: state.name, content: state.text, options }), "Not read");
    if (n !== seq) return;  // a newer preview, of the options chosen since, answers instead
    shown = options;
    if (!p) { fill(out); return; }
    fill(out, ...usagePreview(p, (opt, value) => { Object.assign(state.options, opt === "period" ? value : { [opt]: value }); check(); }));
    go.disabled = !p.ready;
  };
  input.onchange = async () => {
    const f = input.files[0];
    if (!f) return;
    if (f.size > 64 * 1024 * 1024) { fill(out, h("p", { class: "error-text" }, "The file is larger than 64 MB. Export a shorter period.")); return; }
    state.name = f.name; state.text = await f.text(); state.options = { tz: tz.value.trim() || "UTC" };
    check();
  };
  tz.onchange = check;
  showDialog("Import measured usage", h("div", { class: "scroll" },
    h("p", { class: "text" }, "A CSV exported from the Capacity Metrics app (the Timepoint page's operations table with Total CU (s), or the Compute page's items matrix with CU (s)), or from Azure Cost Management (cost details with Actual cost, or FOCUS). A capacity admin or finance can export it and give it to you. The file is read on this PC and kept here; nothing is sent anywhere."),
    h("div", { class: "form-grid" }, h("label", { class: "field" }, "File", input), h("label", { class: "field" }, "Times are in", tz)),
    out),
  [btn("Cancel", null, closeDialog, "tool outline"), go]);
}

// What importing the file would add, and anything the person has to choose first.
function usagePreview(p, choose) {
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const row = (k, v) => v ? h("tr", {}, h("td", { class: "muted-text" }, k), h("td", {}, v)) : null;
  const need = p.needs;
  let ask = null;
  if (need && need.option === "period") {
    const from = h("input", { type: "date" }), to = h("input", { type: "date" });
    ask = h("div", { class: "row wrap" }, h("label", { class: "field" }, "From", from), h("label", { class: "field" }, "To", to),
      btn("Use this period", "ok", () => choose("period", { period_start: from.value, period_end: to.value }), "tool outline small"));
  } else if (need) {
    ask = h("div", { class: "row wrap" }, Object.entries(need.choices).map(([v, label]) => btn(label, null, () => choose(need.option, v), "tool outline small")));
  }
  const cols = p.columns;
  return [
    p.problem ? h("p", { class: p.ready ? "muted-text" : "error-text" }, p.problem) : null,
    ask,
    p.provider ? h("table", { class: "list compact" }, h("tbody", {},
      row("Recognized as", `${p.provider.label}, ${p.layout.label}`),
      cols ? row("Columns used", Object.values(cols.used).join(", ")) : null,
      cols?.not_stored?.length ? row("Not stored", cols.not_stored.join(", ")) : null,
      cols?.ignored?.length ? row("Not used", cols.ignored.filter((c) => !cols.not_stored.includes(c)).join(", ")) : null,
      p.rows_read !== undefined ? row("Rows", `${p.rows_read} read · ${p.to_import} to import` +
        (p.already_imported ? ` · ${p.already_imported} already imported` : "")
          + (p.replaces_earlier ? ` · ${p.replaces_earlier} for days an earlier import also has (the export that reaches the later day counts)` : "") + (p.duplicates_in_file ? ` · ${p.duplicates_in_file} repeated in the file` : "")) : null,
      p.period?.start ? row("Period", `${dateTimeText(p.period.start)} to ${dateTimeText(p.period.end || p.period.start)}`) : null,
      p.workspaces?.length ? row("Workspaces", p.workspaces.join(", ")) : null,
      p.capacities?.length ? row("Capacities", p.capacities.join(", ")) : null,
      p.total && p.to_import ? row(p.provider.id === "cost_management" ? "Compute billed" : "Measured CU", measureFigure("Measured CU in this file", p.total)) : null,
      p.cost?.length && p.to_import ? row("Billed cost", h("span", {}, p.cost.map((c) => measureFigure(`Billed cost in this file (${c.currency})`, c)))) : null)) : null,
    p.skipped?.length ? h("div", {}, h("p", { class: "text" }, h("b", {}, `${plural(p.skipped.reduce((a, s) => a + s.count, 0), "row")} skipped`)),
      h("ul", { class: "text" }, p.skipped.map((s) => h("li", {}, `${s.reason}: ${s.count} (line${s.lines.length === 1 ? "" : "s"} ${s.lines.join(", ")}${s.count > s.lines.length ? ", ..." : ""})`)))) : null,
    p.notes?.length ? h("ul", { class: "muted-text small" }, p.notes.map((n) => h("li", {}, n))) : null,
  ];
}

function costBody(r) {
  const cur = r.settings.currency;
  const stat = (figure, label, sub) => h("div", { class: "cmp-stat" }, h("span", { class: "v" }, figure), h("span", { class: "k" }, label),
    sub ? h("span", { class: "k" }, sub) : null);
  const t = r.total;
  const ev = t.evidence;
  const daysChip = (d) => h("button", { class: `chip${costPage.days === d ? " on" : ""}`, onclick: () => { costPage.days = d; render(); } }, `${d} days`);
  const unavailable = Object.entries(r.unavailable || {});
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  return [
    h("div", { class: "row wrap cost-head" }, daysChip(7), daysChip(30), daysChip(90), h("span", { class: "spacer" }),
      h("span", { class: "muted-text small" }, `${money(r.settings.price_per_cu_hour, cur)} per CU hour · Python ${r.settings.python_cu} CU · Spark ${r.settings.spark_cu} CU`),
      btn("Prices", "gauge", () => costSettingsDialog(r.settings), "tool outline small")),
    h("div", { class: "cmp-stats" },
      stat(measureFigure("Fabric capacity used", ev.fabric_cost, cur), "Fabric capacity used",
        h("span", {}, measureFigure("Fabric capacity used, in CU hours", ev.fabric_job_cu, cur), ` · ${plural(t.fabric_runs, "run")}`)),
      stat(measureFigure("Fabric use avoided on this PC", ev.avoided_cost, cur), "Fabric use avoided on this PC",
        h("span", {}, measureFigure("Fabric use avoided on this PC, in CU hours", ev.avoided_cu, cur), ` · ${plural(t.local_runs, "run")}`)),
      r.by_env.filter((e) => e.capacity_share_evidence && e.capacity_share_evidence.classification !== "unknown" && e.fabric_runs).map((e) =>
        stat(measureFigure(`Share of ${e.capacity || "the"} capacity`, e.capacity_share_evidence, cur), `of ${e.capacity || "the"} capacity (${e.sku})`, `${ENV_LABEL[e.env] || e.env}'s Fabric runs`))),
    capacityStrip(r.capacities),
    usageCard(r.usage, cur),
    t.uncalibrated_runs ? h("p", { class: "muted-text small" }, `${t.uncalibrated_runs === 1 ? "1 run" : `${t.uncalibrated_runs} runs`} on this PC had no comparable Fabric run of the same table (same definition, last 90 days), so what ${t.uncalibrated_runs === 1 ? "it" : "they"} avoided isn't known and is left out, not counted as 0.`) : null,
    t.unknown_runs ? h("p", { class: "muted-text small" }, `${t.unknown_runs === 1 ? "1 run's" : `${t.unknown_runs} runs'`} capacity use isn't known (still running, or no run time recorded). They are left out of the totals, not counted as 0.`) : null,
    r.by_item.length ? h("table", { class: "list" },
      h("thead", {}, h("tr", {}, ["What ran", "Env", "Runs", "Time", "Fabric", "Avoided on this PC"].map((c) => h("th", {}, c)))),
      h("tbody", {}, r.by_item.slice(0, 60).map((x) => h("tr", {},
        h("td", {}, h("b", {}, x.name || x.kind), h("div", { class: "muted-text small" }, x.kind)),
        h("td", {}, ENV_LABEL[x.env] || x.env || ""),
        h("td", { class: "n" }, x.runs), h("td", { class: "n" }, x.seconds ? took(x.seconds) : "Not known"),
        h("td", { class: "n" }, x.fabric_runs ? measureFigure(`${x.name || x.kind}: Fabric capacity used`, x.evidence.fabric_cost, cur) : ""),
        h("td", { class: "n" }, x.local_runs ? measureFigure(`${x.name || x.kind}: Fabric use avoided on this PC`, x.evidence.avoided_cost, cur) : "")))))
      : h("p", { class: "muted-text" }, `Nothing ran in the last ${r.days} days.`),
    h("p", { class: "muted-text small" }, "Estimated: run time times the capacity units of what it ran on. A Python notebook (KratosForge's runner) uses 1 CU per 2 vCores; Spark jobs such as table maintenance use the Spark figure. ",
      "Avoided on this PC: estimated from the same table's runs in Fabric (their run time times the configured CUs), never from how long it took here. OneLake reads and writes from this PC still use capacity and aren't counted. Click a figure to see how it was worked out; Fabric's Capacity Metrics app has the CU your capacity recorded.",
      unavailable.length ? ` Left out: ${unavailable.map(([env, why]) => `${ENV_LABEL[env]} (${why})`).join(", ")}.` : ""),
  ];
}

async function costSettingsDialog(cfg) {
  const risk = await rpc("risk_settings").catch(() => null);
  const before = risk ? riskSettingsFields(risk) : null;
  const field = (label, key, attrs) => {
    const input = h("input", { value: cfg[key], ...attrs });
    return [h("label", { class: "field" }, label, input), () => input.value];
  };
  const [price, getPrice] = field("Price of one CU hour", "price_per_cu_hour", { type: "number", min: "0.001", step: "0.001" });
  const [currency, getCurrency] = field("Currency", "currency", { maxlength: "5" });
  const [py, getPy] = field("CUs of a Python notebook (2 vCores = 1 CU)", "python_cu", { type: "number", min: "0.5", step: "0.5" });
  const [spark, getSpark] = field("CUs of a Spark job (starter pool, 2 medium nodes = 8 CU)", "spark_cu", { type: "number", min: "0.5", step: "0.5" });
  showDialog(before ? "Prices and warnings" : "Cost estimate", h("div", { class: "scroll" },
    h("p", { class: "text" }, "Pay-as-you-go is about $0.18 per CU hour in US regions; a reserved capacity costs less. Use your own rate."),
    h("div", { class: "form-grid" }, price, currency, py, spark),
    before ? before.el : null),
  [btn("Cancel", null, closeDialog, "tool outline"), btn("Save", "save", async () => {
    const res = await attempt(() => rpc("set_cost_settings", { price_per_cu_hour: getPrice(), currency: getCurrency(), python_cu: getPy(), spark_cu: getSpark() }), "Not saved");
    if (res && before && !(await attempt(() => rpc("set_risk_settings", before.values()), "Warnings not saved"))) return;
    if (res) { closeDialog(); render(); }
  }, "tool primary")]);
}

// ---------- OneLake page: the lakehouses the Shortcuts and Access tabs work on ----------

async function onelakeCatalog() {
  if (catalogPage.data) return catalogPage.data;
  const data = await attempt(() => rpc("catalog", { all_types: true }), "Could not list the catalog");
  if (data) { catalogPage.data = data; state.workspaces = data.workspaces; }
  return data;
}

function lakehouseRows(data) {
  return data.items.filter((r) => r.type === "Lakehouse")
    .sort((a, b) => `${a.workspace_name} ${a.name}`.localeCompare(`${b.workspace_name} ${b.name}`, undefined, { sensitivity: "base" }));
}

// The Shortcuts tab itself, the shortcut command center, is in shortcuts.js.

// ---------- OneLake page: Access tab ----------

const accessPage = { extra: [], data: null, lakehouse: null, lh: null, view: "all" };
// All workspaces reads the access cache (access-cache.js); the other two ask Fabric directly.
const ACCESS_VIEWS = [["all", "All workspaces"], ["roles", "Environments"], ["tables", "Table access"]];

async function renderAccess() {
  const ap = accessPage;
  const view = ACCESS_VIEWS.some(([id]) => id === ap.view) ? ap.view : "all";
  const people = h("div", { class: "estate" }, h("p", { class: "muted-text" }, state.user ? "Reading workspace roles..." : ""));
  const lhSlot = h("div", { class: "estate" });
  const allSlot = h("div", { class: "estate" }, h("p", { class: "muted-text" }, state.user ? "Reading the access cache..." : ""));
  const seg = h("div", { class: "seg", role: "group", "aria-label": "Show" }, ACCESS_VIEWS.map(([id, label]) =>
    h("button", { class: view === id ? "on" : null, onclick: () => { ap.view = id; render(); } }, label)));
  const body = view === "all" ? allSlot
    : view === "roles" ? h("div", { class: "card pad" }, h("h2", { class: "flush" }, "Workspace roles"), people)
      : h("div", { class: "card pad" }, h("h2", { class: "flush" }, "Table access in a lakehouse"), lhSlot);
  setView(h("div", { class: "page" }, signInCallout(), onelakeTabs(),
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Access"),
        h("p", { class: "sub" }, view === "all" ? "Who can reach every workspace you can open, from the access cache, with what's worth a look."
          : "Who can reach each environment's workspace, and who can read which tables. Changes apply in Fabric right away.")),
      h("div", { class: "head-actions" }, seg,
        view === "roles" ? btn("Add a workspace", "add", addReviewWorkspace, "tool outline") : null,
        view === "roles" ? btn("Give access", "person", () => grantDialog(), "tool outline") : null,
        view !== "all" ? btn("Refresh", "sync", () => { ap.data = null; ap.lh = null; render(); }, "tool outline") : null)),
    view === "all" ? withHowItWorks(body, howItWorks("onelake.access-all", {
      reads: "Each workspace's roles and each lakehouse's OneLake data access roles, as you, from Fabric, when you refresh. You only see workspaces you can open in Fabric.",
      changes: "Refresh changes adds rows to the access cache in the central lakehouse, only for what changed since the last refresh. Nothing in the workspaces it reads changes. The first refresh asks before adding the cache's tables. Who may read it asks before it changes the central lakehouse's OneLake security roles.",
      results: "One row per workspace with its people, groups, apps and guests, and what's worth a look. A workspace whose roles couldn't be read keeps its last known list, marked out of date. Fixes open Environments or Table access, which ask before changing anything in Fabric.",
      anchor: "access-preview",
    })) : body));
  if (!state.user) return;
  if (view === "all") { renderAccessAll(allSlot); return; }
  if (view === "tables") { drawLakehouseAccess(lhSlot); return; }
  if (!ap.data) ap.data = await attempt(() => rpc("access_review", { extra: ap.extra }), "Could not read workspace roles");
  if (state.view !== "onelake" || catalogPage.tab !== "access") return;
  if (!ap.data) { fill(people, h("p", { class: "muted-text" }, "Couldn't read the workspace roles. Details are in Activity (the bell).")); return; }
  fill(people, ...accessTable(ap.data));
}

async function addReviewWorkspace() {
  const workspaces = await loadWorkspaces();
  const ws = await pickWorkspace(workspaces, { title: "Add a workspace to the review", okLabel: "Add" });
  if (!ws) return;
  accessPage.extra.push({ workspace_id: ws.id, workspace_name: ws.displayName });
  accessPage.data = null;
  render();
}

function accessTable(d) {
  const ws = d.workspaces;
  if (!ws.length) {
    return [h("p", { class: "muted-text" }, "No environment has a workspace yet. Assign them in Data mesh > Environments and promote, or add any workspace to review it.")];
  }
  const roleSelect = (p, w) => {
    const cur = p.roles[w.key];
    const sel = h("select", { "aria-label": `${p.name} in ${w.label}`, disabled: w.error ? true : null },
      h("option", { value: "" }, "No access"), d.roles.map((r) => h("option", { value: r, selected: cur?.role === r ? true : null }, r)));
    sel.addEventListener("change", async () => {
      const role = sel.value;
      const what = role ? `${cur ? "change" : "give"} ${p.name} ${role} on` : `remove ${p.name} from`;
      // No access takes the person's access away: a danger confirm, which starts on Cancel.
      if (!(await confirmDialog("Change access?", `This will ${what} ${w.workspace_name || w.label} in Fabric now.`, "Change", { danger: !role }))) { sel.value = cur?.role || ""; return; }
      const ok = await attempt(() => rpc("access_set_role", { workspace_id: w.workspace_id, role, assignment_id: cur?.assignment_id || "",
        principal_id: p.id, principal_type: p.type || "User" }), "Access not changed");
      if (ok) { toast(`Access changed for ${p.name}`, false, { log: true }); accessPage.data = null; render(); } else sel.value = cur?.role || "";
    });
    return sel;
  };
  return [
    d.flagged ? h("div", { class: "problems" }, h("b", {}, `${d.flagged} to look at`), h("span", { class: "muted-text" }, " Flags are listed next to each person.")) : null,
    h("div", { class: "monitor-scroll" }, h("table", { class: "list" },
      h("thead", {}, h("tr", {}, h("th", {}, "Person, group or app"),
        ws.map((w) => h("th", { title: w.workspace_name || w.workspace_id }, w.label, w.error ? h("div", { class: "reason small" }, "Can't read roles") : h("div", { class: "muted-text small" }, w.workspace_name))),
        h("th", {}, "Worth a look"))),
      h("tbody", {}, d.people.map((p) => h("tr", {},
        h("td", {}, h("span", { class: "item" }, icon(p.type === "Group" ? "workspaces" : p.type === "User" ? "person" : "bot", 16), h("b", {}, p.name)),
          h("div", { class: "muted-text small" }, [p.type, p.email].filter(Boolean).join(" · "))),
        ws.map((w) => h("td", {}, roleSelect(p, w))),
        h("td", {}, p.flags.map((f) => h("div", { class: "reason small" }, f)))))))),
    ws.some((w) => w.error) ? h("p", { class: "muted-text small" }, `Listing roles needs Member or Admin on the workspace: ${ws.filter((w) => w.error).map((w) => w.label).join(", ")}.`) : null,
  ];
}

async function grantDialog() {
  const d = accessPage.data;
  if (!d?.workspaces.length) return toast("Add a workspace to the review first", true);
  const who = h("input", { placeholder: "name@contoso.com, or a group's object ID" });
  const wsSel = h("select", {}, d.workspaces.filter((w) => !w.error).map((w) => h("option", { value: w.workspace_id }, `${w.label} · ${w.workspace_name || ""}`)));
  const roleSel = h("select", {}, d.roles.map((r) => h("option", { value: r, selected: r === "Viewer" ? true : null }, r)));
  const typeSel = h("select", {}, ["User", "Group", "ServicePrincipal"].map((t) => h("option", { value: t }, t === "ServicePrincipal" ? "App (service principal)" : t)));
  showDialog("Give access", h("div", { class: "scroll" },
    h("div", { class: "form-grid" }, h("label", { class: "field" }, "Who", who), h("label", { class: "field" }, "Kind (for an object ID)", typeSel),
      h("label", { class: "field" }, "Workspace", wsSel), h("label", { class: "field" }, "Role", roleSel)),
    h("p", { class: "muted-text small" }, "Viewer reads; Contributor also writes items; Member also shares; Admin manages access.")),
  [btn("Cancel", null, closeDialog, "tool outline"), btn("Give access", "person", async () => {
    // An email goes to Fabric as is; only an object ID needs its kind.
    const text = who.value.trim();
    const args = text.includes("@") ? { email: text } : { principal_id: text, principal_type: typeSel.value };
    const ok = await attempt(() => rpc("access_set_role", { workspace_id: wsSel.value, role: roleSel.value, ...args }), "Access not given");
    if (!ok) return;
    closeDialog();
    toast(`${text} is now ${roleSel.value}`, false, { log: true });
    accessPage.data = null;
    render();
  }, "tool primary")]);
}

async function drawLakehouseAccess(slot) {
  const ap = accessPage;
  const data = await onelakeCatalog();
  if (!data || !slot.isConnected) return;
  const reviewed = new Set((ap.data?.workspaces || []).map((w) => w.workspace_id));
  const lakehouses = lakehouseRows(data).sort((a, b) => (reviewed.has(b.workspace_id) - reviewed.has(a.workspace_id)));
  if (!ap.lakehouse) ap.lakehouse = lakehouses[0]?.item_id || null;
  const sel = h("select", { "aria-label": "Lakehouse" }, lakehouses.map((l) => h("option", { value: l.item_id, selected: l.item_id === ap.lakehouse ? true : null }, `${l.name} · ${l.workspace_name}`)));
  const out = h("div", { class: "estate" }, h("p", { class: "muted-text" }, "Reading its data access roles..."));
  sel.addEventListener("change", () => { ap.lakehouse = sel.value; ap.lh = null; drawLakehouseAccess(slot); });
  fill(slot, h("div", { class: "row wrap" }, h("label", { class: "field grow" }, "Lakehouse", sel)), out);
  const lh = lakehouses.find((l) => l.item_id === ap.lakehouse);
  if (!lh) { fill(out, h("p", { class: "muted-text" }, "No lakehouses yet.")); return; }
  if (!ap.lh || ap.lh.item_id !== lh.item_id) ap.lh = await attempt(() => rpc("access_lakehouse", { workspace_id: lh.workspace_id, item_id: lh.item_id }), "Could not read table access");
  if (!ap.lh || !out.isConnected) return;
  fill(out, ...lakehouseAccessBody(ap.lh, lh, () => drawLakehouseAccess(slot)));
}

function lakehouseAccessBody(a, lh, redraw) {
  const names = Object.fromEntries(a.people.map((p) => [p.id, p.name]));
  const pathLabel = (p) => (p === "*" ? "Everything" : p.replace(/^\/Tables\//, "").replace(/\//g, ".").replace(/^\/Files/, "Files"));
  const who = h("div", {});
  const tableSel = h("select", { "aria-label": "Table" }, h("option", { value: "" }, "Pick a table"), a.tables.map((t) => h("option", { value: t }, pathLabel(t))));
  tableSel.addEventListener("change", async () => {
    if (!tableSel.value) { fill(who); return; }
    fill(who, h("p", { class: "muted-text small" }, "Working it out..."));
    const res = await attempt(() => rpc("access_who_can_read", { workspace_id: lh.workspace_id, item_id: lh.item_id, table: tableSel.value }), "Could not work it out");
    if (!res) { fill(who); return; }
    const list = res.readers;
    // Without the data access roles the list misses everyone they let in: say so (QA-005).
    const partial = res.partial ? h("p", { class: "warn-text small" }, `Not complete: people given access by data access roles may be missing. ${res.problem}`) : null;
    fill(who, partial, ...(list.length ? list.map((p) => h("div", { class: "row tight" }, h("b", {}, p.name), h("span", { class: "muted-text small" }, p.how)))
      : [h("p", { class: "muted-text small" }, res.partial ? "No one found so far." : "Only workspace admins.")]));
  });
  const whoBox = h("div", { class: "card pad who-reads" }, h("b", {}, "Who can read a table"), h("label", { class: "field" }, "Table", tableSel), who);
  if (!a.enabled) {
    return [h("div", { class: "problems" }, h("b", {}, "OneLake security isn't on for this lakehouse, or you can't manage it."),
      h("p", {}, "Until it is, Admins, Members and Contributors read every table and Viewers read through the SQL endpoint's permissions. Turn it on in the lakehouse in Fabric (Manage OneLake security)."),
      h("details", {}, h("summary", { class: "muted-text small" }, "Details"), h("pre", { class: "sql-block" }, friendlyError(a.problem).details || a.problem))),
    btn("Open the lakehouse in Fabric", "open", () => openInFabric(fabricItemLink(lh)), "tool outline"), whoBox];
  }
  return [
    h("div", { class: "row" }, h("span", { class: "muted-text grow" }, "Data access roles decide which tables Viewers (and people outside the workspace) can read. Admins, Members and Contributors read everything."),
      btn("New role", "add", () => roleDialog(a, lh, null, redraw), "tool primary small")),
    a.roles.length ? h("table", { class: "list" },
      h("thead", {}, h("tr", {}, ["Role", "Can read", "Members", ""].map((c) => h("th", {}, c)))),
      h("tbody", {}, a.roles.map((r) => h("tr", {},
        h("td", {}, h("b", {}, r.name)),
        h("td", {}, r.paths.map((p) => h("span", { class: "pill-s info" }, pathLabel(p)))),
        h("td", {}, r.entra.map((o) => h("div", { class: "small" }, names[o] || o)), r.item_members.map((m) => h("div", { class: "muted-text small" }, `Everyone with ${m} permission`))),
        h("td", { class: "actions" }, btn("Edit", null, () => roleDialog(a, lh, r, redraw), "tool small link"),
          btn("Delete", "delete", async () => {
            if (!(await confirmDialog(`Delete role ${r.name}?`, r.name === "DefaultReader" ? "DefaultReader is what lets people with read permission see every table. Without it they only see what other roles give them." : "Its members lose what this role let them read.", "Delete", { danger: true }))) return;
            const res = await attempt(() => rpc("access_delete_role", { workspace_id: lh.workspace_id, item_id: lh.item_id, name: r.name }), "Role not deleted");
            if (res) { accessPage.lh = res; toast(`Deleted role ${r.name}`, false, { log: true }); redraw(); }
          }, "tool small link"))))))
      : h("p", { class: "muted-text" }, "No roles yet."),
    whoBox,
  ];
}

function roleDialog(a, lh, role, redraw) {
  const name = h("input", { value: role?.name || "", placeholder: "SalesReaders" });
  const chosen = new Set(role?.paths || []);
  const members = new Set(role?.entra || []);
  const extra = [];
  const pathLabel = (p) => (p === "*" ? "Everything in the lakehouse" : p.replace(/^\/Tables\//, "").replace(/\//g, "."));
  const box = (value, label, set) => h("label", { class: "check" },
    h("input", { type: "checkbox", checked: set.has(value) ? true : null, onchange: (ev) => { ev.target.checked ? set.add(value) : set.delete(value); } }), label);
  const peopleList = h("div", { class: "check-list" });
  const drawPeople = () => fill(peopleList, ...[...a.people, ...extra].map((p) => box(p.id, `${p.name}${p.role ? ` (${p.role})` : ""}`, members)),
    ...[...members].filter((o) => ![...a.people, ...extra].some((p) => p.id === o)).map((o) => box(o, o, members)));
  drawPeople();
  const add = h("input", { placeholder: "name@contoso.com or an object ID" });
  showDialog(role ? `Edit role ${role.name}` : "New data access role", h("div", { class: "scroll estate" },
    h("label", { class: "field" }, "Name (letters and digits)", name),
    h("b", {}, "Can read"),
    h("div", { class: "check-list" }, box("*", pathLabel("*"), chosen), a.schemas.map((s) => box(s, `Schema ${pathLabel(s)}`, chosen)),
      a.tables.map((t) => box(t, pathLabel(t), chosen))),
    h("b", {}, "Members"), peopleList,
    h("div", { class: "row" }, h("label", { class: "field grow" }, "Someone else", add), btn("Add", "add", async () => {
      const p = await attempt(() => rpc("access_find_person", { text: add.value, workspace_id: lh.workspace_id }), "Couldn't find them");
      if (!p) return;
      extra.push(p); members.add(p.id); add.value = ""; drawPeople();
    }, "tool outline small")),
    role?.item_members?.length ? h("p", { class: "muted-text small" }, `Kept as is: everyone with ${role.item_members.join(", ")} permission.`) : null),
  [btn("Cancel", null, closeDialog, "tool outline"), btn("Save role", "save", async () => {
    const res = await attempt(() => rpc("access_save_role", { workspace_id: lh.workspace_id, item_id: lh.item_id, name: name.value.trim(),
      paths: [...chosen], members: [...members], original_name: role?.name || null }), "Role not saved");
    if (!res) return;
    closeDialog();
    accessPage.lh = res;
    toast(`Saved role ${name.value.trim()}`, false, { log: true });
    redraw();
  }, "tool primary")]);
}
