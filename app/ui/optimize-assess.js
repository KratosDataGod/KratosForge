// Optimize > Assessment (OE-003, Preview): one workspace or capacity read for duplicate work, repeated
// copies and full refreshes, with the evidence behind each opportunity (sidecar opportunities/,
// optimization_assess). Built from the approved mockup (UX thread, 2026-11-07). Reads only.

const optimizeAssess = { scope: "workspace", workspaceId: "", capacityId: "", days: 30, result: null, running: false, error: "" };

const ASSESS_TYPE = { DataPipeline: "Pipeline", Notebook: "Notebook", Lakehouse: "Lakehouse", SemanticModel: "Semantic model" };
const ASSESS_CONF = { proven: "pass", high: "info", medium: "wrn", low: "neu" };
const cap1 = (s) => (s ? s[0].toUpperCase() + s.slice(1) : "");

function assessHours(secs) {
  const hrs = secs / 3600;
  return `${hrs >= 10 ? Math.round(hrs).toLocaleString() : hrs.toFixed(1)} h`;
}

// A run-time measurement in hours; CU and money use the shared measureFigure (estate.js).
function assessRun(m) {
  if (!m || m.value === null || m.value === undefined) return h("span", {}, "Unknown ", measureBadge(m || { classification: "unknown" }));
  return h("span", {}, assessHours(m.value), " ", measureBadge(m));
}

function assessCu(m, cur) {
  if (!m) return h("span", { class: "muted-text" }, "No capacity claimed");
  return measureFigure("Capacity a month", m.unit === "cu_seconds" && m.value != null ? { ...m, unit: "cu_hours", value: m.value / 3600 } : m, cur);
}

function fixtureChip(result) {
  return result?.fixture ? h("span", { class: "fx-chip" }, "Demonstration fixture") : null;
}

async function optimizeAssessPanel(body) {
  if (!previewShown()) {
    fill(body, h("p", { class: "muted-text" }, "The assessment is a Preview feature. Turn on preview features in Settings to use it."));
    return;
  }
  const a = optimizeAssess;
  const scope = h("div", { class: "row assess-scope" });
  const view = h("div", { class: "assess-view" });
  fill(body, scope, view);
  const draw = () => {
    if (a.running) fill(view, h("p", { class: "muted-text", role: "status" }, "Reading definitions and run history... Nothing is written and no Fabric job starts."));
    else if (a.error) fill(view, h("p", { class: "muted-text", role: "alert" }, a.error));
    else if (!a.result) fill(view, assessEmpty(() => run({ demo: true })));
    else fill(view, ...assessResult(a.result));
  };
  const run = async (params) => {
    a.running = true; a.error = ""; draw(); drawScope();
    try {
      a.result = await rpc("optimization_assess", params);
    } catch (err) {
      a.error = friendlyError(err).text;
    } finally {
      a.running = false;
    }
    if (!body.isConnected) return;
    drawScope(); draw();
  };
  const drawScope = () => {
    const demo = !!a.result?.fixture;
    const seg = h("span", { class: "assess-seg", role: "radiogroup", "aria-label": "Assess" },
      [["workspace", "A workspace"], ["capacity", "A capacity"]].map(([id, label]) =>
        h("button", { type: "button", class: "chip", role: "radio", "aria-checked": a.scope === id ? "true" : "false",
          onclick: () => { a.scope = id; drawScope(); } }, label)));
    const pick = h("select", { "aria-label": a.scope === "capacity" ? "Capacity" : "Workspace", class: "assess-pick" },
      h("option", { value: "" }, "Sign in to list them..."));
    const days = h("select", { "aria-label": "Run history" }, [7, 30, 90].map((d) =>
      h("option", { value: d, selected: d === a.days ? true : null }, `Last ${d} days`)));
    days.onchange = () => { a.days = Number(days.value); };
    const go_ = btn(a.result && !demo ? "Assess again" : "Assess", "search", () => {
      const id = pick.value;
      if (!id) { toast(`Pick a ${a.scope} first`, true); return; }
      if (a.scope === "capacity") a.capacityId = id; else a.workspaceId = id;
      run(a.scope === "capacity" ? { capacity_id: id, days: a.days } : { workspace_id: id, days: a.days });
    }, "tool primary", { disabled: a.running ? true : null });
    const demoBtn = demo
      ? btn("Leave the demonstration", null, () => { a.result = null; drawScope(); draw(); }, "tool link")
      : btn("Try the demonstration fixture", null, () => run({ demo: true }), "tool link", { disabled: a.running ? true : null });
    scope.replaceChildren(seg, h("label", {}, a.scope === "capacity" ? "Capacity " : "Workspace ", pick),
      h("label", {}, "Run history ", days), go_, h("span", { class: "spacer" }), demoBtn,
      h("p", { class: "muted-text small assess-reads" }, "Reads only. No Fabric job starts, and nothing is written to Fabric or OneLake. ",
        "A capacity assessment covers the workspaces on it that you can see, up to 25."));
    fillPicker(pick);
  };
  const fillPicker = async (pick) => {
    if (!state.user) return;
    const workspaces = await loadWorkspaces();
    if (!pick.isConnected) return;
    if (a.scope === "workspace") {
      pick.replaceChildren(h("option", { value: "" }, "Pick a workspace"), ...workspaces.map((w) =>
        h("option", { value: w.id, selected: w.id === a.workspaceId ? true : null }, w.displayName)));
      return;
    }
    // Capacities from the workspaces you can see; names when Fabric lists the capacity to you.
    const ids = [...new Set(workspaces.map((w) => (w.capacityId || "").toLowerCase()).filter(Boolean))];
    const caps = await rpc("list_capacities").catch(() => []);
    if (!pick.isConnected) return;
    const name = (id) => caps.find((c) => (c.id || "").toLowerCase() === id);
    pick.replaceChildren(h("option", { value: "" }, "Pick a capacity"), ...ids.map((id) => {
      const c = name(id), n = workspaces.filter((w) => (w.capacityId || "").toLowerCase() === id).length;
      return h("option", { value: id, selected: id === a.capacityId ? true : null },
        `${c ? `${c.displayName}${c.sku ? ` (${c.sku})` : ""}` : id} · ${n} workspace${n === 1 ? "" : "s"}`);
    }));
  };
  drawScope();
  draw();
  if (!state.user) scope.querySelector(".assess-pick").replaceChildren(h("option", { value: "" }, "Sign in to list workspaces"));
}

function assessEmpty(tryDemo) {
  return h("div", { class: "card assess-empty" },
    h("h3", {}, "Find work that doesn't need to run, runs twice or could run better"),
    h("p", { class: "muted-text" }, "Pick a workspace or a capacity and choose Assess. KratosForge reads:"),
    h("ul", { class: "text" },
      h("li", {}, "the items in each workspace, and the notebook and pipeline definitions (up to 300 per workspace)"),
      h("li", {}, "their job history for the period you pick, to see how often and how long they run"),
      h("li", {}, "usage files you imported in Cost and savings, the only source of measured CU")),
    h("p", { class: "muted-text" }, "Code is read, never run. Anything that can't be read is listed, not guessed."),
    btn("See how it works with the demonstration fixture", null, tryDemo, "tool link"));
}

function assessResult(r) {
  const out = [];
  if (r.fixture) out.push(h("div", { class: "assess-fixture", role: "note" }, h("b", {}, "DEMONSTRATION FIXTURE. "),
    String(r.fixture_note || "").replace(/^Demonstration fixture:\s*/i, "")));
  const ops = r.opportunities;
  const top = ops.find((o) => o.id === r.next_action.opportunity_id);
  out.push(h("div", { class: "assess-summary" }, assessNext(r, top), assessTotals(r)));
  out.push(assessCoverage(r));
  out.push(h("section", { class: "assess-ops", "aria-labelledby": "assess-ops-h" },
    h("div", { class: "row" }, h("h3", { id: "assess-ops-h" }, "Opportunities"),
      h("span", { class: "muted-text" }, `${ops.length} found, ${r.counts.counted} counted in the totals. Ranked by what counts, then confidence, known size, effort and risk.`),
      fixtureChip(r)),
    ops.length ? assessTable(r) : h("p", { class: "muted-text" }, r.next_action.label),
    h("p", { class: "muted-text small" }, "Run time is measured from Fabric's job history and scaled to a month (Estimated). Where an opportunity removes only part of a workload, how much is Unknown until a before and after run is measured.")));
  return out;
}

function assessNext(r, top) {
  const card = h("section", { class: "card assess-next", "aria-labelledby": "assess-next-h" },
    h("h4", { id: "assess-next-h" }, "Highest-value next action ", fixtureChip(r)));
  if (!top) {
    card.append(h("p", { class: "text" }, r.next_action.label));
    return card;
  }
  card.append(
    h("p", { class: "assess-next-title" }, top.title),
    h("p", { class: "text" }, `${top.current} ${top.recommendation}`),
    h("div", { class: "row" }, h("span", { class: `pill-s ${ASSESS_CONF[top.confidence]}` }, cap1(top.confidence)),
      h("span", { class: "pill-s neu" }, `Effort ${cap1(top.effort)}`), h("span", { class: "pill-s neu" }, `Risk ${cap1(top.risk)}`),
      top.impact.run_seconds_per_month ? h("span", { class: "muted-text small" }, assessRun(top.impact.run_seconds_per_month), " of Fabric run time a month") : null),
    h("div", { class: "row" }, btn("See evidence and rollback", "open", () => assessEvidence(r, top), "tool primary")));
  return card;
}

function assessTotals(r) {
  const t = r.totals, cur = r.price?.currency;
  const part = (m) => m && m.complete === false
    ? h("span", {}, h("b", { class: "assess-incomplete" }, "Incomplete: "), `${m.unknown_parts} of ${m.parts} workloads Unknown. The figure covers the rest.`) : null;
  const row = (k, fig, note) => h("div", { class: "assess-tot-row" }, h("span", {}, k), h("span", { class: "assess-tot-v" }, fig), h("span", { class: "muted-text small assess-tot-n" }, note));
  return h("section", { class: "card", "aria-labelledby": "assess-tot-h" },
    h("h4", { id: "assess-tot-h" }, `If all ${t.workloads_affected} counted opportunities were done, per month `, fixtureChip(r)),
    row("Fabric run time freed", assessRun(t.run_seconds_per_month), part(t.run_seconds_per_month)),
    row("Capacity freed", measureFigure("Capacity freed a month", t.cu_per_month, cur), [part(t.cu_per_month), " Measured CU comes only from imported usage files."]),
    row("CU value", measureFigure("CU value a month", t.cu_value_per_month, cur), t.cu_value_per_month.reason || ""),
    row("Verified after a change", h("span", {}, "Not yet ", measureBadge(t.verified)), t.verified.reason),
    h("p", { class: "assess-not-saving" }, "This is capacity freed, not money saved. ", t.realizable_reason,
      " Each workload counts once, even when two opportunities touch it."));
}

function assessCoverage(r) {
  const c = r.coverage;
  const types = Object.entries(c.by_type).map(([k, v]) => `${v} ${ASSESS_TYPE[k] || k}${v > 1 ? "s" : ""}`).join(" · ");
  const gaps = h("ul", { class: "text" },
    ...c.not_read.map((n) => h("li", {}, h("b", {}, n.name), ` (${ASSESS_TYPE[n.type] || n.type}): ${n.problem || "not read"} It isn't in the results, so they may be incomplete.`)),
    ...r.workspaces.filter((w) => w.problem).map((w) => h("li", {}, h("b", {}, w.name || w.id), `: ${w.problem}`)),
    c.not_analyzed_types.length ? h("li", {}, `${c.not_analyzed_types.map((k) => `${ASSESS_TYPE[k] || k}s`).join(" and ")} are listed but not analyzed yet.`) : null,
    r.scope?.budget?.reached ? h("li", {}, r.scope.note) : null,
    r.scope?.workspaces_left_out ? h("li", {}, `${r.scope.workspaces_left_out} more workspaces on this capacity weren't read (25 at most).`) : null,
    h("li", {}, "Checked for: duplicate notebooks, repeated copies and full refreshes. Finding nothing isn't proof there is nothing."));
  const runs = (w) => w.runs.read ? String(w.runs.runs) : "Not read";
  const wl = h("table", { class: "list compact" },
    h("thead", {}, h("tr", {}, ["Workload", "Type", "Definition", `Runs (${r.days} days)`, "Run time"].map((x) => h("th", {}, x)))),
    h("tbody", {}, r.workloads.map((w) => h("tr", {}, h("td", {}, w.name), h("td", {}, ASSESS_TYPE[w.type] || w.type),
      h("td", {}, { read: "Read", not_read: "Not read", unsupported: "Not analyzed" }[w.definition]), h("td", {}, runs(w)),
      h("td", {}, w.runs.seconds_total ? assessHours(w.runs.seconds_total) : "None")))));
  const issues = c.not_read.length + (c.history_not_read ? 1 : 0);
  return h("section", { class: "card assess-cov", "aria-labelledby": "assess-cov-h" },
    h("h4", { id: "assess-cov-h" }, "What was read"),
    h("div", { class: "row" }, h("b", {}, `${c.items} items, ${c.analyzed} analyzed`), h("span", {}, types),
      h("span", {}, c.not_read.length ? h("b", { class: "assess-incomplete" }, `${c.not_read.length} couldn't be read`) : "Every definition was read"),
      h("span", {}, c.history_not_read ? h("b", { class: "assess-incomplete" }, `${c.history_not_read} run histories couldn't be read`) : "Every run history was read"),
      h("span", {}, `${c.usage_items} items matched an imported usage file`)),
    h("details", { open: issues ? true : null }, h("summary", {}, "Gaps and what isn't analyzed yet"), gaps, h("div", { class: "assess-scroll" }, wl)));
}

function assessTable(r) {
  const ops = r.opportunities, cur = r.price?.currency;
  const rows = ops.map((o, i) => {
    const ov = ops.findIndex((x) => o.overlaps.includes(x.id) && x.counted) + 1;
    const why = o.counted ? null : h("div", { class: "muted-text small" },
      ov ? `Not counted: same workload as #${ov}` : (o.impact.run_seconds_per_month ? "Not counted" : "Not counted: saves maintenance, not capacity"));
    const open = () => assessEvidence(r, o);
    return h("tr", { class: `click${o.counted ? "" : " assess-dim"}`, onclick: open,
      onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } },
      "aria-label": `Open evidence for ${o.title}` },
      h("td", { class: "muted-text" }, String(i + 1)),
      h("td", {}, h("div", {}, h("b", {}, o.title)), h("div", { class: "muted-text small" }, `${o.category_label} · ${o.rule}`), why),
      h("td", {}, o.action_label), h("td", {}, h("span", { class: `pill-s ${ASSESS_CONF[o.confidence]}` }, cap1(o.confidence))),
      h("td", {}, cap1(o.effort)), h("td", {}, cap1(o.risk)),
      h("td", {}, o.impact.run_seconds_per_month ? assessRun(o.impact.run_seconds_per_month) : h("span", { class: "muted-text" }, "No capacity claimed")),
      h("td", {}, o.impact.cu_per_month ? assessCu(o.impact.cu_per_month, cur) : h("span", { class: "muted-text" }, "No capacity claimed")));
  });
  return h("div", { class: "assess-scroll" }, h("table", { class: "list assess-table" },
    h("thead", {}, h("tr", {}, ["#", "Opportunity", "Action", "Confidence", "Effort", "Risk", "Run time a month", "Capacity a month"].map((x) => h("th", {}, x)))),
    h("tbody", {}, rows)));
}

function assessEvidence(r, o) {
  const cur = r.price?.currency;
  const sec = (title, ...kids) => h("section", {}, h("h4", {}, title), ...kids);
  const how = (m) => h("span", { class: "muted-text small" }, m.classification === "unknown"
    ? (m.reason || "Not measured yet.") : [m.method, ...(m.assumptions || [])].filter(Boolean).join("; "));
  const imp = o.impact;
  const worth = imp.run_seconds_per_month
    ? [h("table", { class: "list compact" }, h("tbody", {},
        h("tr", {}, h("td", { class: "muted-text" }, "Run time"), h("td", {}, assessRun(imp.run_seconds_per_month), h("br"), how(imp.run_seconds_per_month))),
        h("tr", {}, h("td", { class: "muted-text" }, "Capacity"), h("td", {}, assessCu(imp.cu_per_month, cur), h("br"), how(imp.cu_per_month))),
        h("tr", {}, h("td", { class: "muted-text" }, "CU value"), h("td", {}, measureFigure("CU value a month", imp.cu_value_per_month, cur), h("br"), how(imp.cu_value_per_month))),
        h("tr", {}, h("td", { class: "muted-text" }, "Performance"), h("td", { class: "muted-text small" }, imp.performance || "")))),
      h("p", { class: "assess-not-saving" }, `CU value is what that capacity is worth at ${r.price?.currency || ""} ${r.price?.price_per_cu_hour ?? ""} per CU hour (${r.price?.source || "as configured"}). It is not a saving until a capacity is resized, paused or retired.`)]
    : [h("p", { class: "text" }, imp.note || "No capacity is claimed.")];
  if (!o.counted && o.overlaps.length) worth.push(h("p", { class: "muted-text small" }, "Not counted in the totals because it touches the same workload as a higher-ranked opportunity."));
  const items = o.next_step.items || [];
  const actions = r.fixture
    ? [h("span", { class: "muted-text small" }, "Demonstration items don't exist in Fabric, so there's nothing to open.")]
    : items.map((it) => btn(`Open ${it.name} in Fabric`, "open", () => openLink(fabricItemLink(it)), "tool outline"));
  const body = h("div", { class: "scroll assess-evidence" },
    h("div", { class: "row" }, fixtureChip(r), h("span", { class: `pill-s ${ASSESS_CONF[o.confidence]}` }, cap1(o.confidence)),
      h("span", { class: "pill-s neu" }, `Effort ${cap1(o.effort)}`), h("span", { class: "pill-s neu" }, `Risk ${cap1(o.risk)}`),
      h("span", { class: "pill-s neu" }, o.action_label)),
    sec("Now", h("p", { class: "text" }, `${o.current} ${o.problem}`)),
    sec("Recommendation", h("p", { class: "text" }, o.recommendation)),
    sec("Evidence", h("ul", { class: "assess-ev" }, o.evidence.map((e) => h("li", {}, e.text, h("span", { class: "muted-text small" }, e.source))))),
    sec(`What it's worth, per month (${imp.basis || "maintenance"})`, ...worth),
    sec("Check before you change it", h("ol", { class: "text" }, o.validation.map((v) => h("li", {}, v)))),
    sec("How to undo it", h("p", { class: "text" }, o.rollback)),
    o.alternatives?.length ? sec("Other ways", h("ul", { class: "text" }, o.alternatives.map((v) => h("li", {}, v)))) : null,
    sec(`Next: ${o.next_step.label}`, h("div", { class: "row" }, actions)),
    h("p", { class: "muted-text small" }, "Items: ", o.artifacts.map((x) => `${x.name} (${x.role})`).join(", ")));
  const overlay = showDialog(o.title, body, [btn("Close", null, closeDialog, "tool outline")]);
  overlay.querySelector(".dialog")?.classList.add("assess-sheet");
}
