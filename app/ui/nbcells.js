// More ways to organise a notebook's cells, as in Fabric, Jupyter and Databricks:
// - collapse a Markdown heading's section (the cells under it until the next heading of the same or
//   a higher level), saved the way JupyterLab saves it;
// - a title on any cell, shown in its header and the outline, saved where Databricks keeps it;
// - tags beyond "parameters", saved as Jupyter cell tags;
// - duplicate a cell, and drag a cell by its grip to move it.

const CELL_MIME = "application/x-kratosforge-cell";

// A Markdown cell's heading lines: [{ level, text }], "# Title" and a line underlined with === or ---.
// A "# comment" inside a fenced code block (``` or ~~~) isn't a heading; as in CommonMark, only a
// bare fence of the same kind, at least as long, closes it (```python doesn't).
function markdownHeadings(source) {
  const out = [];
  let fence = null;
  let para = [];  // the paragraph lines so far, which an underline makes a heading
  let inList = false;  // in a list item, whose continuation lines aren't heading text
  for (const line of source.split("\n")) {
    if (fence) {
      const close = line.match(/^ {0,3}(`{3,}|~{3,})\s*$/);
      if (close && close[1][0] === fence[0] && close[1].length >= fence.length) fence = null;
      continue;
    }
    const open = line.match(/^ {0,3}(`{3,}|~{3,})/);
    if (open) { fence = open[1]; para = []; inList = false; continue; }
    const m = line.match(/^(#{1,6})\s+(\S.*)$/);
    const under = para.length && line.match(/^ {0,3}(=+|-+)\s*$/);
    if (m) out.push({ level: m[1].length, text: m[2].trim() });
    else if (under) out.push({ level: under[1][0] === "=" ? 1 : 2, text: para.map((l) => l.trim()).join(" ") });
    // Paragraph text: not blank, not a heading, list item or quote, and not an indented code line
    // (four spaces or a tab, outside a paragraph).
    if (!line.trim()) inList = false;
    else if (/^\s*([-*+]|\d+[.)])\s/.test(line)) inList = true;
    const text = !m && !under && !inList && line.trim() && !/^ {0,3}>/.test(line)
      && (para.length || !/^( {4}|\t)/.test(line));
    para = text ? [...para, line] : [];
  }
  return out;
}

// The heading level a Markdown cell starts a section at: its first heading line, or 0.
function headingLevel(cell) {
  return cell.type === "markdown" ? markdownHeadings(cell.source)[0]?.level || 0 : 0;
}

// Ids of the cells hidden by collapsed sections, and how many each collapsed heading hides.
function collapsedCells(cells) {
  const hidden = new Set();
  const counts = new Map();
  let open = null;  // { cell, level } of the collapsed heading whose section we're in
  for (const cell of cells) {
    const level = headingLevel(cell);
    if (open && level && level <= open.level) open = null;
    if (open) {
      hidden.add(cell.id);
      counts.set(open.cell.id, (counts.get(open.cell.id) || 0) + 1);
      continue;
    }
    if (level && cell.meta?.heading_collapsed) open = { cell, level };
  }
  return { hidden, counts };
}

function toggleSection(cell, on = !cell.meta?.heading_collapsed) {
  if (!headingLevel(cell)) return;
  cell.meta = cell.meta || {};
  if (on) cell.meta.heading_collapsed = true;
  else delete cell.meta.heading_collapsed;
  // The selected cell can't be hidden: the heading takes the selection.
  if (on && collapsedCells(state.notebook.cells).hidden.has(state.selected)) state.selected = cell.id;
  markDirty();
  renderNotebook();
  refreshOutline();
}

function setAllSections(on) {
  state.notebook.cells.filter(headingLevel).forEach((c) => {
    c.meta = c.meta || {};
    if (on) c.meta.heading_collapsed = true;
    else delete c.meta.heading_collapsed;
  });
  if (on && collapsedCells(state.notebook.cells).hidden.has(state.selected)) {
    state.selected = state.notebook.cells.find((c) => headingLevel(c))?.id ?? state.selected;
  }
  markDirty();
  renderNotebook();
  refreshOutline();
}

// Opens every collapsed section that hides `cell`, so it can be shown (outline, find, run errors).
function revealCell(cell) {
  const cells = state.notebook.cells;
  let changed = false;
  while (collapsedCells(cells).hidden.has(cell.id)) {
    const i = cells.indexOf(cell);
    const heading = cells.slice(0, i).reverse().find((c) => c.meta?.heading_collapsed && headingLevel(c));
    if (!heading) break;
    delete heading.meta.heading_collapsed;
    changed = true;
  }
  if (changed) { markDirty(); renderNotebook(); }
  return changed;
}

// What renderNotebook draws for the cells: hidden ones left out.
function visibleCells(cells) {
  const { hidden } = collapsedCells(cells);
  return hidden.size ? cells.filter((c) => !hidden.has(c.id)) : cells;
}

// The chevron in a heading cell's gutter, and the "N cells hidden" bar under a collapsed one.
function sectionToggle(cell) {
  if (!headingLevel(cell)) return null;
  const on = !!cell.meta?.heading_collapsed;
  return h("button", { class: `tool small section-toggle${on ? " collapsed" : ""}`, "aria-expanded": String(!on),
    title: on ? "Expand this section" : "Collapse this section", "aria-label": on ? "Expand section" : "Collapse section",
    onclick: () => toggleSection(cell) }, icon(on ? "chevronRight" : "chevronDown", 14));
}

function sectionHiddenBar(cell) {
  if (!cell.meta?.heading_collapsed) return null;
  const n = collapsedCells(state.notebook.cells).counts.get(cell.id) || 0;
  if (!n) return null;
  return h("button", { class: "section-hidden", onclick: () => toggleSection(cell, false) },
    `${n} cell${n === 1 ? "" : "s"} hidden. Click to show.`);
}

// ---------- titles and tags ----------

async function editTitle(cell) {
  const title = await askText(cell.meta?.title ? "Rename cell title" : "Add a cell title", "Title", cell.meta?.title || "", "Save",
    (v) => (v.length > 200 ? "Keep the title under 200 characters." : ""));
  if (title == null) return;
  cell.meta = cell.meta || {};
  cell.meta.title = title;
  markDirty();
  updateCell(cell);
  refreshOutline();
}

function removeTitle(cell) {
  delete cell.meta.title;
  markDirty();
  updateCell(cell);
  refreshOutline();
}

const TAG_RE = /^[\w.:-]{1,40}$/;

async function addTag(cell) {
  const value = await askText("Add a tag", "Tag (letters, numbers, - _ . :)", "", "Add",
    (v) => (!TAG_RE.test(v) ? "Use letters, numbers and - _ . : with no spaces, up to 40 characters." : ""));
  if (value == null) return;
  if (value === "parameters") { toggleParams(cell); return; }  // one parameter cell per notebook
  cell.meta = cell.meta || {};
  const tags = cell.meta.tags || (cell.meta.tags = []);
  if (!tags.includes(value)) tags.push(value);
  markDirty();
  updateCell(cell);
}

function removeTag(cell, tag) {
  cell.meta.tags = (cell.meta.tags || []).filter((t) => t !== tag);
  if (!cell.meta.tags.length) delete cell.meta.tags;
  markDirty();
  updateCell(cell);
}

// Title and tag chips for the cell header (the parameters tag has its own chip in notebook.js).
function cellHeadExtras(cell) {
  const out = [];
  if (cell.meta?.title) {
    out.push(h("button", { class: "cell-title", title: "Rename the title", onclick: () => editTitle(cell) }, cell.meta.title));
  }
  for (const tag of (cell.meta?.tags || []).filter((t) => t !== "parameters")) {
    out.push(h("span", { class: "tag user-tag" }, tag,
      h("button", { class: "tag-x", title: `Remove the tag ${tag}`, "aria-label": `Remove tag ${tag}`, onclick: () => removeTag(cell, tag) }, "×")));
  }
  return out;
}

function cellMenuExtras(cell) {
  const items = [{ label: cell.meta?.title ? "Rename title" : "Add title", icon: "documentTable", action: () => editTitle(cell) }];
  if (cell.meta?.title) items.push({ label: "Remove title", icon: "dismiss", action: () => removeTitle(cell) });
  items.push({ label: "Add tag", icon: "add", action: () => addTag(cell) });
  items.push({ label: "Duplicate cell", icon: "copyArrowRight", action: () => duplicateCell(cell) });
  if (headingLevel(cell)) items.push({ label: cell.meta?.heading_collapsed ? "Expand section" : "Collapse section", icon: "chevronDown", action: () => toggleSection(cell) });
  return items;
}

// ---------- duplicate and drag to move ----------

function duplicateCell(cell) {
  const cells = state.notebook.cells;
  const meta = structuredClone(cell.meta || {});
  if (meta.tags) meta.tags = meta.tags.filter((t) => t !== "parameters");  // one parameter cell
  if (meta.tags && !meta.tags.length) delete meta.tags;
  delete meta.heading_collapsed;
  const copy = cellFrom({ type: cell.type, source: cell.source, meta });
  if (copy.type === "markdown") copy.editing = false;
  cells.splice(cells.indexOf(cell) + 1, 0, copy);
  state.selected = copy.id;
  markDirty();
  renderNotebook();
  refreshOutline();
  enterCommandMode(copy);
}

// A shown cell and the cells its collapsed section hides right after it: they move together, as
// in JupyterLab.
function withSection(cell, cells = state.notebook.cells, hidden = collapsedCells(cells).hidden) {
  const out = [cell];
  for (let i = cells.indexOf(cell) + 1; i < cells.length && hidden.has(cells[i].id); i++) out.push(cells[i]);
  return out;
}

// Puts `moving` (whole sections) before `target`, or after target and its hidden cells. A cell that
// was showing stays showing: a collapsed section it lands in is opened.
function placeCells(moving, target, after) {
  const cells = state.notebook.cells;
  const { hidden } = collapsedCells(cells);
  const shown = cells.filter((c) => !hidden.has(c.id));
  const span = withSection(target, cells, hidden).length;
  const rest = cells.filter((c) => !moving.includes(c));
  rest.splice(rest.indexOf(target) + (after ? span : 0), 0, ...moving);
  cells.splice(0, cells.length, ...rest);
  shown.forEach(revealCell);
}

// Moves `block` (cells next to each other on screen, in order) one shown cell up or down.
// "apart" when they aren't next to each other on screen, "edge" at the top or bottom.
function moveShown(block, delta) {
  const cells = state.notebook.cells;
  const { hidden } = collapsedCells(cells);
  const shown = cells.filter((c) => !hidden.has(c.id));
  const first = shown.indexOf(block[0]);
  if (first < 0 || block.some((c, k) => shown[first + k] !== c)) return "apart";
  const neighbour = shown[delta < 0 ? first - 1 : first + block.length];
  if (!neighbour) return "edge";
  placeCells(block.flatMap((c) => withSection(c, cells, hidden)), neighbour, delta > 0);
  return "moved";
}

// Where a dragged cell goes: before or after `target`, by which half the pointer is over. A
// collapsed heading brings its hidden cells.
function moveCellTo(cell, target, after) {
  const moving = withSection(cell);
  if (moving.includes(target)) return;
  placeCells(moving, target, after);
  state.selected = cell.id;
  markDirty();
  renderNotebook();
  refreshOutline();
}

function cellGrip(cell) {
  return h("span", { class: "cell-grip", draggable: "true", title: "Drag to move this cell", "aria-hidden": "true",
    ondragstart: (e) => {
      e.dataTransfer.setData(CELL_MIME, String(cell.id));
      e.dataTransfer.effectAllowed = "move";
      e.target.closest(".cell")?.classList.add("dragging");
    },
    ondragend: (e) => {
      e.target.closest(".cell")?.classList.remove("dragging");
      document.querySelectorAll(".drop-above, .drop-below").forEach((el) => el.classList.remove("drop-above", "drop-below"));
    } }, "⋮⋮");
}

const isCellDrag = (e) => [...(e.dataTransfer?.types || [])].includes(CELL_MIME);

document.addEventListener("dragover", (e) => {
  if (state.view !== "notebook" || !isCellDrag(e)) return;
  const el = e.target.closest?.("[data-cell]");
  if (!el) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  const box = el.getBoundingClientRect();
  const after = e.clientY > box.top + box.height / 2;
  document.querySelectorAll(".drop-above, .drop-below").forEach((x) => x !== el && x.classList.remove("drop-above", "drop-below"));
  el.classList.toggle("drop-below", after);
  el.classList.toggle("drop-above", !after);
}, true);

document.addEventListener("drop", (e) => {
  if (state.view !== "notebook" || !isCellDrag(e)) return;
  const el = e.target.closest?.("[data-cell]");
  if (!el) return;
  e.preventDefault();
  e.stopPropagation();
  const cells = state.notebook.cells;
  const dragged = cells.find((c) => String(c.id) === e.dataTransfer.getData(CELL_MIME));
  const target = cells.find((c) => String(c.id) === el.dataset.cell);
  const after = el.classList.contains("drop-below");
  el.classList.remove("drop-above", "drop-below");
  if (dragged && target) moveCellTo(dragged, target, after);
}, true);
