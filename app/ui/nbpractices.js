// The notebook's Best practices panel (Preview): the notebook rules (NB001 to NB012, NB018 to NB020)
// (notebook_practices.py) on this notebook's code cells, read on this PC as you type. Nothing runs
// and no capacity is used. The policies in force for the open project tune the rules
// (governance_practice_settings): one a policy turns off isn't shown, and a policy can change a
// rule's severity. The rules for scheduled runs (display() and %pip) aren't checked here.

const PRACTICE_SEVERITIES = ["Error", "Warning", "Info"];
const PRACTICE_PILL = { Error: "fail", Warning: "wrn", Info: "info" };

// The code cells the engine checks, in order, and where each one is in the notebook: the engine
// numbers cells 1, 2, ... over the sources it's given, so its cell n is notebook cell at[n - 1] + 1.
function practiceSources(cells) {
  const at = [];
  const sources = [];
  cells.forEach((c, i) => {
    if (c.type !== "code") return;
    at.push(i);
    sources.push(String(c.source || ""));
  });
  return { sources, at };
}

// The engine's findings with their notebook cell numbers, and the counts by severity.
function practiceRows(result, at) {
  const rows = (result?.findings || []).map((f) => ({ ...f, n: at[f.cell - 1] + 1 }));
  const counts = Object.fromEntries(PRACTICE_SEVERITIES.map((s) => [s, rows.filter((f) => f.severity === s).length]));
  return { rows, counts };
}

// The toolbar label: the number to look at, when there is one.
function practiceLabel(p) {
  const n = p?.shown ?? (p?.rows?.length || 0);
  return p?.open && n ? `Best practices · ${n}` : "Best practices";
}

function practicesOf(nb) {
  if (!nb.practices) nb.practices = { open: false, rows: null, counts: null, sig: "", settings: null, policies: [], note: "", error: "", timer: 0 };
  return nb.practices;
}

function practicesOpen(nb) {
  return !!nb?.practices?.open;
}

function openPractices(nb) {
  const p = practicesOf(nb);
  p.open = true;
  if (nb.comments?.open) nb.comments.open = false;  // one panel beside the cells at a time
  if (nb.prove?.open) nb.prove.open = false;
  if (nb.validate?.open) nb.validate.open = false;
  renderNotebook();
  loadPracticeSettings(nb);
  clearInterval(p.timer);
  p.timer = setInterval(() => checkPractices(nb), 1500);  // as you type: only when the code changed
}

function closePractices(nb) {
  const p = practicesOf(nb);
  p.open = false;
  clearInterval(p.timer);
  renderNotebook();
}

function practicesToggle(nb) {
  const on = practicesOpen(nb);
  return preview("practices", btn(practiceLabel(nb.practices), "ok", () => (on ? closePractices(nb) : openPractices(nb)),
    `tool${on ? " toggled" : ""}`, { "aria-pressed": String(on), "data-practices": true,
      title: "Best practices for this notebook's code, read on this PC as you type. Nothing runs." }));
}

async function loadPracticeSettings(nb) {
  const p = practicesOf(nb);
  let out;
  try {
    out = await rpc("governance_practice_settings", { kind: "notebook" });
  } catch (err) {
    out = { settings: {}, policies: [], note: `The policies couldn't be read, so every rule is on as built: ${err.message || err}` };
  }
  Object.assign(p, { settings: out.settings || {}, policies: out.policies || [], note: out.note || "", sig: "" });
  checkPractices(nb);
}

async function checkPractices(nb) {
  const p = practicesOf(nb);
  if (!p.open || p.settings === null) return;
  if (state.view !== "notebook" || state.notebook !== nb) { clearInterval(p.timer); p.open = false; return; }
  const { sources, at } = practiceSources(nb.cells);
  const sig = JSON.stringify(sources);
  if (sig === p.sig) return;
  p.sig = sig;
  let result;
  try {
    result = await rpc("notebook_practices", { sources, scheduled: false, settings: p.settings });
    p.error = "";
  } catch (err) {
    p.error = String(err.message || err);
  }
  if (p.sig !== sig) return;  // typed again while the engine read it
  if (!p.error) {
    const out = practiceRows(result, at);
    await learnPrepareFindings(out.rows, (f) => String(nb.cells[f.n - 1]?.source || "").split("\n")[f.line - 1]);  // learn.js
    if (p.sig !== sig) return;
    Object.assign(p, out);
  }
  drawPractices(nb);
}

// Repaint the panel and the toolbar button in place, so the editor keeps focus.
function drawPractices(nb) {
  if (state.view !== "notebook" || state.notebook !== nb) return;
  document.querySelector(".practicespane")?.replaceChildren(...practicesContent(nb));
  document.querySelectorAll("button[data-practices]").forEach((b) => { const fresh = practicesToggle(nb); if (fresh) b.replaceWith(fresh); });
}

function practicesPanel(nb) {
  if (!practicesOpen(nb)) return null;
  if (nb.comments?.open || nb.prove?.open || validateOpen(nb)) return null;  // the one opened last shows
  return h("aside", { class: "commentspane practicespane", "aria-label": "Best practices" }, practicesContent(nb));
}

// Go to a finding: its cell, and its line in the cell's editor.
function practiceGo(nb, f) {
  const cell = nb.cells[f.n - 1];
  if (!cell) return;
  revealCell(cell);  // nbcells.js
  depsGo(nb, f.n);
  const view = editors.get(cell.id);
  if (!view || cell.meta?.source_hidden) return;
  const line = view.state.doc.line(Math.min(Math.max(f.line, 1), view.state.doc.lines));
  view.dispatch({ selection: { anchor: line.from }, scrollIntoView: true });
}

function practicesContent(nb) {
  const p = practicesOf(nb);
  const head = h("div", { class: "explorer-head" }, "Best practices",
    h("button", { class: "tool small", "aria-label": "Close best practices", onclick: () => closePractices(nb) }, icon("dismiss", 14)));
  const body = h("div", { class: "prove-body" },
    h("p", { class: "muted-text" }, "Read on this PC as you type. Nothing runs and no capacity is used."));
  if (p.note) body.append(h("p", { class: "muted-text" }, p.note));
  if (p.error) body.append(h("p", { class: "error-text" }, `Couldn't check this notebook: ${p.error}`));
  if (!p.rows) {
    body.append(h("p", { class: "muted-text" }, "Checking..."));
  } else {
    // Dismissed findings (learn.js) leave the list and the counts; a rule a policy names stays.
    const ctx = { subject: "notebooks", where: `${nb.place?.root || "notebooks"}/${nb.name}`, required: (id) => !!p.settings?.[id]?.enabled, redraw: () => drawPractices(nb) };
    const { shown, hidden } = learnSplitFindings(p.rows, ctx.where, ctx.required);
    p.shown = shown.length;
    const counts = Object.fromEntries(PRACTICE_SEVERITIES.map((s) => [s, shown.filter((f) => f.severity === s).length]));
    body.append(h("div", { class: "row practice-counts" }, PRACTICE_SEVERITIES.map((s) =>
      h("span", { class: `pill-s ${counts[s] ? PRACTICE_PILL[s] : "neu"}` }, `${counts[s]} ${s === "Info" ? "info" : `${s.toLowerCase()}${counts[s] === 1 ? "" : "s"}`}`))));
    if (!shown.length) body.append(h("p", { class: "muted-text" }, "Nothing to look at. Every rule that's on passes."));
    shown.forEach((f) => body.append(h("div", { class: "comment-thread practice", "data-rule": f.rule_id },
      h("div", { class: "row" }, h("span", { class: `pill-s ${PRACTICE_PILL[f.severity]}` }, f.severity), h("b", {}, f.rule)),
      h("p", { class: "comment-text" }, f.description),
      h("div", { class: "row" }, h("button", { class: "link-btn", onclick: () => practiceGo(nb, f) }, `Go to cell ${f.n}, line ${f.line}`),
        h("span", { class: "muted-text" }, `${f.rule_id} · ${f.category}`),
        f.url ? docLink(f.url, `About ${f.rule_id}`) : null),
      learnFindingActions(f, ctx))));
    body.append(learnDismissedBlock(hidden, ctx) || "");
  }
  body.append(h("p", { class: "muted-text" }, p.policies.length
    ? `Tuned by ${p.policies.join(", ")}: a rule a policy turns off isn't shown.`
    : "No policy for this project names a notebook rule, so every rule is on as built. A governance policy can name them in Checked by."));
  return [head, body, h("div", { class: "validate-how" }, howItWorks("practices", {
    reads: "This notebook's code cells as they are in the editor, and the governance policies in force for the open project (to see which rules they turn off or change).",
    changes: "Nothing. The cells aren't run, and nothing is saved or sent to Fabric.",
    results: "This panel only. Check now on the Governance page runs the rules a policy names on every notebook on this PC.",
    more: [["Not checked here", "display() and %pip in a scheduled notebook (NB004, NB008): they matter only for scheduled runs. Scala, R and other %% cells, except %%sql."]],
    anchor: "notebook-best-practices-preview" }))];
}

registerFeature({
  id: "practices",
  commands: [
    { label: "Show best practices for this notebook", icon: "ok", keywords: "best practices lint check rules governance",
      when: () => state.view === "notebook" && !!state.notebook, run: () => openPractices(state.notebook) },
  ],
});
