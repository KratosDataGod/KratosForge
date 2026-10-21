// Agents: Fabric Desktop running on a team's own servers. Each agent writes a status record per
// environment it serves (agents/<name>.json in that environment's LH_Meta, agent_status.py); this
// page only reads those records. Nothing here starts, stops or changes an agent. An agent whose
// promised next report has passed shows "Stopped reporting" with its last report as it was.
// Monitor > Agents lists them; the agent page shows one.

const AG_STATE = {
  live: ["pass", "Running"],
  offline: ["fail", "Stopped reporting"],
  stopped: ["neu", "Stopped"],
};
const AG_VERSION = { same: "same as this app", older: "older than this app", newer: "newer than this app", unknown: "" };

// Monitor > Agents, kept for the session.
const agPage = { name: null, detail: null };

// relTime for times that may be ahead: a schedule's next run, a promised report.
function agAt(iso) {
  const t = new Date(iso);
  const ahead = Math.round((t.getTime() - Date.now()) / 1000);
  if (!iso || Number.isNaN(t.getTime()) || ahead < 45) return relTime(iso);
  if (ahead < 3600) return `in ${Math.round(ahead / 60)} min`;
  return ahead < 86400 ? `at ${t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : t.toLocaleString();
}

function agStatePill(a) {
  const [cls, label] = AG_STATE[a.state] || AG_STATE.offline;
  return h("span", { class: `pill-s ${cls}` }, label);
}

function agStateNote(a) {
  if (a.state === "live") return `heartbeat ${relTime(a.heartbeat_at)}`;
  if (a.state === "stopped") return `shut down cleanly ${relTime(a.heartbeat_at)}`;
  return `last seen ${relTime(a.heartbeat_at)}, due again ${agAt(a.stale_after)}`;
}

function agEnvs(a) {
  return Object.keys(a.envs || {}).map(envName);
}

function agSum(a, key) {
  return Object.values(a.envs || {}).reduce((n, e) => n + (Number(e.counts?.[key]) || 0), 0);
}

// What it is doing: only a running agent's report is current; the others are its last word.
function agDoing(a) {
  const running = agSum(a, "running");
  const queued = agSum(a, "queued");
  const first = Object.entries(a.envs || {}).flatMap(([env, e]) => (e.running || []).map((j) => ({ ...j, env })))[0];
  const text = running || queued ? `${running} running, ${queued} queued` : "Nothing";
  if (a.state !== "live") {
    return h("div", { class: "muted-text" }, a.state === "stopped" ? "Nothing" : "Unknown",
      a.state === "offline" && (running || queued) ? h("div", { class: "small" }, `its last report said ${text.toLowerCase()}`) : null);
  }
  return h("div", {}, text, first ? h("div", { class: "muted-text small" },
    `${(first.entities || []).join(", ") || first.source || first.job_id} since ${relTime(first.started_at)} (${envName(first.env)})`) : null);
}

function agNext(a) {
  const next = Object.entries(a.envs || {}).flatMap(([env, e]) => (e.schedules || []).filter((s) => s.next).map((s) => ({ ...s, env })))
    .sort((x, y) => String(x.next).localeCompare(String(y.next)))[0];
  if (a.state !== "live") {
    return h("div", { class: "muted-text" }, a.state === "stopped" ? "None while stopped" : "Won't run while it is down",
      next ? h("div", { class: "small" }, `${next.id} due ${agAt(next.next)}`) : null);
  }
  return next ? h("div", {}, agAt(next.next), h("div", { class: "muted-text small" }, `${next.id} (${envName(next.env)})`))
    : h("span", { class: "muted-text" }, "No schedules");
}

function agRecent(a) {
  const jobs = Object.values(a.envs || {}).flatMap((e) => e.recent || []);
  if (!jobs.length) return h("span", { class: "muted-text" }, "None yet");
  const by = {};
  for (const j of jobs) by[j.state] = (by[j.state] || 0) + 1;
  const failed = jobs.filter((j) => j.state === "failed").sort((x, y) => String(y.finished_at).localeCompare(String(x.finished_at)))[0];
  return h("div", {}, Object.entries(by).map(([s, n]) => `${n} ${s}`).join(", "),
    failed?.error ? h("div", { class: "reason small" }, `${String(failed.error).split("\n")[0].slice(0, 80)} · ${relTime(failed.finished_at)}`) : null);
}

function agChecks(a) {
  const on = Object.entries(a.envs || {}).filter(([, e]) => e.incidents?.enabled);
  if (!on.length) {
    const unsaid = Object.values(a.envs || {}).some((e) => !e.incidents);
    return h("span", { class: "muted-text" }, unsaid ? "Doesn't say (an older version)" : "No");
  }
  const [, e] = on[0];
  return h("div", {}, `${on.map(([env]) => envName(env)).join(", ")}, every ${Math.round((e.incidents.seconds || 300) / 60) || 1} min`,
    a.state !== "live" ? h("div", { class: "reason small" }, "Not checking while it is down") : null);
}

function agVersion(a) {
  const note = AG_VERSION[a.version_compare] || "";
  return h("div", {}, a.version || "Unknown",
    note ? h("div", { class: `small ${a.version_compare === "older" ? "inc-escalated" : "muted-text"}` }, note) : null);
}

const AG_IDENTITY = { service_principal: "a service principal", managed_identity: "a managed identity", user: "a user's cached sign-in" };

// One schedule in an overlap: "agent etl-01 (hourly, every 60 minutes)" or "a schedule on this PC
// (daily at 06:00)". This PC's schedules have generated ids, so they go by when they run.
function agPlace(s) {
  const text = s.text ? `${s.text.charAt(0).toLowerCase()}${s.text.slice(1)}` : "";
  if (s.place === "pc") return `a schedule on this PC${text ? ` (${text})` : ""}`;
  return `agent ${s.agent}${s.state === "offline" ? ", which stopped reporting," : ""} (${[s.schedule, text].filter(Boolean).join(", ")})`;
}

// "Scheduled in two places": schedules in different places that run the same table. Each place has
// its own queue, so both run it; table leases only stop them writing at the same moment.
function agOverlaps(out) {
  const items = out.overlaps || [];
  const unknown = out.unknown || [];
  if (!items.length && !unknown.length && out.pc_schedules_read !== false) return null;
  const lines = items.map((o) => {
    const [a, b] = o.schedules;
    const what = o.entities ? o.entities.join(", ") : "Every table";
    return h("li", {}, `${what} in ${envName(o.env)} runs on ${agPlace(a)} and on ${agPlace(b)}. `,
      o.entities && o.entities.length === 1 ? "Both write the same table." : "Both write the same tables.");
  });
  const unsaid = [...new Set(unknown.map((u) => u.agent))];
  return h("div", { class: "ag-overlap" },
    items.length ? h("b", {}, "Scheduled in two places") : null,
    items.length ? h("ul", {}, lines) : null,
    items.length ? h("p", { class: "small" }, "Give each table's schedule to one place. ",
      btn("Review schedules", null, () => { monitorPage.panel = "schedules"; go("monitor"); }, "tool link small")) : null,
    unsaid.length ? h("p", { class: "small muted-text" }, `Can't tell what ${unsaid.join(", ")} ${unsaid.length === 1 ? "schedules" : "schedule"}: `,
      `${unsaid.length === 1 ? "it is" : "they are"} an older version that doesn't say which tables ${unsaid.length === 1 ? "its" : "their"} schedules run.`) : null,
    out.pc_schedules_read === false ? h("p", { class: "small muted-text" }, "This PC's schedules couldn't be read, so they weren't compared.") : null);
}

function openAgent(name) {
  agPage.name = name;
  go("agent");
}

function backToAgents() {
  monitorPage.panel = "agents";
  go("monitor");
}

// ---------- Monitor > Agents ----------

function agentsPanel() {
  const box = h("div", { class: "inc-panel" }, h("p", { class: "muted-text monitor-empty" }, "Reading agent status records..."));
  loadAgents(box);
  return h("div", { class: "mon-how" }, withHowItWorks(box, agGuide()));
}

// A link to a section of docs/agent.md.
function agDoc(anchor, label) {
  return h("a", { href: "#", class: "learn-more", onclick: (e) => {
    e.preventDefault();
    attempt(() => invoke("open_doc", { page: `agent.md#${anchor}` }), "Could not open the guide");
  } }, label);
}

// How agents work and where they are going, beside the list (testing items 18 and 34).
function agGuide() {
  return howItWorks("monitor.agents", {
    reads: "The status record each agent writes to every environment it serves, in that environment's LH_Meta, read with your sign-in when this view opens and when you press Refresh. It never reaches the agent's server.",
    changes: "Nothing. Nothing here starts, stops or changes an agent.",
    results: ["Nothing is stored. An agent that misses its promised report shows Stopped reporting, with its last report as it was. ", agDoc("status-records", "Status records")],
    more: [
      ["What an agent is", ["KratosForge running on a server of yours, with no one signed in. It runs a metadata project's tables on their schedules, and the runs Fabric pipelines ask it for, so work keeps going when your PC is off. It signs in as a service principal or a managed identity, never as you. ", agDoc("what-it-does", "What it does")]],
      ["Setting one up", ["Give it an identity with access to the environment's workspaces (", agDoc("set-up-the-identity", "Identity"),
        "), write agent.toml with the project, environments and schedules (", agDoc("configure", "Configure"),
        "), install it as a service on Windows or Linux or run the container (", agDoc("install", "Install"),
        "), then check it shows in this list within a minute of starting (", agDoc("verify-on-your-tenant", "Verify"), ")."]],
      ["Where this is going", ["Now, an agent per server with its own durable queue, retries and a write lease per table. In Preview and off by default, several agents sharing a schedule and taking over from one that stops reporting (",
        agDoc("shared-schedules-preview", "Shared schedules"), "). Agents haven't been run against a live tenant yet."]],
    ],
    anchor: "agents-preview",
  });
}

async function loadAgents(box) {
  let out;
  try {
    out = await rpc("agents_list", {});
  } catch (err) {
    box.replaceChildren(h("p", { class: "reason monitor-empty" }, `Couldn't read agents: ${friendlyError(err).text}`));
    return;
  }
  if (!box.isConnected) return;
  const rows = (out.agents || []).filter((a) => matches(`${a.agent} ${a.host} ${a.version} ${agEnvs(a).join(" ")}`));
  const sources = (out.envs || []).map((e) => e.count === null
    ? h("span", {}, h("span", { class: "pill-s neu" }, e.observation?.label || "Can't read"), ` ${envName(e.env)}: ${e.observation?.detail || "couldn't be read"}`)
    : h("span", {}, h("b", {}, envName(e.env)), e.count ? ` ${e.count} ${e.count === 1 ? "agent" : "agents"}, read ${relTime(e.observation.collected_at)}` : " no agents"));
  box.replaceChildren(
    h("div", { class: "monitor-filters" },
      h("p", { class: "muted-text small grow" }, "Each agent writes a status file in every environment it serves; this page reads those files. Nothing here starts, stops or changes an agent."),
      btn("Refresh", "sync", () => loadAgents(box), "tool outline")),
    h("div", { class: "wl-sources inc-watchers" }, sources),
    agOverlaps(out) || "",  // replaceChildren would print a null
    rows.length ? h("div", { class: "monitor-scroll" }, h("table", { class: "list inc-list" },
      h("thead", {}, h("tr", {}, ["Agent", "State", "Serves", "Doing now", "Next scheduled", "Last 10 jobs", "Checks incidents", "Version"].map((c) => h("th", {}, c)))),
      h("tbody", {}, rows.map((a) => h("tr", { class: `click${a.state === "stopped" ? " inc-done" : ""}`, onclick: () => openAgent(a.agent) },
        h("td", { class: "nowrap" }, h("b", {}, a.agent), h("div", { class: "muted-text small" }, a.host ? `on ${a.host}` : "")),
        h("td", {}, agStatePill(a), h("div", { class: "muted-text small" }, agStateNote(a))),
        h("td", {}, agEnvs(a).join(", ")),
        h("td", {}, agDoing(a)),
        h("td", {}, agNext(a)),
        h("td", {}, agRecent(a)),
        h("td", {}, agChecks(a)),
        h("td", {}, agVersion(a)))))))
      : h("p", { class: "muted-text monitor-empty" }, state.query ? "No agents match your search."
        : "No agents serve these environments. An agent runs KratosForge on your own server; see Learn more to set one up."),
    h("p", { class: "muted-text small inc-foot" }, "An agent counts as stopped reporting once the time it promised to report again has passed. Its last report is shown as it was."));
}

// ---------- one agent ----------

async function renderAgent() {
  setView(h("div", { class: "page wl-page" }, loading()));
  const out = await attempt(() => rpc("agents_list", {}), "Couldn't read the agent");
  if (state.view !== "agent") return;
  const a = (out?.agents || []).find((x) => x.agent === agPage.name);
  if (!a) {
    setView(h("div", { class: "page" }, h("h1", {}, agPage.name || "Agent"),
      h("p", { class: "muted-text" }, out ? "No environment you can read has a status record from this agent." : "Its status couldn't be read."),
      btn("Back to Agents", null, backToAgents, "tool outline")));
    return;
  }
  agPage.detail = a;
  renderChrome();  // the breadcrumb names it
  const facts = [a.host ? `Server ${a.host}` : "",
    a.owner ? `Owner ${a.owner}` : "owner" in a ? "No owner set in agent.toml" : "",
    AG_IDENTITY[a.identity] ? `Signs in as ${AG_IDENTITY[a.identity]}` : "", `Version ${a.version || "unknown"}${a.version_compare === "older" ? ` (this app is ${out.app_version})` : ""}`,
    a.started_at ? `Started ${relTime(a.started_at)}` : "", `Last heartbeat ${relTime(a.heartbeat_at)}`,
    a.status_seconds ? `Reports every ${a.status_seconds} s` : ""].filter(Boolean);
  setView(h("div", { class: "page wl-page" },
    h("div", { class: "row wrap wl-head" },
      h("div", { class: "grow" },
        h("h1", {}, a.agent, " ", agStatePill(a), preview("agents", h("span", {}))),
        h("p", { class: "sub" }, facts.join(" · ")),
        h("p", { class: "muted-text small" }, a.state === "live" ? "From its status records, written on its own timer."
          : `Everything below is its last report, from ${relTime(a.heartbeat_at)}.`))),
    Object.entries(a.envs).map(([env, e]) => h("div", { class: "wl-grid inc-grid" },
      h("section", { class: "card pad" }, h("h2", {}, `What it runs in ${envName(env)}`), agRuns(a, e)),
      h("section", { class: "card pad" }, h("h2", {}, a.state === "live" ? "Doing now" : "Doing at its last report"), agNow(e, a)))),
    h("section", { class: "card pad" }, h("h2", {}, "Last jobs"), agJobs(a)),
    a.state === "live" && a.version_compare !== "older" ? null
      : h("section", { class: "card pad" }, h("h2", {}, "What you can do"), agAdvice(a, out.app_version))));
}

function agRuns(a, e) {
  const p = e.pipeline || {};
  const scheds = e.schedules || [];
  return h("div", { class: "inc-what" },
    h("div", {}, p.enabled ? `Pipeline jobs for ${(p.entities || []).join(", ") || "every entity"} · ${e.concurrency || 1} at a time · backfill ${p.allow_backfill ? "allowed" : "not allowed"}`
      : "Doesn't take pipeline jobs"),
    scheds.length ? h("table", { class: "mini" }, h("thead", {}, h("tr", {}, ["Schedule", "Runs", "Next", ""].map((c) => h("th", {}, c)))),
      h("tbody", {}, scheds.map((s) => h("tr", {}, h("td", {}, s.id, s.shared ? h("span", { class: "pill-s neu", title: "Shared with other agents through the mailbox: each due time runs once, on whichever claims it" }, "Shared") : null),
        h("td", { class: Array.isArray(s.entities) ? null : "muted-text" }, !Array.isArray(s.entities) ? "Doesn't say (an older version)"
          : s.entities.join(", ") || "Every table"),
        h("td", {}, s.next ? agAt(s.next) : "None"),
        h("td", { class: a.state === "live" ? "muted-text" : "reason" }, a.state === "live" ? ""
          : s.shared ? "Runs on the other agents that share it, if any are up"
          : s.next && new Date(s.next) < new Date() ? "Missed; runs once when the agent is back" : "Won't run while it is down")))))
      : h("p", { class: "muted-text" }, "No schedules here."));
}

function agNow(e, a) {
  const c = e.counts || {};
  const running = e.running || [];
  const inc = e.incidents;
  return h("div", { class: "inc-what" },
    h("div", {}, `${c.running || 0} running, ${c.queued || 0} queued`),
    running.map((j) => h("div", {}, `Job ${String(j.job_id).slice(0, 8)} · ${(j.entities || []).join(", ") || j.source} · attempt ${j.attempts || 1} · started ${relTime(j.started_at)}`,
      a.state === "offline" ? h("div", { class: "muted-text small" }, "Unknown since its last report. When the agent starts again it picks this job up again, because its worker never finished it.") : null)),
    h("h3", {}, "Checks incidents"),
    h("div", {}, !inc ? "Doesn't say (an older version)" : inc.enabled
      ? `Yes, every ${Math.round((inc.seconds || 300) / 60) || 1} min${inc.quiet_hours ? `, quiet ${inc.quiet_hours}` : ""}` : "No"));
}

function agJobs(a) {
  const jobs = Object.entries(a.envs).flatMap(([env, e]) => (e.recent || []).map((j) => ({ ...j, env })))
    .sort((x, y) => String(y.finished_at).localeCompare(String(x.finished_at)));
  if (!jobs.length) return h("p", { class: "muted-text" }, "No finished jobs in its reports yet.");
  return h("table", { class: "list" }, h("thead", {}, h("tr", {}, ["Finished", "Environment", "Job", "Started by", "Result"].map((c) => h("th", {}, c)))),
    h("tbody", {}, jobs.map((j) => h("tr", {},
      h("td", {}, when(j.finished_at)), h("td", {}, envName(j.env)), h("td", {}, String(j.job_id).slice(0, 8)), h("td", {}, j.source || ""),
      h("td", { class: j.state === "failed" ? "reason" : null }, j.state, j.error ? h("div", { class: "small" }, String(j.error).split("\n")[0].slice(0, 120)) : null)))));
}

function agAdvice(a, appVersion) {
  return h("div", { class: "inc-what" },
    a.state === "offline" ? h("div", {}, `This app can't restart an agent. On ${a.host || "its server"}, check the service, then run `,
      h("code", {}, "fabric-desktop agent status"), ". When it starts again it runs each missed schedule once and picks up any job it was running.") : null,
    a.state === "stopped" ? h("div", {}, "It shut down cleanly, so nothing was lost. Start it again on its server if its schedules should run.") : null,
    a.version_compare === "older" ? h("div", {}, `It runs ${a.version}; this app is ${appVersion}. Update it the way it was installed.`) : null,
    learnMore("agents-preview"));
}

registerFeature({
  id: "agents", preview: true,
  views: { agent: renderAgent },
  commands: [
    { label: "Agents", icon: "desktop", keywords: "monitor agents servers heartbeat queue workers status",
      run: backToAgents },
  ],
});
