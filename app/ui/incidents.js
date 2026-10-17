// Incidents: one record per problem in each environment, kept in its LH_Meta and shared by everyone
// who can read it (workloads/incidents.py). Agents check on a timer; this app reads the list, checks
// only when asked, and lets a person acknowledge, assign, note and resolve. Teams messages come from
// agents, never from this page. Monitor > Incidents lists them; the incident page shows one.

const INC_STATE_PILL = { open: "fail", acknowledged: "wrn", resolved: "pass" };
const INC_RESOLUTION = { recovered: "Recovered", cleared: "Cleared", resolved: "Resolved" };
const INC_FILTERS = [["active", "Active"], ["mine", "Mine"], ["resolved", "Resolved"]];
const INC_EVIDENCE = { run_id: "Run", started_at: "Started", ran_on: "Ran on", job_id: "Job", host: "Host",
  heartbeat_at: "Last heartbeat", stale_after: "Due again by", version: "Version", state: "State", expected: "Promised",
  last_success: "Last good run", success_rate: "Success rate", objective: "Objective" };

// Monitor > Incidents, kept for the session. checked: when this PC last checked each environment.
const incPage = { env: null, filter: "active", id: null, env_of: null, detail: null, checked: {} };

function incStateLabel(inc) {
  if (inc.state === "resolved") return INC_RESOLUTION[inc.resolution] || "Resolved";
  return inc.state === "acknowledged" ? "Acknowledged" : "Open";
}

function incStatePill(inc) {
  return h("span", { class: `pill-s ${INC_STATE_PILL[inc.state] || "neu"}` }, incStateLabel(inc));
}

function envName(env) {
  return ENV_LABEL[env] || env;
}

// Where the problem is: the workload, or the agent, or (for "can't evaluate") the environment.
function incSubject(inc) {
  if (inc.kind === "agent_offline") return `Agent ${inc.name}`;
  if (inc.kind === "cant_evaluate") return `${envName(inc.env)} itself`;
  return `Workload: ${inc.name}`;
}

function incEscalation(inc) {
  if (inc.escalated_at) return h("div", {}, h("span", { class: "inc-escalated" }, `Escalated ${relTime(inc.escalated_at)}`),
    h("div", { class: "muted-text small" }, `to ${inc.escalated_to}`));
  if (inc.state === "acknowledged") return h("span", { class: "muted-text" }, "Not needed: acknowledged");
  if (inc.state === "resolved") return h("span", { class: "muted-text" }, "None");
  return h("span", { class: "muted-text" }, "Not escalated");
}

function incOwner(inc) {
  return inc.owner ? h("span", {}, inc.owner) : h("span", { class: "inc-unowned" }, "No owner");
}

function openIncident(inc) {
  incPage.id = inc.id;
  incPage.env_of = inc.env;
  incPage.detail = inc;
  go("incident");
}

function backToIncidents() {
  monitorPage.panel = "incidents";
  go("monitor");
}

// ---------- Monitor > Incidents ----------

function incidentsPanel() {
  const box = h("div", { class: "inc-panel" }, h("p", { class: "muted-text monitor-empty" }, "Reading incidents..."));
  loadIncidents(box);
  return box;
}

async function loadIncidents(box) {
  let summary;
  try {
    summary = await rpc("incidents_summary", {});
  } catch (err) {
    box.replaceChildren(h("p", { class: "reason monitor-empty" }, `Couldn't read incidents: ${friendlyError(err).text}`));
    return;
  }
  if (!box.isConnected) return;
  const envs = summary.envs || [];
  if (!incPage.env) incPage.env = (envs.find((e) => e.count) || envs.find((e) => e.count !== null) || envs[0] || {}).env || "dev";
  const env = incPage.env;
  const list = await rpc("incidents_list", { env }).catch((err) => ({ env, incidents: [], error: friendlyError(err).text }));
  if (!box.isConnected) return;
  const again = (changes = {}) => { Object.assign(incPage, changes); loadIncidents(box); };
  const envChip = (e) => h("button", { class: `chip${e.env === env ? " on" : ""}`, title: e.count === null ? e.observation?.detail || "" : "",
    onclick: () => again({ env: e.env }) }, `${envName(e.env)} · ${e.count === null ? e.observation?.label || "Can't read" : e.count}`);
  const filterChip = ([id, label]) => h("button", { class: `chip${incPage.filter === id ? " on" : ""}`, onclick: () => again({ filter: id }) }, label);
  const items = (list.incidents || []).filter((i) => incPage.filter === "resolved" ? i.state === "resolved"
    : incPage.filter === "mine" ? i.state !== "resolved" && list.user && i.owner && i.owner.toLowerCase() === list.user.toLowerCase()
    : i.state !== "resolved").filter((i) => matches(`${i.title} ${i.text} ${i.name} ${i.owner} ${i.kind_label}`));
  const unreadable = list.error || (list.observation && list.observation.state !== "live");
  const empty = unreadable ? `Couldn't read ${envName(env)}'s incidents: ${list.error || list.observation.detail || list.observation.label}`
    : incPage.filter === "mine" ? (list.user ? `Nothing in ${envName(env)} is owned by ${list.user}.` : "Sign in so Fabric Desktop by KratosBI knows which incidents are yours.")
    : incPage.filter === "resolved" ? `Nothing in ${envName(env)} was resolved recently.`
    : state.query ? "No incidents match your search." : `No active incidents in ${envName(env)}.`;
  box.replaceChildren(
    h("div", { class: "monitor-filters" },
      h("div", { class: "row wrap", role: "group", "aria-label": "Environment" }, envs.map(envChip)),
      h("span", { class: "inc-gap" }),
      h("div", { class: "row wrap", role: "group", "aria-label": "Show" }, INC_FILTERS.map(filterChip)),
      h("span", { class: "spacer" }),
      btn(`Check ${envName(env)} now`, "sync", () => checkNow(env, () => again()), "tool outline")),
    incWatchers(list, env),
    items.length ? h("div", { class: "monitor-scroll" }, h("table", { class: "list inc-list" },
      h("thead", {}, h("tr", {}, ["State", "Problem", "Kind", "Opened", "Seen", "Owner", "Escalation", ""].map((c) => h("th", {}, c)))),
      h("tbody", {}, items.map((i) => incidentRow(i, () => again()))))) : h("p", { class: unreadable ? "reason monitor-empty" : "muted-text monitor-empty" }, empty),
    h("p", { class: "muted-text small inc-foot" }, "Open incidents and the last 200 resolved ones are kept per environment."));
}

// Who keeps this list current: agents that check it on a timer, from their status records.
function incWatchers(list, env) {
  const bits = [];
  const a = list.agents;
  const checking = (a?.agents || []).filter((x) => x.incidents?.enabled);
  for (const x of checking) {
    const every = x.incidents.seconds ? `every ${Math.round(x.incidents.seconds / 60) || 1} min` : "on a timer";
    bits.push(h("span", {}, h("span", { class: `pill-s ${x.state === "live" ? "pass" : "fail"}` }, x.state === "live" ? "Live" : x.state === "stopped" ? "Stopped" : "Offline"),
      ` Checked by agent ${x.agent} ${every}`, h("span", { class: "muted-text small" }, ` · reported ${relTime(x.heartbeat_at)}`)));
  }
  if (a && !a.readable) bits.push(h("span", {}, h("span", { class: "pill-s wrn" }, "Unknown"), ` Couldn't read the agent records: ${a.detail}`));
  else if (!checking.some((x) => x.state === "live")) {
    bits.push(h("span", {}, h("span", { class: "pill-s neu" }, "Not checked on a timer"),
      ` No running agent checks ${envName(env)}'s incidents. Problems show here only when someone checks.`));
  }
  const old = (a?.agents || []).filter((x) => !x.incidents);
  if (old.length) bits.push(h("span", { class: "muted-text small" }, `${old.map((x) => x.agent).join(", ")} ${old.length === 1 ? "doesn't" : "don't"} say whether ${old.length === 1 ? "it checks" : "they check"} incidents (an older version).`));
  if (incPage.checked[env]) bits.push(h("span", { class: "muted-text small" }, `Checked from this PC ${relTime(incPage.checked[env])}`));
  return h("div", { class: "wl-sources inc-watchers" }, bits);
}

function incidentRow(i, reload) {
  return h("tr", { class: `click${i.state === "resolved" ? " inc-done" : ""}`, onclick: () => openIncident(i) },
    h("td", {}, incStatePill(i)),
    h("td", {}, h("div", {}, h("b", {}, i.title)),
      i.text ? h("div", { class: i.state === "open" ? "reason" : "muted-text" }, i.text) : null,
      h("div", { class: "muted-text small" }, [incSubject(i), i.classification ? `Likely: ${i.classification.label}` : ""].filter(Boolean).join(" · "))),
    h("td", {}, i.kind_label),
    h("td", {}, when(i.opened_at), h("div", { class: "muted-text small" }, `by ${i.opened_by}`)),
    h("td", {}, i.occurrences > 1 ? `${i.occurrences} times` : "Once", h("div", { class: "muted-text small" }, `last ${relTime(i.last_seen_at)}`)),
    h("td", {}, incOwner(i)),
    h("td", {}, incEscalation(i)),
    h("td", { class: "inc-actions", onclick: (e) => e.stopPropagation() },
      i.state === "open" ? btn("Acknowledge", null, () => acknowledgeIncident(i, reload), "tool primary small") : null,
      i.state !== "resolved" ? btn("More", "chevronDown", (e) => incidentMenu(e.currentTarget, i, reload), "tool outline small") : null));
}

function incidentMenu(anchor, i, reload) {
  showMenu(anchor, [
    { label: "Assign", icon: "person", action: () => assignIncident(i, reload) },
    { label: "Add note", icon: "markdown", action: () => noteIncident(i, reload) },
    { label: "Resolve", icon: "ok", action: () => resolveIncident(i, reload) },
    i.workload && i.workload.startsWith("meta:") ? { label: "Open workload", icon: "table", action: () => openWorkload(i.workload) } : null,
  ].filter(Boolean));
}

async function checkNow(env, reload) {
  const res = await attempt(() => rpc("incidents_evaluate", { env }), `Couldn't check ${envName(env)}`);
  if (!res) return;
  incPage.checked[env] = res.at;
  const opened = res.changes.filter((c) => c.change !== "recovered").length;
  const recovered = res.changes.length - opened;
  toast(!res.evaluated ? `${envName(env)}'s run log couldn't be read, so nothing was marked recovered.`
    : res.changes.length ? `Checked ${envName(env)}: ${opened} new, ${recovered} recovered. Agents tell Teams; this check didn't.`
    : `Checked ${envName(env)}: nothing changed.`, !res.evaluated);
  reload();
}

// ---------- acting on one: say what will happen, then do it ----------

function incConfirm(verb, title, consequence, field, label, run) {
  const input = field ? h("input", { type: "text", placeholder: field }) : null;
  showDialog(title, h("div", { class: "wl-confirm" },
    h("p", { class: "text" }, consequence),
    input ? h("label", { class: "field" }, label, input) : null),
  [btn("Cancel", null, closeDialog, "tool outline"), btn(verb, null, async () => {
    const value = input ? input.value.trim() : "";
    if (input && label.endsWith("(required)") && !value) { input.focus(); return; }
    const done = await attempt(() => run(value), "Not saved");
    if (done) closeDialog();
  }, "tool primary")]);
  input?.focus();
}

function who() {
  return state.user || "someone on this PC";
}

function after(next, reload) {
  if (incPage.detail && next && incPage.detail.id === next.id) incPage.detail = { ...incPage.detail, ...next };
  reload();
  return true;
}

function acknowledgeIncident(i, reload) {
  incConfirm("Acknowledge", `Acknowledge ${i.title}?`,
    `Marks it acknowledged in ${envName(i.env)}'s incident list for everyone, as ${who()}. It won't be escalated after this. Nothing runs and no data changes.`,
    "What you're doing about it", "Note (optional)",
    async (note) => after(await rpc("incident_acknowledge", { env: i.env, id: i.id, note }), reload));
}

function assignIncident(i, reload) {
  incConfirm("Assign", `Assign ${i.title}?`,
    `Sets who owns this incident in ${envName(i.env)}'s list, for everyone. It changes this incident only; the workload's own owner stays as it is.`,
    "A name or email", "Owner (required)",
    async (owner) => after(await rpc("incident_assign", { env: i.env, id: i.id, owner }), reload));
}

function noteIncident(i, reload) {
  incConfirm("Add note", `Add a note to ${i.title}?`, `Everyone who can read ${envName(i.env)}'s incidents sees it, with your name and the time.`,
    "What you found or did", "Note (required)",
    async (text) => after(await rpc("incident_note", { env: i.env, id: i.id, text }), reload));
}

function resolveIncident(i, reload) {
  incConfirm("Resolve", `Resolve ${i.title}?`,
    `Marks it resolved in ${envName(i.env)}'s incident list for everyone, as ${who()}. It opens again only on new evidence, `
    + `such as another failed run; what it has seen so far won't reopen it. Nothing runs and no data changes.`,
    "What fixed it", "Note (optional)",
    async (note) => after(await rpc("incident_resolve", { env: i.env, id: i.id, note }), reload));
}

// ---------- one incident ----------

async function renderIncident() {
  setView(h("div", { class: "page wl-page" }, h("p", { class: "muted-text" }, "Loading...")));
  const list = await attempt(() => rpc("incidents_list", { env: incPage.env_of }), "Couldn't read the incident");
  if (state.view !== "incident") return;
  const inc = (list?.incidents || []).find((x) => x.id === incPage.id);
  if (!inc) {
    setView(h("div", { class: "page" }, h("h1", {}, "Incident"),
      h("p", { class: "muted-text" }, list ? "It isn't in the list any more: resolved incidents beyond the last 200 are removed." : "It couldn't be read."),
      btn("Back to Incidents", null, backToIncidents, "tool outline")));
    return;
  }
  incPage.detail = inc;
  renderChrome();  // the breadcrumb names it
  const reload = () => render();
  const opened = `opened ${relTime(inc.opened_at)} by ${inc.opened_by}`;
  const seen = inc.occurrences > 1 ? `seen ${inc.occurrences} times, last ${relTime(inc.last_seen_at)}` : `last seen ${relTime(inc.last_seen_at)}`;
  setView(h("div", { class: "page wl-page" },
    h("div", { class: "row wrap wl-head" },
      h("div", { class: "grow" },
        h("h1", {}, inc.title, " ", incStatePill(inc), inc.escalated_at ? h("span", { class: "pill-s wrn" }, "Escalated") : null,
          preview("incidents", h("span", {}))),
        h("p", { class: "sub" }, [inc.kind_label, inc.criticality ? inc.criticality[0].toUpperCase() + inc.criticality.slice(1) : "",
          envName(inc.env), opened, seen].filter(Boolean).join(" · "))),
      h("div", { class: "row" },
        inc.state === "open" ? btn("Acknowledge", null, () => acknowledgeIncident(inc, reload), "tool primary") : null,
        inc.state !== "resolved" ? btn("Assign", "person", () => assignIncident(inc, reload), "tool outline") : null,
        btn("Add note", "markdown", () => noteIncident(inc, reload), "tool outline"),
        inc.state !== "resolved" ? btn("Resolve", "ok", () => resolveIncident(inc, reload), "tool outline") : null,
        inc.workload && inc.workload.startsWith("meta:") ? btn("Open workload", "table", () => openWorkload(inc.workload), "tool outline") : null)),
    h("div", { class: "wl-grid inc-grid" },
      h("section", { class: "card pad" }, h("h2", {}, "What happened"), incWhatHappened(inc)),
      h("section", { class: "card pad" }, h("h2", {}, "Who is told"), incWhoIsTold(inc, list))),
    h("section", { class: "card pad" }, h("h2", {}, "History"), incHistory(inc))));
}

function incWhatHappened(inc) {
  const ev = inc.evidence || {};
  const rows = Object.entries(INC_EVIDENCE).filter(([k]) => ev[k] !== undefined && ev[k] !== null && ev[k] !== "")
    .map(([k, label]) => h("tr", {}, h("td", { class: "muted-text" }, label),
      h("td", {}, /_at$|^stale_after$/.test(k) ? when(ev[k]) : typeof ev[k] === "object" ? JSON.stringify(ev[k]) : String(ev[k]))));
  const c = inc.classification;
  return h("div", { class: "inc-what" },
    inc.text ? h("div", { class: inc.state === "open" ? "reason" : null }, inc.text) : null,
    c ? h("div", { class: "row inc-likely" }, h("span", { class: "wl-cap" }, `Likely: ${c.label}`),
      h("span", { class: "muted-text small" }, c.hint)) : null,
    rows.length || ev.source ? h("table", { class: "mini" }, h("tbody", {},
      ev.source ? h("tr", {}, h("td", { class: "muted-text" }, "Evidence"), h("td", {}, `From ${ev.source}`)) : null, rows)) : null,
    ev.run_id ? btn("Open run", null, () => showRun(ev.run_id, inc.env), "tool link small") : null,
    c ? h("p", { class: "muted-text small" }, "\"Likely\" comes from matching the error text against known patterns. It is a hint; the evidence is what happened.") : null);
}

// Which agents would post this incident, and where, from their status records. Route matching is
// the agents' rule (workloads/notify.py): every matching team route, else the default webhook.
function incRoutes(inc, cfg) {
  const ok = (allowed, value) => !(allowed || []).length || allowed.map((a) => String(a).toLowerCase()).includes(String(value || "").toLowerCase());
  const named = (cfg.routes || []).filter((r) => r.name !== cfg.escalate_to && ok(r.teams, inc.team) && ok(r.owners, inc.owner)
    && ok(r.criticality, inc.criticality) && ok(r.kinds, inc.kind) && ok(r.envs, inc.env)).map((r) => r.name);
  return named.length ? named.join(", ") : "its default Teams webhook, if it has one";
}

function incWhoIsTold(inc, list) {
  const row = (k, v) => h("tr", {}, h("td", { class: "muted-text" }, k), h("td", {}, v));
  const agents = (list?.agents?.agents || []).filter((x) => x.incidents?.enabled);
  const rows = [row("Owner", inc.owner || h("span", { class: "inc-unowned" }, "No owner")),
    inc.team ? row("Team", inc.team) : null,
    row("Criticality", inc.criticality || h("span", { class: "muted-text" }, "Not set"))];
  for (const a of agents) {
    const cfg = a.incidents;
    const bits = [`Posts to ${incRoutes(inc, cfg)}`];
    if (cfg.quiet_hours) bits.push(`quiet ${cfg.quiet_hours} except ${(cfg.quiet_except || []).join(", ") || "nothing"}`);
    if (cfg.escalate_after_minutes && cfg.escalate_to) bits.push(`escalates to ${cfg.escalate_to} after ${cfg.escalate_after_minutes} min unacknowledged`);
    rows.push(row(`Agent ${a.agent}`, `${bits.join("; ")}.`));
  }
  return h("div", {}, h("table", { class: "mini" }, h("tbody", {}, rows)),
    h("p", { class: "muted-text small" }, agents.length
      ? "Set in each agent's config by its admin and read from its status record. Agents send; this app doesn't."
      : "No agent checks this environment's incidents, so nobody is told automatically."));
}

function incHistory(inc) {
  const events = [{ at: inc.opened_at, text: `Opened by ${inc.opened_by}` }];
  if (inc.acknowledged_at) events.push({ at: inc.acknowledged_at, text: `Acknowledged by ${inc.acknowledged_by}` });
  for (const n of inc.notes || []) events.push({ at: n.at, text: `${n.by}: ${n.text}` });
  if (inc.resolved_at) events.push({ at: inc.resolved_at, text: `${INC_RESOLUTION[inc.resolution] || "Resolved"} by ${inc.resolved_by}` });
  events.sort((a, b) => String(b.at).localeCompare(String(a.at)));
  return h("div", { class: "wl-timeline" }, events.map((e) => h("div", { class: "wl-event" },
    h("span", { class: "muted-text small wl-when" }, when(e.at)), h("div", {}, e.text))));
}

// ---------- on a workload's page ----------

// Active incidents for one workload in every environment; empty when there are none.
function workloadIncidentsCard(w) {
  if (!previewShown() || w.kind !== "meta_entity") return null;
  const box = h("section", { class: "card pad inc-card" }, h("h2", {}, "Incidents", preview("incidents", h("span", {}))),
    h("p", { class: "muted-text" }, "Reading incidents..."));
  rpc("incidents_summary", { workload: w.id }).then((s) => {
    if (!box.isConnected) return;
    const items = (s.envs || []).flatMap((e) => e.incidents);
    const unread = (s.envs || []).filter((e) => e.count === null && e.observation?.state !== "not_set_up").map((e) => envName(e.env));
    box.replaceChildren(h("h2", {}, "Incidents", preview("incidents", h("span", {}))),
      items.length ? h("div", { class: "wl-findings" }, items.map((i) => h("div", { class: "wl-finding click", onclick: () => openIncident(i) },
        incStatePill(i), h("div", { class: "grow" }, h("b", {}, i.title), h("div", { class: "muted-text small" },
          `${envName(i.env)} · opened ${relTime(i.opened_at)}${i.escalated_at ? ` · escalated to ${i.escalated_to}` : ""}`))))) : h("p", { class: "muted-text" }, "No active incidents."),
      unread.length ? h("p", { class: "muted-text small" }, `Couldn't read ${unread.join(", ")}.`) : "");
  }).catch(() => box.replaceChildren(h("h2", {}, "Incidents"), h("p", { class: "muted-text" }, "Couldn't read incidents.")));
  return box;
}

registerFeature({
  id: "incidents", preview: true,
  views: { incident: renderIncident },
  commands: [
    { label: "Incidents", icon: "warning", keywords: "monitor incidents alerts acknowledge resolve escalate on-call",
      run: () => { incPage.filter = "active"; backToIncidents(); } },
    { label: "My incidents", icon: "person", keywords: "monitor incidents mine owner",
      run: () => { incPage.filter = "mine"; backToIncidents(); } },
  ],
});
