// Where every page, command and preview flag is declared, so a new feature is added in one
// place and lands somewhere people already look. Rules (docs/ui-guidelines.md):
//   - The left rail holds at most RAIL_LIMIT pages. A new feature becomes a tab or group inside
//     an existing page; the rail grows only for a new kind of work.
//   - Every page and action registers commands, so the command bar (Ctrl+K) finds it.
//   - A feature not yet tried on a real tenant is marked preview: it shows a Preview tag, and
//     people can hide all of them in Settings.
// Loaded before app.js; the functions here run only after every script has loaded.

const RAIL_LIMIT = 8;
const features = { list: [], views: {}, commands: [] };

// f: {id, rail?: {label, icon, order, bottom?, also?: [view ids that light this rail item]},
//     views?: {viewId: render function}, commands?: [{label, icon, keywords, run, preview}], preview?}
function registerFeature(f) {
  features.list.push(f);
  Object.assign(features.views, f.views || {});
  for (const c of f.commands || []) features.commands.push({ preview: f.preview, ...c, feature: f.id });
  const main = features.list.filter((x) => x.rail && !x.rail.bottom).length;
  if (main > RAIL_LIMIT) console.warn(`The left rail has ${main} pages; the limit is ${RAIL_LIMIT}. Put ${f.id} inside an existing page.`);
}

function railFeatures(bottom = false) {
  return features.list.filter((f) => f.rail && !!f.rail.bottom === bottom && (!f.preview || previewShown()))
    .sort((a, b) => a.rail.order - b.rail.order);
}

function railItem(f) {
  const view = Object.keys(f.views || {})[0];
  const lit = [view, ...(f.rail.also || [])].includes(state.view);
  return h("button", { class: `rail-item${lit ? " active" : ""}`, "aria-current": lit ? "page" : null, onclick: () => go(view) },
    icon(f.rail.icon, 22), f.rail.label);
}

// ---------- preview ----------

// Features not yet tried on a live Fabric tenant. Each loses its tag once it has been.
const PREVIEW_FEATURES = {
  fabricRun: "Run in Fabric",
  schedules: "Schedules",
  sideBySide: "Side by side",
  prove: "Prove this notebook",
  validate: "Validate notebook",
  notebookRuns: "Notebook runs",
  fabricEdit: "Edit Fabric notebooks on this PC",
  wizard: "New notebook wizard",
  deps: "Cell dependencies",
  practices: "Notebook best practices",
  debug: "Breakpoints, variable history and watches",
  replay: "Failure replay bundles",
  inputs: "Parameters and widgets bar",
  cost: "Cost",
  capacity: "Capacity status",
  usage: "Measured usage import",
  shortcuts: "OneLake shortcuts",
  shortcutFixes: "Shortcut fixes",
  access: "OneLake access",
  history: "Table history and restore",
  health: "Table health",
  model: "Semantic model",
  ossie: "Semantic models and Apache Ossie",
  directLakeCheck: "Direct Lake check",
  dataDictionary: "Data dictionary",
  tests: "Tests",
  xray: "Estate X-ray",
  optimize: "Optimize",
  itemPreview: "Item preview",
  resultViewer: "Result downloads",
  chartBuilder: "Chart builder",
  severalCharts: "Several charts per result",
  dataWrangler: "Data Wrangler",
  queryProfile: "Query profile",
  sharedSessions: "Shared notebook sessions",
  ipywidgets: "Notebook widgets",
  altair: "Altair charts in notebooks",
  mesh: "Data mesh",
  platform: "Platform",
  sourceControl: "Source control",
  workloads: "Workloads",
  incidents: "Incidents",
  agents: "Agents",
  queue: "Queue",
  runAs: "Run as",
  integrations: "External AI tools",
  releases: "Release records",
  lineage: "Column lineage",
  rollback: "Roll back a release",
  promote: "Promote",
  syncKeys: "Sync keys",
  varlib: "Variable library",
  quarantineReplay: "Quarantine replay",
  risk: "Risk check before a run",
  sessions: "Notebook sessions",
  environments: "Notebook environments",
  nbFormats: "Notebook import and export formats",
  profile: "Profile",
  notebookFolderPublish: "Publishing notebooks from folders",
  fabricStep: "Run the last cell on Fabric",
  popOut: "Pop-out windows",
  pipelines: "Pipelines",
  pipelineRuns: "Pipeline runs in Monitor",
  cancelFabricRun: "Cancel a Fabric run",
  requestsCoreFour: "Workspace, domain and exception requests",
  trustBadges: "Endorsement and business terms",
  warehouse: "Warehouse queries",
  sqlResultViews: "Messages and Chart on the SQL page",
  sqlPractices: "SQL best practices",
  bestPractices: "Best practices: checks and templates",
  updateBanner: "New build banner",
  engines: "Notebook engine picker",
  vscode: "VS Code pairing",
  runSeveral: "Run several notebooks from Home",
  itemPins: "Items pinned to Home",
  learn: "Learn",
};

function previewShown() {
  return state.settings?.show_preview?.value !== false;
}

function previewTag() {
  return h("span", { class: "preview-tag", title: "In preview: this may change as it's tried on more tenants. Settings can hide preview features." }, "Preview");
}

// The node with a Preview tag, or null when this person hides preview features.
function preview(id, node) {
  if (!PREVIEW_FEATURES[id]) return node;
  if (!previewShown()) return null;
  node.append(previewTag());
  return node;
}

// Core steps that are untested on a tenant but must not disappear when preview features are hidden:
// they always show, always with the Preview tag (previewMarked).
const PREVIEW_ALWAYS_SHOWN = ["promote", "syncKeys", "varlib", "quarantineReplay", "risk", "vscode"];

// The node with a Preview tag, whatever the Settings switch says.
function previewMarked(id, node) {
  if (PREVIEW_FEATURES[id]) node.append(previewTag());
  return node;
}

// A small "Learn more" link to a section of the user guide.
function learnMore(anchor, label = "Learn more") {
  return h("a", { href: "#", class: "learn-more", onclick: (e) => {
    e.preventDefault();
    attempt(() => invoke("open_doc", { page: `user-guide.md${anchor ? `#${anchor}` : ""}` }), "Could not open the guide");
  } }, label);
}

// A link to a docs page section the engine names, like a best-practice finding's url
// ("sql-best-practices.md#sq002"). open_doc refuses anything that isn't a docs page.
function docLink(page, label) {
  return h("a", { href: "#", class: "learn-more", onclick: (e) => {
    e.preventDefault();
    attempt(() => invoke("open_doc", { page }), "Could not open the guide");
  } }, label);
}

// "How this works": the panel beside a page or tab that says what it reads, what it changes and where
// its results go, then links to its section of the guide. It folds to its title, remembered on this
// PC. Every new page or tab gets one (docs/ui-guidelines.md); withHowItWorks puts it on the right,
// or under the content in a narrow window. With explanations on request, the page's own panel opens from
// the title bar's info button and any other starts folded to its title where it is.
function howItWorks(id, { reads, changes, results, more = [], anchor }) {
  const part = (title, text) => (text ? h("div", { class: "how-part" }, h("b", {}, title), h("p", {}, text)) : null);
  return foldable(`how.${id}`, h("h2", {}, "How this works"),
    h("div", { class: "how-body" }, part("What it reads", reads), part("What it changes", changes),
      part("Where results go", results), ...more.map(([title, text]) => part(title, text)), anchor ? learnMore(anchor) : null),
    { cls: "card pad how-it-works", start: layoutChoice("ui_explanations") === "on_request" });
}

function withHowItWorks(main, panel) {
  return h("div", { class: "with-how" }, h("div", { class: "with-how-main" }, main), panel);
}

// ---------- Settings > Appearance > Layout ----------

// Three choices (app_settings ui_explanations, ui_preview_marks, ui_density) that change how every
// page shows the same things, as classes on <body>; styles.css does the rest. Nothing is hidden for
// good: with explanations "on request" (the default), a page's How this works panel and its one-line
// explanation (pageIntro) open as a drawer from the title bar's info button, and a Preview dot keeps
// the tag's words for screen readers and as its tooltip.
const LAYOUT_CHOICES = {
  ui_explanations: ["Explanations", "Each page's one-line explanation and How this works panel: what it reads, what it changes and where results go.",
    [["beside", "Beside the page"], ["on_request", "On request"]]],
  ui_preview_marks: ["Preview marks", "How features still in preview are marked. Page titles keep the full tag.",
    [["tags", "Tags"], ["dots", "Quiet dots"]]],
  ui_density: ["Density", "Spacing in tables, lists and toolbars.", [["comfortable", "Comfortable"], ["compact", "Compact"]]],
  learn_mode: ["Learning guidance (Preview)", "Expert shows findings, their About link and Dismiss. Professional, the default, adds a line on why each finding matters, with Explain and Show example when you want them. Guided opens the explanation, adds the Learn lesson that covers each finding and suggests your next lesson on Learn. A preference only: checks and your organization's rules are the same at every level.",
    [["expert", "Expert"], ["professional", "Professional"], ["guided", "Guided"]]],
  // GD-08: a level for one subject, under Learning guidance. Same follows it.
  learn_mode_sql: ["SQL guidance (Preview)", "Beside SQL best-practice findings, on the SQL and warehouse pages. Same follows Learning guidance.",
    [["same", "Same"], ["expert", "Expert"], ["professional", "Professional"], ["guided", "Guided"]]],
  learn_mode_notebooks: ["Notebook guidance (Preview)", "Beside notebook best-practice findings. Same follows Learning guidance.",
    [["same", "Same"], ["expert", "Expert"], ["professional", "Professional"], ["guided", "Guided"]]],
};
const LAYOUT_DEFAULTS = { ui_explanations: "on_request", learn_mode: "professional", learn_mode_sql: "same", learn_mode_notebooks: "same" };  // as app_settings.DEFAULTS; others: the first choice
const layoutChoice = (key) => state.settings?.[key]?.value || LAYOUT_DEFAULTS[key] || LAYOUT_CHOICES[key][2][0][0];
const howView = { open: null };  // the view the drawer was opened on

function applyAppearance() {
  const body = document.body;
  body.classList.toggle("explain-on-request", layoutChoice("ui_explanations") === "on_request");
  body.classList.toggle("preview-dots", layoutChoice("ui_preview_marks") === "dots");
  body.classList.toggle("density-compact", layoutChoice("ui_density") === "compact");
  syncHowToggle();
}

// A page's one-line explanation under its title. With explanations on request it leaves the page and
// opens at the top of the info drawer. Facts about what's open (counts, owners, status) stay plain
// "sub" lines: only what explains the page goes here.
function pageIntro(...children) {
  return h("p", { class: "sub page-intro" }, ...children);
}

// The page's own panel. Home's cards, the notebook's side panes and query results keep theirs in place.
const HOW_IN_PLACE = ".ready, .xray, .explorer-content, .validate-how, .warehouse-how, .dialog";
function howPanel() {
  return [...document.querySelectorAll("#view .how-it-works")].find((p) => !p.closest(HOW_IN_PLACE));
}

// The drawer's copy of the page's explanation, first in the panel. A page with an explanation and no
// panel gets a panel of its own for it. Learn more stays on the panel's own link.
function howIntro() {
  const intro = document.querySelector("#view .page-intro");
  let panel = howPanel();
  if (!intro) {
    if (panel?.classList.contains("how-intro-only")) panel.remove();
    return howPanel();
  }
  // A tab that draws its own panel after the page's explanation got one of its own: that one goes.
  if (panel && !panel.classList.contains("how-intro-only")) document.querySelectorAll("#view .how-intro-only").forEach((p) => p.remove());
  if (!panel) {
    panel = h("section", { class: "card pad how-it-works how-intro-only" }, h("h2", {}, "About this page"));
    (intro.closest(".page") || document.getElementById("view")).append(panel);
  }
  const copy = intro.cloneNode(true);
  copy.querySelectorAll(".learn-more, .preview-tag").forEach((el) => el.remove());
  // A panel of its own takes the explanation's Learn more link (the page's own, so it still works).
  const link = panel?.classList.contains("how-intro-only") || !panel ? intro.querySelector(".learn-more") : null;
  const text = copy.textContent.trim();
  let about = panel.querySelector(":scope > .how-about");
  if (about?.textContent !== text) {
    about?.remove();
    about = h("p", { class: "how-about" }, text);
    if (panel.classList.contains("how-intro-only")) panel.querySelector(":scope > h2").after(about);
    else panel.prepend(about);
  }
  if (link) panel.append(link);
  return panel;
}

// Shows the info button while the page has a panel or an explanation to open, and keeps the drawer
// on the page it was opened on: a re-render puts it on the new copy of the panel, another page closes it.
function syncHowToggle() {
  const toggle = document.getElementById("how-toggle");
  if (!toggle) return;
  const panel = document.body.classList.contains("explain-on-request") ? howIntro() : null;
  if (!panel) document.querySelectorAll("#view .how-intro-only").forEach((p) => {
    // The page's own Learn more goes back under its title with the line it came from.
    const link = p.querySelector(":scope > .learn-more");
    if (link) document.querySelector("#view .page-intro")?.append(link);
    p.remove();
  });
  document.querySelectorAll("#view .how-page").forEach((p) => p !== panel && p.classList.remove("how-page"));
  panel?.classList.add("how-page");
  toggle.hidden = !panel;
  if (howView.open && (!panel || howView.open !== state.view)) setHowDrawer(false);
  else if (howView.open && !panel.classList.contains("how-shown")) panel.classList.add("how-shown");
}

function setHowDrawer(open) {
  document.querySelectorAll(".how-it-works.how-shown").forEach((p) => p.classList.remove("how-shown"));
  const panel = open ? howIntro() : null;
  panel?.classList.add("how-shown");
  howView.open = panel ? state.view : null;
  document.body.classList.toggle("how-open", !!panel);
  document.getElementById("how-toggle")?.setAttribute("aria-expanded", String(!!panel));
}

function initAppearance() {
  const toggle = document.getElementById("how-toggle");
  if (!toggle) return;
  toggle.addEventListener("click", () => setHowDrawer(!howView.open));
  // Escape closes the drawer, unless a dialog or menu is open: that Escape is theirs.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && howView.open && !document.querySelector(".overlay, #menu")) setHowDrawer(false);
  });
  let queued = false;
  new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; syncHowToggle(); });
  }).observe(document.getElementById("view"), { childList: true, subtree: true });
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initAppearance);
else initAppearance();

// ---------- keyboard shortcuts ----------

// One registry of keyboard shortcuts. The Keyboard shortcuts dialog ("?", the command bar, Settings >
// Help and about) lists whatever is registered, so a feature that adds keys adds them here once and
// they can't go missing from the list:
//
//   registerShortcuts("Notebook", [{ keys: ["Shift+Enter"], label: "Run the cell and select the next one",
//     when: () => state.view === "notebook" }]);
//
// keys: what to press, each a combination written with "+" ("Ctrl+K"); several mean "any of these".
// label: what it does. when (optional): list it only while this returns true.
// An entry with an id is also matched by isShortcut(e, id): match lists KeyboardEvent.key values and
// mod means Ctrl (or Cmd) is held, alt that Alt is. The app-wide handlers in app.js read their keys that way.
const shortcutRegistry = [];

function registerShortcuts(group, list) {
  for (const s of list) shortcutRegistry.push({ ...s, group });
}

// The keys that work everywhere in the window (app.js handles each through isShortcut).
registerShortcuts("Anywhere", [
  { id: "palette", keys: ["Ctrl+K"], match: ["k"], mod: true,
    label: "Open the command bar: find a page, action, notebook, workspace or entity" },
  { id: "shortcuts", keys: ["?"], match: ["?"], label: "Show this list (when you aren't typing in a box)" },
  { id: "close", keys: ["Esc"], match: ["Escape"],
    label: "Close the command bar, a menu, a dialog or the activity tray, then go back to where you were" },
]);
registerShortcuts("Moving around", [
  { id: "focus", keys: ["Tab"], match: ["Tab"],
    label: "Move to the next control (Shift+Tab: the previous one). In a dialog, it stays inside the dialog" },
  { id: "open", keys: ["Enter", "Space"], match: ["Enter", " "],
    label: "Open the row or card that has focus, such as a notebook, run or workload" },
  // app.js navKey: not while typing in a box or with a dialog open.
  { id: "history", keys: ["Alt+Left", "Alt+Right"], match: ["ArrowLeft", "ArrowRight"], alt: true, label: "Go back or forward to the page you were on (not while typing in a box)" },
]);
registerShortcuts("Menus and tabs", [
  { id: "menu", keys: ["Up", "Down", "Home", "End"], match: ["ArrowDown", "ArrowUp", "Home", "End"],
    label: "Move through an open menu. Enter picks the item" },
  { id: "tabs", keys: ["Left", "Right", "Home", "End"], match: ["ArrowLeft", "ArrowRight", "Home", "End"],
    label: "On a tab: move to the previous, next, first or last tab" },
]);

// Whether a key press is the registered shortcut with this id. Without mod, held modifiers are left
// to the handler.
function isShortcut(e, id) {
  const s = shortcutRegistry.find((x) => x.id === id);
  if (!s?.match || (s.mod && !(e.ctrlKey || e.metaKey)) || (s.alt && !e.altKey)) return false;
  const key = String(e.key || "");
  return s.match.some((k) => (k.length === 1 ? key.toLowerCase() === k : key === k));
}

// Whether focus is in a box someone types in, where a plain "?" must stay a character.
function typingIn(el) {
  return !!el && (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || !!el.isContentEditable
    || !!el.closest?.(".cm-editor"));
}

// The shortcuts to list now, by group, in the order they were registered.
function shortcutGroups() {
  const shown = shortcutRegistry.filter((s) => {
    try { return !s.when || s.when(); } catch { return false; }
  });
  return [...new Set(shown.map((s) => s.group))].map((group) => [group, shown.filter((s) => s.group === group)]);
}

function shortcutsDialog() {
  const keys = (s) => h("td", { class: "kbd-keys" }, ...s.keys.flatMap((combo, i) => [
    i ? h("span", { class: "muted-text" }, "/") : null,
    ...combo.split(/(?<=.)\+(?=.)/).flatMap((k, j) => [j ? h("span", { class: "muted-text" }, "+") : null, h("kbd", {}, k)])]));
  const groups = shortcutGroups();
  const body = h("div", { class: "scroll shortcuts-list" },
    ...groups.map(([group, list]) => h("section", {}, h("h3", {}, group),
      h("table", { class: "list" }, h("tbody", {},
        list.map((s) => h("tr", { "data-shortcut": s.id || null }, keys(s), h("td", {}, s.label))))))),
    h("p", { class: "text" }, "The notebook editor has more keys of its own, such as running a cell or adding one: ",
      h("a", { href: "#", class: "learn-more", onclick: (e) => {
        e.preventDefault();
        attempt(() => invoke("open_doc", { page: "notebooks.md#editing" }), "Could not open the guide");
      } }, "see the notebook guide"), "."),
    h("p", { class: "text" }, learnMore("keyboard-shortcuts", "Keyboard shortcuts in the user guide")));
  showDialog("Keyboard shortcuts", body, [btn("Close", null, closeDialog, "tool primary")]);
}

// ---------- command bar (Ctrl+K) ----------

const palette = { notebooks: null, selected: 0, opener: null };

// Everything the bar can find for this text: commands, then notebooks, workspaces and entities.
function paletteItems(text) {
  const q = text.trim().toLowerCase();
  const hit = (...parts) => !q || parts.join(" ").toLowerCase().includes(q);
  const items = [];
  for (const f of railFeatures().concat(railFeatures(true))) {
    if (hit("go to", f.rail.label)) items.push({ group: "Pages", label: f.rail.label, icon: f.rail.icon, run: () => go(Object.keys(f.views)[0]) });
  }
  for (const c of features.commands) {
    if (c.preview && !previewShown()) continue;
    if (c.when && !c.when()) continue;
    if (hit(c.label, c.keywords || "")) items.push({ group: "Actions", label: c.label, icon: c.icon || "flash", run: c.run });
  }
  if (q) {
    for (const n of (palette.notebooks || []).filter((n) => hit(n.name)).slice(0, 6)) {
      items.push({ group: "Notebooks", label: n.name, icon: "notebook", run: () => openNotebook(n.name) });
    }
    for (const ws of (state.workspaces || []).filter((w) => hit(w.displayName)).slice(0, 6)) {
      items.push({ group: "Workspaces", label: ws.displayName, icon: "workspace", run: () => openWorkspace(ws) });
    }
    for (const e of (state.meta?.project?.entities || []).filter((e) => hit(e.name)).slice(0, 6)) {
      items.push({ group: "Entities", label: e.name, icon: "table", run: () => openEntity(e.name) });
    }
    items.push({ group: "Filter", label: `Show only items matching "${text.trim()}" on this page`, icon: "dataFunnel",
      run: () => { state.query = text.trim(); syncSearchBox(); render(); } });
  }
  return items;
}

// A modal dialog: focus moves in, Tab stays in, Escape closes it and focus goes back to where it was.
function openPalette(initial = "") {
  // Not over another dialog: Ctrl+K there would leave two modals, with focus trapped in the one behind.
  if (document.getElementById("dialog-root")?.childElementCount) return;
  const opener = document.getElementById("palette") ? palette.opener : document.activeElement;
  palette.opener = opener;
  closeMenu();
  const input = h("input", { type: "text", value: initial, placeholder: "Search pages, actions, notebooks, workspaces and entities",
    "aria-label": "Command bar" });
  const list = h("div", { class: "palette-list", role: "listbox", "aria-label": "Results" });
  const close = () => {
    const open = document.getElementById("palette");
    if (!open) return;
    open.remove();
    palette.opener = null;
    if (opener?.isConnected && typeof opener.focus === "function") opener.focus();
  };
  const draw = () => {
    const items = paletteItems(input.value);
    palette.selected = Math.min(palette.selected, Math.max(0, items.length - 1));
    let group = null;
    list.replaceChildren(...(items.length ? items.flatMap((it, i) => {
      const head = it.group !== group ? h("div", { class: "palette-group" }, (group = it.group)) : null;
      const row = h("button", { class: `palette-item${i === palette.selected ? " on" : ""}`, role: "option",
        onmousemove: () => { if (palette.selected !== i) { palette.selected = i; draw(); } },
        onclick: () => { close(); it.run(); } }, icon(it.icon, 16), it.label);
      return head ? [head, row] : [row];  // replaceChildren would print a null as the text "null"
    }) : [h("div", { class: "palette-empty" }, "Nothing matches. Try fewer letters.")]));
    list.querySelector(".on")?.scrollIntoView({ block: "nearest" });
    return items;
  };
  input.addEventListener("input", () => { palette.selected = 0; draw(); });
  input.addEventListener("keydown", (e) => {
    const items = paletteItems(input.value);
    if (e.key === "ArrowDown") { e.preventDefault(); palette.selected = Math.min(items.length - 1, palette.selected + 1); draw(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); palette.selected = Math.max(0, palette.selected - 1); draw(); }
    else if (e.key === "Enter" && items[palette.selected]) { e.preventDefault(); close(); items[palette.selected].run(); }
  });
  document.getElementById("palette")?.remove();  // reopening keeps the first opener
  const box = h("div", { id: "palette", class: "palette-overlay", onmousedown: (e) => { if (e.target === box) close(); },
    onkeydown: (e) => {
      if (e.key === "Tab") trapFocus(e, box);
      // One Escape closes one thing, not the activity tray behind it too.
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
    } },
    h("div", { class: "palette", role: "dialog", "aria-modal": "true", "aria-label": "Command bar" },
      h("label", { class: "palette-input" }, icon("search", 18), input, h("kbd", {}, "Esc")), list));
  document.body.append(box);
  palette.selected = 0;
  draw();
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
  // Notebook names come from the engine; the list redraws when they arrive.
  rpc("list_notebooks").then((n) => { palette.notebooks = n || []; if (box.isConnected) draw(); }).catch(() => {});
}

// The top bar's box shows the page filter, if any, and opens the command bar.
function syncSearchBox() {
  const box = document.getElementById("search");
  if (!box) return;
  box.value = state.query || "";
  document.querySelector(".search")?.classList.toggle("filtering", !!state.query);
}

// Back and Forward (UX-008; app.js keeps the history), also on Alt+Left and Alt+Right.
registerFeature({
  id: "navigation",
  commands: [
    { label: "Go back", icon: "history", keywords: "back previous page history alt+left", run: () => navBack(), when: () => navCan(-1) },
    { label: "Go forward", icon: "chevronRight", keywords: "forward next page history alt+right", run: () => navForward(), when: () => navCan(1) },
  ],
});
