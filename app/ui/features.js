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
  fabricEdit: "Edit Fabric notebooks on this PC",
  wizard: "New notebook wizard",
  deps: "Cell dependencies",
  debug: "Breakpoints, variable history and watches",
  replay: "Failure replay bundles",
  cost: "Cost",
  capacity: "Capacity status",
  usage: "Measured usage import",
  shortcuts: "OneLake shortcuts",
  access: "OneLake access",
  history: "Table history and restore",
  health: "Table health",
  model: "Semantic model",
  tests: "Tests",
  xray: "Estate X-ray",
  optimize: "Optimize",
  itemPreview: "Item preview",
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
  replay: "Quarantine replay",
  risk: "Risk check before a run",
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
const PREVIEW_ALWAYS_SHOWN = ["promote", "syncKeys", "varlib", "replay", "risk"];

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

// "How this works": the panel beside a page or tab that says what it reads, what it changes and where
// its results go, then links to its section of the guide. It folds to its title, remembered on this
// PC. Every new page or tab gets one (docs/ui-guidelines.md); withHowItWorks puts it on the right,
// or under the content in a narrow window.
function howItWorks(id, { reads, changes, results, more = [], anchor }) {
  const part = (title, text) => (text ? h("div", { class: "how-part" }, h("b", {}, title), h("p", {}, text)) : null);
  return foldable(`how.${id}`, h("h2", {}, "How this works"),
    h("div", { class: "how-body" }, part("What it reads", reads), part("What it changes", changes),
      part("Where results go", results), ...more.map(([title, text]) => part(title, text)), anchor ? learnMore(anchor) : null),
    { cls: "card pad how-it-works" });
}

function withHowItWorks(main, panel) {
  return h("div", { class: "with-how" }, h("div", { class: "with-how-main" }, main), panel);
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
  const list = h("div", { class: "palette-list", role: "listbox" });
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
