// Incidents: one record per problem in each environment, kept in its LH_Meta and shared by everyone
// who can read it (workloads/incidents.py), each with a number, a priority, SLA targets and a timeline
// (incident_desk.py). Agents check on a timer; this app reads the list, checks only when asked, and
// lets a person report, acknowledge, assign, re-prioritize, note and resolve. Teams messages come from
// agents, never from this page. Monitor > KratosForge > Operations > Incidents lists them; the incident page shows one.

const INC_STATE_PILL = { open: "fail", acknowledged: "wrn", resolved: "pass" };
const INC_RESOLUTION = { recovered: "Recovered", cleared: "Cleared", resolved: "Resolved" };
const INC_FILTERS = [["active", "Active", "open"], ["mine", "Mine", "mine"], ["unassigned", "Unassigned", "unassigned"],
  ["at_risk", "SLA at risk", "late"], ["resolved", "Resolved", null]];
const INC_PRIORITY_PILL = { P1: "fail", P2: "wrn", P3: "neu", P4: "neu" };
const INC_PRIORITIES = [["P1", "Many people blocked, no workaround"], ["P2", "A team blocked, or due soon"],
  ["P3", "Wrong or slow, has a workaround"], ["P4", "Minor, or a question"]];  // workloads/incidents.py PRIORITY_LABELS
const INC_EVIDENCE = { run_id: "Run", started_at: "Started", ran_on: "Ran on", job_id: "Job", host: "Host",
  heartbeat_at: "Last heartbeat", stale_after: "Due again by", version: "Version", state: "State", expected: "Promised",
  last_success: "Last good run", success_rate: "Success rate", objective: "Objective" };

// Monitor > KratosForge > Operations > Incidents, kept for the session. checked: when this PC last checked each environment.
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

function incPriority(inc) {
  const p = inc.priority || "P3";
  return h("span", { class: `pill-s ${INC_PRIORITY_PILL[p] || "neu"}`, title: (INC_PRIORITIES.find(([k]) => k === p) || [])[1] || "" }, p);
}

function incMinutes(n) {
  if (n < 60) return `${Math.max(n, 0)} min`;
  const hrs = Math.floor(n / 60), m = n % 60;
  return m ? `${hrs} h ${m} min` : `${hrs} h`;
}

// Where an incident stands against its priority's SLA (workloads/sla.py status): the clock that matters now.
function incSlaText(s) {
  if (!s || s.state === "none") return { text: "No SLA", cls: "muted-text" };
  if (s.state === "met") return { text: "Met", cls: "muted-text" };
  if (s.state === "missed") return { text: "Missed", cls: "reason" };
  const running = (c) => c && ["due", "at_risk", "breached"].includes(c.state);
  const [verb, c] = running(s.acknowledge) ? ["Acknowledge", s.acknowledge] : ["Resolve", s.resolve];
  if (!c || c.state === "none") return { text: "No SLA", cls: "muted-text" };
  if (c.state === "breached") return { text: `${verb} overdue ${incMinutes(c.over_minutes || 0)}`, cls: "reason" };
  return { text: `${verb} due in ${incMinutes(c.left_minutes || 0)}`, cls: c.state === "at_risk" ? "inc-at-risk" : "muted-text" };
}

function incSla(inc) {
  const t = incSlaText(inc.sla);
  return h("span", { class: t.cls }, t.text);
}

function incTitle(inc) {
  return inc.number ? `${inc.number} · ${inc.title}` : inc.title;
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

// ---------- Monitor > KratosForge > Operations > Incidents ----------

function incidentsPanel() {
  const box = h("div", { class: "inc-panel" }, h("p", { class: "muted-text monitor-empty" }, "Reading incidents..."));
  loadIncidents(box);
  return h("div", { class: "mon-how" }, withHowItWorks(box, howItWorks("monitor.incidents", {
    reads: "Each environment's incident list, incidents/incidents.json in its LH_Meta, and the status records of the agents that serve it, with your sign-in.",
    changes: ["That one shared list, with a write that only lands if nobody else wrote in between. ",
      "Acknowledge, Assign, Add note and Resolve change one incident, recorded with your name and time. ",
      "Check now, and the check this app makes every 10 minutes while no running agent checks the environment, open and recover incidents. Nothing here runs anything or changes data."],
    results: "Everyone who can read the environment, and every agent that serves it, sees the same list. Only the app's own 10-minute check posts new incidents to Teams, to the webhook in Settings > Notifications if one is set; Check now never posts. Agents post to the routes their admin set.",
    more: [["Can't check is not failing", "When an environment can't be read, that is its own incident kind, never a workload failure, and an open incident is only marked Recovered after a check that could read the environment."]],
    anchor: "incidents-preview",
  })));
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
  const list = await rpc("incidents_board", { env }).catch((err) => ({ env, incidents: [], error: friendlyError(err).text }));
  if (!box.isConnected) return;
  const again = (changes = {}) => { Object.assign(incPage, changes); loadIncidents(box); };
  const envPick = h("select", { class: "inc-env", "aria-label": "Environment", onchange: (ev) => again({ env: ev.target.value }) },
    envs.map((e) => h("option", { value: e.env, selected: e.env === env, title: e.count === null ? e.observation?.detail || "" : "" },
      `${envName(e.env)} · ${e.count === null ? e.observation?.label || "Can't read" : e.count}`)));
  const counts = list.counts || {};
  const late = (counts.at_risk || 0) + (counts.breached || 0);
  const count = (key) => key === "late" ? late : counts[key];
  const filterChip = ([id, label, key]) => h("button", { class: `chip${incPage.filter === id ? " on" : ""}`, onclick: () => again({ filter: id }) },
    key && count(key) !== undefined ? `${label} · ${count(key)}` : label);
  const mine = (i) => list.user && i.owner && i.owner.toLowerCase() === list.user.toLowerCase();
  const items = (list.incidents || []).filter((i) => incPage.filter === "resolved" ? i.state === "resolved"
    : i.state === "resolved" ? false
    : incPage.filter === "mine" ? mine(i)
    : incPage.filter === "unassigned" ? !i.owner
    : incPage.filter === "at_risk" ? ["at_risk", "breached", "missed"].includes(i.sla?.state)
    : true).filter((i) => matches(`${i.number || ""} ${i.title} ${i.text} ${i.name} ${i.owner} ${i.kind_label} ${i.priority || ""}`));
  const unreadable = list.error || (list.observation && list.observation.state !== "live");
  const empty = unreadable ? `Couldn't read ${envName(env)}'s incidents: ${list.error || list.observation.detail || list.observation.label}`
    : incPage.filter === "mine" ? (list.user ? `Nothing in ${envName(env)} is owned by ${list.user}.` : "Sign in so KratosForge knows which incidents are yours.")
    : incPage.filter === "unassigned" ? `Every active incident in ${envName(env)} has an owner.`
    : incPage.filter === "at_risk" ? `Nothing in ${envName(env)} is near or past its SLA.`
    : incPage.filter === "resolved" ? `Nothing in ${envName(env)} was resolved recently.`
    : state.query ? "No incidents match your search." : `No active incidents in ${envName(env)}.`;
  box.replaceChildren(
    h("div", { class: "monitor-filters" },
      envPick,
      h("span", { class: "inc-gap" }),
      h("div", { class: "row wrap", role: "group", "aria-label": "Show" }, INC_FILTERS.map(filterChip)),
      h("span", { class: "spacer" }),
      btn(`Check ${envName(env)} now`, "sync", () => checkNow(env, () => again()), "tool outline"),
      btn("Report an incident", "add", () => reportIncident({ env }, () => again()), "tool primary")),
    incWatchers(list, env),
    items.length ? h("div", { class: "monitor-scroll" }, h("table", { class: "list inc-list" },
      h("thead", {}, h("tr", {}, ["Number", "Priority", "State", "Problem", "Kind", "Opened", "Seen", "Owner", "SLA", "Escalation", ""].map((c) => h("th", {}, c)))),
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
    bits.push(h("span", {}, h("span", { class: "pill-s neu" }, "Only while the app is open"),
      ` No running agent checks ${envName(env)}'s incidents, so KratosForge checks every 10 minutes while it's open. When no one has it open, nothing does.`));
  }
  const old = (a?.agents || []).filter((x) => !x.incidents);
  if (old.length) bits.push(h("span", { class: "muted-text small" }, `${old.map((x) => x.agent).join(", ")} ${old.length === 1 ? "doesn't" : "don't"} say whether ${old.length === 1 ? "it checks" : "they check"} incidents (an older version).`));
  if (incPage.checked[env]) bits.push(h("span", { class: "muted-text small" }, `Checked from this PC ${relTime(incPage.checked[env])}`));
  return h("div", { class: "wl-sources inc-watchers" }, bits);
}

function incidentRow(i, reload) {
  return h("tr", { class: `click${i.state === "resolved" ? " inc-done" : ""}`, onclick: () => openIncident(i) },
    h("td", { class: "inc-number" }, i.number || ""),
    h("td", {}, incPriority(i)),
    h("td", {}, incStatePill(i)),
    h("td", {}, h("div", {}, h("b", {}, i.title)),
      i.text ? h("div", { class: i.state === "open" ? "reason" : "muted-text" }, i.text) : null,
      h("div", { class: "muted-text small" }, [incSubject(i), i.classification ? `Likely: ${i.classification.label}` : ""].filter(Boolean).join(" · "))),
    h("td", {}, i.kind_label),
    h("td", {}, when(i.opened_at), h("div", { class: "muted-text small" }, `by ${i.opened_by}`)),
    h("td", {}, i.occurrences > 1 ? `${i.occurrences} times` : "Once", h("div", { class: "muted-text small" }, `last ${relTime(i.last_seen_at)}`)),
    h("td", {}, incOwner(i)),
    h("td", {}, incSla(i)),
    h("td", {}, incEscalation(i)),
    h("td", { class: "inc-actions", onclick: (e) => e.stopPropagation() },
      i.state === "open" ? btn("Acknowledge", null, () => acknowledgeIncident(i, reload), "tool primary small") : null,
      i.state !== "resolved" ? btn("More", "chevronDown", (e) => incidentMenu(e.currentTarget, i, reload), "tool outline small") : null));
}

function incidentMenu(anchor, i, reload) {
  showMenu(anchor, [
    { label: "Assign", icon: "person", action: () => assignIncident(i, reload) },
    { label: "Add note", icon: "markdown", action: () => noteIncident(i, reload) },
    { label: "Change priority", action: () => priorityIncident(i, reload) },
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
  toast(res.not_set_up ? `${envName(env)} isn't set up, so there's nothing to check: ${res.not_set_up.replace(/\.$/, "")}.`
    : !res.evaluated ? `${envName(env)}'s run log couldn't be read, so nothing was marked recovered.`
    : res.changes.length ? `Checked ${envName(env)}: ${opened} new, ${recovered} recovered. Agents tell Teams; this check didn't.`
    : `Checked ${envName(env)}: nothing changed.`, !res.evaluated && !res.not_set_up);
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

// A required field left empty: say so beside it, and mark it for screen readers, until it's filled.
function incRequired(input, message) {
  const id = `${input.id || (input.id = `inc-f-${Math.random().toString(36).slice(2, 8)}`)}-why`;
  let note = document.getElementById(id);
  if (!note) {
    note = h("p", { class: "reason small inc-required", id, role: "alert" });
    (input.closest("label") || input).insertAdjacentElement("afterend", note);  // outside the label, so it stays out of the field's name
    input.addEventListener("input", () => { if (input.value.trim()) { input.removeAttribute("aria-invalid"); note.remove(); } }, { once: false });
  }
  note.textContent = message;
  input.setAttribute("aria-invalid", "true");
  input.setAttribute("aria-describedby", id);
  input.focus();
}

function priorityIncident(i, reload) {
  const current = i.priority || "P3";
  const select = h("select", {}, INC_PRIORITIES.map(([p, label]) => h("option", { value: p, selected: p === current }, `${p}: ${label}`)));
  const why = h("input", { type: "text", placeholder: "Why it's more or less urgent" });
  showDialog(`Change the priority of ${incTitle(i)}?`, h("div", { class: "wl-confirm" },
    h("p", { class: "text" }, `Sets its priority for everyone in ${envName(i.env)}'s list, as ${who()}, and its SLA targets follow. `
      + "A check never changes a priority a person set. Nothing runs and no data changes."),
    h("label", { class: "field" }, "Priority", select),
    h("label", { class: "field" }, "Note (required)", why)),
  [btn("Cancel", null, closeDialog, "tool outline"), btn("Change priority", null, async () => {
    const note = why.value.trim();
    if (!note) { incRequired(why, "Add a note saying why."); return; }
    const done = await attempt(async () => after(await rpc("incident_set_priority", { env: i.env, id: i.id, priority: select.value, note }), reload), "Not saved");
    if (done) closeDialog();
  }, "tool primary")]);
  select.focus();
}

// Report a problem a person noticed that checks can't see (wrong numbers in a report, say).
// prefill: { env, workload, name } from the page it was opened on.
function reportIncident(prefill = {}, reload = null) {
  const envs = Object.keys(ENV_LABEL);
  const env = h("select", {}, envs.map((e) => h("option", { value: e, selected: e === (prefill.env || incPage.env || "prod") }, envName(e))));
  const title = h("input", { type: "text", maxlength: 200, placeholder: "Sales dashboard shows yesterday's numbers" });
  const text = h("textarea", { rows: 3, placeholder: "What you saw, when, and who it blocks" });
  let priority = "P3";
  const hint = h("p", { class: "muted-text small" });
  const options = h("div", { class: "inc-priorities" });
  const pick = (p) => {
    priority = p;
    for (const r of options.querySelectorAll("input")) r.checked = r.value === p;
    const t = incPage.sla?.targets?.[p];
    hint.textContent = t && (t.acknowledge_minutes || t.resolve_minutes)
      ? `${p} means acknowledge within ${incMinutes(t.acknowledge_minutes || 0)} and resolve within ${incMinutes(t.resolve_minutes || 0)}, your organization's SLA.`
      : `${p} has no SLA target.`;
  };
  for (const [p, label] of INC_PRIORITIES) {
    options.append(h("label", { class: "inc-priority" }, h("input", { type: "radio", name: "inc-priority", value: p, onchange: () => pick(p) }),
      h("span", {}, h("b", {}, p), h("span", {}, label))));
  }
  rpc("incident_sla_policy", {}).then((pol) => { incPage.sla = pol; pick(priority); }).catch(() => {});
  pick(priority);
  const about = prefill.workload ? h("p", { class: "muted-text small" }, `About ${prefill.name || prefill.workload}. Its owner, team and criticality come from the workload.`) : null;
  showDialog("Report an incident", h("div", { class: "wl-confirm inc-report" },
    h("p", { class: "text" }, "For a problem people notice that KratosForge can't see, like wrong numbers in a report. "
      + "Failed runs, late tables and silent agents open incidents by themselves."),
    h("label", { class: "field" }, "What's wrong (required)", title),
    h("label", { class: "field" }, "Details", text),
    about,
    h("label", { class: "field" }, "Environment", env),
    h("fieldset", { class: "field inc-fieldset" }, h("legend", {}, "How bad is it"), options, hint),
    h("p", { class: "muted-text small" }, "It goes into that environment's shared incident list with your name and a new number. Agents that check incidents tell Teams; this app doesn't.")),
  [btn("Cancel", null, closeDialog, "tool outline"), btn("Report", null, async () => {
    if (!title.value.trim()) { incRequired(title, "Say what's wrong first."); return; }
    const inc = await attempt(() => rpc("incident_report", { env: env.value, title: title.value.trim(), text: text.value.trim(), priority,
      workload: prefill.workload || "", name: prefill.name || "" }), "Not reported");
    if (!inc) return;
    closeDialog();
    toast(`Reported as ${inc.number || "a new incident"}.`);
    incPage.env = inc.env || env.value;
    if (reload) reload(); else openIncident(inc);
  }, "tool primary")]);
  title.focus();
}

// What to do, by kind: good practice shown on the incident page. A detected incident closes itself
// once a check sees the problem clear; a reported one only when a person resolves it.
const INC_GUIDE = {
  run_failed: ["Acknowledge it so others know someone is on it.", "Open the run and read its error; \"Likely\" is a hint, the evidence is what happened.",
    "Fix the cause (a missing file, a changed source, a permission), then run the workload again.", "It closes itself when the next run succeeds."],
  late: ["Acknowledge it, then check whether the run that feeds the table ran, failed or is still waiting.",
    "Tell the people who read the table if it will miss its promise.", "It closes itself once a good run lands."],
  needs_decision: ["Open the run: it is waiting for a person to choose, such as replaying quarantined rows.", "Make the choice there; it closes itself once the run goes on."],
  objective: ["Look at the recent runs to see what missed the objective: failures, lateness or slow runs.", "Fix the cause, or change the objective if it no longer fits. It closes itself once the objective is met again."],
  agent_offline: ["Check the agent's machine: is it on, signed in and running the agent?", "Its requests are taken over only where take-over is turned on.", "It closes itself when the agent reports again."],
  cant_evaluate: ["KratosForge couldn't read this environment, so it can't say whether anything failed.", "Check access to the environment's workspace and LH_Meta.", "It closes itself once a check can read it again."],
  reported: ["Acknowledge it and say what you're doing in a note, so the reporter sees it.", "Set the priority if the reporter's guess was off; say why.",
    "Resolve it with a note when it's fixed: reported incidents never close by themselves."],
};

function incGuide(inc) {
  const steps = INC_GUIDE[inc.kind];
  if (!steps || inc.state === "resolved") return null;
  return h("section", { class: "card pad" }, h("h2", {}, "What to do"), h("ol", { class: "inc-guide" }, steps.map((t) => h("li", {}, t))));
}

// ---------- one incident ----------

async function renderIncident() {
  setView(h("div", { class: "page wl-page" }, loading()));
  const list = await attempt(() => rpc("incidents_board", { env: incPage.env_of }), "Couldn't read the incident");
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
        h("h1", {}, incTitle(inc), " ", incStatePill(inc), " ", incPriority(inc), inc.escalated_at ? h("span", { class: "pill-s wrn" }, "Escalated") : null,
          preview("incidents", h("span", {}))),
        h("p", { class: "sub" }, [inc.kind_label, inc.criticality ? inc.criticality[0].toUpperCase() + inc.criticality.slice(1) : "",
          envName(inc.env), opened, seen].filter(Boolean).join(" · "), " ", learnMore("incidents-preview"))),
      h("div", { class: "row" },
        inc.state === "open" ? btn("Acknowledge", null, () => acknowledgeIncident(inc, reload), "tool primary")
          : inc.state === "acknowledged" ? btn("Resolve", "ok", () => resolveIncident(inc, reload), "tool primary") : null,
        inc.state !== "resolved" ? btn("Assign", "person", () => assignIncident(inc, reload), "tool outline") : null,
        btn("More", "chevronDown", (e) => showMenu(e.currentTarget, [
          inc.state !== "resolved" ? { label: "Change priority", action: () => priorityIncident(inc, reload) } : null,
          { label: "Add note", icon: "markdown", action: () => noteIncident(inc, reload) },
          inc.state === "open" ? { label: "Resolve", icon: "ok", action: () => resolveIncident(inc, reload) } : null,
          inc.workload && inc.workload.startsWith("meta:") ? { label: "Open workload", icon: "table", action: () => openWorkload(inc.workload) } : null,
          inc.kind === "agent_offline" && previewShown() ? { label: "Open agent", icon: "desktop", action: () => openAgent(inc.name) } : null,
        ].filter(Boolean)), "tool outline", { "aria-haspopup": "menu" }))),
    h("div", { class: "wl-grid inc-grid" },
      h("section", { class: "card pad" }, h("h2", {}, "What happened"), incWhatHappened(inc)),
      h("section", { class: "card pad" }, h("h2", {}, "Who is told"), incWhoIsTold(inc, list))),
    incGuide(inc),
    incImpactCard(inc, reload),
    h("section", { class: "card pad" }, h("h2", {}, "Timeline"), incHistory(inc))));
}

// ---------- who it reaches: data products on this table and the projects that read them ----------

function incImpactCard(inc, reload) {
  if (!inc.workload || !inc.workload.startsWith("meta:")) return null;
  const head = () => h("h2", {}, "Who's affected", " ", previewTag());
  const box = h("section", { class: "card pad inc-impact" }, head(), h("p", { class: "muted-text" }, "Reading the data mesh..."));
  rpc("incident_impact", { env: inc.env, id: inc.id }).then((out) => {
    if (!box.isConnected) return;
    box.replaceChildren(head(), ...incImpactBody(inc, out, reload));
  }).catch((err) => box.isConnected && box.replaceChildren(head(),
    h("p", { class: "reason" }, `Couldn't read the data mesh: ${friendlyError(err).text}`)));
  return withHowItWorks(box, howItWorks("incidents.impact", {
    reads: "The platform's catalog: data products whose ports in this environment publish this table, the projects linked to those ports, and who follows each product.",
    changes: ["Tell them posts one Teams message through the platform's Teams routes (Settings > Notifications), naming the people below and the products. ",
      "It also adds a note to this incident saying who was told, with your name and the time. Nothing runs and no data changes."],
    results: "The people below read about it in the Teams channel the route posts to. Telling again only posts when someone new is reached.",
    more: [["One hop", "It shows the projects that link to these products. What those projects build from the data isn't traced further."]],
    anchor: "whos-affected-preview",
  }));
}

function incImpactBody(inc, out, reload) {
  if (!out.available) return [h("p", { class: "muted-text" }, out.reason)];
  if (!out.products.length) {
    return [h("p", { class: "muted-text" }, `No published data product in ${envName(inc.env)} is built on ${out.entity}, so nobody downstream reads it through the mesh.`)];
  }
  const products = h("table", { class: "mini inc-impact-products" }, h("tbody", {}, out.products.map((p) => h("tr", { "data-product": p.id },
    h("td", {}, h("b", {}, p.name), h("div", { class: "muted-text small" }, `Port ${p.ports.join(", ")}${p.owner ? ` · owner ${p.owner}` : ""}`)),
    h("td", {}, p.consumers.length ? `Read by ${p.consumers.join(", ")}` : h("span", { class: "muted-text" }, "No project links to it yet")),
    h("td", {}, btn("Open product", null, () => openImpactProduct(p.id), "tool link small"))))));
  const people = out.people.length ? h("ul", { class: "inc-impact-people" }, out.people.map((p) => h("li", {},
    h("b", {}, p.upn), h("span", { class: "muted-text small" }, ` · ${p.why.join("; ")}`))))
    : h("p", { class: "muted-text" }, "Nobody to tell but you.");
  const told = out.told ? h("p", { class: "muted-text small" }, `Told in Teams ${relTime(out.told.at)} by ${out.told.by}.`) : null;
  const unreachable = out.unreachable.length ? h("p", { class: "muted-text small" },
    `Not an email, so not in the message: ${out.unreachable.join(", ")}.`) : null;
  // Who follows a product is personal data: the platform lists it only to its owners and admins.
  const hiddenNames = (out.followers_hidden || []).map((id) => out.products.find((p) => p.id === id)?.name || id);
  const hidden = hiddenNames.length ? h("p", { class: "muted-text small inc-impact-hidden" },
    `Followers of ${hiddenNames.join(", ")} aren't listed or told: only a product's owner or a platform admin can see who follows it. Its owner can tell them.`) : null;
  const upns = out.people.map((p) => p.upn);
  const actions = upns.length ? h("div", { class: "row wrap" },
    btn(out.told ? "Tell them again" : "Tell them", "comment", () => tellImpacted(inc, out, reload), "tool primary small"),
    btn("Copy emails", "copyArrowRight", () => navigator.clipboard.writeText(upns.join("; ")).then(
      () => toast(`Copied ${upns.length} ${upns.length === 1 ? "email" : "emails"}.`), () => toast("Couldn't copy the emails.", true)), "tool outline small")) : null;
  return [h("p", { class: "sub" }, `${out.products.length} data ${out.products.length === 1 ? "product is" : "products are"} built on ${out.entity} in ${envName(inc.env)}.`),
    products, h("h3", {}, "People to tell"), people, unreachable, hidden, told, actions];
}

async function openImpactProduct(id) {
  const out = await platformCall("platform_products", {}, "Could not read the catalog");
  const prod = out?.products.find((x) => x.id === id);
  if (!prod) return;
  openProducts(prod);
}

function tellImpacted(inc, out, reload) {
  const n = out.people.length;
  confirmDialog(`Tell ${n} ${n === 1 ? "person" : "people"} about ${inc.title}?`,
    `Posts one Teams message through the platform's Teams routes, naming ${out.people.map((p) => p.upn).join(", ")} and the data products it reaches. `
      + "The message has the incident's title and the products' names, not its notes. A note on this incident records who was told.",
    "Tell them").then(async (ok) => {
    if (!ok) return;
    const res = await attempt(() => rpc("incident_tell_impacted", { env: inc.env, id: inc.id, people: out.people.map((p) => p.upn) }), "Couldn't tell them");
    if (!res) return;
    if (!res.told) {
      toast(res.reason === "already posted" ? "Already told these people about this incident."
        : `Not posted: ${res.reason}. Copy the emails and tell them yourself.`, res.reason !== "already posted");
      return;
    }
    toast(res.result === "sent" ? `Told ${n} ${n === 1 ? "person" : "people"} in Teams.`
      : res.result === "held" ? "Held for quiet hours; it goes out after them." : "Teams couldn't be reached; it's queued and will be sent.");
    reload();
  });
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
  const s = inc.sla || {};
  const clock = (c) => !c || c.state === "none" ? h("span", { class: "muted-text" }, "No target")
    : h("span", { class: ["breached", "missed"].includes(c.state) ? "reason" : null },
      `${when(c.due)}${{ met: " (met)", missed: " (missed)", breached: " (overdue)", at_risk: " (soon)" }[c.state] || ""}`);
  const rows = [row("Priority", h("span", {}, incPriority(inc), inc.priority_source === "person" ? " set by a person" : " from its criticality")),
    row("Acknowledge by", clock(s.acknowledge)), row("Resolve by", clock(s.resolve)),
    row("Owner", inc.owner || h("span", { class: "inc-unowned" }, "No owner")),
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

const INC_EVENT = { opened: "Opened", reported: "Reported", seen: "Seen again", acknowledged: "Acknowledged", assigned: "Assigned",
  note: "Note", priority: "Priority changed", escalated: "Escalated", resolved: "Resolved", recovered: "Recovered", cleared: "Cleared", reopened: "Opened again" };

// The incident's timeline (events, newest first). Records from before events existed show what they kept.
function incHistory(inc) {
  if ((inc.events || []).length) {
    const events = [...inc.events].sort((a, b) => String(b.at).localeCompare(String(a.at)));
    return h("div", { class: "wl-timeline" }, events.map((e) => h("div", { class: "wl-event" },
      h("span", { class: "muted-text small wl-when" }, when(e.at)),
      h("div", {}, h("b", {}, INC_EVENT[e.what] || e.what), e.by ? ` by ${e.by}` : "", e.text ? `: ${e.text}` : ""))));
  }
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
    const report = btn("Report an incident", "add", () => reportIncident({ workload: w.id, name: w.name || w.id }, () => render()), "tool outline small");
    box.replaceChildren(h("div", { class: "row" }, h("h2", { class: "grow" }, "Incidents", preview("incidents", h("span", {}))), report),
      items.length ? h("div", { class: "wl-findings" }, items.map((i) => h("div", { class: "wl-finding click", onclick: () => openIncident(i) },
        incStatePill(i), h("div", { class: "grow" }, h("b", {}, i.title), h("div", { class: "muted-text small" },
          `${[i.number, i.priority, envName(i.env)].filter(Boolean).join(" · ")} · opened ${relTime(i.opened_at)}${i.escalated_at ? ` · escalated to ${i.escalated_to}` : ""}`))))) : h("p", { class: "muted-text" }, "No active incidents."),
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
    { label: "Report an incident", icon: "add", keywords: "incident report problem broken wrong numbers ticket",
      run: () => reportIncident() },
  ],
});
