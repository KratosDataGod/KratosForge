// Notebook > View > Present (Preview, parity N49): the notebook as slides, full screen. Like the
// report (nbreport.js), it shows the Markdown and the outputs saved from the last run, never the code,
// and nothing in it runs: each code cell's outputs are the engine's web page export
// (notebook_print_page, notebook_formats.to_html), parsed inert and kept to their plain elements
// (nbfPrintNodes), and the Markdown is the Markdown cell's own escaping renderer.
//
// A slide starts at each # or ## heading in a Markdown cell (### stays on the slide). When any cell
// is marked "Starts a slide" (its cell menu; saved as metadata.fabric_desktop.slide_start in the
// .ipynb), only the marks start slides. A cell with nothing to show is left out.

const NBP_MAX_DOTS = 20;
const nbPresent = { opening: false, root: null, slides: [], at: 0, from: null, strip: false, fullscreen: false, keys: null, focus: null };

// A Markdown source cut at its # and ## headings (outside fenced code): the text before the first such
// heading, then one piece per heading. An underlined heading (=== or ---, a level 1 or 2 heading) cuts
// before its paragraph, read as markdownHeadings (nbcells.js) reads one, so a heading starts the same
// slide wherever it is in the cell.
function nbPresentPieces(source) {
  const pieces = [[]];
  let fence = null;
  let para = 0;  // the paragraph lines so far at the end of the current piece, which an underline makes a heading
  let inList = false;
  const cut = (keep) => {  // a new piece from the last `keep` lines, when there's text before them
    const cur = pieces.at(-1);
    if (cur.slice(0, cur.length - keep).some((l) => l.trim())) pieces.push(cur.splice(cur.length - keep));
  };
  for (const line of String(source || "").split("\n")) {
    const mark = line.match(/^ {0,3}(`{3,}|~{3,})/);
    if (fence) {
      if (mark && mark[1][0] === fence[0] && mark[1].length >= fence.length && !line.slice(line.indexOf(mark[1]) + mark[1].length).trim()) fence = null;
      pieces.at(-1).push(line);
      continue;
    }
    if (mark) {
      fence = mark[1];
      para = 0;
      inList = false;
      pieces.at(-1).push(line);
      continue;
    }
    const atx = /^ {0,3}#{1,2}(\s|$)/.test(line);
    const under = para > 0 && /^ {0,3}(=+|-+)\s*$/.test(line);
    if (atx) cut(0);
    else if (under) cut(para);
    pieces.at(-1).push(line);
    if (!line.trim()) inList = false;
    else if (/^\s*([-*+]|\d+[.)])\s/.test(line)) inList = true;
    const text = !atx && !/^ {0,3}#{1,6}(\s|$)/.test(line) && !under && !inList && line.trim() && !/^ {0,3}>/.test(line)
      && (para > 0 || !/^( {4}|\t)/.test(line));
    para = text ? para + 1 : 0;
  }
  return pieces.map((p) => p.join("\n"));
}

// The slides, each a list of parts: { cell, md } for Markdown text, { cell, code: true } for a code
// cell's outputs. start is the cell the slide begins at.
function nbPresentSplit(cells) {
  const marked = cells.some((c) => c.meta?.slide_start);
  const slides = [];
  const open = (cell) => { slides.push({ start: cell, parts: [] }); };
  for (const cell of cells) {
    if (marked && cell.meta?.slide_start) open(cell);
    if (cell.type === "markdown") {
      const pieces = marked ? [cell.source || ""] : nbPresentPieces(cell.source);
      pieces.forEach((md, i) => {
        // A piece after the first begins at a # or ## heading; the first starts a slide when its first
        // heading is # or ## (it begins the piece, or is an underlined heading).
        const starts = !marked && (i > 0 || (markdownHeadings(md)[0]?.level || 9) <= 2);
        if (starts || !slides.length) open(cell);
        if (md.trim()) slides.at(-1).parts.push({ cell, md });
      });
    } else {
      if (!slides.length) open(cell);
      slides.at(-1).parts.push({ cell, code: true });
    }
  }
  return slides;
}

// A slide's title for the strip: its first heading, or its number.
function nbPresentTitle(slide, n) {
  for (const p of slide.parts) {
    const head = p.md && markdownHeadings(p.md)[0];
    if (head) return head.text.replace(/[*_`]/g, "");
  }
  return `Slide ${n}`;
}

// The line saying which run the outputs are from, or null when the app doesn't know when it was.
function nbPresentRanLine(cells) {
  const last = Math.max(0, ...cells.map((c) => c.finishedAtMs || 0));
  return last ? `Outputs from the last run, ${dateTimeText(last)}` : null;
}

async function openNotebookPresent() {
  const nb = state.notebook;
  if (!nb || nbPresent.root || nbPresent.opening || !previewShown()) return;
  nbPresent.opening = true;
  try {
    await nbPresentOpen(nb);
  } finally {
    nbPresent.opening = false;
  }
}

async function nbPresentOpen(nb) {
  const split = nbPresentSplit(nb.cells);
  // The outputs as the report shows them: one export page for the code cells that have any.
  const code = nb.cells.filter((c) => c.type === "code" && (c.outputs || []).length);
  const outputs = new Map();
  if (code.length) {
    const payload = nbfExportPayload({ ...nb, cells: code }, "html").cells;
    const page = await attempt(() => rpc("notebook_print_page", { name: nb.name, cells: payload }), "Could not show the notebook's outputs");
    if (!page) return;
    const boxes = nbfPrintNodes(page.html).filter((n) => n.nodeType === 1 && n.classList.contains("nbfp-cell"));
    code.forEach((c, i) => {
      const kept = [...(boxes[i]?.childNodes || [])].filter((n) => n.nodeType === 1 && !n.matches(".nbfp-label, .nbfp-src"));
      if (kept.length) outputs.set(c, kept);
    });
  }
  const slides = split.map((s) => ({ ...s, parts: s.parts.filter((p) => !p.code || outputs.has(p.cell)) })).filter((s) => s.parts.length);
  if (state.notebook !== nb || state.view !== "notebook") return;  // left the notebook while the outputs were read
  if (!slides.length) {
    toast("There's nothing to present, because the notebook has no Markdown and no outputs yet. Add a Markdown cell or run the notebook first.");
    return;
  }
  slides.forEach((s, i) => { s.title = nbPresentTitle(s, i + 1); });
  Object.assign(nbPresent, { slides, outputs, at: 0, from: state.selected, strip: false, fullscreen: false });
  const ran = nbPresentRanLine(nb.cells);
  const root = h("div", { class: "nb-present", role: "dialog", "aria-modal": "true", "aria-label": `${nb.name}, presenting`, tabindex: "-1" },
    h("div", { class: "nbp-top" },
      h("span", { class: "nbp-name" }, nb.name), previewTag(),
      h("span", { class: "pill nbp-pill" }, "Read-only. Nothing runs."),
      ran ? h("span", { class: "muted-text nbp-ran" }, ran) : null,
      h("span", { class: "spacer" }),
      btn("Slides", "panel", () => nbPresentStrip(!nbPresent.strip), "tool", { "aria-keyshortcuts": "S", "aria-expanded": "false", "data-nbp-strip": true, title: "Show the slides (S)" }),
      btn("Leave", "dismiss", closeNotebookPresent, "tool", { "aria-keyshortcuts": "Escape", title: "Leave Present (Esc)" })),
    h("div", { class: "nbp-body" },
      h("nav", { class: "nbp-strip", "aria-label": "Slides", hidden: true }),
      h("div", { class: "nbp-stage" }, h("section", { class: "nbp-slide", tabindex: "-1", "aria-roledescription": "slide" }))),
    h("div", { class: "nbp-bottom" },
      btn("‹ Previous", null, () => nbPresentGo(nbPresent.at - 1), "tool", { "data-nbp-prev": true, "aria-keyshortcuts": "ArrowLeft PageUp" }),
      h("span", { class: "nbp-count" }),
      btn("Next ›", null, () => nbPresentGo(nbPresent.at + 1), "tool", { "data-nbp-next": true, "aria-keyshortcuts": "ArrowRight PageDown" }),
      // A dot per slide while they fit on the bar; a longer deck has the count alone (400 dots ran off it).
      slides.length <= NBP_MAX_DOTS ? h("span", { class: "nbp-dots", "aria-hidden": "true" }, slides.map(() => h("span", { class: "nbp-dot" }))) : null,
      h("span", { class: "spacer" }),
      h("span", { class: "muted-text nbp-hint" }, "← → to move · Esc to leave")),
    h("div", { class: "sr-only", role: "status", "aria-live": "polite", "data-nbp-live": true }));
  nbPresent.root = root;
  document.body.append(root);
  nbPresent.keys = (e) => nbPresentKey(e);
  nbPresent.focus = (e) => { if (nbPresent.root && !nbPresent.root.contains(e.target)) nbPresent.root.querySelector(".nbp-slide")?.focus(); };
  window.addEventListener("keydown", nbPresent.keys, true);
  document.addEventListener("focusin", nbPresent.focus);
  nbPresentGo(0);
  root.querySelector(".nbp-slide").focus();
  // Full screen when the window lets it; otherwise the overlay already covers the window.
  try {
    root.requestFullscreen?.().then(() => { nbPresent.fullscreen = document.fullscreenElement === root; }, () => {});
  } catch { /* refused: stay as the overlay */ }
}

// Leaving full screen another way (the system's own Esc) leaves Present too.
document.addEventListener("fullscreenchange", () => {
  if (nbPresent.root && nbPresent.fullscreen && document.fullscreenElement !== nbPresent.root) closeNotebookPresent();
});

function closeNotebookPresent() {
  const root = nbPresent.root;
  if (!root) return;
  window.removeEventListener("keydown", nbPresent.keys, true);
  document.removeEventListener("focusin", nbPresent.focus);
  nbPresent.root = null;
  nbPresent.fullscreen = false;
  if (document.fullscreenElement === root) document.exitFullscreen?.().catch(() => {});
  root.remove();
  const cell = document.querySelector(`[data-cell="${CSS.escape(String(nbPresent.from ?? ""))}"]`);
  if (cell) cell.focus({ preventScroll: false });
  else document.getElementById("view")?.focus?.();
}

function nbPresentGo(i) {
  const root = nbPresent.root;
  if (!root) return;
  const n = nbPresent.slides.length;
  nbPresent.at = Math.max(0, Math.min(n - 1, i));
  const slide = nbPresent.slides[nbPresent.at];
  const box = root.querySelector(".nbp-slide");
  box.replaceChildren(...slide.parts.map((p) => p.code
    ? h("div", { class: "nbp-out" }, nbPresent.outputs.get(p.cell).map((node) => node.cloneNode(true)))
    : h("div", { class: "nbp-md md-view" }, markdown(p.md, { attachments: p.cell.attachments }))));
  box.setAttribute("aria-label", `Slide ${nbPresent.at + 1} of ${n}: ${slide.title}`);
  box.scrollTop = 0;
  root.querySelector(".nbp-count").textContent = `${nbPresent.at + 1} / ${n}`;
  root.querySelector("[data-nbp-prev]").disabled = nbPresent.at === 0;
  root.querySelector("[data-nbp-next]").disabled = nbPresent.at === n - 1;
  root.querySelectorAll(".nbp-dot").forEach((d, k) => d.classList.toggle("on", k === nbPresent.at));
  root.querySelector("[data-nbp-live]").textContent = `Slide ${nbPresent.at + 1} of ${n}`;
  if (nbPresent.strip) nbPresentDrawStrip();
  // A disabled button can't keep focus: the slide takes it.
  if (document.activeElement?.disabled || !root.contains(document.activeElement)) box.focus();
}

function nbPresentDrawStrip() {
  const strip = nbPresent.root.querySelector(".nbp-strip");
  strip.replaceChildren(h("ol", {}, nbPresent.slides.map((s, i) => h("li", {},
    h("button", { class: `nbp-thumb${i === nbPresent.at ? " current" : ""}`, "aria-current": i === nbPresent.at ? "true" : null,
      onclick: () => nbPresentGo(i) },
    h("span", { class: "nbp-thumb-title" }, `${i + 1}. ${s.title}`),
    s.start?.meta?.slide_start ? h("span", { class: "tag nbp-mark" }, "Starts a slide") : null)))));
}

function nbPresentStrip(on) {
  const root = nbPresent.root;
  if (!root) return;
  nbPresent.strip = on;
  const strip = root.querySelector(".nbp-strip");
  strip.hidden = !on;
  root.querySelector("[data-nbp-strip]").setAttribute("aria-expanded", String(on));
  if (on) {
    nbPresentDrawStrip();
    strip.querySelector(".nbp-thumb.current")?.focus();
  } else if (strip.contains(document.activeElement) || !root.contains(document.activeElement)) {
    root.querySelector(".nbp-slide").focus();
  }
}

// While presenting, the keys are Present's: the window's other key handlers (Back on Alt+Left, the
// notebook's command keys) don't see the ones it uses. Tab stays inside.
function nbPresentKey(e) {
  if (!nbPresent.root) return;
  const plain = !e.ctrlKey && !e.metaKey && !e.altKey;
  let handled = true;
  if (e.key === "Tab") trapFocus(e, nbPresent.root);
  else if (e.key === "Escape") closeNotebookPresent();
  else if (plain && (e.key === "ArrowRight" || e.key === "PageDown")) nbPresentGo(nbPresent.at + 1);
  else if (plain && (e.key === "ArrowLeft" || e.key === "PageUp")) nbPresentGo(nbPresent.at - 1);
  else if (plain && e.key === "Home") nbPresentGo(0);
  else if (plain && e.key === "End") nbPresentGo(nbPresent.slides.length - 1);
  else if (plain && !e.shiftKey && (e.key === "s" || e.key === "S")) nbPresentStrip(!nbPresent.strip);
  else if (e.key === "F5" && e.ctrlKey && e.shiftKey) { /* already presenting */ }
  else handled = false;
  if (!handled) return;
  if (e.key !== "Tab" || e.defaultPrevented) e.preventDefault();
  e.stopImmediatePropagation();
}

// The View tab's Present button (with Preview features shown).
function nbPresentButton() {
  return preview("nbPresent", btn("Present", "desktop", openNotebookPresent, "tool",
    { title: "Show the Markdown and the outputs from the last run as slides, full screen (Ctrl+Shift+F5). Nothing runs.", "aria-keyshortcuts": "Control+Shift+F5" }));
}

// The cell menu's "Starts a slide", on or off.
function nbPresentMenuItems(cell) {
  if (!PREVIEW_FEATURES.nbPresent || !previewShown()) return [];
  const on = !!cell.meta?.slide_start;
  return [btn("Starts a slide", "desktop", () => setMeta(cell, "slide_start", !on), "tool",
    { "aria-pressed": String(on), title: "In Present, a slide starts at this cell. When any cell is marked, only the marks start slides." })];
}

// The cell header's small mark.
function nbPresentHeadTag(cell) {
  return cell.meta?.slide_start && previewShown() ? h("span", { class: "tag nbp-mark" }, "Starts a slide") : null;
}

registerShortcuts("Notebook", [
  { keys: ["Ctrl+Shift+F5"], label: "Present the notebook as slides (Esc leaves)", when: () => state.view === "notebook" && previewShown() },
]);
document.addEventListener("keydown", (e) => {
  if (e.key !== "F5" || !e.ctrlKey || !e.shiftKey || e.altKey || e.metaKey) return;
  if (state.view !== "notebook" || !state.notebook || !previewShown() || nbPresent.root) return;
  if (document.getElementById("dialog-root")?.childElementCount) return;
  e.preventDefault();
  openNotebookPresent();
});
