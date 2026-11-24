// Rich-text editing of Markdown cells (Preview: mdToolbar). While a Markdown cell is being edited
// (cell.editing), a formatting toolbar sits above its editor and a View switch shows the editor, the
// rendered cell, or both side by side. Every button edits the Markdown source through the cell's
// CodeMirror editor, so Ctrl+Z undoes it, and the cell is saved as Markdown, as it is today. The View
// choice is remembered per notebook on this PC, beside side by side's toggle (sidebyside.js), never in
// the .ipynb. With Preview features hidden, Markdown cells edit exactly as before.

const MD_VIEWS = [["edit", "Edit"], ["split", "Split"], ["preview", "Preview"]];
const MD_HINT = "Saved as Markdown. Press Esc or click outside the cell to finish editing.";

function mdRichOn() {
  return previewShown();
}

function mdViewOf(nb) {
  let v = null;
  try { v = localStorage.getItem(`fd.mdview.${nb.name}`); } catch { /* storage off: this session's choice */ }
  return MD_VIEWS.some(([id]) => id === v) ? v : nb.mdView || "split";
}

function setMdView(nb, v) {
  nb.mdView = v;
  try { localStorage.setItem(`fd.mdview.${nb.name}`, v); } catch { /* this session only */ }
  document.querySelectorAll(".md-rich").forEach((el) => mdShowView(el, v));
}

function mdShowView(el, v) {
  el.dataset.view = v;
  el.querySelectorAll("[data-md-view]").forEach((b) => {
    const on = b.dataset.mdView === v;
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", String(on));
  });
  el.querySelectorAll(".md-fmt").forEach((b) => { b.disabled = v === "preview"; });
  const bar = el.querySelector(".md-toolbar");
  if (bar) mdStop(bar, "button", [...bar.querySelectorAll("button")].find((b) => b.tabIndex === 0 && !b.disabled));
}

// ---------- edits, all through the editor ----------

function mdEdit(view, from, to, insert, selFrom, selTo) {
  view.dispatch({ changes: { from, to, insert }, selection: { anchor: selFrom, head: selTo }, userEvent: "input.format", scrollIntoView: true });
  view.focus();
}

// **text**, *text* or `text` around the selection, or around a placeholder that is left selected.
function mdWrap(view, mark, placeholder) {
  const { from, to } = view.state.selection.main;
  const text = view.state.sliceDoc(from, to) || placeholder;
  mdEdit(view, from, to, mark + text + mark, from + mark.length, from + mark.length + text.length);
}

// A prefix on each selected line ("## ", "- ", or "1. ", "2. " ...), or a new line with a placeholder.
function mdPrefix(view, prefix, placeholder) {
  const st = view.state;
  const { from, to, empty } = st.selection.main;
  const pre = (i) => (typeof prefix === "function" ? prefix(i) : prefix);
  if (empty) {
    const line = st.doc.lineAt(from);
    const lead = from === line.from ? "" : "\n";
    const start = from + lead.length + pre(0).length;
    return mdEdit(view, from, from, lead + pre(0) + placeholder, start, start + placeholder.length);
  }
  const first = st.doc.lineAt(from);
  const last = st.doc.lineAt(to > from && to === st.doc.lineAt(to).from ? to - 1 : to);
  const lines = [];
  for (let n = first.number; n <= last.number; n++) lines.push(st.doc.line(n).text);
  const insert = lines.map((l, i) => pre(i) + l).join("\n");
  mdEdit(view, first.from, last.to, insert, first.from, first.from + insert.length);
}

function mdInsertLink(view) {
  const { from, to } = view.state.selection.main;
  const text = view.state.sliceDoc(from, to);
  if (text) {
    const at = from + text.length + 3;
    return mdEdit(view, from, to, `[${text}](url)`, at, at + 3);
  }
  mdEdit(view, from, to, "[link text](url)", from + 1, from + 10);
}

// `code` on one line; a fenced block when the selection spans lines.
function mdCode(view) {
  const st = view.state;
  const { from, to } = st.selection.main;
  const text = st.sliceDoc(from, to);
  if (!text.includes("\n")) return mdWrap(view, "`", "code");
  const lead = from === st.doc.lineAt(from).from ? "" : "\n";
  const tail = to === st.doc.lineAt(to).to ? "" : "\n";
  const body = text.replace(/\n$/, "");
  const start = from + lead.length + 4;
  mdEdit(view, from, to, `${lead}\`\`\`\n${body}\n\`\`\`${tail}`, start, start + body.length);
}

// A table of two columns and two rows, on lines of its own after a blank line.
function mdTable(view) {
  const st = view.state;
  const { from, to } = st.selection.main;
  const line = st.doc.lineAt(from);
  const before = st.sliceDoc(line.from, from);
  const lead = !before.trim() ? (line.number > 1 && st.doc.line(line.number - 1).text.trim() ? "\n" : "") : "\n\n";
  const table = "| Column 1 | Column 2 |\n| --- | --- |\n| Cell | Cell |\n| Cell | Cell |\n";
  const start = from + lead.length + 2;
  mdEdit(view, from, to, lead + table, start, start + 8);
}

const MD_ACTIONS = {
  bold: { label: "Bold", title: "Bold (Ctrl+B)", keys: "Control+B", text: "B", run: (v) => mdWrap(v, "**", "bold text") },
  italic: { label: "Italic", title: "Italic (Ctrl+I)", keys: "Control+I", text: "I", run: (v) => mdWrap(v, "*", "italic text") },
  heading: { label: "Heading", title: "Heading", text: "H", run: (v) => mdPrefix(v, "## ", "Heading") },
  bullets: { label: "Bulleted list", title: "Bulleted list", text: "•", run: (v) => mdPrefix(v, "- ", "List item") },
  numbers: { label: "Numbered list", title: "Numbered list", text: "1.", run: (v) => mdPrefix(v, (i) => `${i + 1}. `, "List item") },
  link: { label: "Link", title: "Link (Ctrl+K)", keys: "Control+K", text: "Link", run: mdInsertLink },
  code: { label: "Code", title: "Code", text: "Code", run: mdCode },
  table: { label: "Table", title: "Table", text: "Table", run: mdTable },
};

// Ctrl+B, Ctrl+I and Ctrl+K in a Markdown cell's editor, ahead of CodeMirror's own Ctrl+I and the
// command bar's Ctrl+K. With Preview features hidden they do what they did before.
function mdKeys(cell) {
  const keys = { b: "bold", i: "italic", k: "link" };
  return CM.Prec.highest(CM.EditorView.domEventHandlers({
    keydown(e, view) {
      const id = keys[String(e.key).toLowerCase()];
      if (!id || !(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey || cell.type !== "markdown" || !mdRichOn() || view.state.readOnly) return false;
      e.preventDefault();
      e.stopPropagation();
      MD_ACTIONS[id].run(view);
      return true;
    },
  }));
}

// ---------- the cell while it is edited ----------

function mdFinish(cell) {
  if (!cell.editing) return;
  cell.editing = false;
  updateCell(cell);
  refreshOutline();
  enterCommandMode(cell);
}

// Arrow keys move along a toolbar or a tab row, and Tab leaves it: one Tab stop each.
function mdStop(row, selector, b = null) {
  const items = [...row.querySelectorAll(selector)].filter((x) => !x.disabled);
  const on = b || items.find((x) => x.getAttribute("aria-selected") === "true") || items[0];
  row.querySelectorAll(selector).forEach((x) => { x.tabIndex = x === on ? 0 : -1; });
}

function mdRoving(row, selector) {
  row.addEventListener("focusin", (e) => { if (e.target.matches(selector)) mdStop(row, selector, e.target); });
  row.addEventListener("keydown", (e) => {
    const list = [...row.querySelectorAll(selector)].filter((b) => !b.disabled);
    const i = list.indexOf(e.target);
    if (i < 0) return;
    const to = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: list.length - 1 }[e.key];
    if (to === undefined) return;
    e.preventDefault();
    list[(to + list.length) % list.length].focus();
  });
}

function mdRichBody(cell) {
  const nb = state.notebook;
  const view = editorFor(cell);
  const v = mdViewOf(nb);
  const tool = (id) => {
    const a = MD_ACTIONS[id];
    return h("button", { class: `tool small md-fmt md-fmt-${id}`, type: "button", title: a.title, "aria-label": a.label,
      "aria-keyshortcuts": a.keys || null, onmousedown: (e) => e.preventDefault(), onclick: () => a.run(view) }, a.text);
  };
  const sep = () => h("span", { class: "sep", role: "separator", "aria-orientation": "vertical" });
  const labelId = `md-view-label-${cell.id}`;
  const toolbar = h("div", { class: "md-toolbar", role: "toolbar", "aria-label": "Markdown formatting" },
    tool("bold"), tool("italic"), tool("heading"), sep(), tool("bullets"), tool("numbers"), sep(),
    tool("link"), tool("code"), tool("table"), h("span", { class: "spacer" }));
  preview("mdToolbar", toolbar);
  toolbar.append(h("span", { class: "md-view-pick" }, h("span", { class: "md-view-label", id: labelId }, "View"),
    h("div", { class: "md-switch", role: "group", "aria-labelledby": labelId }, MD_VIEWS.map(([id, label]) =>
      h("button", { type: "button", "data-md-view": id, onclick: () => setMdView(nb, id) }, label)))));
  mdRoving(toolbar, "button");

  const editId = `md-edit-${cell.id}`;
  const prevId = `md-prev-${cell.id}`;
  const tab = (id, label, controls) => h("button", { class: "tab", role: "tab", type: "button", "data-md-tab": id, "aria-controls": controls,
    "aria-selected": String((cell.mdTab || "edit") === id),
    onclick: () => mdShowTab(rich, id, cell) }, label);
  const tabs = h("div", { class: "md-tabs", role: "tablist", "aria-label": "Markdown cell" },
    tab("edit", "Edit", editId), tab("preview", "Preview", prevId));
  mdRoving(tabs, "[role=tab]");
  const rich = h("div", { class: "md-rich", onkeydown: (e) => {
    // Esc from the toolbar or the preview; in the editor, the cell's keymap has it (cellKeymap).
    if (e.key === "Escape" && !e.target.closest(".cm-editor")) { e.preventDefault(); e.stopPropagation(); mdFinish(cell); }
  } },
  toolbar, tabs,
  h("div", { class: "md-panes" },
    h("div", { class: "md-edit", id: editId }, view.dom),
    h("div", { class: "md-view md-preview", id: prevId }, markdown(cell.source, { attachments: cell.attachments }))),
  h("p", { class: "md-hint" }, MD_HINT));
  mdShowView(rich, v);
  mdShowTab(rich, cell.mdTab || "edit", cell);
  return rich;
}

// On a narrow cell, Split shows the editor or the preview, picked with these tabs. The pick lasts while
// the cell is open (cell.mdTab) and isn't saved.
function mdShowTab(rich, id, cell) {
  rich.dataset.tab = id;
  rich.querySelectorAll("[data-md-tab]").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.mdTab === id)));
  mdStop(rich.querySelector(".md-tabs"), "[role=tab]");
  cell.mdTab = id;
}

// The preview follows the source as you type, a moment after you stop.
const mdTimers = new Map();
function mdSourceChanged(cell) {
  clearTimeout(mdTimers.get(cell.id));
  mdTimers.set(cell.id, setTimeout(() => {
    mdTimers.delete(cell.id);
    document.querySelector(`[data-cell="${cell.id}"] .md-preview`)?.replaceChildren(markdown(cell.source, { attachments: cell.attachments }));
  }, 150));
}

// A click elsewhere among the cells finishes editing a Markdown cell, as the hint says. On click rather
// than mousedown, so the cell drawn smaller doesn't move what is under the pointer before the click lands.
document.addEventListener("click", (e) => {
  if (state.view !== "notebook" || !state.notebook || !mdRichOn() || !e.target.closest?.(".cells")) return;
  const at = e.target.closest(".cell")?.dataset.cell;
  for (const cell of state.notebook.cells) {
    if (cell.type === "markdown" && cell.editing && String(cell.id) !== at && document.querySelector(`[data-cell="${cell.id}"] .md-rich`)) {
      cell.editing = false;
      updateCell(cell);
      refreshOutline();
    }
  }
}, true);
