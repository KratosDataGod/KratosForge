// Queue: what is running and what runs next, wherever it runs. Each place has its own scheduler
// and queue (Windows Task Scheduler on this PC, each agent's queue, Fabric's scheduler); this
// page reads what each one says (queue_list, workloads/queue.py) and labels who owns each row.
// It never starts, stops or reorders work, and makes no Fabric call of its own.
// Monitor > Queue shows it.

const QU_STATE = {
  running: ["info", "Running"],
  queued: ["neu", "Queued"],
  unknown: ["neu", "Unknown"],
  missed: ["warn", "Missed"],
  next: ["", "Next"],
};

// Monitor > Queue, kept for the session.
const quPage = { filter: "all" };

function quStatePill(r) {
  const [cls, label] = QU_STATE[r.state] || QU_STATE.unknown;
  return cls ? h("span", { class: `pill-s ${cls}` }, label) : h("span", { class: "muted-text" }, label);
}

function quWhere(r) {
  const w = r.where || {};
  if (w.kind === "agent") return btn(`Agent ${w.name}`, null, () => openAgent(w.name), "tool link small");
  if (w.kind === "agents") return h("span", {}, "Agents ", (w.names || []).map((n, i) => [i ? ", " : "", btn(n, null, () => openAgent(n), "tool link small")]));
  return w.kind === "fabric" ? `Fabric · ${w.name}` : w.name || "";
}

// When: a time ahead or behind, or how often for a Fabric schedule, which has no next time.
function quWhen(r) {
  if (r.state === "running" || r.state === "unknown") return r.at ? `started ${relTime(r.at)}` : r.as_of ? `at its last report, ${relTime(r.as_of)}` : "";
  if (r.state === "missed") return `was due ${relTime(r.at)}`;
  if (r.state === "queued") return r.where?.kind === "agent" ? "after what it is running" : "waiting to start";
  return r.at ? agAt(r.at) : r.text ? r.text : "";
}

function quSources(out) {
  const s = out.sources || {};
  const agents = (s.agents || []).filter((e) => e.count === null);
  const fab = s.fabric_schedules || {};
  return h("div", { class: "wl-sources inc-watchers" },
    s.pc ? h("span", {}, h("b", {}, "This PC"), s.pc.state === "live" ? ` read ${relTime(s.pc.collected_at)}` : `: ${s.pc.detail || "couldn't be read"}`) : null,
    h("span", {}, h("b", {}, "Agents"), " from their status records"),
    agents.map((e) => h("span", {}, h("span", { class: "pill-s neu" }, e.observation?.label || "Can't read"), ` ${envName(e.env)}: ${e.observation?.detail || "couldn't be read"}`)),
    h("span", {}, h("b", {}, "Fabric"), " jobs this app started, as last checked"),
    h("span", {}, h("b", {}, "Fabric schedules"), fab.state === "read" ? ` read ${relTime(fab.at)}`
      : fab.state === "reading" ? " being read on Schedules"
      : fab.state === "error" ? `: ${friendlyError(fab.detail).text}`
      : [" not read yet. ", btn("Read them on Schedules", null, () => { monitorPage.panel = "schedules"; go("monitor"); }, "tool link small")]));
}

function quRow(r) {
  return h("tr", { class: r.state === "missed" || r.state === "unknown" ? "inc-done" : null },
    h("td", {}, quStatePill(r)),
    h("td", {}, r.what),
    h("td", {}, r.env ? envName(r.env) : h("span", { class: "muted-text" }, "Not in an environment")),
    h("td", {}, quWhere(r)),
    h("td", {}, r.owner),
    h("td", {}, quWhen(r)),
    h("td", {}, r.started_by || "", r.note ? h("div", { class: "muted-text small" }, r.note) : null,
      r.url ? h("div", {}, btn("Open in Fabric", null, () => openLink(r.url), "tool link small")) : null));
}

function queuePanel() {
  const box = h("div", { class: "inc-panel" }, h("p", { class: "muted-text monitor-empty" }, "Reading queues..."));
  loadQueue(box);
  return box;
}

async function loadQueue(box) {
  let out;
  try {
    out = await rpc("queue_list", {});
  } catch (err) {
    box.replaceChildren(h("p", { class: "reason monitor-empty" }, `Couldn't read the queues: ${friendlyError(err).text}`));
    return;
  }
  if (!box.isConnected) return;
  const all = (out.rows || []).filter((r) => matches(`${r.what} ${r.owner} ${r.where?.name || ""} ${r.env || ""}`));
  const now = all.filter((r) => ["running", "queued", "unknown"].includes(r.state));
  const next = all.filter((r) => r.state === "next" || r.state === "missed");
  const count = { all: all.length, running: all.filter((r) => r.state === "running").length,
    queued: all.filter((r) => r.state === "queued").length, next: next.length };
  const pick = (id, label) => h("button", { class: `chip${quPage.filter === id ? " on" : ""}`, onclick: () => { quPage.filter = id; loadQueue(box); } }, `${label} · ${count[id]}`);
  const shown = quPage.filter === "running" ? { now: now.filter((r) => r.state === "running"), next: [] }
    : quPage.filter === "queued" ? { now: now.filter((r) => r.state === "queued"), next: [] }
    : quPage.filter === "next" ? { now: [], next } : { now, next };
  const group = (title, rows) => rows.length ? [h("tr", { class: "qu-group" }, h("td", { colspan: 7 }, title)), rows.map(quRow)] : null;
  box.replaceChildren(
    h("div", { class: "monitor-filters" },
      h("p", { class: "muted-text small grow" }, "Each place has its own scheduler and queue; this page reads what each one says and labels who owns each item. Nothing here starts, stops or reorders work."),
      btn("Refresh", "sync", () => loadQueue(box), "tool outline")),
    quSources(out),
    h("div", { class: "row qu-chips", role: "group", "aria-label": "Show" }, pick("all", "All"), pick("running", "Running"), pick("queued", "Queued"), pick("next", `Next ${out.hours || 24} h`)),
    shown.now.length || shown.next.length ? h("div", { class: "monitor-scroll" }, h("table", { class: "list inc-list" },
      h("thead", {}, h("tr", {}, ["State", "What", "Environment", "Where it runs", "Owned by", "When", "Started by"].map((c) => h("th", {}, c)))),
      h("tbody", {}, group("Running and queued", shown.now), group(`Next ${out.hours || 24} hours`, shown.next))))
      : h("p", { class: "muted-text monitor-empty" }, state.query ? "Nothing matches your search." : "Nothing is running, queued or scheduled in the next day."),
    h("p", { class: "muted-text small inc-foot" }, "Fabric shows only the jobs this app started and the schedules on items in your environments' workspaces. Everything else running in Fabric is in its Monitoring hub; this page doesn't poll your tenant."));
}

registerFeature({
  id: "queue", preview: true,
  commands: [
    { label: "Queue", icon: "history", keywords: "monitor queue running queued next scheduled agents fabric",
      run: () => { monitorPage.panel = "queue"; go("monitor"); } },
  ],
});
