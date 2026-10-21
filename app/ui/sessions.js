// Notebook sessions (kernel_sessions.py): each notebook's kernel, like a Fabric or Databricks
// session. "Sessions" in the notebook status bar lists them with their state, memory and last
// use, and stops one. With notebooks in their own process (Settings) every notebook has its own
// process and several run at once; inside the engine they share one kernel.

function sessionsButton() {
  return preview("sessions", h("button", { class: "status-link", title: "Each notebook's session: state, memory, Stop",
    onclick: () => sessionsDialog() }, "Sessions"));
}

async function sessionsDialog() {
  const data = await attempt(() => rpc("kernel_sessions"), "Could not list sessions");
  if (!data) return;
  data.notes.forEach((n) => toast(n));
  const iso = (t) => new Date(t * 1000).toISOString();
  const rows = data.sessions.map((r) => h("tr", {},
    h("td", {}, r.notebook || "All notebooks", r.active && data.process ? h("span", { class: "muted" }, " (open)") : null),
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
