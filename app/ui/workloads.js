// Workloads: one list of everything you build and run, on this PC, on agents and in Fabric, with
// how healthy each one is, who owns it and what you can do with it. Monitor > KratosForge > Operations > Workloads lists them,
// the workload page shows one, and Home shows yours. The engine (workload_service.py) builds the
// records from what the app already reads; nothing here guesses a state the engine didn't report.

const WL_HEALTH_PILL = { failing: "fail", late: "wrn", attention: "wrn", unknown: "neu", healthy: "pass", idle: "neu" };
const WL_LEVEL_PILL = { failing: "fail", late: "wrn", attention: "wrn", unknown: "neu", notice: "info", ok: "pass" };
const WL_LEVEL_LABEL = { failing: "Failing", late: "Late", attention: "Needs attention", unknown: "Not observed", notice: "Note", ok: "OK" };
const WL_OBS_PILL = { live: "pass", cached: "info", stale: "wrn", partial: "wrn", throttled: "wrn" };
const WL_CAPS = { discoverable: "Discoverable", observable: "Observable", runnable: "Runnable", schedulable: "Schedulable",
  cancelable: "Cancelable", deployable: "Deployable", read_only: "Read only" };
const WL_ROLES = [["team", "Team"], ["technical_owner", "Technical owner"], ["business_contact", "Business contact"],
  ["support_contact", "Support contact"]];
const WL_GROUPS = [["project", "Project"], ["owner", "Owner"], ["environment", "Environment"], ["none", "None"]];
const WL_RUN_ACTIONS = new Set(["run", "run_item", "run_in_fabric", "run_schedule"]);

// Monitor > KratosForge > Operations > Workloads filters, kept for the session. id is the workload the page shows.
const wlPage = { view: "attention", group: "project", offset: 0, limit: 100, id: null, detail: null };

function healthPill(health, label) {
  return h("span", { class: `pill-s ${WL_HEALTH_PILL[health] || "neu"}` }, label || health);
}

// How fresh one reading is: "Live", "Cached 3 h", "Signed out". Never a workload state.
function obsPill(o) {
  if (!o) return null;
  const age = o.state === "cached" || o.state === "stale" ? ` ${relTime(o.collected_at).replace(/ ago$/, "")}` : "";
  return h("span", { class: `pill-s ${WL_OBS_PILL[o.state] || "neu"}`, title: [o.source, o.detail].filter(Boolean).join(": ") },
    `${o.label}${age}`);
}

// Who answers for it: the team, else the technical owner. A placeholder ("me") is no owner.
function ownerLine(owners) {
  for (const [role] of WL_ROLES.slice(0, 2)) {
    const o = owners?.[role];
    if (o?.value && !o.placeholder) {
      return h("span", {}, o.value, h("span", { class: "muted-text small" }, ` · ${o.source === "declared" ? "declared" : "on this PC"}`));
    }
  }
  return h("span", { class: "muted-text" }, "No owner");
}

function deploymentChips(deps) {
  return h("div", { class: "wl-deps" }, (deps || []).map((d) => h("span", { class: "wl-dep", title: d.observation?.detail || "" },
    d.env_label ? h("b", {}, d.env_label) : null, d.env_label ? " · " : null, d.location.label)));
}

function lastRunText(run) {
  if (!run) return h("span", { class: "muted-text" }, "No runs recorded");
  const label = { ok: "OK", failed: "Failed", running: "Running", needs_decision: "Needs a decision", skipped: "Skipped",
    cancelled: "Cancelled" }[run.state] || "Unknown";
  return h("span", {}, h("span", { class: run.state === "failed" ? "reason" : null }, label),
    run.state === "ok" && run.mocked ? [" ", h("span", { class: "pill-s wrn", title: "Some activities were mocked, so this isn't a real run of the whole pipeline" }, "with mocks")] : null, " ",
    h("span", { class: "muted-text small" }, relTime(run.started_at)));
}

function capabilityChips(caps) {
  return h("div", { class: "wl-caps" }, (caps || []).map((c) => h("span", { class: "wl-cap" }, WL_CAPS[c] || c)));
}

function openWorkload(id) {
  wlPage.id = id;
  wlPage.detail = null;
  go("workload");
}

// ---------- Monitor > KratosForge > Operations > Workloads ----------

function workloadsPanel() {
  const box = h("div", { class: "wl-panel" }, h("p", { class: "muted-text monitor-empty" }, "Reading your workloads..."));
  loadWorkloads(box, false);
  return h("div", { class: "mon-how" }, withHowItWorks(box, howItWorks("monitor.workloads", {
    reads: "Your project's metadata entities, notebooks on this PC, pipelines on this PC (the Git folders this PC opened, its schedules and local run records; Preview), and the pipelines, notebooks and Spark job definitions in the workspaces the app works in (pinned, your environments' and the ones your notebooks publish to), with their run records and Fabric's job history. Never your whole tenant. It reads when this view opens and when you press Refresh.",
    changes: "Nothing from the list. On a workload's page, Edit changes its owner, criticality and tags on this PC, and Run, Retry and Cancel the run first say which environment, where it runs, as whom and what it writes.",
    results: "Ownership, tags, links and the action log stay on this PC in workloads.sqlite and aren't shared with your team yet. When there's a platform, each action and change is also written to its audit (Platform > Govern > Audit) with who, where, before, after and how it ended; until the platform takes a line, this PC keeps it and sends it again. A run you start writes where that environment's runs always write.",
    more: [["Not observed is not failing", "When your sign-in has expired or a workspace refuses access, a workload shows Not observed with the reason, never Failing. Its last reading shows as Cached, then Stale after an hour."]],
    anchor: "workloads-preview",
  })));
}

async function loadWorkloads(box, refresh) {
  let data;
  try {
    data = await rpc("workloads_list", { view: wlPage.view, query_text: state.query || "", group: wlPage.group,
      offset: wlPage.offset, limit: wlPage.limit, refresh });
  } catch (err) {
    const f = friendlyError(err);
    box.replaceChildren(h("p", { class: "reason monitor-empty" }, `Couldn't read workloads: ${f.text}`));
    return;
  }
  if (!box.isConnected) return;
  const again = (changes = {}) => { Object.assign(wlPage, changes); loadWorkloads(box, false); };
  const chip = (v) => h("button", { class: `chip${wlPage.view === v.id ? " on" : ""}`,
    onclick: () => again({ view: v.id, offset: 0 }) }, `${v.label} · ${v.count}`);
  const groupSelect = h("select", { class: "group-select", "aria-label": "Group by", onchange: (e) => again({ group: e.target.value, offset: 0 }) },
    WL_GROUPS.map(([id, label]) => h("option", { value: id, selected: wlPage.group === id }, label)));
  const sources = h("div", { class: "wl-sources" }, (data.sources || []).map((s) => h("span", { class: "wl-source" }, obsPill(s),
    " ", s.source, s.detail ? h("span", { class: "muted-text small" }, ` · ${s.detail}`) : null)),
  data.scanning ? h("span", { class: "muted-text small" }, "Reading Fabric items in the background...") : null,
  btn("Refresh", "sync", () => { box.replaceChildren(h("p", { class: "muted-text monitor-empty" }, "Reading again...")); loadWorkloads(box, true); }, "tool link small"));

  const groups = new Map((data.groups || []).map((g) => [g.id, g]));
  const rows = [];
  let current = null;
  for (const r of data.rows || []) {
    if (r.group_key !== current && wlPage.group !== "none") {
      current = r.group_key;
      const g = groups.get(current) || { label: current, count: 0, health: {} };
      const bad = ["failing", "late", "attention"].filter((x) => g.health?.[x]).map((x) => `${g.health[x]} ${x === "attention" ? "need attention" : x}`);
      rows.push(h("tr", { class: "wl-group" }, h("td", { colspan: 7 }, h("b", {}, g.label), current === "pipelines" ? [" ", previewTag()] : null,
        h("span", { class: "muted-text small" }, ` · ${g.count} ${g.count === 1 ? "workload" : "workloads"}${bad.length ? ` · ${bad.join(", ")}` : ""}`))));
    }
    rows.push(workloadRow(r));
  }
  const pager = data.total > data.limit ? h("div", { class: "row wl-pager" },
    h("span", { class: "muted-text small grow" }, `Showing ${data.offset + 1} to ${Math.min(data.offset + data.limit, data.total)} of ${data.total}`),
    data.offset > 0 ? btn("Previous", null, () => again({ offset: Math.max(0, data.offset - data.limit) }), "tool outline small") : null,
    data.offset + data.limit < data.total ? btn("Next", null, () => again({ offset: data.offset + data.limit }), "tool outline small") : null) : null;
  const empty = {
    attention: "Nothing needs attention. Failing, late and stuck workloads show here.",
    mine: data.user ? `Nothing is owned by ${data.user}. Owners come from project files or are assigned on a workload's page.`
      : "Sign in so KratosForge knows who you are.",
    unowned: "Every workload has an owner.",
  }[wlPage.view] || (state.query ? "No workloads match your search." : "No workloads yet. Notebooks, metadata entities, pipelines on this PC and Fabric items in your workspaces show here.");
  box.replaceChildren(
    h("div", { class: "monitor-filters" }, h("div", { class: "row wrap", role: "group", "aria-label": "Show" }, (data.views || []).map(chip)),
      h("span", { class: "spacer" }), h("label", { class: "muted-text small row" }, "Group by", groupSelect)),
    sources,
    rows.length ? h("div", { class: "monitor-scroll" }, h("table", { class: "list wl-list" },
      h("thead", {}, h("tr", {}, ["Health", "Workload", "Owner", "Deployed to", "Last run", "Can", "Observed"].map((c) => h("th", {}, c)))),
      h("tbody", {}, rows))) : h("p", { class: "muted-text monitor-empty" }, empty),
    pager || "");
  // Fabric items are read in the background the first time; show them once they're in.
  if (data.scanning) setTimeout(() => { if (box.isConnected) loadWorkloads(box, false); }, 4000);
}

// The run that explains the row's health: the failed one when it's failing, else the newest.
function rowRun(r) {
  const runs = r.deployments.map((d) => d.latest_run).filter(Boolean);
  if (r.health === "failing") return runs.find((x) => x.state === "failed") || r.last_run;
  return r.last_run;
}

function workloadRow(r) {
  // "Not observed" already shows in Health and Observed; the line under the name is for real problems.
  const head = r.headline?.level === "unknown" ? null : r.headline;
  const runnable = r.deployments.some((d) => (d.actions || []).some((a) => WL_RUN_ACTIONS.has(a)));
  const can = runnable ? "Run" : (r.capabilities || []).includes("read_only") ? h("span", { class: "muted-text" }, "Read only") : "Open";
  const obs = r.deployments.map((d) => d.observation).filter(Boolean);
  const worst = obs.find((o) => !["live", "cached"].includes(o.state)) || obs[0];
  return h("tr", { class: "click", onclick: () => openWorkload(r.id) },
    h("td", {}, healthPill(r.health, r.health_label)),
    h("td", {}, h("div", {}, h("b", {}, r.name)),
      h("div", { class: "muted-text small" }, [r.kind_label, r.description].filter(Boolean).join(" · "), r.preview ? [" ", previewTag()] : null),
      head ? h("div", { class: head.level === "failing" ? "reason" : "muted-text small" }, head.title, head.text ? `: ${head.text}` : "") : null),
    h("td", {}, ownerLine(r.owners), r.criticality ? h("div", { class: "muted-text small" }, `${r.criticality.value} criticality`) : null),
    h("td", {}, deploymentChips(r.deployments)),
    h("td", {}, lastRunText(rowRun(r))),
    h("td", { class: "small" }, can),
    h("td", {}, obsPill(worst)));
}

// ---------- the workload page ----------

async function renderWorkload() {
  if (!wlPage.id) return go("monitor");
  setView(h("div", { class: "page wl-page" }, loading()));
  const w = wlPage.detail?.id === wlPage.id ? wlPage.detail : await attempt(() => rpc("workload_detail", { id: wlPage.id }), "Couldn't open the workload");
  if (state.view !== "workload") return;
  if (!w) {
    setView(h("div", { class: "page" }, h("h1", {}, "Workload"), h("p", { class: "muted-text" }, "It couldn't be read."),
      btn("Back to Workloads", null, backToWorkloads, "tool outline")));
    return;
  }
  wlPage.detail = w;
  renderChrome();  // the breadcrumb names it
  // Retry leads only when the last run failed; a decision or a late table needs a person first.
  const worst = w.findings.find((f) => f.level === "failing" && f.kind === "run");
  const runActions = (w.available_actions || []).filter((a) => WL_RUN_ACTIONS.has(a.action));
  const primary = worst && runActions.find((a) => (a.env || null) === (worst.env || null)) || null;
  setView(h("div", { class: "page wl-page" },
    h("div", { class: "row wrap wl-head" },
      h("div", { class: "grow" },
        h("h1", {}, w.name, " ", healthPill(w.health, w.health_label), preview("workloads", h("span", {}))),
        h("p", { class: "sub" }, workloadSubtitle(w), w.preview ? [" ", previewTag()] : null, " ", learnMore("workloads-preview")),
        capabilityChips(w.capabilities)),
      h("div", { class: "row" },
        primary ? btn(`Retry${primary.env ? ` in ${ENV_LABEL[primary.env] || primary.env}` : ""}`,
          "play", () => confirmWorkloadAction(w, primary, "Retry"), "tool primary") : null,
        openButton(w),
        btn("More", "chevronDown", (e) => moreMenu(e.currentTarget, w), "tool outline"))),
    h("div", { class: "wl-grid" },
      h("section", { class: "card pad" }, h("h2", {}, "Health and readiness"), findingsList(w),
        h("div", { class: "muted-text small wl-ready" }, `Readiness: ${w.readiness.label}`)),
      h("section", { class: "card pad" }, h("div", { class: "row" }, h("h2", { class: "grow" }, "Ownership"),
        btn("Edit", "person", () => ownershipDialog(w), "tool link small")), ownershipTable(w))),
    workloadIncidentsCard(w),
    workloadCapacityCard(w),
    h("section", { class: "card pad" }, h("h2", {}, "Deployments"), deploymentsTable(w)),
    h("div", { class: "wl-grid" },
      h("section", { class: "card pad" }, h("h2", {}, "What changed"), timeline(w)),
      h("section", { class: "card pad" }, h("h2", {}, "Who is affected"), affected(w))),
    h("section", { class: "card pad" }, h("h2", {}, "Recent runs"), runsTable(w)),
    (w.actions || []).length ? h("section", { class: "card pad" }, h("h2", {}, "Actions from KratosForge"), actionsTable(w), platformAuditNote(w.platform_audit)) : null));
}

// Whether the actions and changes above are on the platform's audit (workload_audit_state).
function platformAuditNote(a) {
  if (!a) return null;
  const n = a.waiting === 1 ? "1 action or change hasn't" : `${a.waiting} actions or changes haven't`;
  const text = a.platform === "none"
    ? (a.waiting ? `Kept on this PC only: there's no platform yet. ${n} reached an audit; they go to Platform > Govern > Audit once a platform is set up.` : "Kept on this PC only: there's no platform yet.")
    : a.waiting ? `${n} reached Platform > Govern > Audit yet${a.error ? `: ${a.error.replace(/\.$/, "")}` : ""}. This PC keeps them and sends them again.`
      : "Each one is also on Platform > Govern > Audit.";
  return h("p", { class: "muted-text small" }, text);
}

// Capacity use of the runs this app started or scheduled on this PC in the last 30 days
// (workload_capacity_use; not Fabric's own job history, which Recent runs reads): measured where an imported
// usage row matched the run, else the app's estimate; a run with neither is left out, not counted as 0.
function workloadCapacityCard(w) {
  const head = () => h("h2", {}, "Capacity use ", h("span", { class: "muted-text small" }, "runs from this app, last 30 days"), preview("workloads", h("span", {})));
  const box = h("section", { class: "card pad wl-capacity" }, head(), h("p", { class: "muted-text" }, "Adding up its runs..."));
  rpc("workload_capacity_use", { id: w.id, days: 30 }).then((u) => {
    if (!box.isConnected) return;
    const toCost = btn("Optimize > Cost and savings", null, () => { optimizePage.tab = "cost"; go("optimize"); }, "tool link small");
    const plural = (n, one) => `${n} ${one}${n === 1 ? "" : "s"}`;
    const lines = [];
    if (u.fabric_runs) {
      lines.push(h("p", {}, `${plural(u.fabric_runs, "run")} this app started in Fabric used `, measureFigure("Capacity use of its Fabric runs", u.total)));
      lines.push(u.measured_runs
        ? h("p", { class: "small" }, `${u.measured_runs} of ${u.fabric_runs} matched imported usage: `, measureFigure("Measured capacity use", u.measured),
          h("div", { class: "muted-text" }, "The rest count with the app's estimate, and a run with neither is left out, not counted as 0."))
        : h("p", { class: "small" }, "None matched imported usage, so the figure is the app's estimate. Import a Capacity Metrics export in ", toCost, " to measure it."));
    }
    if (u.pc_runs) lines.push(h("p", { class: "small" }, `${plural(u.pc_runs, "run")} on this PC started no Fabric job. Their OneLake reads and writes still use some capacity, which isn't counted here.`));
    if (!lines.length) lines.push(h("p", { class: "muted-text" }, "No runs started from this app in the last 30 days."));
    lines.push(h("p", { class: "muted-text small" }, "Runs Fabric started on its own schedule, or that someone started in Fabric, aren't counted here; Optimize > Cost and savings shows the capacity's imported usage."));
    if (u.problem) lines.push(h("p", { class: "reason small" }, u.problem));
    if (u.measured_runs) lines.push(h("p", { class: "muted-text small" }, "A match is likely, not proven: usage exports have names and times, not job ids."));
    box.replaceChildren(head(), ...lines);
  }).catch((err) => box.replaceChildren(head(), h("p", { class: "muted-text" }, `Couldn't add up its capacity use: ${friendlyError(err).text}`)));
  return box;
}

function backToWorkloads() {
  monitorPage.panel = "workloads";
  go("monitor");
}

function workloadSubtitle(w) {
  const d = w.definition || {};
  if (w.kind === "meta_entity") {
    const file = d.path ? String(d.path).split(/[\\/]/).slice(-2).join("/") : "";
    return [`Metadata entity in ${w.group?.label || d.project}`, d.layer ? `${d.layer} layer` : "", file, w.description].filter(Boolean).join(" · ");
  }
  if (w.kind === "pipeline") return ["Pipeline on this PC", w.description].filter(Boolean).join(" · ");
  if (w.kind === "notebook") {
    return ["Notebook on this PC", w.link ? `linked to its Fabric item (${w.link.how})` : "not linked to a Fabric item", w.description].filter(Boolean).join(" · ");
  }
  return [w.kind_label, `in ${d.workspace_name || "a workspace"}`, w.description].filter(Boolean).join(" · ");
}

function openButton(w) {
  const d = w.definition || {};
  if (w.kind === "meta_entity") return btn("Open entity", "table", () => openEntity(w.name), "tool outline");
  if (w.kind === "notebook") return btn("Open notebook", "notebook", () => openNotebook(w.name), "tool outline");
  if (w.kind === "pipeline") return btn("Open pipeline", "flow", () => openWorkloadPipeline(w), "tool outline");
  return d.url ? btn("Open in Fabric", "cloud", () => openInFabric(d.url), "tool outline") : null;
}

function moreMenu(anchor, w) {
  const items = (w.available_actions || []).map((a) => ({ label: a.label, icon: a.action === "cancel" ? "dismiss" : "play",
    action: () => confirmWorkloadAction(w, a) }));
  if (w.kind === "notebook" && w.link) items.push({ label: "Unlink from its Fabric item", icon: "dismiss", action: () => unlinkNotebook(w) });
  items.push({ label: "Read again", icon: "sync", action: async () => {
    wlPage.detail = await attempt(() => rpc("workload_detail", { id: w.id, refresh: true }), "Couldn't read it again");
    render();
  } });
  showMenu(anchor, items);
}

function findingsList(w) {
  const shown = w.findings.length ? w.findings : [{ level: "ok", title: "Nothing to report", text: "", evidence: {}, next: [] }];
  return h("div", { class: "wl-findings" }, shown.map((f) => h("div", { class: "wl-finding" },
    h("span", { class: `pill-s ${WL_LEVEL_PILL[f.level] || "neu"}` }, WL_LEVEL_LABEL[f.level] || f.level),
    h("div", { class: "grow" },
      h("div", {}, h("b", {}, f.title)),
      f.text ? h("div", { class: f.level === "failing" ? "reason" : "muted-text small" }, f.text) : null,
      evidenceLine(f.evidence)),
    h("div", { class: "row" }, (f.next || []).map((n) => btn(n.label, null, () => findingAction(w, f, n), "tool link small"))))));
}

// Where a finding came from, so it can be checked.
function evidenceLine(ev) {
  if (!ev || !ev.source) return null;
  const bits = [`From ${ev.source}`];
  if (ev.ran_on) bits.push(ev.ran_on);
  if (ev.started_at) bits.push(relTime(ev.started_at));
  if (ev.collected_at) bits.push(`read ${relTime(ev.collected_at)}`);
  if (ev.expected) bits.push(`promised ${ev.expected}`);
  return h("div", { class: "muted-text small" }, bits.join(" · "));
}

function findingAction(w, f, n) {
  if (n.action === "open_run") return openRun(f.evidence?.open, w);
  if (WL_RUN_ACTIONS.has(n.action)) {
    const a = (w.available_actions || []).find((x) => x.action === n.action && (x.env || null) === (f.env || null));
    return a ? confirmWorkloadAction(w, a, n.label) : toast("That can't run here right now.", true);
  }
  if (n.action === "sign_in") return signIn().then((ok) => { if (ok) { wlPage.detail = null; render(); } });
  if (n.action === "refresh") return attempt(() => rpc("workload_detail", { id: w.id, refresh: true }), "Couldn't read it again")
    .then((d) => { if (d) { wlPage.detail = d; render(); } });
  if (n.action === "promote") { if (f.env) state.meta.env = f.env; return goMeta("environments"); }
  if (n.action === "assign") return ownershipDialog(w);
  if (n.action === "link") return confirmLink(w);
  if (n.action === "open_definition") {
    return w.kind === "meta_entity" ? openEntity(w.name) : w.kind === "pipeline" ? openWorkloadPipeline(w) : openNotebook(w.name);
  }
  return undefined;
}

function openRun(open, w = null) {
  if (!open && w?.definition?.url) return openInFabric(w.definition.url);  // Fabric's job history is on the item
  if (!open) return toast("This run can only be opened where it ran.");
  if (open.kind === "meta_run") return showRun(open.run_id, open.env);
  if (open.kind === "url" && open.url) return openInFabric(open.url);
  if (open.kind === "pc_schedule") { monitorPage.panel = "schedules"; return go("monitor"); }
  if (open.kind === "pipeline_run" && open.run_id) return openPipelineRun(open.run_id);
  return undefined;
}

// A local pipeline opens on the Pipelines page, from its folder (pipelines.js).
function openWorkloadPipeline(w) {
  const d = w.definition || {};
  return openPipeline({ path: d.path }, { label: [d.repo, d.relative].filter(Boolean).join(" · ") || d.path });
}

function ownershipTable(w) {
  const o = w.owners || {};
  const rows = WL_ROLES.map(([role, label]) => {
    const x = o[role];
    return h("tr", {}, h("td", { class: "muted-text" }, label),
      h("td", {}, x?.value ? [x.value, x.placeholder ? h("span", { class: "pill-s wrn" }, "Placeholder") : null]
        : h("span", { class: "muted-text" }, "Not set")),
      h("td", { class: "muted-text small" }, x ? (x.source === "declared" ? x.where : "on this PC") : ""));
  });
  rows.push(h("tr", {}, h("td", { class: "muted-text" }, "Criticality"),
    h("td", {}, w.criticality?.value || h("span", { class: "muted-text" }, "Not set")),
    h("td", { class: "muted-text small" }, w.criticality ? "on this PC" : "")));
  if ((w.tags || []).length) rows.push(h("tr", {}, h("td", { class: "muted-text" }, "Tags"), h("td", {}, w.tags.join(", ")), h("td", {})));
  return h("div", {}, h("table", { class: "mini" }, h("tbody", {}, rows)),
    h("p", { class: "muted-text small" }, "Roles in the project's files win and are changed there. Others are kept on this PC only."));
}

function ownershipDialog(w) {
  const o = w.owners || {};
  const inputs = {};
  const fields = WL_ROLES.map(([role, label]) => {
    const x = o[role];
    const declared = x?.source === "declared";
    inputs[role] = h("input", { value: x?.value || "", disabled: declared ? true : null,
      placeholder: role === "team" ? "A team or group" : "A name or email" });
    return h("label", { class: "field" }, label, inputs[role],
      declared ? h("span", { class: "muted-text small" }, `Set in the ${x.where}. Change it there.`) : null);
  });
  const crit = h("select", {}, h("option", { value: "" }, "Not set"),
    ["critical", "high", "standard", "low"].map((c) => h("option", { value: c, selected: w.criticality?.value === c ? true : null },
      c[0].toUpperCase() + c.slice(1))));
  const tags = h("input", { value: (w.tags || []).join(", "), placeholder: "finance, daily" });
  const save = async () => {
    const changes = {};
    for (const [role] of WL_ROLES) {
      if (o[role]?.source === "declared") continue;
      const value = inputs[role].value.trim();
      if (value !== (o[role]?.value || "")) changes[role] = value;
    }
    if (crit.value !== (w.criticality?.value || "")) changes.criticality = crit.value;
    const tagList = tags.value.split(",").map((t) => t.trim()).filter(Boolean);
    if (tagList.join(",") !== (w.tags || []).join(",")) changes.tags = tagList;
    if (!Object.keys(changes).length) return closeDialog();
    const next = await attempt(() => rpc("workload_assign", { id: w.id, changes }), "Not saved");
    if (next) { closeDialog(); wlPage.detail = next; render(); toast("Saved on this PC."); }
  };
  showDialog(`Ownership of ${w.name}`, h("div", { class: "wl-form" }, fields,
    h("label", { class: "field" }, "Criticality", crit), h("label", { class: "field" }, "Tags, separated by commas", tags),
    h("p", { class: "muted-text small" }, "Kept on this PC. Clear a box to remove it. Each change is recorded with your name.")),
  [btn("Cancel", null, closeDialog, "tool outline"), btn("Save", null, save, "tool primary")]);
}

function deploymentsTable(w) {
  if (!w.deployments.length) return h("p", { class: "muted-text" }, "Not deployed anywhere yet.");
  return h("table", { class: "mini" },
    h("thead", {}, h("tr", {}, ["Environment", "Runs on", "As", "Tables in", "Version", "Schedule", "Latest run", "Observed"].map((c) => h("th", {}, c)))),
    h("tbody", {}, w.deployments.map((d) => {
      const v = d.version;
      return h("tr", {},
        h("td", {}, d.env_label || h("span", { class: "muted-text" }, "Not in an environment")),
        h("td", {}, d.location.label),
        h("td", { class: "small" }, d.identity?.label || ""),
        h("td", {}, d.tables_in || ""),
        h("td", {}, v ? [h("span", { class: "mono" }, String(v.deployed || "unknown").slice(0, 8)), " ",
          v.behind ? h("span", { class: "pill-s wrn" }, "Behind") : v.deployed ? h("span", { class: "muted-text small" }, "current") : null] : ""),
        h("td", { class: "small" }, (d.schedules || []).map((s) => h("div", {}, `${s.text} · ${s.where}${s.enabled === false ? " (paused)" : ""}`))),
        h("td", {}, lastRunText(d.latest_run)),
        h("td", {}, obsPill(d.observation)));
    })));
}

function timeline(w) {
  const events = (w.timeline || []).slice(0, 12);
  if (!events.length) return h("p", { class: "muted-text" }, "Nothing recorded yet.");
  return h("div", { class: "wl-timeline" }, events.map((e) => h("div", { class: "wl-event" },
    h("span", { class: "muted-text small wl-when" }, when(e.at)),
    h("div", {}, h("div", { class: e.state === "failed" ? "reason" : null }, e.text),
      e.source ? h("div", { class: "muted-text small" }, e.source) : null))));
}

function affected(w) {
  const dep = w.dependencies || {};
  // Downstream entries are entities in the same project; upstream ones can also be sources.
  const project = w.definition?.project;
  const names = (list) => list.map((x) => h("button", { class: "tool link small", onclick: () => openWorkload(`meta:${project}:${x.name}`) }, x.name));
  const parts = [];
  if ((dep.downstream || []).length) parts.push(h("div", {}, h("b", {}, "Downstream: "), names(dep.downstream)));
  if ((dep.upstream || []).length) parts.push(h("div", {}, h("b", {}, "Upstream: "), dep.upstream.map((x) => x.name).join(", ")));
  for (const p of dep.products || []) {
    const consumers = (w.consumers || []).filter((c) => c.product === p.product || String(c.product).endsWith(p.product)).map((c) => c.consumer);
    parts.push(h("div", {}, h("b", {}, "Data product: "), p.product, consumers.length ? ` · consumers: ${consumers.join(", ")}` : ""));
  }
  if (!parts.length) parts.push(h("p", { class: "muted-text" }, "Nothing declared depends on it."));
  parts.push(h("p", { class: "muted-text small" }, "Only declared links are listed. Reports or notebooks that read its tables directly aren't visible here."));
  return h("div", { class: "wl-affected" }, parts);
}

function runsTable(w) {
  const runs = w.runs || [];
  if (!runs.length) return h("p", { class: "muted-text" }, "No runs recorded where KratosForge can read them.");
  return h("table", { class: "mini" },
    h("thead", {}, h("tr", {}, ["Status", "When", "Environment", "Ran on", "Took", "Rows", ""].map((c) => h("th", {}, c)))),
    h("tbody", {}, runs.slice(0, 20).map((r) => h("tr", r.open || w.definition?.url ? { class: "click", onclick: () => openRun(r.open, w) } : {},
      h("td", {}, r.state === "cancelled" ? h("span", { class: "pill-s neu" }, "Cancelled") : statusPill(r.state === "unknown" ? r.native_status || "unknown" : r.state)),
      h("td", {}, when(r.started_at)),
      h("td", {}, ENV_LABEL[r.env] || r.env || ""),
      h("td", {}, r.ran_on || ""),
      h("td", { class: "n" }, took(r.seconds)),
      h("td", { class: "n" }, r.rows ?? ""),
      h("td", {}, r.message && r.state !== "ok" ? h("span", { class: "reason" }, r.message) : null)))));
}

function actionsTable(w) {
  const label = { submitted: "Submitted", succeeded: "Succeeded", failed: "Failed", cancelled: "Cancelled" };
  const cls = { submitted: "info", succeeded: "pass", failed: "fail", cancelled: "neu" };
  return h("table", { class: "mini" },
    h("thead", {}, h("tr", {}, ["When", "Action", "By", "Outcome", ""].map((c) => h("th", {}, c)))),
    h("tbody", {}, w.actions.map((a) => h("tr", {},
      h("td", {}, when(a.at)),
      h("td", {}, `${a.action.replace(/_/g, " ")}${a.env ? ` in ${ENV_LABEL[a.env] || a.env}` : ""}`),
      h("td", {}, a.by || "you"),
      h("td", {}, h("span", { class: `pill-s ${cls[a.status] || "neu"}` }, label[a.status] || a.status)),
      h("td", { class: "muted-text small" }, a.detail || "")))));
}

// ---------- actions: say what will happen, then do it ----------

// verb is how the button that led here put it ("Retry"); otherwise the action's own name.
// recheck reads the capacity again for the risk check (FINOPS-004) instead of using the last reading.
// opts.pre is a preflight the caller already has; opts.done(res) runs after the action is sent.
async function confirmWorkloadAction(w, a, verb = "", recheck = false, opts = {}) {
  const pre = opts.pre || await attempt(() => rpc("workload_preflight", { id: w.id, action: a.action, env: a.env || null }), "Couldn't check that action");
  if (!pre) return;
  if (recheck && pre.risk) pre.risk = await rpc("risk_check", { id: w.id, action: a.action, env: a.env || null, refresh: true }).catch(() => pre.risk);
  if (!pre.allowed) {
    showDialog(`${a.label}?`, h("p", { class: "text" }, pre.reason), [
      /sign in/i.test(pre.reason) ? btn("Sign in", null, async () => { closeDialog(); if (await signIn()) confirmWorkloadAction(w, a, verb, false, { done: opts.done, ends: opts.ends }); }, "tool primary")
        : btn("Close", null, closeDialog, "tool primary")]);
    return;
  }
  verb = verb || pre.label;
  const inEnv = pre.env_label ? ` in ${pre.env_label}` : "";
  const row = (k, v) => v ? h("tr", {}, h("td", { class: "muted-text" }, k), h("td", {}, v)) : null;
  const target = { id: w.id, action: a.action, env: a.env || null };
  const box = riskAckBox(pre.risk);
  const go_ = async () => {
    if (!(await riskProceed(target, pre.risk, box))) return;
    closeDialog();
    const res = await attempt(() => rpc("workload_act", { id: w.id, action: a.action, env: a.env || null,
      schedule_id: a.schedule_id || "", job_id: a.job_id || "" }), `${verb} didn't start`);
    if (!res) { wlPage.detail = null; if (state.view === "workload") render(); return; }
    const s = res.action?.status;
    toast(s === "submitted" ? `${verb}: submitted. ${opts.ends || "This page shows how it ends when the run finishes."}`
      : s === "failed" ? `${verb} failed: ${res.action.detail || "see the run"}` : `${verb}: done.`, s === "failed", { log: true });
    wlPage.detail = null;
    if (opts.done) opts.done(res);
    if (state.view === "workload") render();
  };
  showDialog(a.action === "cancel" ? `Cancel the running job of ${pre.workload}?` : `${verb} ${pre.workload}${inEnv}?`, h("div", { class: "wl-confirm" },
    h("table", { class: "mini" }, h("tbody", {},
      row("Environment", pre.env_label || null),
      row("Runs on", pre.where),
      row("As", pre.identity),
      row("Target", pre.target),
      row("What happens", pre.consequence))),
    (pre.warnings || []).map((t) => h("div", { class: "callout wl-warning" }, icon("warning", 18), h("span", {}, t))),
    riskPanel(pre.risk, { box, onRecheck: () => { closeDialog(); confirmWorkloadAction(w, a, verb, true, { done: opts.done, ends: opts.ends }); } }),
    h("p", { class: "muted-text small" }, pre.risk?.note ? `${pre.risk.note} ` : "", pre.recorded)),
  pre.risk?.result === "blocked"
    ? [btn("Check again", null, () => { closeDialog(); confirmWorkloadAction(w, a, verb, true, { done: opts.done, ends: opts.ends }); }, "tool outline"),
      btn("Close", null, closeDialog, "tool primary")]
    : [btn("Cancel", null, closeDialog, "tool outline"),
      btn(`${verb}${inEnv}`, null, go_, "tool primary")]);
}

// A notebook matched to its Fabric item by name: say which item, then keep it by id.
function confirmLink(w) {
  const link = w.link;
  if (!link) return;
  showDialog(`Link ${w.name} to its Fabric item?`, h("div", {},
    h("p", { class: "text" }, `KratosForge found a notebook named ${w.name} in ${link.workspace_name || "a workspace"} and matched it by name. `,
      "Linking keeps it by its id, so renaming either one won't break the link."),
    h("p", { class: "muted-text small" }, "Nothing changes in Fabric. The link is kept on this PC.")),
  [btn("Cancel", null, closeDialog, "tool outline"), btn("Link", null, async () => {
    const next = await attempt(() => rpc("workload_link", { notebook: w.name, workspace_id: link.workspace_id, item_id: link.item_id,
      workspace_name: link.workspace_name || "" }), "Not linked");
    if (next) { closeDialog(); wlPage.detail = next; render(); }
  }, "tool primary")]);
}

// A link this PC keeps decides which Fabric item Run in Fabric runs, so unlinking one asks first.
// A name match isn't kept (inventory.notebook_link), so unlinking it changes nothing to ask about.
async function unlinkNotebook(w) {
  const link = w.link || {};
  const runs = (w.available_actions || []).some((a) => a.action === "run_in_fabric");
  if (runs && link.how !== "name match" && !(await confirmDialog(`Unlink ${w.name} from its Fabric item?`,
    `Run in Fabric runs the notebook in ${link.workspace_name || "its workspace"} (item ${link.item_id}) now. After unlinking, `
    + "KratosForge finds its item again from runs this app started in Fabric, or by its name in its publish workspace; "
    + "if neither finds it, Run in Fabric isn't offered until you publish or link it again. Nothing changes in Fabric.",
    "Unlink", { danger: true }))) return;
  if (await attempt(() => rpc("workload_unlink", { notebook: w.name }), "Not unlinked")) {
    wlPage.detail = null;
    toast(`${w.name} is no longer linked. If a Fabric notebook has the same name, it's matched by name again.`);
    render();
  }
}

// ---------- Home: your workloads ----------

// What you own, worst first. Empty until it knows who you are; never from who last ran something.
function yourWorkloadsCard() {
  if (!previewShown()) return null;
  const body = h("div", { class: "muted-text" }, "Reading your workloads...");
  const box = h("section", { class: "card pad wl-home" },
    h("div", { class: "row" }, h("h2", { class: "grow" }, "Your workloads", preview("workloads", h("span", {}))),
      btn("See all in Monitor", null, () => { wlPage.view = "mine"; wlPage.offset = 0; backToWorkloads(); }, "tool link small")),
    body);
  box.load = async () => {
    let data;
    try {
      data = await rpc("workloads_list", { view: "mine", group: "none", limit: 200 });
    } catch {
      body.replaceWith(h("p", { class: "muted-text" }, "Couldn't read workloads."));
      return;
    }
    if (!box.isConnected) return;
    const rows = data.rows || [];
    const unowned = (data.views || []).find((v) => v.id === "unowned")?.count || 0;
    const counts = {};
    for (const r of rows) counts[r.health] = (counts[r.health] || 0) + 1;
    const order = ["failing", "late", "attention", "unknown", "healthy", "idle"];
    const labels = { failing: "Failing", late: "Late", attention: "Need attention", unknown: "Not observed", healthy: "Healthy", idle: "No recorded runs" };
    const problems = rows.filter((r) => ["failing", "late", "attention"].includes(r.health)).slice(0, 4);
    const next = h("div", {},
      h("p", { class: "muted-text small" }, data.user
        ? `You own ${data.total}: declared in project files or assigned on this PC.`
        : "Sign in so KratosForge knows which workloads are yours."),
      rows.length ? h("div", { class: "row wrap wl-counts" }, order.filter((k) => counts[k]).map((k) =>
        h("button", { class: "chip", onclick: () => { wlPage.view = k === "unknown" || k === "healthy" || k === "idle" ? "mine" : "attention"; wlPage.offset = 0; backToWorkloads(); } },
          `${counts[k]} ${labels[k]}`))) : null,
      problems.length ? h("div", { class: "home-attention" }, problems.map((r) => h("div", { class: "card attention" },
        healthPill(r.health, r.health_label),
        h("div", { class: "grow" },
          h("div", {}, h("b", {}, r.name), h("span", { class: "muted-text" }, ` · ${r.kind_label}`)),
          r.headline ? h("div", { class: r.health === "failing" ? "reason" : "muted-text small" }, r.headline.title,
            r.headline.text ? `: ${r.headline.text}` : "") : null,
          rowRun(r) ? h("div", { class: "muted-text small" }, `${rowRun(r).ran_on || ""} · ${relTime(rowRun(r).started_at)}`) : null),
        btn("Open", null, () => openWorkload(r.id), "tool primary")))) : null,
      unowned ? h("div", { class: "callout" }, icon("person", 20),
        h("span", {}, `${unowned} ${unowned === 1 ? "workload has" : "workloads have"} no owner, so nobody is told when ${unowned === 1 ? "it fails" : "they fail"}.`),
        h("span", { class: "spacer" }), btn("Review", null, () => { wlPage.view = "unowned"; wlPage.offset = 0; backToWorkloads(); }, "tool outline")) : null,
      !rows.length && !unowned ? h("p", { class: "muted-text" }, "Nothing is assigned to you yet. Open a workload in Monitor > KratosForge > Operations > Workloads to set its owner.") : null);
    body.replaceWith(next);
  };
  return box;
}

registerFeature({
  id: "workloads", preview: true,
  views: { workload: renderWorkload },
  commands: [
    { label: "Workloads that need attention", icon: "warning", keywords: "monitor failing late health workload inventory",
      run: () => { wlPage.view = "attention"; wlPage.offset = 0; backToWorkloads(); } },
    { label: "My workloads", icon: "person", keywords: "monitor mine owner workload inventory",
      run: () => { wlPage.view = "mine"; wlPage.offset = 0; backToWorkloads(); } },
    { label: "Unowned workloads", icon: "person", keywords: "monitor owner assign workload inventory",
      run: () => { wlPage.view = "unowned"; wlPage.offset = 0; backToWorkloads(); } },
    { label: "All workloads", icon: "monitor", keywords: "monitor inventory pipelines notebooks entities fabric items",
      run: () => { wlPage.view = "all"; wlPage.offset = 0; backToWorkloads(); } },
  ],
});
