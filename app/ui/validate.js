// Validate notebook: the ribbon's Validate button opens a panel beside the cells with two tabs.
// Problems shows the code checks at once and, after a clean run (every cell from the top in a fresh
// kernel on this PC, with table writes kept in a sandbox folder), what failed and where. Clean up
// lists tidy-ups the person ticks and applies (Undo puts the cells back) and suggestions that point
// at a cell. The engine does the work (validate_service.py, cleanup.py); docs/validate-notebook.md
// describes it.

// ---------- pure helpers (app/tests/ui.test.cjs runs these) ----------

const VALIDATE_VERDICTS = {
  ready: ["pass", "Ready", "no errors or warnings"],
  ready_with_warnings: ["wrn", "Ready with warnings", ""],
  not_ready: ["fail", "Not ready", ""],
  checked: ["neu", "Code checks", "nothing has run yet"],
};

// "Not ready: 1 error" and its style, from a check or a clean run's record.
function validateHeadline(result) {
  if (!result) return ["neu", ""];
  const [cls, label, none] = VALIDATE_VERDICTS[result.verdict] || VALIDATE_VERDICTS.checked;
  const c = result.counts || {};
  const parts = [];
  if (c.error) parts.push(`${c.error} error${c.error > 1 ? "s" : ""}`);
  if (c.warning) parts.push(`${c.warning} warning${c.warning > 1 ? "s" : ""}`);
  return [cls, `${label}: ${parts.join(", ") || none || "no errors or warnings"}`];
}

// The findings a count chip shows: all, or one severity.
function validateShown(findings, filter) {
  return (findings || []).filter((f) => !filter || filter === "all" || f.severity === filter);
}

// The code a result was made from, cell by cell, to tell what changed since.
function validateSnapshot(cells) {
  return (cells || []).map((c) => [c.cell_id, c.type === "markdown" ? "" : c.source || ""]);
}

// The numbers (from 1) of the cells added or changed since the snapshot; [] when nothing changed.
// A deleted cell counts as a change to the notebook, shown as cell 0.
function validateChanged(snapshot, cells) {
  if (!snapshot) return [];
  const was = new Map(snapshot);
  const out = [];
  (cells || []).forEach((c, i) => {
    const now = c.type === "markdown" ? "" : c.source || "";
    if (!was.has(c.cell_id) || was.get(c.cell_id) !== now) out.push(i + 1);
  });
  const ids = new Set((cells || []).map((c) => c.cell_id));
  if (snapshot.some(([id]) => !ids.has(id))) out.unshift(0);
  return out;
}

// The tidy-ups to apply: the ticked ones, or those ticked at first when nothing was touched.
function cleanupChosen(plan, chosen) {
  return (plan?.tidy || []).filter((t) => (chosen && t.id in chosen ? chosen[t.id] : t.on)).map((t) => t.id);
}

// The cells as Undo compares them: order, kind, code and settings.
function cleanupState(cells) {
  return JSON.stringify((cells || []).map((c) => [c.cell_id, c.type, c.source || "", c.meta || {}]));
}

// Undo is offered only while the cells are exactly what Apply made, so it never drops a later edit.
function cleanupUndoable(undo, cells) {
  return !!undo && undo.after === cleanupState(cells);
}

// ---------- state ----------

const VALIDATE_POLL_MS = 1000;

function validateOpen(nb) {
  return !!nb?.validate?.open && previewShown();
}

function validateRunning(nb) {
  return nb?.validate?.record?.state === "running";
}

function validateToggle(nb) {
  const on = validateOpen(nb);
  return preview("validate", btn("Validate", "ok", () => (on ? closeValidate(nb) : openValidate(nb)), `tool${on ? " toggled" : ""}`,
    { "aria-pressed": String(on), title: "Check this notebook works from the top, and clean up its code" }));
}

function validatePayload(nb) {
  return {
    name: nb.name,
    cells: nb.cells.map((c) => ({ type: c.type, source: c.source, meta: c.meta, cell_id: c.cell_id })),
    lakehouse_ref: nbLakehouse(nb) || null,
    runs_on: nbWhere(nb).fabric ? "fabric" : "pc",
  };
}

async function openValidate(nb, tab) {
  const v = nb.validate || {};
  nb.validate = { open: true, tab: tab || v.tab || "problems", filter: "all", check: null, record: v.record || null,
    snapshot: v.snapshot || null, plan: null, chosen: {}, undo: v.undo || null, problem: "" };
  if (nb.prove?.open) nb.prove.open = false;  // one panel beside the cells at a time
  renderNotebook();
  if (!nb.validate.record) {
    const last = (await rpc("notebook_validate_list", { name: nb.name, limit: 1 }).catch(() => []))[0];
    if (last) nb.validate.record = await rpc("notebook_validate_status", { id: last.id }).catch(() => null);
    if (nb.validate.record?.state === "running") followValidation(nb, nb.validate.record.id);
  }
  await Promise.all([refreshValidateCheck(nb), refreshCleanup(nb)]);
}

function closeValidate(nb) {
  if (validateRunning(nb)) toast("The clean run keeps going. Open Validate again to follow it.");
  nb.validate.open = false;
  renderNotebook();
}

async function refreshValidateCheck(nb) {
  const v = nb.validate;
  v.problem = "";
  try {
    const p = validatePayload(nb);
    v.check = await rpc("notebook_validate_check", { name: p.name, cells: p.cells, lakehouse_ref: p.lakehouse_ref, runs_on: p.runs_on });
    v.checkSnapshot = validateSnapshot(nb.cells);
  } catch (e) {
    v.problem = String(e?.message || e);
  }
  drawValidate(nb);
}

async function refreshCleanup(nb) {
  const v = nb.validate;
  try {
    v.plan = await rpc("notebook_cleanup_plan", { cells: validatePayload(nb).cells });
    v.chosen = {};
  } catch (e) {
    v.plan = { tidy: [], suggestions: [], problem: String(e?.message || e) };
  }
  drawValidate(nb);
}

// ---------- the clean run ----------

async function startCleanRun(nb) {
  const v = nb.validate;
  const p = validatePayload(nb);
  const uncontained = v.check?.run?.uncontained || [];
  let allow = false;
  if (uncontained.length) {
    const lines = uncontained.map((u) => `Cell ${u.cell}${u.line ? `, line ${u.line}` : ""}: ${u.text}`).join("\n");
    allow = await confirmDialog("Run it anyway?",
      `A clean run can't contain everything these cells do:\n${lines}\nTable writes still go to the sandbox.`, "Run clean check");
    if (!allow) return;
  }
  const run = await attempt(() => rpc("notebook_validate_start", { ...p, allow_uncontained: allow }), "Could not start the clean run");
  if (!run) return;
  v.record = await rpc("notebook_validate_status", { id: run.id }).catch(() => ({ id: run.id, state: "running" }));
  v.snapshot = validateSnapshot(nb.cells);
  v.tab = "problems";
  logActivity({ id: `validate:${run.id}`, status: "running", title: `${nb.name} · Validate`, text: "Running every cell from the top on this PC" });
  drawValidate(nb);
  followValidation(nb, run.id);
}

async function followValidation(nb, id) {
  const v = nb.validate;
  if (v.following === id) return;
  v.following = id;
  for (;;) {
    await new Promise((r) => setTimeout(r, VALIDATE_POLL_MS));
    const now = await rpc("notebook_validate_status", { id }).catch(() => null);
    if (!now) continue;
    v.record = now;
    drawValidate(nb);
    if (now.state !== "running") break;
  }
  v.following = null;
  const r = v.record;
  const ok = r.verdict === "ready" || r.verdict === "ready_with_warnings";
  logActivity({ id: `validate:${id}`, status: ok ? "ok" : "error", title: `${nb.name} · Validate`, text: r.summary || r.state });
  toast(`${nb.name}: ${r.summary || r.state}`, r.state === "failed");
  profileEarned(r.earned);
}

async function cancelCleanRun(nb) {
  await attempt(() => rpc("notebook_validate_cancel", { id: nb.validate.record.id }), "Could not cancel the clean run");
}

async function saveValidateReport(nb) {
  const out = await attempt(() => rpc("notebook_validate_save", { id: nb.validate.record.id }), "Could not save the report");
  if (out) toast(`Saved ${out.path}`);
}

// ---------- clean up ----------

function cleanupSnapshot(nb) {
  return nb.cells.map((c) => ({ type: c.type, source: c.source, meta: structuredClone(c.meta || {}), cell_id: c.cell_id }));
}

async function applyCleanup(nb) {
  const v = nb.validate;
  const ids = cleanupChosen(v.plan, v.chosen);
  if (!ids.length) return;
  const out = await attempt(() => rpc("notebook_cleanup_apply", { cells: validatePayload(nb).cells, ids }), "Could not clean up");
  if (!out) return;
  const before = cleanupSnapshot(nb);
  const gone = new Set(out.removed);
  const next = new Map(out.cells.map((c) => [c.cell_id, c.source]));
  for (const cell of [...nb.cells]) {
    if (cell.meta?.frozen) continue;
    if (gone.has(cell.cell_id)) {
      dropEditor(cell);
      nb.cells.splice(nb.cells.indexOf(cell), 1);
    } else if (next.has(cell.cell_id) && next.get(cell.cell_id) !== cell.source) {
      setSource(cell, next.get(cell.cell_id));
    }
  }
  v.undo = { cells: before, after: cleanupState(nb.cells) };
  markDirty();
  renderNotebook();
  toast(`Cleaned up: ${out.applied.length} tidy-up${out.applied.length > 1 ? "s" : ""}. Undo puts the cells back.`);
  await Promise.all([refreshValidateCheck(nb), refreshCleanup(nb)]);
}

async function undoCleanup(nb) {
  const v = nb.validate;
  if (!cleanupUndoable(v.undo, nb.cells)) {
    v.undo = null;
    toast("The cells changed after the clean-up, so Undo would lose those edits. Undo isn't offered any more.");
    drawValidate(nb);
    return;
  }
  disposeEditors();
  nb.cells = v.undo.cells.map(cellFrom);
  nb.cells.forEach((c) => { if (c.type === "markdown") c.editing = false; });
  v.undo = null;
  markDirty();
  renderNotebook();
  toast("The cells are back as they were.");
  await Promise.all([refreshValidateCheck(nb), refreshCleanup(nb)]);
}

// ---------- the panel ----------

function validatePanel(nb) {
  if (!validateOpen(nb)) return null;
  return h("aside", { class: "validatepane", "aria-label": "Validate notebook" }, validateContent(nb));
}

function drawValidate(nb) {
  if (state.notebook !== nb || state.view !== "notebook" || !validateOpen(nb)) return;
  document.querySelector(".validatepane")?.replaceChildren(...validateContent(nb));
}

function validateContent(nb) {
  const v = nb.validate;
  const head = h("div", { class: "explorer-head" }, h("span", { class: "row" }, "Validate", previewTag()),
    h("button", { class: "tool small", "aria-label": "Close Validate", onclick: () => closeValidate(nb) }, icon("dismiss", 14)));
  const tab = (id, label) => h("button", { class: `tab${v.tab === id ? " active" : ""}`, role: "tab", "aria-selected": String(v.tab === id),
    onclick: () => { v.tab = id; drawValidate(nb); } }, label);
  const result = validateResult(nb);
  const tabs = h("div", { class: "ribbon-tabs validate-tabs", role: "tablist", "aria-label": "Validate" },
    tab("problems", `Problems ${result ? (result.counts?.error || 0) + (result.counts?.warning || 0) : ""}`.trim()),
    tab("cleanup", `Clean up ${v.plan ? v.plan.tidy.length + v.plan.suggestions.length : ""}`.trim()));
  return [head, tabs, v.tab === "cleanup" ? cleanupBody(nb) : problemsBody(nb), validateHow()];
}

// The clean run when there is one for this code, else the code checks.
function validateResult(nb) {
  const v = nb.validate;
  if (v.record && v.record.state !== "running" && v.record.findings) return v.record;
  return v.check;
}

function validateFinding(nb, f) {
  const tag = { error: ["fail", "Error"], warning: ["wrn", "Warning"], info: ["neu", "Info"] }[f.severity] || ["neu", f.severity];
  return h("div", { class: `validate-find ${f.severity}` },
    h("div", { class: "row" }, h("span", { class: `pill-s ${tag[0]}` }, tag[1]), h("b", {}, f.title)),
    f.text ? h("p", {}, f.text) : null,
    f.fix ? h("p", { class: "fix" }, f.fix) : null,
    (f.found_by || []).length > 1 ? h("span", { class: "muted small" }, "Found by the clean run and by reading the code") : null,
    f.cell ? h("button", { class: "link-btn", onclick: () => depsGo(nb, f.cell) }, `Go to cell ${f.cell}`) : null);
}

function problemsBody(nb) {
  const v = nb.validate;
  const body = h("div", { class: "prove-body" });
  if (v.problem) {
    body.append(h("div", { class: "problems" }, v.problem), btn("Check again", "restart", () => refreshValidateCheck(nb), "tool outline"));
    return body;
  }
  const r = v.record;
  if (r?.state === "running") {
    const done = (r.cells || []).length;
    body.append(h("div", { class: "prove-banner" }, h("span", { class: "spinner", "aria-hidden": "true" }),
      h("span", {}, h("b", {}, "Clean run: "), r.cell ? `running cell ${r.cell}` : "starting a fresh kernel", done ? `, ${done} done` : "")),
    btn("Cancel", "stop", () => cancelCleanRun(nb), "tool outline"));
    return body;
  }
  const result = validateResult(nb);
  if (!result) {
    body.append(h("span", { class: "muted" }, "Checking the notebook..."));
    return body;
  }
  const isRun = result === r;
  const changed = validateChanged(isRun ? v.snapshot : v.checkSnapshot, nb.cells);
  if (changed.length) {
    const which = changed.filter((n) => n > 0);
    body.append(h("div", { class: "prove-note" }, h("b", {}, "Out of date. "),
      which.length ? `Cell${which.length > 1 ? "s" : ""} ${which.join(", ")} changed after this check` : "Cells were deleted after this check",
      ", so the result below is for the notebook as it was. ",
      h("button", { class: "link-btn", onclick: () => refreshValidateCheck(nb) }, "Check again")));
  }
  const [cls, headline] = validateHeadline(result);
  body.append(h("div", { class: `prove-banner ${cls === "pass" ? "ok" : cls === "fail" ? "bad" : ""}` },
    icon(cls === "pass" ? "ok" : cls === "fail" ? "error" : "info", 18),
    h("span", {}, h("b", {}, headline), h("br"), isRun ? (r.summary || "") : "From reading the code. A clean run is what proves it works from the top.")));
  const counts = result.counts || {};
  const chip = (id, label, n) => h("button", { class: `chip${v.filter === id ? " on" : ""}`, "aria-pressed": String(v.filter === id),
    onclick: () => { v.filter = id; drawValidate(nb); } }, `${label} ${n}`);
  body.append(h("div", { class: "row wrap" }, chip("all", "All", (result.findings || []).length), chip("error", "Errors", counts.error || 0),
    chip("warning", "Warnings", counts.warning || 0), chip("info", "Info", counts.info || 0)));
  const shown = validateShown(result.findings, v.filter);
  for (const sev of ["error", "warning", "info"]) {
    const rows = shown.filter((f) => f.severity === sev);
    if (rows.length) body.append(proveSection({ error: "Errors", warning: "Warnings", info: "Info" }[sev], ...rows.map((f) => validateFinding(nb, f))));
  }
  if (isRun && (r.cells || []).length) {
    const status = { ok: "Succeeded", error: "Failed", cancelled: "Cancelled" };
    const ran = new Map(r.cells.map((c) => [c.cell, c]));
    const code = nb.cells.map((c, i) => [i + 1, c]).filter(([, c]) => c.type !== "markdown" && c.source.trim());
    body.append(proveSection("Clean run", ...code.map(([n]) => {
      const c = ran.get(n);
      return proveRow(`Cell ${n}`, c ? `${status[c.status] || c.status}${c.seconds != null ? ` · ${c.seconds.toFixed(1)} s` : ""}` : "Didn't run");
    })));
  }
  if (!isRun || changed.length) {
    body.append(proveSection("Clean run",
      h("p", { class: "muted small" }, "Starts a fresh kernel on this PC, with none of your variables, and runs every cell from the top, stopping at the first failure. ",
        "Tables are read as they are now. What the notebook writes goes to a sandbox folder in the attached lakehouse (",
        h("span", { class: "mono" }, "Files/_fabric_desktop/proofs/validate-…/"), "), never to its tables, and that folder is deleted when the run ends. ",
        "No Fabric job starts; reading and writing OneLake still uses some capacity. Your notebook's own kernel and variables stay as they are.")));
  }
  const plan = v.check?.run;  // what stops a clean run of the code as it is now
  const blocked = !!plan && !plan.ok;
  if (blocked) {
    const b = plan.blockers[0];
    body.append(h("div", { class: "prove-note bad" }, b.text.startsWith("uses Spark")
      ? `A clean run doesn't run Spark cells yet (cell ${b.cell} uses Spark), so this notebook gets code checks only for now.`
      : `A clean run can't contain cell ${b.cell}: it ${b.text}.`));
  }
  body.append(h("div", { class: "row wrap" },
    btn(isRun ? "Validate again" : "Run clean check", "play", () => startCleanRun(nb), "tool primary", { disabled: blocked }),
    isRun ? btn("Save report", "save", () => saveValidateReport(nb), "tool outline") : btn("Check again", "restart", () => refreshValidateCheck(nb), "tool outline")));
  return body;
}

function cleanupBody(nb) {
  const v = nb.validate;
  const body = h("div", { class: "prove-body" },
    h("p", { class: "muted small" }, "Tidy-ups change only the cells, never the lakehouse. Nothing changes until you apply them, and Undo puts the cells back."));
  if (v.undo && !cleanupUndoable(v.undo, nb.cells)) v.undo = null;  // edited since: Undo would lose those edits
  if (v.undo) body.append(h("div", { class: "prove-note" }, "Cleaned up. ", h("button", { class: "link-btn", onclick: () => undoCleanup(nb) }, "Undo")));
  const plan = v.plan;
  if (!plan) {
    body.append(h("span", { class: "muted" }, "Reading the code..."));
    return body;
  }
  if (plan.problem) body.append(h("div", { class: "problems" }, plan.problem));
  if (!plan.tidy.length && !plan.suggestions.length) {
    body.append(h("div", { class: "prove-banner ok" }, icon("ok", 18), h("span", {}, "Nothing to clean up.")));
    return body;
  }
  const chosen = new Set(cleanupChosen(plan, v.chosen));
  if (plan.tidy.length) {
    body.append(proveSection(`Tidy-ups (${plan.tidy.length})`, ...plan.tidy.map((t) => h("label", { class: "validate-change" },
      h("input", { type: "checkbox", checked: chosen.has(t.id), onchange: (e) => { v.chosen[t.id] = e.target.checked; drawValidate(nb); } }),
      h("span", {}, h("b", {}, t.title), h("span", { class: "muted small" }, t.text),
        ...(t.diff || []).map((d) => h("pre", { class: "validate-diff", "aria-label": `Lines removed from cell ${d.cell}` },
          d.removed.map((line) => h("span", {}, `- ${line}`))))))),
    h("div", { class: "row wrap" }, btn(`Apply ${chosen.size} tidy-up${chosen.size === 1 ? "" : "s"}`, "ok", () => applyCleanup(nb), "tool primary",
      { disabled: !chosen.size }))));
  }
  if (plan.suggestions.length) {
    body.append(proveSection(`Suggestions (${plan.suggestions.length})`, ...plan.suggestions.map((s) => h("div", { class: "validate-find warning" },
      h("div", { class: "row" }, h("span", { class: "pill-s wrn" }, s.kind === "optimize" ? "Optimize" : "Suggestion"), h("b", {}, s.title)),
      h("p", {}, s.text), s.fix ? h("p", { class: "fix" }, s.fix) : null,
      h("button", { class: "link-btn", onclick: () => depsGo(nb, s.cell) }, `Go to cell ${s.cell}`)))));
  }
  return body;
}

function validateHow() {
  return h("div", { class: "validate-how" }, howItWorks("validate", {
    reads: "The notebook's cells. A clean run also reads the attached lakehouse's tables, as they are when it starts.",
    changes: "Code checks and suggestions change nothing. A clean run writes tables only to Files/_fabric_desktop/proofs/validate-<id>/pc in the attached lakehouse and deletes that folder when it ends; what a cell does outside the lakehouse (files on this PC, network calls) really happens, so you're asked first. Applying tidy-ups changes the notebook's cells, saved to the notebook file on this PC.",
    results: "Shown here and kept on this PC (the last 100). Save report writes a new file to Downloads.",
    anchor: "validate-a-notebook-preview",
  }));
}
