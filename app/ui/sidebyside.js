// Side by side: with the ribbon's Side by side toggle on, Run all runs the notebook on this PC,
// then publishes it and runs it in Fabric, then checks both wrote the same tables. The engine
// does the work (side_by_side_service.py); this file starts it, follows it and shows the result.
// The toggle is remembered per notebook on this PC.

const SBS_RUNNING = new Set(["local", "fabric", "checking"]);

function sbsOn(nb) {
  if (!nb || !previewShown()) return false;
  try { return localStorage.getItem(`fd.sbs.${nb.name}`) === "1"; } catch { return !!nb.sbsOn; }
}

function sbsRunning(nb) {
  return !!nb?.sbs && SBS_RUNNING.has(nb.sbs.state);
}

function setSbsOn(nb, on) {
  try { localStorage.setItem(`fd.sbs.${nb.name}`, on ? "1" : "0"); } catch { /* this session only */ }
  nb.sbsOn = on;
  if (on) toast("Side by side is on: Run all runs this notebook on this PC, then in Fabric, and compares them.");
  if (state.notebook === nb && state.view === "notebook") renderNotebook();
}

// The ribbon button.
function sbsToggle(nb) {
  const on = sbsOn(nb);
  return preview("sideBySide", btn(`Side by side${on ? ": On" : ""}`, "dataBarVertical", () => setSbsOn(nb, !on),
    `tool${on ? " toggled" : ""}`, { "aria-pressed": String(on), disabled: sbsRunning(nb),
      title: "Run all on this PC and in Fabric, then compare time and results" }));
}

async function runSideBySide() {
  const nb = state.notebook;
  if (!nb.lakehouse) {
    toast("Attach a lakehouse first. Both runs read and write it, so their results can be compared.", true);
    return;
  }
  if (!(await ensureSignedIn())) return;
  // The Fabric side runs on the workspace's capacity: the risk check, then a confirm, as Prove does.
  if (!(await riskGate({ id: `notebook:${nb.name}`, action: "run_in_fabric", title: `Run ${nb.name} side by side?`, runLabel: "Run side by side" }))) return;
  const confirm = await confirmSbsWrites(nb);
  if (!confirm) return;
  await nbInputsSaved();  // nbinputs.js: both runs get the values the Parameters bar shows
  const run = await attempt(() => rpc("side_by_side_start", { ...publishPayload(), ...confirm }), "Could not start the side-by-side run");
  if (run) depsRestarted();  // it restarts the kernel (deps.js)
  if (!run) return;
  nb.dirty = false;
  nb.sbs = run;
  nb.sbsCells = nb.cells.slice();  // the engine numbers cells as they were when the run started
  nb.cells.forEach((c) => { if (c.type === "code" && c.source.trim() && !c.meta?.frozen) { c.status = "queued"; c.outputs = []; } });
  setKernel("busy");
  renderNotebook();
  logActivity({ id: `sbs:${run.id}`, status: "running", title: `${nb.name} · Side by side`, text: "Running on this PC, then in Fabric" });
  followSideBySide(nb, run.id);
}

// Run id -> the notebook showing it, as proveFollows in prove.js.
const sbsFollows = new Map();

async function followSideBySide(nb, id) {
  const following = sbsFollows.has(id);
  sbsFollows.set(id, nb);
  if (following) return;
  const end = await pollRun(() => rpc("side_by_side_status", { id }), { onStatus: (now) => sbsUpdate(sbsFollows.get(id), now) });
  nb = sbsFollows.get(id);
  sbsFollows.delete(id);
  if (end.stopped === "lost") nb.sbs = { ...nb.sbs, state: "failed", message: `Lost track of the run: ${end.message}` };
  (nb.sbsCells || []).forEach((c) => { if (c.status === "queued") { c.status = null; if (state.notebook === nb) updateResults(c); } });
  if (state.notebook === nb) setKernel("idle");
  const s = nb.sbs;
  const text = s.state === "done" ? [s.verdict?.text, s.speed?.end_to_end].filter(Boolean).join(" ")
    : s.state === "cancelled" ? "Cancelled." : `Didn't finish: ${s.message}`;
  logActivity({ id: `sbs:${id}`, status: s.state === "done" && s.verdict?.same !== false ? "ok" : "error",
    title: `${nb.name} · Side by side`, text });
  toast(`${nb.name}: ${text}`, s.state === "failed");
}

// One status from the engine: copy this PC's cell results back and redraw. False once the run has ended.
function sbsUpdate(nb, now) {
  nb.sbs = now;
  for (const [i, c] of Object.entries(now.local?.cells || {})) {
    const cell = nb.sbsCells?.[Number(i)];
    if (!cell) continue;
    Object.assign(cell, { status: c.status, outputs: c.outputs || [], seconds: c.seconds, count: c.execution_count ?? cell.count });
    if (state.notebook === nb && state.view === "notebook" && nb.cells.includes(cell)) updateResults(cell);
  }
  drawSbs(nb);
  return SBS_RUNNING.has(now.state);
}

async function cancelSideBySide(nb) {
  await attempt(() => rpc("side_by_side_cancel", { id: nb.sbs.id }), "Could not cancel");
  toast("Cancelling the side-by-side run...");
}

// The last comparison of this notebook, when the notebook opens with the toggle on.
async function loadLastComparison(nb) {
  nb.sbs = null;
  const rows = await rpc("side_by_side_list", { name: nb.name, limit: 1 }).catch(() => []);
  if (!rows?.length || state.notebook !== nb) return;
  nb.sbs = rows[0];
  if (sbsRunning(nb)) {
    setKernel("busy");
    followSideBySide(nb, nb.sbs.id);
  }
  drawSbs(nb);
}

async function addBenchmarkNotebooks() {
  const ref = state.notebook?.lakehouse || null;
  const out = await attempt(() => rpc("benchmark_notebooks", { lakehouse_ref: ref }), "Could not add the benchmark notebooks");
  if (!out) return;
  try { localStorage.setItem(`fd.sbs.${out.notebooks[1]}`, "1"); } catch { /* the toggle starts off */ }
  await openNotebook(out.notebooks[0]);
  toast(out.added.length
    ? `Added ${out.added.join(" and ")}. ${ref ? "" : "Attach a lakehouse with Add data items, then "}Run all here once to make the data, then open ${out.notebooks[1]}.`
    : `${out.notebooks.join(" and ")} are already here.`);
}

// ---------- the panel above the cells ----------

// Both runs write the attached lakehouse's real tables, so the person confirms which ones first.
// A notebook that writes no tables still asks, as the Fabric run uses capacity.
// Resolves to the confirmation the engine needs, or null when they cancel.
async function confirmSbsWrites(nb) {
  const writes = await attempt(() => rpc("side_by_side_writes", { cells: notebookPayload().cells }), "Could not check the notebook");
  if (!writes) return null;
  const confirm = { confirm_writes: writes.tables, confirm_unknown: writes.unknown };
  const capacity = "The Fabric run uses Fabric capacity for one notebook run.";
  if (!writes.tables.length && !writes.unknown) {
    return (await confirmDialog(`Run ${nb.name} side by side?`, "Runs it on this PC, then publishes it and runs it in Fabric. "
      + `${capacity} It writes no tables.`, "Run side by side")) ? confirm : null;
  }
  return new Promise((resolve) => {
    const done = (value) => { closeDialog(); resolve(value); };
    showDialog("Overwrite these tables twice?", h("div", { class: "dialog-text" },
      h("p", {}, `Side by side runs this notebook on this PC and then in Fabric, and both runs write to ${nb.lakehouse.name} for real. ${capacity} What's in these tables now is replaced, then replaced again:`),
      writes.tables.length ? h("ul", {}, writes.tables.map((t) => h("li", { class: "mono" }, t))) : null,
      writes.unknown ? h("p", {}, "Some cells can write tables reading the code can't see: ", writes.reasons.join("; "), ".") : null,
      h("p", {}, "Prove runs both sides from the same table versions and keeps their writes in a sandbox, so your tables aren't changed.")), [
      btn("Overwrite and run", null, () => done(confirm), "tool primary"),
      previewShown() ? btn("Use Prove instead", null, () => { done(null); openProve(nb); }, "tool outline") : null,
      btn("Cancel", null, () => done(null), "tool outline"),
    ].filter(Boolean));
  });
}

function sbsPanel(nb) {
  const slot = h("div", { class: "sbs-slot" });
  if (sbsOn(nb) && nb.sbs === undefined) loadLastComparison(nb);
  slot.append(...[sbsContent(nb)].filter(Boolean));
  return slot;
}

function drawSbs(nb) {
  if (state.notebook !== nb || state.view !== "notebook") return;
  document.querySelector(".sbs-slot")?.replaceChildren(...[sbsContent(nb)].filter(Boolean));
  document.querySelectorAll("[aria-pressed]").forEach((b) => { if (b.textContent.startsWith("Side by side")) b.disabled = sbsRunning(nb); });
}

function sbsTime(s) {
  if (s === null || s === undefined) return "–";
  if (s < 59.95) return `${Number(s).toFixed(1)} s`;
  const secs = Math.round(s); // rounded once, so 779.6 s is 13 min 0 s, not 12 min 60 s
  if (secs < 3600) return `${Math.floor(secs / 60)} min ${secs % 60} s`;
  const mins = Math.round(secs / 60);
  return `${Math.floor(mins / 60)} h ${mins % 60} min`;
}

function sbsSize(n) {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(n >= 1e8 ? 0 : 1)} MB`;
  return `${Math.max(1, Math.round(n / 1e3))} KB`;
}

// "Cores on this PC" for the next run, kept with the notebook (side_by_side_cores). Match Fabric holds the
// run to the vCores of the notebook's Fabric session, so both sides compute on the same number of cores.
async function loadSbsCores(nb) {
  nb.sbsCores = null;
  const c = await rpc("side_by_side_cores", { name: nb.name, cells: notebookPayload().cells }).catch(() => null);
  if (state.notebook !== nb) return;
  nb.sbsCores = c;
  drawSbs(nb);
}

async function setSbsCores(nb, choice) {
  const c = await attempt(() => rpc("side_by_side_cores_set", { name: nb.name, cells: notebookPayload().cells, choice }), "Not saved");
  if (!c) return;
  nb.sbsCores = c;
  drawSbs(nb);
}

function sbsCoresText(c) {
  const picked = typeof c.choice === "number" ? "custom" : c.choice;
  if (picked === "match" && !c.fabric_vcores) {
    return `A Spark notebook's cores depend on its pool, so this uses all ${c.pc_cores} cores. Pick a number to match the pool.`;
  }
  if (picked === "match" && c.cores >= c.pc_cores) return `This PC has no more cores than the Fabric session's ${c.fabric_vcores} vCores, so it uses all ${c.pc_cores}.`;
  if (picked === "match") {
    return `Uses ${c.cores} cores, the vCores of this notebook's Fabric session (a Python notebook's default; a %%configure cell changes it). `
      + "Use this for a like-for-like comparison.";
  }
  if (picked === "custom") return `Uses ${c.cores} of ${c.pc_cores} cores, for example to match a bigger Fabric session.`;
  return c.fabric_vcores && c.fabric_vcores < c.pc_cores
    ? `Uses all ${c.pc_cores} cores. Fabric's session has ${c.fabric_vcores} vCores, so the comparison is about hardware as much as engines.`
    : `Uses all ${c.pc_cores} cores.`;
}

function sbsCores(nb, label = "Cores on this PC") {
  if (nb.sbsCores === undefined) loadSbsCores(nb);
  const c = nb.sbsCores;
  if (!c) return null;
  const picked = typeof c.choice === "number" ? "custom" : c.choice;
  const options = [["all", `All ${c.pc_cores} cores`], ["match", c.fabric_vcores ? `Match Fabric (${c.fabric_vcores} vCores)` : "Match Fabric"],
    ["custom", "Pick a number"]];
  const busy = sbsRunning(nb);
  return h("div", { class: "sbs-cores" },
    h("span", { class: "sbs-cores-label" }, label),
    h("div", { class: "row wrap" },
      h("div", { class: "seg", role: "group", "aria-label": "Cores on this PC" }, options.map(([value, text]) =>
        h("button", { class: picked === value ? "on" : null, "aria-pressed": String(picked === value), disabled: busy,
          onclick: () => { if (picked !== value) setSbsCores(nb, value === "custom" ? c.cores : value); } }, text))),
      picked === "custom" ? h("label", { class: "row" }, "Cores",
        h("input", { type: "number", min: "1", max: String(c.pc_cores), value: String(c.cores), class: "sbs-cores-n", disabled: busy, "aria-label": "Number of cores",
          onchange: (e) => {
            const n = Math.round(Number(e.target.value));
            if (n === nb.sbsCores?.cores) return;
            if (n >= 1 && n <= c.pc_cores) setSbsCores(nb, n);
            else e.target.value = String(c.cores);
          } }), h("span", { class: "muted" }, `of ${c.pc_cores}`)) : null),
    h("span", { class: "muted small" }, sbsCoresText(c), " Only side by side runs are held to it; Run all on its own uses every core."));
}

// This PC's time as processing and waiting on OneLake uploads, and what the uploads did. Uploads that ran
// in the background while cells computed (writes local first) cost no time, so they aren't the wait.
function sbsLocalSplit(local) {
  const up = local.upload || {};
  const wait = local.upload_wait_seconds || 0;
  const lines = [];
  lines.push(local.processing_seconds !== undefined && wait >= 0.05
    ? h("span", { class: "sbs-split", title: "Waiting is the time cells were held up by their writes: at the end of a cell, a read of a table the cell was still uploading, or a write straight to OneLake." }, h("i", { class: "sbs-key pc" }), `Processing on this PC ${sbsTime(local.processing_seconds)}`,
      h("i", { class: "sbs-key upload" }), `Waiting for OneLake uploads ${sbsTime(wait)}`)
    : h("span", {}, "No wait to start"));
  if (up.tables) {
    const what = up.bytes ? `${sbsSize(up.bytes)} to OneLake` : `${up.rows.toLocaleString()} rows to OneLake`;
    const rate = up.mb_per_second ? ` · about ${up.mb_per_second.toFixed(1)} MB/s` : "";
    const skipped = up.skipped ? ` · ${up.skipped} unchanged, skipped` : "";
    lines.push(h("span", { class: "muted", title: "Uploading time counts writes going side by side once. With writes local first, much of it ran while cells computed. Large writes can't upload faster than this PC's line, so compare processing on this PC with Fabric's run time for the compute." },
      `Uploaded ${what} in ${sbsTime(up.seconds)}${rate}${skipped}`));
  }
  return lines;
}

function sbsContent(nb) {
  const s = nb.sbs;
  if (!sbsOn(nb) && !sbsRunning(nb)) return null;
  if (!s) {
    return h("div", { class: "card pad sbs" },
      h("div", { class: "row" }, icon("dataBarVertical", 18), h("b", { class: "grow" }, "Side by side is on")),
      h("span", {}, "Run all runs this notebook on this PC, then publishes it and runs it in Fabric, one after the other. ",
        "Then it compares the time each took and checks both wrote the same tables: rows, columns and a checksum of every row."),
      sbsCores(nb),
      h("div", { class: "row" },
        h("a", { href: "#", onclick: (e) => { e.preventDefault(); addBenchmarkNotebooks(); } }, "Add the TPC-H benchmark notebooks"),
        sbsDocLink()));
  }
  if (SBS_RUNNING.has(s.state)) return sbsProgress(s);
  return sbsResult(nb, s);
}

function sbsDocLink() {
  return h("a", { href: "#", class: "learn-more", onclick: (e) => {
    e.preventDefault();
    attempt(() => invoke("open_doc", { page: "side-by-side.md" }), "Could not open the guide");
  } }, "How side by side works");
}

function sbsProgress(s) {
  const step = { local: 1, fabric: 2, checking: 3 }[s.state];
  const local = s.local || {};
  const f = s.fabric || {};
  const box = (n, title, text, extra) => h("div", { class: `sbs-step${n < step ? " done" : n === step ? " now" : ""}` },
    h("b", {}, `${n < step ? "✓" : n === step ? "●" : "○"} ${n}. ${title}`), h("span", {}, text), extra || null);
  return h("div", { class: "card pad sbs" },
    h("div", { class: "row" }, icon("dataBarVertical", 18), h("b", { class: "grow" }, `Side by side · step ${step} of 3`)),
    h("div", { class: "sbs-steps" },
      box(1, "Run on this PC", step > 1 ? `${sbsTime(local.seconds)}${local.status !== "ok" ? ` · ${local.status}` : ""}`
        : `Cell ${local.cell || 1} of ${local.cells_total || "?"}`),
      box(2, "Run in Fabric", step < 2 ? `Publishes to ${s.workspace_name || "its workspace"} and runs there`
        : step > 2 ? sbsTime(f.total_seconds) : `${f.status === "publishing" ? "Publishing" : f.job_status === "InProgress" ? "Running" : "Starting"}${f.elapsed ? ` · ${sbsTime(f.elapsed)}` : ""}`,
      f.url ? h("a", { href: "#", onclick: (e) => { e.preventDefault(); openInFabric(f.url); } }, "Open in Fabric") : null),
      box(3, "Check the outputs", "Rows, columns and checksums of every table both runs wrote")),
    h("span", { class: "muted" }, "The runs go one after the other so they never write the same table at once. ",
      "You can leave this page; the bell tells you when the comparison is ready."));
}

function sbsResult(nb, s) {
  const local = s.local || {};
  const f = s.fabric || {};
  const m = s.machine || {};
  const failed = s.state !== "done";
  const verdict = failed ? { same: false, text: s.state === "cancelled" ? "Cancelled." : `Didn't finish: ${s.message}` } : s.verdict;
  const pill = h("span", { class: `pill-s ${verdict.same === true ? "pass" : verdict.same === false ? "fail" : "neu"}` },
    verdict.same === true ? "✓ " : verdict.same === false ? "✗ " : "", verdict.text);
  const longest = Math.max(local.seconds || 0, f.total_seconds || 0) || 1;
  const width = (secs) => Math.max(2, Math.round(100 * (secs || 0) / longest));
  const bar = (secs, cls) => h("div", { class: `sbs-bar ${cls}`, style: `width:${width(secs)}%` });
  const wait = local.upload_wait_seconds || 0;
  const pcBar = wait >= 0.05 && local.processing_seconds !== undefined
    ? h("div", { class: "sbs-bar split", style: `width:${width(local.seconds)}%` },
      h("div", { class: "pc", style: `flex:${local.processing_seconds}` }), h("div", { class: "upload", style: `flex:${wait}` }))
    : bar(local.seconds, "pc");
  const money = (v) => (v === null || v === undefined ? "–" : `${f.currency === "USD" ? "$" : ""}${v.toFixed(v < 1 ? 3 : 2)}${f.currency && f.currency !== "USD" ? ` ${f.currency}` : ""}`);
  const at = dateTimeText(s.started_at);
  const sideStatus = (status, cell) => (status === "ok" ? null
    : h("span", { class: "pill-s fail" }, status === "failed" ? `Failed${cell ? ` at cell ${cell}` : ""}` : status));
  const rows = s.tables || [];
  const shown = nb.sbsAll ? rows : rows.slice(0, 8);
  const result = (r) => ({ match: ["pass", "✓ Match"], differs: ["fail", "✗ Differs"], only_local: ["wrn", "This PC only"],
    only_fabric: ["wrn", "Fabric only"], error: ["fail", "Couldn't read"] })[r.result] || ["neu", r.result];
  return h("div", { class: "card pad sbs" },
    h("div", { class: "row wrap" }, icon("dataBarVertical", 18), h("b", { class: "grow" }, `Side by side · ${at}`), pill,
      btn("Run again", "play", () => runAll(), "tool small", { disabled: state.kernel === "busy" }),
      btn("Close", "dismiss", () => { nb.sbs = null; drawSbs(nb); }, "tool small")),
    h("div", { class: "sbs-sides" },
      h("div", { class: "sbs-side" },
        h("span", { class: "muted" }, `This PC${m.cores_used ? ` · ${m.cores_used} of ${m.cores} cores` : m.cores ? ` · ${m.cores} cores` : ""}${m.memory_gb ? ` · ${m.memory_gb} GB` : ""}`),
        h("b", { class: "sbs-big" }, sbsTime(local.seconds)), sideStatus(local.status, local.failed_cell),
        ...sbsLocalSplit(local), h("span", { class: "muted", title: "Its OneLake reads and writes still use some capacity." }, "No Fabric job"), pcBar),
      h("div", { class: "sbs-side" },
        h("span", { class: "muted" }, `Fabric · ${s.workspace_name || ""} · ${f.engine === "python" ? "Python" : "Spark"} notebook${f.vcores ? ` · ${f.vcores} vCores` : ""}`),
        h("b", { class: "sbs-big" }, sbsTime(f.total_seconds)), sideStatus(f.status),
        h("span", {}, f.start_seconds !== undefined && f.start_seconds !== null
          ? `Session start ${sbsTime(f.start_seconds)} + run ${sbsTime(f.run_seconds)}`
          : f.job_seconds ? `Job ${sbsTime(f.job_seconds)} in Fabric, the rest publishing and queueing` : "Click to done"),
        h("span", { class: "muted" }, f.cu_seconds !== undefined && f.cu_seconds !== null
          ? `About ${Math.round(f.cu_seconds).toLocaleString()} CU-seconds · about ${money(f.cost)} (estimate)` : "Capacity use not known"),
        bar(f.total_seconds, "fabric"),
        f.url ? h("a", { href: "#", onclick: (e) => { e.preventDefault(); openInFabric(f.url); } }, "Open the run in Fabric") : null)),
    s.speed?.cores ? h("span", {}, s.speed.cores) : null,
    s.speed?.end_to_end ? h("b", {}, s.speed.end_to_end) : null,
    s.speed?.run ? h("span", {}, s.speed.run) : null,
    s.speed?.processing ? h("span", {}, s.speed.processing) : null,
    local.message ? h("div", { class: "problems" }, `This PC: ${local.message}`) : null,
    f.message ? h("div", { class: "problems" }, `Fabric: ${f.message}`) : null,
    rows.length ? h("div", { class: "sbs-table" },
      h("div", { class: "sbs-tr head" }, h("span", {}, "Table written"), h("span", {}, "Rows: this PC"), h("span", {}, "Rows: Fabric"),
        h("span", {}, "Columns"), h("span", {}, "Result")),
      shown.map((r) => {
        const [cls, label] = result(r);
        return h("div", { class: "sbs-tr", title: r.note || "" },
          h("span", { class: "mono" }, r.table), h("span", {}, r.local ? r.local.rows.toLocaleString() : "–"),
          h("span", {}, r.fabric ? r.fabric.rows.toLocaleString() : "–"), h("span", {}, r.columns || "–"),
          h("span", {}, h("span", { class: `pill-s ${cls}` }, label), r.note ? h("span", { class: "muted small" }, ` ${r.note}`) : null));
      }),
      rows.length > 8 ? h("a", { href: "#", onclick: (e) => { e.preventDefault(); nb.sbsAll = !nb.sbsAll; drawSbs(nb); } },
        nb.sbsAll ? "Show fewer" : `Show all ${rows.length} tables`) : null) : null,
    sbsCores(nb, "Cores on this PC for the next run"),
    h("span", { class: "muted small" }, "Both runs ran the same code against the same lakehouse, one after the other. Each table is checked at the ",
      "Delta version each run wrote: rows, column names and types, and a checksum of every row in any order (numbers to 10 significant digits). ",
      sbsDocLink()));
}

registerFeature({
  id: "sideBySide", preview: true,
  commands: [
    { label: "Side by side: compare this notebook on this PC and in Fabric", icon: "dataBarVertical",
      keywords: "benchmark compare performance speed local fabric laptop", when: () => state.view === "notebook" && !!state.notebook,
      run: () => setSbsOn(state.notebook, !sbsOn(state.notebook)) },
    { label: "Add the TPC-H benchmark notebooks", icon: "notebook", keywords: "benchmark tpch performance side by side test at scale",
      run: addBenchmarkNotebooks },
  ],
});
