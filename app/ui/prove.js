// Prove this notebook: the ribbon's Prove button opens a panel beside the cells that checks the
// notebook, runs it on this PC and in Fabric from the same pinned table versions with every write
// sandboxed, and shows whether the results match, how, and what it cost. The engine does the
// work (prove_service.py); this file starts it, follows it and shows the result, the differences,
// the scorecard and the receipt. docs/prove/how-proofs-work.md describes the engine.

const PROVE_RUNNING = new Set(["preparing", "pc", "fabric", "verifying"]);

// ---------- pure helpers (app/tests/ui.test.cjs runs these) ----------

// "a, b ,c" -> ["a", "b", "c"]
function proveNames(text) {
  return String(text || "").split(",").map((s) => s.trim()).filter(Boolean);
}

// A number from a form field, or undefined when it's empty. Text that isn't a number goes to the
// engine as typed so it refuses it (NaN would be sent as null and read as "use the default").
function proveNumber(text) {
  const t = String(text ?? "").trim();
  if (t === "") return undefined;
  const n = Number(t);
  return Number.isFinite(n) ? n : t;
}

// Rules for one table from the rules form's values; only what was filled in.
function proveRulesFrom(form) {
  const out = {};
  const keys = proveNames(form.keys);
  const ignore = proveNames(form.ignore);
  if (keys.length) out.keys = keys;
  if (ignore.length) out.ignore = ignore;
  for (const k of ["abs", "rel"]) {
    const n = proveNumber(form[k]);
    if (n !== undefined) out[k] = n;
  }
  if (form.timestamps && form.timestamps !== "us") out.timestamps = form.timestamps;
  return out;
}

// The words and style for each kind of scorecard number. Estimates and missing numbers are never
// shown as measurements.
function proveKind(kind) {
  return ({ measured: ["Measured", "info"], estimated: ["Estimate", "wrn"], "not available": ["Not available", "neu"] })[kind]
    || ["Not available", "neu"];
}

const PROVE_VERDICTS = {
  same: ["pass", "Same"], differs: ["fail", "Differs"], inconclusive: ["wrn", "Inconclusive"],
  failed: ["fail", "Failed"], nothing: ["neu", "Nothing to compare"], cancelled: ["neu", "Cancelled"],
};

function proveVerdict(record) {
  if (!record) return ["neu", ""];
  if (record.state === "cancelled") return PROVE_VERDICTS.cancelled;
  return PROVE_VERDICTS[record.verdict?.verdict] || ["neu", record.state];
}

// The rows of a difference grid from one table's verification: one row per changed column of a
// keyed comparison, or one per differing whole row. status filters: changed | only_pc | only_fabric.
function proveDiffRows(result, status) {
  const out = [];
  for (const e of result?.examples || []) {
    if (e.status !== status) continue;
    if (e.columns) {
      for (const [col, v] of Object.entries(e.columns)) out.push({ key: e.key, column: col, pc: v.pc, fabric: v.fabric });
    } else {
      out.push({ key: e.key || null, row: e.row || null, times: e.times || 1 });
    }
  }
  return out;
}

function proveValue(v) {
  if (v === null || v === undefined) return "null";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

function proveDifference(pc, fabric) {
  const a = Number(pc), b = Number(fabric);
  if (typeof pc === "number" && typeof fabric === "number" && Number.isFinite(a) && Number.isFinite(b)) {
    return String(Math.abs(a - b).toPrecision(3));
  }
  return "";
}

// Follows a run's status until it finishes (`onStatus` returns false), the run is gone (the engine
// says it doesn't know it) or `limit` checks in a row fail. Waits longer between failed checks.
// Prove and side by side both use it (HD-035). It goes on after the notebook closes, so the bell
// still gets the result.
async function pollRun(fetchStatus, { onStatus, every = 1500, limit = 20, sleep = (ms) => new Promise((r) => setTimeout(r, ms)) }) {
  let failures = 0;
  for (;;) {
    await sleep(every * Math.min(2 ** failures, 8));
    let now;
    try {
      now = await fetchStatus();
    } catch (e) {
      const message = String(e?.message || e);
      if (/(^|LookupError: )No (proof|side-by-side run) /.test(message) || ++failures >= limit) return { stopped: "lost", message };
      continue;
    }
    failures = 0;
    if (!onStatus(now)) return { stopped: "finished" };
  }
}

// ---------- state ----------

function proveOpen(nb) {
  return !!nb?.prove?.open && previewShown();
}

function proveRunning(nb) {
  return !!nb?.prove?.record && PROVE_RUNNING.has(nb.prove.record.state);
}

function proveToggle(nb) {
  const on = proveOpen(nb);
  return preview("prove", btn("Prove", "shield", () => (on ? closeProve(nb) : openProve(nb)), `tool${on ? " toggled" : ""}`,
    { "aria-pressed": String(on), title: "Run this notebook on this PC and in Fabric from the same inputs, and check the results match" }));
}

async function openProve(nb) {
  nb.prove = { open: true, check: null, rules: null, history: null, record: nb.prove?.record || null, problem: "" };
  if (nb.validate?.open) nb.validate.open = false;  // one panel beside the cells at a time
  if (nb.runs?.open) nb.runs.open = false;
  renderNotebook();
  if (!proveRunning(nb)) await refreshProveCheck(nb);
}

function closeProve(nb) {
  if (proveRunning(nb)) {
    toast("The proof keeps running. Open Prove again to follow it; the bell tells you when it's done.");
  }
  nb.prove.open = false;
  renderNotebook();
}

async function refreshProveCheck(nb) {
  const p = nb.prove;
  p.problem = "";
  if (!nb.lakehouse) {
    p.check = { check: { ok: false, blockers: [{ text: "Attach a lakehouse first. A proof pins its tables and keeps its writes there." }], warnings: [], notes: [], limits: [] } };
    drawProve(nb);
    return;
  }
  try {
    const [check, rules, history] = await Promise.all([
      rpc("prove_check", publishPayload()),
      rpc("prove_rules_get", { name: nb.name }).catch(() => ({})),
      rpc("prove_list", { name: nb.name, limit: 5 }).catch(() => []),
    ]);
    Object.assign(p, { check, rules, history });
    if (!p.record && history?.length && PROVE_RUNNING.has(history[0].state)) {
      p.record = history[0];
      followProof(nb, p.record.id);
    }
  } catch (e) {
    p.problem = friendlyError(e).text;
  }
  drawProve(nb);
}

// ---------- running ----------

async function startProof(nb) {
  const p = nb.prove;
  if (!(await ensureSignedIn())) return;
  // The Fabric side runs on the workspace's capacity: the risk check, then a confirm, as Run in Fabric
  // and the Fabric step do.
  if (!(await riskGate({ id: `notebook:${nb.name}`, action: "run_in_fabric", title: `Prove ${nb.name}?`, runLabel: "Start proof" }))) return;
  if (!(await confirmDialog(`Prove ${nb.name}?`, "Runs it on this PC, then publishes a copy and runs it in Fabric, which uses "
    + "Fabric capacity for one notebook run. Writes go to the proof's sandbox, not the lakehouse's tables.", "Start proof"))) return;
  const rules = p.rules || {};
  await nbInputsSaved();  // nbinputs.js: both runs get the values the Parameters bar shows
  const run = await attempt(() => rpc("prove_start", { ...publishPayload(), rules }), "Could not start the proof");
  if (run) depsRestarted();  // it restarts the kernel (deps.js)
  if (!run) return;
  nb.dirty = false;
  p.record = run;
  setKernel("busy");
  logActivity({ id: `prove:${run.id}`, status: "running", title: `${nb.name} · Prove`, text: "Running on this PC, then in Fabric" });
  drawProve(nb);
  followProof(nb, run.id);
}

// Run id -> the notebook showing it. Reopening a notebook makes a new object, which takes over the
// loop already following the run rather than starting a second one.
const proveFollows = new Map();

async function followProof(nb, id) {
  const following = proveFollows.has(id);
  proveFollows.set(id, nb);
  if (following) return;
  const end = await pollRun(() => rpc("prove_status", { id }), {
    onStatus: (now) => { const n = proveFollows.get(id); n.prove.record = now; drawProve(n); return PROVE_RUNNING.has(now.state); },
  });
  nb = proveFollows.get(id);
  proveFollows.delete(id);
  if (end.stopped === "lost") nb.prove.record = { ...nb.prove.record, state: "failed", message: `Lost track of the proof: ${end.message}` };
  if (state.notebook === nb) setKernel("idle");
  const r = nb.prove.record;
  const text = r.state === "cancelled" ? "Cancelled." : r.verdict?.text || r.message || r.state;
  logActivity({ id: `prove:${id}`, status: r.verdict?.verdict === "same" ? "ok" : "error", title: `${nb.name} · Prove`, text });
  toast(`${nb.name}: ${text}`, r.state === "failed");
  if (r.state === "done") profileEarned(r.earned);
  nb.prove.history = await rpc("prove_list", { name: nb.name, limit: 5 }).catch(() => nb.prove.history);
  drawProve(nb);
  // The verdict pill cools when both runs match; an ember when they differ or the proof failed (motion.js).
  const pill = state.notebook === nb && state.view === "notebook" ? document.querySelector(".provepane .prove-body .pill-s") : null;
  if (r.verdict?.verdict === "same") forgeEvent("prove.matched", pill);
  else if (r.state === "failed" || r.verdict?.verdict === "differs") forgeEvent("prove.failed", pill);
}

async function cancelProof(nb) {
  await attempt(() => rpc("prove_cancel", { id: nb.prove.record.id }), "Could not cancel the proof");
  toast("Cancelling the proof...");
}

// ---------- the panel ----------

function provePanel(nb) {
  if (!proveOpen(nb)) return null;
  return h("aside", { class: "provepane", "aria-label": "Prove this notebook" }, proveContent(nb));
}

function drawProve(nb) {
  if (state.notebook !== nb || state.view !== "notebook") return;
  document.querySelector(".provepane")?.replaceChildren(...proveContent(nb));
}

function proveHead(title) {
  const head = h("div", { class: "explorer-head" }, h("span", { class: "row" }, title));
  head.firstChild.append(previewTag());
  head.append(h("button", { class: "tool small", "aria-label": "Close", onclick: () => closeProve(state.notebook) }, icon("dismiss", 14)));
  return head;
}

function proveDocLink() {
  return h("a", { href: "#", class: "learn-more", onclick: (e) => {
    e.preventDefault();
    attempt(() => invoke("open_doc", { page: "prove/how-proofs-work.md" }), "Could not open the guide");
  } }, "How a proof works");
}

function proveContent(nb) {
  const p = nb.prove;
  const r = p.record;
  if (r && PROVE_RUNNING.has(r.state)) return [proveHead(`Proving ${nb.name}`), proveProgress(nb, r)];
  if (r) return [proveHead("Proof result"), proveResult(nb, r)];
  return [proveHead("Prove this notebook"), proveCheck(nb)];
}

function proveSection(title, ...children) {
  return h("section", { class: "prove-sec" }, h("h3", {}, title), ...children);
}

function proveRow(label, value) {
  return h("div", { class: "prove-row" }, h("span", {}, label), h("span", {}, value));
}

function proveCheck(nb) {
  const p = nb.prove;
  const body = h("div", { class: "prove-body" },
    h("p", { class: "muted small" }, "Runs this notebook on this PC and in Fabric from the same table versions, keeps both runs' writes in a sandbox, and checks whether the results match. ", proveDocLink()));
  if (p.problem) {
    body.append(h("div", { class: "problems" }, p.problem), btn("Check again", "restart", () => refreshProveCheck(nb), "tool outline"));
    return body;
  }
  if (!p.check) {
    body.append(h("span", { class: "muted" }, "Checking the notebook..."));
    return body;
  }
  const c = p.check.check;
  const plan = p.check.plan || {};
  body.append(c.ok
    ? h("div", { class: "prove-banner ok" }, icon("ok", 18), h("span", {}, h("b", {}, "Ready to prove. "),
      c.warnings.length ? `${c.warnings.length} warning${c.warnings.length > 1 ? "s" : ""}.` : "No warnings."))
    : h("div", { class: "prove-banner bad" }, icon("error", 18), h("span", {}, h("b", {}, "Can't prove this notebook yet. "),
      "Each reason names the cell to change.")));
  const where = (x) => (x.cell ? `Cell ${x.cell}${x.line ? `, line ${x.line}` : ""}: ` : "");
  if (c.blockers.length) body.append(proveSection("What stops it", ...c.blockers.map((b) => h("div", { class: "prove-note bad" }, where(b), b.text))));
  if (c.warnings.length) body.append(proveSection("Warnings (kept in the receipt)", ...c.warnings.map((w) => h("div", { class: "prove-note" }, where(w), w.text))));
  if (c.ok) {
    body.append(proveSection("What happens",
      plan.inputs ? proveRow("Inputs", plan.inputs) : null,
      plan.pc ? proveRow("This PC", plan.pc) : null,
      plan.fabric ? proveRow("Fabric", plan.fabric) : null,
      plan.outputs ? proveRow("Writes", plan.outputs) : null));
    const mods = (p.check.environment?.modules || []).filter((m) => !["standard", "provided"].includes(m.pc?.status));
    if (mods.length) {
      body.append(proveSection("Packages",
        h("div", { class: "prove-row head" }, h("span", {}, "Package"), h("span", {}, "This PC / Fabric")),
        mods.map((m) => proveRow(h("span", { class: "mono" }, m.module),
          `${m.pc?.version || m.pc?.status || "?"} / ${m.fabric?.version ? `${m.fabric.status} ${m.fabric.version}` : m.fabric?.status || "unknown"}`))));
    }
    body.append(proveRulesSection(nb));
    body.append(h("p", { class: "muted small" }, "Uses Fabric capacity for one Python notebook run. Your own notebook and its schedules aren't changed."),
      h("div", { class: "row wrap" },
        btn("Start proof", "shield", () => startProof(nb), "tool primary", { disabled: state.kernel === "busy" || sbsRunning(nb),
          title: state.kernel === "busy" ? "Wait for the running cell to finish" : "" }),
        btn("Check again", "restart", () => refreshProveCheck(nb), "tool outline")));
  }
  if (p.history?.length) {
    body.append(proveSection("Earlier proofs", p.history.map((r) => {
      const [cls, label] = proveVerdict(r);
      return h("div", { class: "prove-row" },
        h("a", { href: "#", onclick: (e) => { e.preventDefault(); showEarlierProof(nb, r.id); } },
          dateTimeText(r.started_at, { empty: r.id })),
        h("span", { class: `pill-s ${cls}` }, label));
    })));
  }
  return body;
}

async function showEarlierProof(nb, id) {
  const r = await attempt(() => rpc("prove_status", { id }), "Could not open that proof");
  if (!r) return;
  nb.prove.record = r;
  drawProve(nb);
}

function proveRulesSection(nb) {
  const p = nb.prove;
  const rules = p.rules || {};
  const d = rules.default || {};
  const tables = Object.entries(rules.tables || {});
  return proveSection("How results are compared",
    tables.map(([t, r]) => proveRow(h("span", { class: "mono" }, t),
      `${r.keys?.length ? `keys ${r.keys.join(", ")}` : "whole rows"}${r.ignore?.length ? `, ignoring ${r.ignore.join(", ")}` : ""}`)),
    proveRow("Other tables", "whole rows, in any order"),
    proveRow("Numbers", `match within ${d.abs ?? 0} or ${d.rel ?? 1e-9} of the larger value`),
    proveRow("Timestamps", { us: "to the microsecond", ms: "to the millisecond", s: "to the second" }[d.timestamps || "us"] + ", UTC"),
    proveRow("Above 5,000,000 rows", "row counts and checksum only"),
    h("a", { href: "#", onclick: (e) => { e.preventDefault(); editProveRules(nb); } }, "Change rules"));
}

function editProveRules(nb) {
  const p = nb.prove;
  const rules = p.rules || {};
  const d = rules.default || {};
  const rows = Object.entries(rules.tables || {}).map(([t, r]) => ({ table: t, keys: (r.keys || []).join(", "), ignore: (r.ignore || []).join(", ") }));
  rows.push({ table: "", keys: "", ignore: "" });
  const abs = h("input", { value: d.abs ?? "", placeholder: "0", inputmode: "decimal" });
  const rel = h("input", { value: d.rel ?? "", placeholder: "1e-9", inputmode: "decimal" });
  const ts = h("select", {}, ["us", "ms", "s"].map((u) => h("option", { value: u, selected: (d.timestamps || "us") === u },
    { us: "Microseconds", ms: "Milliseconds", s: "Seconds" }[u])));
  const grid = h("div", { class: "prove-rules" },
    h("span", { class: "muted small" }, "Table"), h("span", { class: "muted small" }, "Columns that identify a row"), h("span", { class: "muted small" }, "Columns to ignore"),
    rows.map((r) => [h("input", { value: r.table, placeholder: "dbo.table", "aria-label": "Table", "data-f": "table" }),
      h("input", { value: r.keys, placeholder: "id", "aria-label": "Key columns", "data-f": "keys" }),
      h("input", { value: r.ignore, placeholder: "loaded_at", "aria-label": "Columns to ignore", "data-f": "ignore" })]));
  const problem = h("p", { class: "field-error" });
  const save = async () => {
    const tables = {};
    const inputs = [...grid.querySelectorAll("input")];
    for (let i = 0; i < inputs.length; i += 3) {
      const t = inputs[i].value.trim();
      if (t) tables[t] = proveRulesFrom({ keys: inputs[i + 1].value, ignore: inputs[i + 2].value });
    }
    const next = { default: proveRulesFrom({ abs: abs.value, rel: rel.value, timestamps: ts.value }), tables };
    try {
      p.rules = await rpc("prove_rules_set", { name: nb.name, rules: next });
      closeDialog();
      drawProve(nb);
    } catch (e) {
      problem.textContent = friendlyError(e).text;
    }
  };
  showDialog("How results are compared", h("div", { class: "prove-form" },
    h("p", { class: "dialog-text" }, "Saved in the notebook, so every proof of it uses the same rules. Tables are named as schema.table."),
    grid,
    h("div", { class: "row wrap" },
      h("label", { class: "field" }, "Numbers: absolute difference", abs),
      h("label", { class: "field" }, "Relative difference", rel),
      h("label", { class: "field" }, "Timestamps to the", ts)),
    problem), [btn("Cancel", null, closeDialog, "tool outline"), btn("Save", null, save, "tool primary")]);
}

function proveProgress(nb, r) {
  const order = ["preparing", "pc", "fabric", "verifying"];
  const at = order.indexOf(r.state);
  const pc = r.pc || {};
  const f = r.fabric || {};
  const step = (i, title, text, extra) => h("li", { class: `sbs-step${i < at ? " done" : i === at ? " now" : ""}` },
    h("b", {}, `${i < at ? "✓" : i === at ? "●" : "○"} ${title}`), text ? h("span", {}, text) : null, extra || null);
  return h("div", { class: "prove-body" },
    h("ol", { class: "prove-steps" },
      step(0, "Check and pin the inputs", r.inputs?.pinned_tables !== undefined ? `${r.inputs.pinned_tables} table${r.inputs.pinned_tables === 1 ? "" : "s"} pinned` : ""),
      step(1, "Run on this PC", at > 1 ? `${sbsTime(pc.seconds)}${pc.status !== "ok" ? `, ${pc.status}` : ""}` : at === 1 ? `Cell ${pc.cell || 1} of ${pc.cells_total || "?"}` : ""),
      step(2, "Run in Fabric", at === 2 ? `${f.status === "publishing" ? `Publishing ${r.name}_proof` : f.job_status === "InProgress" ? "Running" : "Starting"}${f.elapsed ? `, ${sbsTime(f.elapsed)}` : ""}` : at > 2 ? sbsTime(f.total_seconds) : "",
        f.url ? h("a", { href: "#", onclick: (e) => { e.preventDefault(); openInFabric(f.url); } }, "Open the run in Fabric") : null),
      step(3, "Verify the results", "")),
    h("p", { class: "muted small" }, "The notebook's kernel is running the proof, so other cells wait. Cancel stops whichever run is going; nothing is written to the lakehouse's tables either way."),
    btn("Cancel proof", "stop", () => cancelProof(nb), "tool outline"));
}

function proveResult(nb, r) {
  const [cls, label] = proveVerdict(r);
  const at = dateTimeText(r.started_at);
  const tables = r.tables || [];
  const resultPill = (t) => {
    const map = { match: ["pass", "Same"], differs: ["fail", "Differs"], only_pc: ["wrn", "This PC only"], only_fabric: ["wrn", "Fabric only"], error: ["fail", "Couldn't verify"] };
    const [c, l] = map[t.result] || ["neu", t.result];
    return h("span", { class: `pill-s ${c}` }, l);
  };
  const card = r.scorecard || {};
  return h("div", { class: "prove-body" },
    h("div", { class: "row wrap" }, h("span", { class: `pill-s ${cls}` }, label), h("span", { class: "muted small" }, at)),
    h("b", {}, r.state === "cancelled" ? "Cancelled." : r.verdict?.text || r.message || ""),
    (r.verdict?.problems || []).slice(1).map((x) => h("div", { class: "prove-note" }, x)),
    r.message && r.verdict?.text ? h("div", { class: "prove-note" }, r.message) : null,
    tables.length ? proveSection("Tables",
      tables.map((t) => h("button", { class: "prove-table", onclick: () => showProveTable(nb, r, t) },
        h("span", { class: "mono" }, t.table), resultPill(t),
        h("span", { class: "muted small" }, t.level === "fingerprint" ? "Checksum only" : t.method === "keyed" ? `By ${t.keys.join(", ")}` : t.level ? "Whole rows" : t.note || "")))) : null,
    card.time ? proveSection("Scorecard",
      proveRow(proveScoreLabel("This PC", card.time.pc.kind), sbsTime(card.time.pc.seconds)),
      proveRow(proveScoreLabel("Fabric", card.time.fabric.kind), sbsTime(card.time.fabric.total_seconds)),
      card.time.faster ? h("span", { class: "small" }, card.time.faster) : null,
      proveRow(proveScoreLabel("Fabric capacity", card.cost?.fabric?.kind), card.cost?.fabric?.cu_seconds !== undefined
        ? `about ${Math.round(card.cost.fabric.cu_seconds).toLocaleString()} CU-s` : "not known"),
      h("a", { href: "#", onclick: (e) => { e.preventDefault(); showScorecard(r); } }, "Full scorecard")) : null,
    h("div", { class: "row wrap" },
      r.receipt_id ? btn("Receipt", "document", () => showReceipt(nb, r.receipt_id), "tool outline") : null,
      btn("New proof", "shield", () => { nb.prove.record = null; nb.prove.check = null; drawProve(nb); refreshProveCheck(nb); }, "tool outline"),
      r.outputs_deleted_at ? null : btn("Delete sandbox outputs", "delete", () => deleteProofOutputs(nb, r), "tool outline")),
    r.outputs_deleted_at ? h("span", { class: "muted small" }, "This proof's outputs were deleted; its receipt stays.") : null);
}

function proveScoreLabel(text, kind) {
  const [label, cls] = proveKind(kind);
  return h("span", {}, `${text} `, h("span", { class: `pill-s ${cls}` }, label));
}

// ---------- differences ----------

function showProveTable(nb, r, t) {
  const view = { result: t, status: "changed", offset: 0, saved: null };
  if (t.method === "whole rows") view.status = (t.counts?.only_pc ? "only_pc" : "only_fabric");
  const body = h("div", { class: "prove-diff" });
  const draw = () => body.replaceChildren(...proveTableBody(nb, r, view, draw));
  draw();
  const overlay = showDialog(`${t.table}: ${t.result === "match" ? "same" : "differences"}`, body, [btn("Close", null, closeDialog, "tool outline")]);
  overlay.querySelector(".dialog").classList.add("wide");
}

function proveTableBody(nb, r, view, draw) {
  const res = view.result;
  const out = [];
  const counts = res.counts || {};
  const describe = res.result === "only_pc" || res.result === "only_fabric" || res.result === "error"
    ? res.note : res.level === "fingerprint"
      ? `Too many rows to compare one by one (${(res.pc?.rows || 0).toLocaleString()}), so row counts and a checksum of every row were compared. A matching checksum makes a difference very unlikely, not impossible.`
      : res.method === "keyed" ? `Every row compared, matched by ${res.keys.join(", ")}.` : "Every row compared as a whole row, in any order.";
  out.push(h("p", { class: "dialog-text" }, describe));
  (res.limitations || []).forEach((l) => out.push(h("div", { class: "prove-note" }, l)));
  const cols = res.columns || {};
  if (cols.only_pc?.length) out.push(h("div", { class: "prove-note" }, `Columns only on this PC: ${cols.only_pc.join(", ")}`));
  if (cols.only_fabric?.length) out.push(h("div", { class: "prove-note" }, `Columns only in Fabric: ${cols.only_fabric.join(", ")}`));
  if (cols.types?.length) out.push(h("div", { class: "prove-note" }, `Different types: ${cols.types.join(", ")}`));
  if (res.level === "complete") {
    const tabs = res.method === "keyed" ? [["changed", "Changed", counts.changed], ["only_pc", "Only on this PC", counts.only_pc], ["only_fabric", "Only in Fabric", counts.only_fabric]]
      : [["only_pc", "Only on this PC", counts.only_pc], ["only_fabric", "Only in Fabric", counts.only_fabric]];
    out.push(h("div", { class: "seg", role: "tablist", "aria-label": "Differences" }, tabs.map(([k, l, n]) =>
      h("button", { class: view.status === k ? "on" : null, role: "tab", "aria-selected": String(view.status === k),
        onclick: () => { view.status = k; draw(); } }, `${l} ${(n || 0).toLocaleString()}`))));
    const diffs = Object.entries(res.column_diffs || {}).filter(([, d]) => d.rows);
    if (diffs.length) {
      out.push(h("div", { class: "muted small" }, diffs.map(([c, d]) => `${c}: ${d.rows.toLocaleString()} rows differ${d.max_abs !== undefined && d.max_abs !== null ? `, largest ${d.max_abs.toPrecision(3)}` : ""}`).join(" · ")));
    }
    out.push(proveGrid(res, view.status));
    const total = counts[view.status] || 0;
    const shown = (res.examples || []).length;
    if (total > shown + view.offset || view.offset > 0) {
      out.push(h("div", { class: "row" },
        h("span", { class: "muted small" }, `Showing rows ${view.offset + 1} to ${view.offset + shown} of the differing rows.`),
        view.offset > 0 ? btn("Previous", "up", () => proveReverify(r, view, draw, Math.max(0, view.offset - 50)), "tool small") : null,
        shown >= 50 ? btn("Next", "down", () => proveReverify(r, view, draw, view.offset + 50), "tool small") : null));
    }
  }
  if (res.pc && res.fabric && r.state !== "running") out.push(proveTolerance(r, view, draw));
  return out;
}

function proveGrid(res, status) {
  const rows = proveDiffRows(res, status);
  if (!rows.length) return h("div", { class: "muted-text" }, "None.");
  if (res.method === "keyed") {
    const keys = res.keys || [];
    const changed = status === "changed";
    return h("div", { class: "prove-grid" }, h("table", { class: "vars" },
      h("thead", {}, h("tr", {}, keys.map((k) => h("th", {}, k)), changed ? [h("th", {}, "Column"), h("th", {}, "This PC"), h("th", {}, "Fabric"), h("th", {}, "Difference")] : null)),
      h("tbody", {}, rows.map((x) => h("tr", {}, keys.map((k) => h("td", { class: "mono" }, proveValue(x.key?.[k]))),
        changed ? [h("td", { class: "mono" }, x.column), h("td", { class: "mono" }, proveValue(x.pc)), h("td", { class: "mono" }, proveValue(x.fabric)),
          h("td", { class: "mono" }, proveDifference(x.pc, x.fabric))] : null)))));
  }
  const cols = Object.keys(rows[0].row || {});
  return h("div", { class: "prove-grid" }, h("table", { class: "vars" },
    h("thead", {}, h("tr", {}, cols.map((c) => h("th", {}, c)), h("th", {}, "Times"))),
    h("tbody", {}, rows.map((x) => h("tr", {}, cols.map((c) => h("td", { class: "mono" }, proveValue(x.row[c]))), h("td", {}, String(x.times)))))));
}

function proveTolerance(r, view, draw) {
  const rules = view.result.rules || {};
  const keys = h("input", { value: (rules.keys || []).join(", "), placeholder: "whole rows" });
  const ignore = h("input", { value: (rules.ignore || []).join(", ") });
  const abs = h("input", { value: rules.abs ?? 0, inputmode: "decimal" });
  const rel = h("input", { value: rules.rel ?? 1e-9, inputmode: "decimal" });
  const problem = h("p", { class: "field-error" });
  view.form = () => proveRulesFrom({ keys: keys.value, ignore: ignore.value, abs: abs.value, rel: rel.value, timestamps: rules.timestamps });
  view.problem = problem;
  return h("section", { class: "prove-sec" }, h("h3", {}, "Verify again under other rules"),
    h("p", { class: "muted small" }, "Re-checks the outputs this proof saved. Nothing runs again."),
    h("div", { class: "row wrap" },
      h("label", { class: "field" }, "Columns that identify a row", keys),
      h("label", { class: "field" }, "Columns to ignore", ignore),
      h("label", { class: "field" }, "Absolute difference", abs),
      h("label", { class: "field" }, "Relative difference", rel)),
    problem,
    h("div", { class: "row wrap" },
      btn("Verify again", "restart", () => proveReverify(r, view, draw, 0), "tool primary"),
      btn("Verify and save a receipt", "document", () => proveReverify(r, view, draw, 0, true), "tool outline",
        { title: "Keeps this check as its own receipt, pointing at the proof's" }),
      view.saved ? h("span", { class: "muted small" }, `Saved as receipt ${view.saved.slice(0, 12)}.`) : null));
}

async function proveReverify(r, view, draw, offset, save = false) {
  const rules = view.form ? view.form() : view.result.rules;
  try {
    const out = await rpc("prove_verify", { id: r.id, table: view.result.table, rules, offset, save });
    view.result = out;
    view.offset = offset;
    if (out.receipt_id) view.saved = out.receipt_id;
    if (view.result.method === "keyed" && !["changed", "only_pc", "only_fabric"].includes(view.status)) view.status = "changed";
    if (view.result.method !== "keyed" && view.status === "changed") view.status = "only_pc";
    draw();
  } catch (e) {
    if (view.problem) view.problem.textContent = friendlyError(e).text;
  }
}

// ---------- scorecard ----------

function showScorecard(r) {
  const c = r.scorecard || {};
  const t = c.time || {};
  const res = c.resources || {};
  const cost = c.cost || {};
  const box = (title, kind, big, text, extra) => {
    const [label, cls] = proveKind(kind);
    return h("div", { class: "prove-card" }, h("span", { class: "muted small" }, title), h("span", { class: `pill-s ${cls}` }, label),
      h("b", { class: "sbs-big" }, big), text ? h("span", { class: "small" }, text) : null, extra || null);
  };
  const pcRes = res.pc || {};
  const fab = cost.fabric || {};
  const body = h("div", { class: "prove-cards" },
    box("Time on this PC", t.pc?.kind, sbsTime(t.pc?.seconds), [t.faster, t.faster_run].filter(Boolean).join(". ")),
    box("Time in Fabric", t.fabric?.kind, sbsTime(t.fabric?.total_seconds),
      `Publish to finish. Job ${sbsTime(t.fabric?.job_seconds)}, notebook run ${sbsTime(t.fabric?.run_seconds)}, session start ${sbsTime(t.fabric?.startup_seconds)}.`),
    box("Resources on this PC", pcRes.kind, pcRes.peak_memory_mb ? `${(pcRes.peak_memory_mb / 1024).toFixed(1)} GB peak` : "–",
      `${pcRes.cpu_seconds !== undefined && pcRes.cpu_seconds !== null ? `${Math.round(pcRes.cpu_seconds)} s of CPU` : ""}${pcRes.cores ? ` on ${pcRes.cores} cores` : ""}${pcRes.memory_gb ? `, ${pcRes.memory_gb} GB` : ""}.`),
    box("Resources in Fabric", res.fabric?.kind, "Not reported", res.fabric?.text),
    box("Fabric capacity", fab.kind, fab.cu_seconds !== undefined ? `about ${Math.round(fab.cu_seconds).toLocaleString()} CU-s` : "Not known",
      fab.amount !== undefined ? `About ${fab.amount} ${fab.currency} at pay-as-you-go prices.` : fab.text,
      fab.assumptions ? h("ul", { class: "small" }, fab.assumptions.map((a) => h("li", {}, a))) : null),
    box("Fabric capacity of this PC's run", cost.pc?.kind, "No Fabric job", cost.pc?.text));
  const notes = (c.notes || []).map((n) => h("li", {}, n));
  const overlay = showDialog("Scorecard", h("div", {}, body, notes.length ? h("ul", { class: "small muted" }, notes) : null),
    [btn("Close", null, closeDialog, "tool outline")]);
  overlay.querySelector(".dialog").classList.add("wide");
}

// ---------- receipts ----------

async function showReceipt(nb, id) {
  const [got, redactions] = await Promise.all([
    attempt(() => rpc("receipt_get", { id }), "Could not open the receipt"),
    rpc("receipt_redactions").catch(() => ({})),
  ]);
  if (!got) return;
  const rc = got.receipt;
  const v = rc.verification || {};
  const inputs = rc.inputs || {};
  const env = rc.environment || {};
  const pkgs = (e) => Object.entries(e?.packages || {}).map(([k, x]) => `${k} ${x}`).join(", ");
  const summary = h("div", { class: "prove-body" },
    h("div", { class: "row" }, icon("ok", 16), h("b", {}, "Content matches its id")),
    h("div", { class: "mono small", style: "word-break:break-all" }, `sha256:${id}`),
    h("p", { class: "muted small" }, "Written once and never replaced. The id is the SHA-256 of the content, so anyone with the receipt file can check it hasn't changed."),
    proveSection("Result", proveRow("Verdict", v.text || v.verdict || rc.proof?.state || "")),
    rc.notebook?.code ? proveSection("Code", proveRow("Revision", h("span", { class: "mono" }, `${String(rc.notebook.code.revision).slice(0, 16)}…, ${rc.notebook.code.cells.length} cells`))) : null,
    (inputs.tables || []).length || (inputs.files || []).length ? proveSection("Inputs",
      (inputs.tables || []).map((t) => proveRow(h("span", { class: "mono" }, t.name), `version ${t.version}`)),
      (inputs.files || []).map((f) => proveRow(h("span", { class: "mono" }, f.path), h("span", { class: "mono" }, `${f.sha256.slice(0, 12)}…`)))) : null,
    env.pc ? proveSection("Environment",
      proveRow("This PC", `Python ${env.pc.python || "?"}${pkgs(env.pc) ? `, ${pkgs(env.pc)}` : ""}`),
      env.fabric?.python ? proveRow("Fabric", `Python ${env.fabric.python}${pkgs(env.fabric) ? `, ${pkgs(env.fabric)}` : ""}`) : null) : null);
  const fmt = h("select", {}, h("option", { value: "html" }, "HTML report"), h("option", { value: "markdown" }, "Markdown report"),
    h("option", { value: "json" }, "Receipt file (JSON, can be checked)"));
  const boxes = Object.entries(redactions).map(([k, label]) => h("label", { class: "field inline" },
    h("input", { type: "checkbox", value: k, checked: k !== "machine" }), label));
  const why = h("p", { class: "muted small" });
  const sync = () => {
    const whole = fmt.value === "json";
    boxes.forEach((b) => { b.querySelector("input").disabled = whole; });
    why.textContent = whole ? "A receipt file is saved whole so it can be checked against its id. To leave parts out, export a report."
      : "A report with parts left out says so at the top.";
  };
  fmt.addEventListener("change", sync);
  sync();
  const save = async () => {
    const hide = fmt.value === "json" ? [] : boxes.map((b) => b.querySelector("input")).filter((i) => i.checked).map((i) => i.value);
    const out = await attempt(() => rpc("receipt_save", { id, format: fmt.value, hide }), "Could not save the receipt");
    if (out) { closeDialog(); toast(`Saved to ${out.path}`); }
  };
  const overlay = showDialog("Proof receipt", h("div", { class: "prove-receipt" }, summary,
    h("section", { class: "prove-sec" }, h("h3", {}, "Export"), h("label", { class: "field" }, "Format", fmt),
      h("div", { class: "prove-checks" }, h("span", { class: "muted small" }, "Leave out"), boxes), why)),
  [btn("Close", null, closeDialog, "tool outline"), btn("Save to Downloads", "save", save, "tool primary")]);
  overlay.querySelector(".dialog").classList.add("wide");
}

// The shared danger confirm (UX-018): drawn red, Cancel has focus.
async function deleteProofOutputs(nb, r) {
  if (!(await confirmDialog("Delete this proof's outputs?",
    `Deletes Files/_fabric_desktop/proofs/${r.id}/ in ${r.lakehouse?.name || "the lakehouse"}: the tables both runs wrote and the Fabric run's log. `
    + "The lakehouse's own tables aren't touched, and the receipt stays. Verifying again won't be possible.", "Delete", { danger: true }))) return;
  const out = await attempt(() => rpc("prove_delete_outputs", { id: r.id }), "Could not delete the outputs");
  if (out) {
    nb.prove.record = await rpc("prove_status", { id: r.id }).catch(() => ({ ...r, outputs_deleted_at: new Date().toISOString() }));
    drawProve(nb);
    toast("Deleted the proof's outputs.");
  }
}

registerFeature({
  id: "prove", preview: true,
  commands: [
    { label: "Prove this notebook on this PC and in Fabric", icon: "shield",
      keywords: "prove verify compare receipt equal same results fabric local", when: () => state.view === "notebook" && !!state.notebook,
      run: () => openProve(state.notebook) },
  ],
});
