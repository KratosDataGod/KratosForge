// Recent runs: the ribbon's Recent runs button opens a panel beside the cells with this notebook's
// recent runs, like Fabric's Recent runs and Databricks' job runs. It lists this PC's scheduled runs and the Fabric runs
// this app started for the notebook, newest first, from the engine's notebook_runs (schedule_service.py),
// which reads the two run logs Monitor > Runs reads. Opening it asks Fabric nothing; Refresh status
// asks Fabric for runs still going, as Monitor does. docs/notebooks.md#recent-runs-preview describes it.

// ---------- pure helpers (app/tests/ui.test.cjs runs these) ----------

const NB_RUN_TRIGGERS = {
  schedule: "Schedule",
  manual: "Schedule, Run now",
  home: "Run from Home",
  fabric: "Run in Fabric",
  run_now: "Fabric schedule, Run now",
  prove: "Prove this notebook",
  side_by_side: "Side by side",
};

// The pill a run shows: this PC's run records say ok/failed/skipped, Fabric's jobs say Completed and so on.
function nbRunStatus(r) {
  if (r.source === "pc") return r.status === "ok" ? "ok" : r.status === "skipped" ? "skipped" : "failed";
  return fabricStatus(r.status);
}

function nbRunWhere(r) {
  if (r.source === "pc") return "This PC";
  return r.workspace_name ? `Fabric: ${r.workspace_name}` : "Fabric";
}

// Fabric runs still going, which Refresh status asks Fabric about.
function nbRunsUnfinished(runs) {
  return (runs || []).filter((r) => r.source === "fabric" && nbRunStatus(r) === "running").length;
}

function nbRunKey(r) {
  return `${r.source}:${r.started}:${r.job_id || r.schedule_id || ""}`;
}

// The line under the panel's title: "4 runs, 1 failed" (of those listed).
function nbRunsSummary(runs) {
  const n = (runs || []).length;
  if (!n) return "No runs yet";
  const failed = runs.filter((r) => nbRunStatus(r) === "failed").length;
  const going = nbRunsUnfinished(runs);
  return [`${n} run${n === 1 ? "" : "s"}`, failed ? `${failed} failed` : "", going ? `${going} still going` : ""].filter(Boolean).join(", ");
}

// ---------- state ----------

const NB_RUNS_LIMIT = 50;

function runsOpen(nb) {
  return !!nb?.runs?.open && previewShown();
}

function runsToggle(nb) {
  const on = runsOpen(nb);
  return preview("notebookRuns", btn("Recent runs", "monitor", () => (on ? closeRuns(nb) : openRuns(nb)), `tool${on ? " toggled" : ""}`,
    { "aria-pressed": String(on), title: "This notebook's recent runs: its schedules on this PC and its runs in Fabric" }));
}

async function openRuns(nb) {
  nb.runs = { open: true, rows: nb.runs?.rows || null, problem: "", details: nb.runs?.details || new Set(), refreshing: false };
  if (nb.prove?.open) nb.prove.open = false;  // one panel beside the cells at a time
  if (nb.validate?.open) nb.validate.open = false;
  renderNotebook();
  await loadRuns(nb, false);
}

function closeRuns(nb) {
  nb.runs.open = false;
  renderNotebook();
}

// refresh: ask Fabric for the status of runs still going (Refresh status). Otherwise only the logs on this PC are read.
async function loadRuns(nb, refresh) {
  const r = nb.runs;
  r.problem = "";
  r.refreshing = refresh;
  drawRuns(nb);
  try {
    r.rows = await rpc("notebook_runs", { name: nb.name, limit: NB_RUNS_LIMIT, refresh });
  } catch (err) {
    r.problem = `Couldn't read this notebook's runs. ${friendlyError(err).text}`;
  }
  r.refreshing = false;
  drawRuns(nb);
}

// ---------- the panel ----------

function runsPanel(nb) {
  if (!runsOpen(nb)) return null;
  return h("aside", { class: "runspane", "aria-label": "Recent runs" }, ...runsContent(nb));
}

function drawRuns(nb) {
  if (state.notebook !== nb || state.view !== "notebook" || !runsOpen(nb)) return;
  document.querySelector(".runspane")?.replaceChildren(...runsContent(nb));
}

function runsContent(nb) {
  const r = nb.runs;
  const head = h("div", { class: "explorer-head" }, h("span", { class: "row" }, "Recent runs", previewTag()),
    h("button", { class: "tool small", "aria-label": "Close Recent runs", onclick: () => closeRuns(nb) }, icon("dismiss", 14)));
  const body = h("div", { class: "prove-body" });
  const going = nbRunsUnfinished(r.rows);
  body.append(h("div", { class: "row wrap" },
    h("span", { class: "muted small grow" }, r.rows ? nbRunsSummary(r.rows) : "Reading runs..."),
    btn("Refresh", "restart", () => loadRuns(nb, false), "tool outline small", { disabled: r.refreshing }),
    going ? btn("Refresh status", "sync", () => loadRuns(nb, true), "tool outline small",
      { disabled: r.refreshing, title: "Ask Fabric how the runs still going are doing. Starts nothing." }) : null));
  if (r.refreshing) body.append(h("div", { class: "prove-banner" }, h("span", { class: "spinner", "aria-hidden": "true" }), "Asking Fabric for the latest status..."));
  if (r.problem) body.append(h("div", { class: "problems" }, r.problem));
  if (r.rows && !r.rows.length) body.append(runsEmpty());
  for (const run of r.rows || []) body.append(runRow(nb, run));
  if (r.rows?.length) {
    body.append(h("p", { class: "muted small" }, "Cells you run in this window aren't listed, nor are runs a Fabric schedule starts on its own: those are in Fabric's Monitoring hub, under Monitor. ",
      h("button", { class: "link-btn", onclick: () => go("monitor") }, "Open Monitor")));
  }
  return [head, body, runsHow()];
}

function runsEmpty() {
  return h("div", { class: "runs-empty" },
    h("p", {}, h("b", {}, "No runs yet. ")),
    h("p", { class: "muted small" }, "Schedule (Home or Run tab) runs the saved notebook at set times, on this PC with Windows Task Scheduler or in Fabric on its capacity. ",
      "To run it once in Fabric, set Runs on to Fabric and press Run all. Runs on this PC show here when they finish; runs in Fabric as soon as they start."),
    h("div", { class: "row wrap" }, btn("Schedule", "history", scheduleNotebook, "tool outline small")));
}

function runRow(nb, run) {
  const key = nbRunKey(run);  // kept open across Refresh, which brings new row objects
  const open = nb.runs.details.has(key);
  const status = nbRunStatus(run);
  const toggle = () => { open ? nb.runs.details.delete(key) : nb.runs.details.add(key); drawRuns(nb); };
  const action = run.source === "fabric"
    ? (run.url ? h("button", { class: "link-btn", onclick: () => openInFabric(run.url) }, "Open in Fabric") : null)
    : h("button", { class: "link-btn", "aria-expanded": String(open), onclick: toggle }, open ? "Hide details" : "Details");
  return h("div", { class: `nbrun ${status}` },
    h("div", { class: "row" }, statusPill(status), h("b", { class: "grow" }, NB_RUN_TRIGGERS[run.trigger] || run.trigger), when(run.started)),
    h("div", { class: "muted small" }, [nbRunWhere(run), took(run.seconds)].filter(Boolean).join(" · ")),
    status === "failed" && run.message ? h("div", { class: "reason" }, run.message) : null,
    action,
    open ? h("dl", { class: "nbrun-detail" },
      h("dt", {}, "Started"), h("dd", {}, dateTimeText(run.started, { seconds: true })),
      h("dt", {}, "Ended"), h("dd", {}, dateTimeText(run.ended, { seconds: true, empty: "Not recorded" })),
      run.cells != null ? [h("dt", {}, "Cells run"), h("dd", {}, String(run.cells))] : null,
      h("dt", {}, "Result"), h("dd", {}, run.message || statusPill(status).textContent),
      run.alert_error ? [h("dt", {}, "Teams alert"), h("dd", {}, run.alert_error)] : null) : null);
}

function runsHow() {
  return h("div", { class: "validate-how" }, howItWorks("notebookRuns", {
    reads: "Two run logs in the app's folder on this PC, for this notebook only: this PC's scheduled runs (schedule_runs.jsonl) and the Fabric jobs this app started (fabric_runs.jsonl). Monitor > Runs reads the same logs. Opening the panel or Refresh asks Fabric nothing.",
    changes: "Nothing in Fabric or the lakehouse. Refresh status asks Fabric for the status of runs still going and saves the answer in fabric_runs.jsonl on this PC, as Monitor does. It starts no job.",
    results: "Shown here only. The same runs are in Monitor > Runs, with every other run.",
    anchor: "notebook-runs-preview",
  }));
}

registerFeature({
  id: "notebookRuns", preview: true,
  commands: [
    { label: "Show this notebook's runs", icon: "monitor", keywords: "runs history recent job runs schedule fabric",
      when: () => state.view === "notebook" && !!state.notebook, run: () => openRuns(state.notebook) },
  ],
});
