// Notebook sessions (kernel_sessions.py): each notebook's kernel, like a Fabric or Databricks
// session. "Sessions" in the notebook status bar lists them with their state, memory and last
// use, and stops one. With notebooks in their own process (Settings) every notebook has its own
// process and several run at once; inside the engine they share one kernel.
// A notebook can also share another's session (Preview), as Fabric's high-concurrency sessions do:
// one process, one spark with its temp views and settings, one cell at a time, variables each its own.

let sharedSessions = {};  // notebook -> the notebook whose session it shares, from the last Sessions list

// How many notebooks share the session ``name`` runs in, counting the one that hosts it; 0 when it's its own.
function sharedCount(name) {
  const host = sharedSessions[name] || name;
  const guests = Object.values(sharedSessions).filter((x) => x === host).length;
  return guests ? guests + 1 : 0;
}

function sessionsButton() {
  const shared = sharedCount(state.notebook?.name);
  return preview("sessions", h("button", { class: `status-link${shared ? " shared-session" : ""}`,
    title: "Each notebook's session: state, memory, Stop, Share",
    onclick: () => sessionsDialog() }, shared ? `Shared session · ${shared} notebooks` : "Sessions"));
}

async function sessionsDialog() {
  const data = await attempt(() => rpc("kernel_sessions"), "Could not list sessions");
  if (!data) return;
  data.notes.forEach((n) => toast(n));
  sharedSessions = data.shared || {};
  const current = state.notebook?.name;
  const iso = (t) => new Date(t * 1000).toISOString();
  const rows = data.sessions.map((r) => h("tr", {},
    h("td", {}, r.notebook || "All notebooks", r.active && data.process ? h("span", { class: "muted" }, " (open)") : null,
      r.shared_with?.length ? h("div", { class: "muted" }, `Shared with ${r.shared_with.join(", ")}`) : null),
    h("td", {}, h("span", { class: `dot ${r.status === "running" ? "busy" : r.status === "stopped" ? "stopped" : "idle"}` }), " ",
      { running: "Running", idle: "Idle", stopped: "Stopped", ready: "Starts with the first cell" }[r.status]),
    h("td", {}, r.process ? (r.pid ? `Process ${r.pid}` : "No process") : "Inside the engine"),
    h("td", {}, r.memory_bytes ? formatBytes(r.memory_bytes) : ""),
    h("td", {}, String(r.cells_run || 0)),
    h("td", {}, when(iso(r.last_used))),
    h("td", {}, r.process && r.notebook ? h("div", { class: "session-actions" },
      r.notebook !== state.notebook?.name ? btn("Open", null, () => { closeDialog(); openNotebook(r.notebook); }, "tool small") : null,
      r.pid ? btn("Stop", "stop", () => stopSession(r.notebook), "tool small", { title: "Ends its process. Its variables are cleared." }) : null) : null)));
  const note = data.process
    ? `Each notebook runs in a process of its own, so several run at once. At most ${data.max_sessions || 5} keep a process; `
      + (data.idle_minutes ? `idle ones stop after ${data.idle_minutes} minutes.` : "idle ones are kept until you stop them.")
    : "Notebooks share one kernel inside the app's engine, so one cell runs at a time. Turn on Run notebooks in their own process in Settings to give each notebook its own session.";
  showDialog("Notebook sessions", h("div", { class: "scroll sessions-body" },
    h("p", { class: "dialog-text" }, note),
    data.process && current ? shareSection(current, data) : null,
    rows.length ? h("table", { class: "list sessions" },
      h("thead", {}, h("tr", {}, ["Notebook", "State", "Where", "Memory", "Cells run", "Last used", ""].map((t) => h("th", {}, t)))),
      h("tbody", {}, rows)) : h("p", { class: "muted" }, "No sessions yet. A session starts with a notebook's first cell.")),
  [btn("Settings", "settings", () => { closeDialog(); go("settings"); }, "tool outline"), btn("Close", null, closeDialog, "tool primary")]);
}

async function stopSession(name) {
  const nb = openNotebooks.get(name);
  if (nb) Object.assign(nb, { stopRequested: true, cancelRequested: true });
  if (await attempt(() => rpc("stop_kernel_process", { notebook: name }), "Could not stop the session")) {
    if (nb && nb.kernelStatus !== "busy") nb.kernelStatus = "stopped";
    toast(`Stopped ${name}'s session. Its variables were cleared.`);
    closeDialog();
    sessionsDialog();
    if (state.view === "notebook" && state.notebook === nb) renderNotebook();
  }
}

// Share: another notebook joins this notebook's session (its own session ends, so its variables are
// cleared). A notebook that shares one says whose and can Leave it.
function shareSection(current, data) {
  const host = sharedSessions[current];
  if (host) {
    return previewMarked("sharedSessions", h("div", { class: "row share-session" },
      h("span", { class: "grow" }, `${current} shares ${host}'s session: its spark, temp views and settings, with variables of its own.`),
      btn("Leave", null, () => leaveSession(current), "tool small", { title: "Its next cell starts a session of its own. Its variables in the shared session are cleared." })));
  }
  const hosting = new Set(Object.values(sharedSessions));
  const candidates = [...new Set([...openNotebooks.keys(), ...data.sessions.map((r) => r.notebook).filter(Boolean)])]
    .filter((n) => n !== current && sharedSessions[n] !== current && !hosting.has(n)).sort();
  if (!candidates.length) return null;
  const pick = h("select", { id: "share-with", "aria-label": "Notebook to share this session with" },
    candidates.map((n) => h("option", { value: n }, `${n} (its variables are cleared)`)));
  return h("div", { class: "share-session" },
    previewMarked("sharedSessions", h("label", { for: "share-with", class: "field-label" }, "Share this notebook's session with")),
    h("div", { class: "row" }, pick, btn("Share", null, () => shareSession(pick.value, current), "tool primary small")),
    h("p", { class: "muted" }, "Notebooks in a shared session share spark, its temp views and settings, and run one cell at a time. "
      + "Variables stay each notebook's own. They need the same default lakehouse and environment. Stop ends the session for all of them."));
}

async function shareSession(guest, host) {
  const nb = openNotebooks.get(guest);
  if (nb?.kernelStatus === "busy") { toast(`A cell is running in ${guest}. Share when it has finished.`); return; }
  if (!await confirmDialog(`Share ${host}'s session with ${guest}?`,
    `${guest}'s own session ends and its variables are cleared. From then on it runs in ${host}'s session, with its own variables.`,
    "Share", { danger: true })) return;
  const out = await attempt(() => rpc("kernel_session_share", { notebook: guest, host }), "Could not share the session");
  if (!out) return;
  if (nb) nb.kernelStatus = "idle";
  toast(`${guest} now shares ${out.host}'s session. Its variables were cleared.`);
  closeDialog();
  sessionsDialog();
  if (state.view === "notebook") renderNotebook();
}

async function leaveSession(name) {
  const out = await attempt(() => rpc("kernel_session_leave", { notebook: name }), "Could not leave the shared session");
  if (!out) return;
  toast(`${name} left ${out.left}'s session. Its next cell starts a session of its own.`);
  closeDialog();
  sessionsDialog();
  if (state.view === "notebook") renderNotebook();
}
