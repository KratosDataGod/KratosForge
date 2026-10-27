// Monitor > Runs: local pipeline runs (P3, Preview). A run's row opens to its activities, read
// from that run's record on this PC when you open it (pipeline_run_status), never in the
// background. A mocked activity says Mocked and a run with any mock says "Succeeded with mocks",
// so a mocked run is never read as a real success. Pipeline runs in Fabric (Data Factory DF-1,
// Preview) are listed too, from the job history of the pipelines in the workspaces Schedules lists;
// their activities are read from Fabric when you open the row, never in the background. Both are
// reads: no Fabric job starts and no capacity is used.

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
      tags: mocked ? [h("span", { class: "pill-s neu" }, `${mocked} mocked`)] : [],
      seconds: run.seconds, rows: "—", pipelineRun: run.run_id,
    };
  });
}

// Fabric's job times often carry no zone; they are UTC.
const fabricUtc = (iso) => (iso && !/(Z|[+-]\d\d:?\d\d)$/.test(iso) ? `${iso}Z` : iso);
const FABRIC_RUN_STATUS = { succeeded: "ok", failed: "failed", running: "running", pending: "running", cancelled: "skipped", skipped: "skipped" };
const FABRIC_INVOKE = { Scheduled: "scheduled", Manual: "started by hand", Pipeline: "started by a pipeline" };

const FABRIC_STALE_MINUTES = 10;  // a run read as going longer ago than this may have finished since
const fabricActivityCache = new Map();  // run id -> what Fabric said; read again only after Refresh

// Refresh on Runs reads open Fabric runs' activities again; other redraws use what was read.
function forgetFabricActivities() { fabricActivityCache.clear(); }

// What each kind of Fabric run is called in Runs. Only a pipeline run opens to its activities.
const FABRIC_RUN_KIND = { pipeline: ["Pipeline", "Pipeline run in Fabric"], "copy job": ["Copy job", "Copy job run in Fabric"],
  dataflow: ["Dataflow", "Dataflow refresh in Fabric"] };

// Runs rows for pipeline, copy job and dataflow runs in Fabric (fabric_pipeline_runs). A run this app started is already
// listed from fabric_runs, so it isn't listed twice. A status is only as fresh as the scan: the row
// says when it was read, and a run read as going a while ago shows Stale, not Running.
function fabricPipelineRunRows(result, cloud) {
  const ours = new Set((cloud || []).map((r) => r.job_id).filter(Boolean));
  const read = result?.scanned ? new Date(result.scanned) : null;
  const asOf = read ? `as of ${read.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "";
  const old = !read || Date.now() - read > FABRIC_STALE_MINUTES * 60000;
  return (result?.runs || []).filter((run) => run.job_id && !ours.has(run.job_id)).map((run) => {
    const going = run.state === "running" || run.state === "pending";
    const stale = going && old;
    const known = run.state in FABRIC_RUN_STATUS;
    const [noun, kind] = FABRIC_RUN_KIND[run.kind] || FABRIC_RUN_KIND.pipeline;
    const pipeline = !run.kind || run.kind === "pipeline";
    return {
      at: fabricUtc(run.started), status: stale || !known ? "unavailable" : FABRIC_RUN_STATUS[run.state],
      pill: stale ? h("span", { class: "pill-s neu", title: "Running when last read; press Refresh to read it again" }, "Stale")
        : !known ? h("span", { class: "pill-s neu", title: `Fabric said ${run.native_status || "nothing"}` }, "Unknown")
        : run.state === "cancelled" ? h("span", { class: "pill-s neu" }, "Cancelled") : undefined,
      activity: run.name || noun, env: "", where: "fabric", ranOn: `Fabric · ${run.workspace_name || "workspace"}`,
      kind, note: [FABRIC_INVOKE[run.invoke], asOf].filter(Boolean).join(" · "),
      reason: run.state === "failed" ? run.message || "" : "",
      envWhy: "Runs in Fabric are listed by workspace, not by the project's environments",
      seconds: run.seconds ?? "", tookText: run.seconds === null || run.seconds === undefined ? "—" : undefined,
      rows: "—", pipelineRun: pipeline ? `fabric:${run.job_id}` : undefined, fabricRun: run,
      open: pipeline ? undefined : () => openInFabric(fabricItemLink({ type: run.kind === "copy job" ? "CopyJob" : "Dataflow",
        workspace_id: run.workspace_id, item_id: run.item_id })),
      openLabel: pipeline ? undefined : "Open in Fabric",
    };
  });
}

// What Runs says about the Fabric pipeline scan: still reading, or why nothing is listed.
function fabricPipelineRunNote(result) {
  if (!result) return "";
  if (result.pending) return "Reading pipeline, copy job and dataflow runs in Fabric...";
  if (result.error) return `Runs in Fabric not shown: ${String(result.error).replace(/\.$/, "")}`;
  return result.missed ? `Runs of ${result.missed} item${result.missed === 1 ? "" : "s"} in Fabric couldn't be read` : "";
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
  if (r.fabricRun) {
    const run = r.fabricRun;
    return h("button", { class: "tool small link", "aria-label": `Open ${r.activity} in Fabric`,
      onclick: (e) => { e.stopPropagation(); openInFabric(fabricItemLink({ type: "DataPipeline", workspace_id: run.workspace_id, item_id: run.item_id })); } },
    "Open in Fabric");
  }
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
  if (r.fabricRun) { await showFabricActivities(tr, r); return; }
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

// A Fabric pipeline run's activities, oldest first, read from Fabric when the row opens.
async function showFabricActivities(tr, r) {
  const id = r.pipelineRun;
  const run = r.fabricRun;
  const wait = pipelineChildRow(id, h("td", { colspan: "8", class: "muted-text small" }, "Activities not loaded yet: reading them from Fabric..."));
  tr.after(wait);
  let { found, problem } = fabricActivityCache.get(id) || {};
  if (!found && !problem) {
    try {
      found = await rpc("fabric_pipeline_activity_runs", { workspace_id: run.workspace_id, job_id: run.job_id,
        started: run.started || "", ended: run.ended || "" });
    } catch (err) {
      const raw = String(err?.message || err);
      problem = /\b(401|403)\b|Unauthorized|Forbidden/i.test(raw) ? "Not authorized to read activities"
        : `Activities not loaded: ${friendlyError(err).text.replace(/\.$/, "")}`;
    }
    fabricActivityCache.set(id, { found, problem });
  }
  if (!wait.isConnected || !pipelineRunsOpen.has(id)) return;
  if (!found) {
    // A read that failed says so; it never looks like a run with no activities, or a failed one.
    wait.replaceWith(pipelineChildRow(id, h("td", { colspan: "8", class: "muted-text small" },
      `${problem || "Activities not loaded: Fabric didn't return them"}. Refresh tries again.`)));
    return;
  }
  const acts = [...(found.activities || [])].sort((a, b) => String(a.started || "").localeCompare(String(b.started || "")));
  const ran = acts.filter((a) => a.state !== "skipped").length;
  tr.querySelector(".pl-summary")?.replaceWith(h("span", { class: "pl-summary" }, ` · ${ran} of ${acts.length} activities ran`));
  wait.replaceWith(...(acts.length ? acts.map((a) => fabricActivityRow(id, a)) : [pipelineChildRow(id,
    h("td", { colspan: "8", class: "muted-text small" }, run.state === "pending" || run.state === "running"
      ? "No activity has started yet. Refresh to see them as they run." : "Fabric has no activity records for this run."))]));
}

// One activity of a Fabric run: status, name and type, its error, and its input and output on request.
function fabricActivityRow(id, a) {
  const shown = (label, part) => (part?.text ? h("details", { class: "pl-io" }, h("summary", {}, label),
    h("pre", {}, part.text), part.truncated ? h("div", { class: "muted-text small" }, "Cut short: open the run in Fabric for all of it.") : null) : null);
  return pipelineChildRow(id,
    h("td", { class: "pl-indent" }, statusPill(FABRIC_RUN_STATUS[a.state] || a.state)),
    h("td", { class: "pl-indent" },
      h("div", {}, a.name, h("span", { class: "muted-text" }, ` · ${PIPELINE_TYPE_LABEL[a.type] || a.type}`),
        a.retry ? h("span", { class: "muted-text" }, ` · retry ${a.retry}`) : null),
      a.error?.message ? h("div", { class: "reason" }, [a.error.code, a.error.message].filter(Boolean).join(": ")) : null,
      shown("Input", a.input), shown("Output", a.output)),
    h("td", {}), h("td", {}), h("td", {}, a.started ? when(fabricUtc(a.started)) : ""),
    h("td", { class: "n" }, a.ms === null || a.ms === undefined ? "—" : took(a.ms / 1000)),
    h("td", { class: "n" }, "—"),
    h("td", {}));
}
