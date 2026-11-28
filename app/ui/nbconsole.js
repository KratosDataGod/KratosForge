// Notebook > View > Scratch (Preview, parity item 19): a console beside the cells that runs in the
// notebook's own session, so it sees the same variables, while nothing typed there is saved in the
// notebook. Copy to a new cell keeps a line you want. Shift+Enter runs, and Up or Down at the start or
// end of the box steps through what ran before. It runs on This PC only, one run at a time, and waits
// while a cell is running, as a cell would.

const CONSOLE_MAX_ENTRIES = 100;
const CONSOLE_MAX_HISTORY = 50;

function consoleOf(nb) {
  return nb.console ||= { open: false, entries: [], history: [], at: null, draft: "" };
}

function consoleToggle(nb) {
  const on = consoleOf(nb).open;
  return preview("nbConsole", btn("Scratch", "code", () => { consoleOf(nb).open = !on; renderNotebook(); if (!on) focusConsole(); },
    `tool${on ? " toggled" : ""}`, { "aria-pressed": String(on), title: "Run lines in this notebook's session without adding cells" }));
}

function focusConsole() {
  requestAnimationFrame(() => document.querySelector(".console-input")?.focus());
}

// Why the console can't run here, or "" when it can.
function consoleBlocked(nb) {
  if (nbWhere(nb).fabric) return "This notebook runs in Fabric, so the scratch console isn't available. Switch Runs on to This PC to use it.";
  if (nbOnEngine(nb)) return "The scratch console runs on this PC's Python. Switch the engine back to Python to use it.";
  return "";
}

function consolePanel(nb) {
  const c = consoleOf(nb);
  if (!c.open) return null;
  const blocked = consoleBlocked(nb);
  const log = h("div", { class: "console-log", role: "log", "aria-label": "Scratch results", "aria-live": "polite" },
    c.entries.length ? c.entries.map(consoleEntry) : h("p", { class: "muted-text small" }, "Type Python and press Shift+Enter. Try df.count() or df.columns."));
  const input = h("textarea", { class: "console-input", rows: "2", "aria-label": "Scratch input", spellcheck: "false",
    placeholder: "Python, in this notebook's session", disabled: blocked ? true : null,
    oninput: (e) => { c.draft = e.target.value; c.at = null; },
    onkeydown: (e) => consoleKeys(e, nb) });
  input.value = c.draft;
  return h("aside", { class: "consolepane", "aria-label": "Scratch" },
    h("div", { class: "explorer-head" }, "Scratch", previewTag(), h("span", { class: "spacer" }),
      h("button", { class: "tool small", onclick: () => { c.entries = []; renderNotebook(); }, disabled: c.entries.length ? null : true }, "Clear"),
      h("button", { class: "tool small", "aria-label": "Close Scratch", onclick: () => { c.open = false; renderNotebook(); } }, icon("dismiss", 14))),
    h("p", { class: "muted-text small console-note" }, "Runs in this notebook's session. Not saved in the notebook. ", learnMore("scratch-console-preview")),
    blocked ? h("p", { class: "muted-text small", role: "note" }, blocked) : null,
    log, input,
    h("div", { class: "row wrap console-actions" },
      btn("Run", "play", () => consoleRun(nb), "tool primary", { disabled: blocked ? true : null }),
      btn("Copy to a new cell", "add", () => consoleCopy(nb), "tool outline", { title: "Adds what's in the box, or else the last line that ran, as a new cell at the end of the notebook" }),
      h("span", { class: "muted-text small" }, "Shift+Enter runs · Up for history")));
}

function consoleEntry(e) {
  return h("div", { class: `console-entry${e.status === "error" ? " failed" : ""}` },
    h("pre", { class: "console-in" }, e.code),
    e.status === "running" ? h("div", { class: "muted-text small" }, "Running...")
      : e.outputs.length ? e.outputs.map((o) => (o.type === "widget" || o.type === "fabric_step"
        ? h("div", { class: "output muted-text" }, "Widgets show in cells, not in the scratch console.") : renderOutput(o, null)))
      : h("div", { class: "muted-text small" }, e.status === "ok" ? "Done, no output." : ""));
}

function consoleKeys(e, nb) {
  const c = consoleOf(nb);
  const box = e.target;
  if (e.key === "Enter" && e.shiftKey) { e.preventDefault(); consoleRun(nb); return; }
  if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); box.blur(); return; }
  // Up at the start or Down at the end of the box steps through what ran (newest first).
  const atStart = box.selectionStart === 0 && box.selectionEnd === 0;
  const atEnd = box.selectionStart === box.value.length;
  if (e.key === "ArrowUp" && atStart && c.history.length) {
    e.preventDefault();
    c.at = c.at === null ? c.history.length - 1 : Math.max(0, c.at - 1);
    box.value = c.draft = c.history[c.at];
    box.setSelectionRange(0, 0);  // Up again steps further back
  } else if (e.key === "ArrowDown" && atEnd && c.at !== null) {
    e.preventDefault();
    c.at = c.at + 1 < c.history.length ? c.at + 1 : null;
    box.value = c.draft = c.at === null ? "" : c.history[c.at];
  }
  e.stopPropagation();  // the notebook's command-mode keys (A, B, DD...) stay out of the box
}

async function consoleRun(nb) {
  const c = consoleOf(nb);
  const code = c.draft.trim();
  if (!code) return;
  const blocked = consoleBlocked(nb);
  if (blocked) { toast(blocked); return; }
  if (kernelBusy(nb)) { toast("A cell is running. Run this again when it finishes."); return; }
  const entry = { code, outputs: [], status: "running" };
  c.entries.push(entry);
  c.entries.splice(0, Math.max(0, c.entries.length - CONSOLE_MAX_ENTRIES));
  if (c.history[c.history.length - 1] !== code) c.history.push(code);
  c.history.splice(0, Math.max(0, c.history.length - CONSOLE_MAX_HISTORY));
  c.draft = "";
  c.at = null;
  nb.stopRequested = false;
  setKernel("busy", nb);
  consoleRedraw(nb);
  try {
    let snap = await rpc("start_cell", { code, lakehouse_ref: nbLakehouse(nb), notebook: nb.name });
    for (;;) {
      if (snap.outputs) entry.outputs = snap.outputs;
      if (snap.status !== "running") break;
      snap = await rpc("poll_cell", { version: snap.version, wait: 0.4, notebook: nb.name });
    }
    entry.status = snap.status;
  } catch (err) {
    entry.status = "error";
    entry.outputs = [{ type: "error", ename: "Kernel error", evalue: String(err) }];
  }
  setKernel(nb.stopRequested ? "stopped" : "idle", nb);
  if (state.notebook === nb) {
    consoleRedraw(nb);
    refreshVariables();  // what the console set shows in Variables
  }
}

// Redraws the panel in place, keeping the focus in the box.
function consoleRedraw(nb) {
  const pane = document.querySelector(".consolepane");
  if (!pane || state.notebook !== nb) return;
  const typing = document.activeElement?.classList.contains("console-input");
  const next = consolePanel(nb);
  pane.replaceWith(next);
  const log = next.querySelector(".console-log");
  log.scrollTop = log.scrollHeight;
  if (typing) next.querySelector(".console-input")?.focus();
}

function consoleCopy(nb) {
  const c = consoleOf(nb);
  const code = c.draft.trim() || c.entries[c.entries.length - 1]?.code;
  if (!code) { toast("Type something, or run a line first, to copy it to a cell."); return; }
  const cell = addCell("code", true);
  setSource(cell, code);
  markDirty();
  toast("Added as a new cell at the end of the notebook.");
}
