// Monitor > Runs: local pipeline runs (P3, Preview). A run's row opens to its activities, read
// from that run's record on this PC when you open it (pipeline_run_status), never in the
// background. A mocked activity says Mocked and a run with any mock says "Succeeded with mocks",
// so a mocked run is never read as a real success. Fabric pipeline runs aren't listed here yet.

const PIPELINE_TYPE_LABEL = {
  Copy: "Copy data", SetVariable: "Set variable", AppendVariable: "Append variable", ForEach: "ForEach",
  IfCondition: "If condition", Switch: "Switch", Until: "Until", Filter: "Filter", Wait: "Wait", Fail: "Fail",
  TridentNotebook: "Notebook", ExecutePipeline: "Invoke pipeline", InvokePipeline: "Invoke pipeline",
  Lookup: "Lookup", GetMetadata: "Get metadata", Script: "Script", WebActivity: "Web", Delete: "Delete",
};
const pipelineRunsOpen = new Set();   // run ids whose activities are showing; a refresh reads them again

// Run state -> the status Runs sorts and filters on, and the pill it shows.
function pipelineRunStatus(run) {
  const mocked = (run.options?.mocked || []).length;
  switch (run.state) {
    case "succeeded":
      return mocked ? { status: "ok", pill: h("span", { class: "pill-s wrn", title: "Some activities were mocked, so this isn't a real run of the whole pipeline" }, "Succeeded with mocks") }
        : { status: "ok" };
    case "failed": return { status: "failed" };
    case "running": case "pending": return { status: "running" };
    case "cancelled": return { status: "skipped", pill: h("span", { class: "pill-s neu" }, "Cancelled") };
    case "interrupted":
      return { status: "needs_decision", pill: h("span", { class: "pill-s wrn", title: "KratosForge stopped while this run was going. It wasn't resumed." }, "Interrupted") };
    default: return { status: "unavailable", pill: h("span", { class: "pill-s neu" }, "Unknown") };
  }
}

// Runs rows for local pipeline runs, in the shape renderMonitor uses.
function pipelineRunRows(runs) {
  return (runs || []).map((run) => {
    const mocked = (run.options?.mocked || []).length;
    return {
      at: run.started, ...pipelineRunStatus(run), activity: run.pipeline || "Pipeline", env: "", where: "local", ranOn: "This PC",
      kind: "Pipeline run", note: "", reason: run.state === "interrupted" ? "KratosForge stopped while this run was going" : "",
      envWhy: "Pipeline runs on this PC aren't tied to an environment",
      tags: [mocked ? h("span", { class: "pill-s neu" }, `${mocked} mocked`) : null, previewTag()],
      seconds: run.seconds, rows: "—", pipelineRun: run.run_id,
    };
  });
}

// After Runs is drawn again (Refresh, a filter, the pop-out's timer), the runs that were open read
// their activities again, so a run still going shows where it has got to.
function reopenPipelineRuns(list, rows) {
  const shown = new Map(rows.filter((r) => r.pipelineRun).map((r) => [r.pipelineRun, r]));
  for (const id of [...pipelineRunsOpen]) if (!shown.has(id)) pipelineRunsOpen.delete(id);
  for (const tr of list.querySelectorAll("tr.pl-run")) {
    const r = shown.get(tr.dataset.plRun);
    if (r && pipelineRunsOpen.has(r.pipelineRun)) showPipelineActivities(tr, r);
  }
}

// Details opens the run view (pipelines.js), where Stop and Run again live; until that view is in
// this build, a pipeline run's row only opens to its activities.
function pipelineRunDetails(r) {
  if (typeof openPipelineRun !== "function") return null;
  return h("button", { class: "tool small link", "aria-label": `Details of ${r.activity}`,
    onclick: (e) => { e.stopPropagation(); openPipelineRun(r.pipelineRun); } }, "Details");
}

// The ▸/▾ button in a pipeline run's Activity cell.
function pipelineToggle(r) {
  const open = pipelineRunsOpen.has(r.pipelineRun);
  return h("button", { class: "pl-toggle", "aria-expanded": String(open),
    "aria-label": `${open ? "Hide" : "Show"} the activities of ${r.activity}` }, open ? "▾" : "▸");
}

// Open or close a pipeline run's activities under its row.
async function togglePipelineRun(tr, r) {
  const id = r.pipelineRun;
  const toggle = tr.querySelector(".pl-toggle");
  let next = tr.nextElementSibling;
  while (next?.dataset.plRun === id) { const gone = next; next = next.nextElementSibling; gone.remove(); }
  if (pipelineRunsOpen.has(id)) {
    pipelineRunsOpen.delete(id);
    toggle?.replaceWith(pipelineToggle(r));
    return;
  }
  pipelineRunsOpen.add(id);
  toggle?.replaceWith(pipelineToggle(r));
  await showPipelineActivities(tr, r);
}

function pipelineChildRow(id, ...cells) {
  return h("tr", { class: "pl-child", "data-pl-run": id }, cells);
}

async function showPipelineActivities(tr, r) {
  const id = r.pipelineRun;
  const wait = pipelineChildRow(id, h("td", { colspan: "8", class: "muted-text small" }, "Loading activities..."));
  tr.after(wait);
  let record = null;
  let problem = "";
  try {
    record = await rpc("pipeline_run_status", { run_id: id, values: true });
  } catch (err) {
    problem = friendlyError(err).text;
  }
  if (!wait.isConnected || !pipelineRunsOpen.has(id)) return;
  if (!record) {
    wait.replaceWith(pipelineChildRow(id, h("td", { colspan: "8", class: "muted-text small" },
      `Activities not loaded: ${problem || "this run's record couldn't be read"}.`)));
    return;
  }
  const acts = record.activities || [];
  const top = acts.filter((a) => !a.parent);
  const ran = top.filter((a) => a.executed_by !== "not_started").length;
  tr.querySelector(".pl-summary")?.replaceWith(h("span", { class: "pl-summary" }, ` · ${ran} of ${top.length} activities ran`));
  const rows = top.length ? top.map((a) => pipelineActivityRow(id, a, acts)) : [pipelineChildRow(id,
    h("td", { colspan: "8", class: "muted-text small" }, record.state === "pending" || record.state === "running"
      ? "No activity has started yet. Refresh to see them as they run." : "This run has no activity records."))];
  wait.replaceWith(...rows);
}

// One top-level activity: its status, name and type, a mocked tag, and for a container its
// iterations or the inner activity that failed, with that activity's error.
function pipelineActivityRow(id, a, acts) {
  const kids = acts.filter((k) => k.parent === a.id);
  const mocked = a.executed_by === "mock";
  const iterations = new Set(kids.map((k) => k.label).filter((l) => l?.startsWith("iteration"))).size;
  const failedKid = a.state === "failed" ? kids.find((k) => k.state === "failed") : null;
  const err = (failedKid || a).error;
  const pill = mocked ? h("span", { class: "pill-s neu", title: "This activity didn't run: the run used the output you gave for it" }, "Mocked")
    : a.state === "cancelled" ? h("span", { class: "pill-s neu" }, a.executed_by === "not_started" ? "Not started" : "Cancelled")
    : statusPill({ succeeded: "ok", failed: "failed", skipped: "skipped", running: "running", interrupted: "needs_decision" }[a.state] || a.state);
  const type = (t) => PIPELINE_TYPE_LABEL[t] || t;
  return pipelineChildRow(id,
    h("td", { class: "pl-indent" }, pill),
    h("td", { class: "pl-indent" },
      h("div", {}, a.name, h("span", { class: "muted-text" }, ` · ${type(a.type)}`),
        iterations ? h("span", { class: "muted-text" }, ` · ${iterations} iteration${iterations === 1 ? "" : "s"}`) : null,
        failedKid ? [" › ", failedKid.name, h("span", { class: "muted-text" }, ` · ${type(failedKid.type)}`)] : null),
      err?.message ? h("div", { class: "reason" }, err.message) : null),
    h("td", {}), h("td", {}), h("td", {}),
    h("td", { class: "n" }, a.seconds === null || a.seconds === undefined ? "—" : took(a.seconds)),
    h("td", { class: "n" }, "—"),
    h("td", {}));
}
