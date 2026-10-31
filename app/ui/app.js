// Fabric Desktop UI. Every data action is one call to the Python sidecar
// through the shell; all compute runs on this PC.
const invoke = (cmd, args) => window.__TAURI__.core.invoke(cmd, args);
const rpc = (method, params = {}) => invoke("rpc", { method, params });

const state = {
  view: "home",
  user: null,
  python: "",
  query: "",
  workspaces: null,          // [{id, displayName}] once signed in
  openWorkspace: null,       // {id, displayName} shown in the rail
  lakehouseView: null,       // {workspace_id, workspace_name, lakehouse_id, name}
  notebook: null,            // {name, cells, lakehouse, dirty}
  selected: null,            // selected cell id
  ribbonTab: "home",
  explorerTab: "data",
  showExplorer: true,
  tree: {},                  // explorer cache: key -> {open, items, loading}
};

// Each notebook has a session of its own (kernel_sessions.py), so the kernel's state is the open
// notebook's: idle | busy | stopped. Inside the engine every notebook shares one kernel, so a
// cell running in any notebook makes them all busy.
Object.defineProperty(state, "kernel", {
  get() { return kernelBusy(state.notebook) ? "busy" : state.notebook?.kernelStatus || "idle"; },
  set(status) { if (state.notebook) state.notebook.kernelStatus = status; },
});

// Notebooks opened since the app started, by name: a notebook still running in its session keeps
// running when another is opened, and opening it again shows its live output. Others reopen
// from disk with their last outputs.
const openNotebooks = new Map();
const KEPT_NOTEBOOKS = 10;

let nextCellId = 1;
let saveTimer = null;

// ---------- small DOM helpers ----------

function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "style") node.style.cssText = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  // A clickable row or card ("click" class) is reached with Tab like a button; Enter or Space opens it (UX-012).
  if (attrs?.onclick && node.classList.contains("click") && !node.hasAttribute("tabindex")) node.tabIndex = 0;
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function btn(label, iconName, onClick, cls = "tool", attrs = {}) {
  return h("button", { class: cls, onclick: onClick, ...attrs }, iconName ? icon(iconName, 16) : null, label);
}

// A folder box with Browse… beside it (#971): the Windows folder dialog (pick_folder in the shell)
// fills the box, which stays editable for pasting. A box policy locks gets no button. `name` says
// which folder, for a page with more than one box. The box needs its own aria-label: inside a
// <label>, its name would otherwise take in the button's.
function folderField(input, name = "a folder") {
  if (input.readOnly || !window.__TAURI__?.core) return input;
  const browse = btn("Browse…", "folderOpen", async () => {
    browse.disabled = true; // one dialog at a time
    const chosen = await invoke("pick_folder", { start: input.value.trim() || null })
      .catch((e) => { toast(friendlyError(e).text, true); return null; })
      .finally(() => { browse.disabled = false; });
    if (!chosen) return;
    input.value = chosen;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
  }, "tool outline", { type: "button", "aria-label": `Browse for ${name}` });
  return h("div", { class: "folder-field" }, input, browse);
}

function hydrateIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((el) => {
    el.replaceWith(icon(el.dataset.icon, Number(el.dataset.size || 20)));
  });
}

// A short message at the bottom of the window. Errors, and messages marked log, also go to the
// activity tray, which keeps them; quiet ones (the tray already has them) don't. opts.action
// ({label, run}) adds a button, such as Retry. Errors are announced at once (role=alert), and the
// toast stays while the pointer or keyboard focus is on it (UX-011).
function toast(text, isError = false, opts = {}) {
  const t = document.getElementById("toast");
  t.textContent = opts.details ? `${text}\nDetails are in Activity (the bell).` : text;
  if (opts.action) {
    t.append(" ", btn(opts.action.label, null, () => { clearTimeout(toast.timer); t.hidden = true; opts.action.run(); },
      "tool small toast-action"));
  }
  if ((isError || opts.log) && !opts.quiet) {
    const title = opts.title || (isError ? "Something didn't work" : text);
    const body = title === text ? "" : opts.title && text.startsWith(`${opts.title}: `) ? text.slice(opts.title.length + 2) : text;
    logActivity({ status: isError ? "error" : "ok", title, text: body, details: opts.details || "" });
  }
  // On Monitor the bottom of the window is Fabric's native view, which would cover the toast.
  t.className = `toast${isError ? " error" : ""}${state.view === "monitor" ? " top" : ""}`;
  t.setAttribute("role", isError ? "alert" : "status");
  t.hidden = false;
  toast.wait = isError ? 8000 : 4000;
  if (!toast.wired) {
    toast.wired = true;
    const hold = () => { toast.held = true; clearTimeout(toast.timer); };
    const release = () => {
      if (t.matches(":hover") || t.contains(document.activeElement)) return;
      toast.held = false;
      toast.later();
    };
    t.addEventListener("mouseenter", hold);
    t.addEventListener("focusin", hold);
    t.addEventListener("mouseleave", release);
    t.addEventListener("focusout", () => setTimeout(release));
  }
  toast.later = () => { clearTimeout(toast.timer); toast.timer = setTimeout(() => { t.hidden = true; }, toast.wait); };
  if (!toast.held) toast.later(); else clearTimeout(toast.timer);
}

// Runs fn and shows a failure as an error toast titled label. With retry (true, or another
// function to run instead), the toast has a Retry button. Pass it only for a read or an action that
// is safe to repeat; Retry runs it again with no further question. What the caller does with the
// result doesn't run after a Retry, so pass a function that does both when that matters.
async function attempt(fn, label, opts = {}) {
  try {
    return await fn();
  } catch (err) {
    const f = friendlyError(err);
    if (f.cancelled) return undefined;  // a cancelled sign-in isn't a failure; every other error is shown
    const again = opts.retry === true ? fn : opts.retry;
    toast(label && !f.text.startsWith(label) ? `${label}: ${f.text}` : f.text, true, {
      title: label, details: f.details,
      action: typeof again === "function" ? { label: "Retry", run: () => attempt(again, label, opts) } : null,
    });
    return undefined;
  }
}

// A click whose handler throws (or whose promise fails) outside attempt() would otherwise do
// nothing at all on screen: say so, with the error in Activity.
function reportUncaught(err) {
  const f = friendlyError(err);
  if (f.cancelled) return;
  toast(`Something didn't work: ${f.text}`, true, { details: f.details || (err?.stack ?? "") });
}
// Only thrown errors: an event without one is browser noise, such as WebView2's "ResizeObserver loop
// completed with undelivered notifications", or a resource that didn't load.
window.addEventListener("error", (e) => { if (e.error) reportUncaught(e.error); });
window.addEventListener("unhandledrejection", (e) => reportUncaught(e.reason));

function matches(text) {
  return !state.query || String(text).toLowerCase().includes(state.query.toLowerCase());
}

// ---------- dialogs and menus ----------

// The open dialog's onClose: closeDialog, or a new dialog replacing it, runs it once (UI-4).
let dialogOnClose = null;

function closeDialog() {
  const onClose = dialogOnClose;
  dialogOnClose = null;
  const root = document.getElementById("dialog-root");
  motionLeave(root.querySelector(":scope > .overlay"));
  root.replaceChildren();
  onClose?.();
}

// Keyboard use of dialogs: focus starts inside, Tab stays inside, Escape closes one that a click
// outside would close, and focus goes back to whatever opened it.
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
  'textarea:not([disabled]), [tabindex]:not([tabindex="-1"]), [contenteditable="true"]';
const dialogFocus = { opener: null, lastOutside: null };
document.addEventListener("focusin", (e) => {
  if (!document.getElementById("dialog-root")?.contains(e.target)) dialogFocus.lastOutside = e.target;
});

function trapFocus(e, root) {
  const items = [...root.querySelectorAll(FOCUSABLE)].filter((el) => el.offsetParent !== null || el === document.activeElement);
  if (!items.length) { e.preventDefault(); return; }
  const first = items[0], last = items[items.length - 1];
  if (!root.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
  else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}

document.addEventListener("keydown", (e) => {
  const root = document.getElementById("dialog-root");
  if (!root?.childElementCount) return;
  if (isShortcut(e, "focus")) trapFocus(e, root);
  else if (isShortcut(e, "close") && root.querySelector(".overlay[data-dismiss]")) {
    closeDialog();
    e.stopImmediatePropagation();  // one Escape closes one thing, not the activity tray behind it too
  }
});

new MutationObserver(() => {
  const root = document.getElementById("dialog-root");
  if (root.childElementCount) {
    // showDialog may already have focused a field, so remember where focus was before it.
    if (!dialogFocus.opener) dialogFocus.opener = dialogFocus.lastOutside;
    // Start inside the dialog unless the code that opened it already chose a field.
    if (!root.contains(document.activeElement)) root.querySelector(FOCUSABLE)?.focus();
  } else {
    const opener = dialogFocus.opener;
    dialogFocus.opener = null;
    if (opener?.isConnected && typeof opener.focus === "function") opener.focus();
  }
}).observe(document.getElementById("dialog-root"), { childList: true });

// onClose runs once however the dialog goes: overlay click, the X, Escape, an action or a new dialog.
function showDialog(title, body, actions = [], onClose = null) {
  const overlay = h("div", { class: "overlay", "data-dismiss": true, onmousedown: (e) => { if (e.target === overlay) closeDialog(); } },
    h("div", { class: "dialog", role: "dialog", "aria-modal": "true", "aria-label": title },
      h("div", { class: "dialog-head" }, title,
        h("button", { class: "tool small", "aria-label": "Close", onclick: closeDialog }, icon("dismiss", 16))),
      body,
      actions.length ? h("div", { class: "dialog-actions" }, actions) : null));
  const previous = dialogOnClose;
  dialogOnClose = onClose;
  document.getElementById("dialog-root").replaceChildren(overlay);
  // A dialog's How this works says what its button changes, so it opens unfolded.
  overlay.querySelectorAll(".how-it-works.folded").forEach((p) => {
    p.classList.remove("folded");
    const toggle = p.querySelector(":scope > .fold-head > .fold-toggle");
    toggle?.setAttribute("aria-expanded", "true");
    if (toggle) toggle.title = "Hide";
  });
  previous?.();
  overlay.querySelector("input, select")?.focus();
  return overlay;
}

// check(value) returns why the value can't be used, or "": the dialog says so and stays open.
function askText(title, label, initial = "", okLabel = "OK", check = null) {
  return new Promise((resolve) => {
    const input = h("input", { value: initial });
    const problem = h("p", { class: "error-text", style: "font-size:13px;margin-top:8px", hidden: true });
    const done = (value) => { resolve(value); closeDialog(); };
    const submit = () => {
      const value = input.value.trim();
      if (!value) return;
      const why = check ? check(value) : "";
      if (why) { problem.textContent = why; problem.hidden = false; input.focus(); return; }
      done(value);
    };
    input.addEventListener("input", () => { problem.hidden = true; });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") submit();
      if (e.key === "Escape") done(null);
    });
    showDialog(title, h("div", {}, h("label", { class: "field" }, label, input), problem), [
      btn("Cancel", null, () => done(null), "tool outline"),
      btn(okLabel, null, submit, "tool primary"),
    ], () => resolve(null));
    input.select();
  });
}

function showMenu(anchor, items) {
  closeMenu();
  const rect = anchor.getBoundingClientRect();
  const menu = h("div", { class: "menu", id: "menu", role: "menu", style: `left:${rect.left}px;top:${rect.bottom + 4}px` },
    items.map((it) => it instanceof Node ? menuNode(it) : it.info ? h("div", { class: "info", role: "presentation" }, it.info)
      : btn(it.label, it.icon, () => { closeMenu(); it.action(); }, "tool", { role: "menuitem", tabindex: "-1" })));
  menuOpener = anchor;
  anchor.setAttribute?.("aria-expanded", "true");
  document.body.append(menu);
  // A button near the right edge (Publish options): keep the menu on screen.
  const overflow = menu.getBoundingClientRect().right - (window.innerWidth - 8);
  if (overflow > 0) menu.style.left = `${Math.max(8, rect.left - overflow)}px`;
  menu.querySelector("[role=menuitem], [role=menuitemcheckbox]")?.focus();
  setTimeout(() => document.addEventListener("mousedown", closeMenuOutside), 0);
}

let menuOpener = null;

// A ribbon button moved into a menu as it is (its Preview tag, on state and title kept): the menu
// closes before its own click runs, so a button that opens a menu of its own can.
function menuNode(node) {
  node.setAttribute("role", node.getAttribute("aria-pressed") ? "menuitemcheckbox" : "menuitem");
  if (node.hasAttribute("aria-pressed")) node.setAttribute("aria-checked", node.getAttribute("aria-pressed"));
  node.setAttribute("tabindex", "-1");
  node.addEventListener("click", () => {
    // Focus goes back to the menu's button. The item may draw the page again, so find the new button by
    // its data-menu-key; anything the item opened that took focus (a dialog, a panel's field) keeps it.
    const key = menuOpener?.dataset?.menuKey;
    closeMenu(true);
    // The page may be drawn again a little later (a panel loading), so keep it up for a moment.
    if (key) {
      const until = performance.now() + 1000;
      const keep = () => {
        if (!document.activeElement || document.activeElement === document.body) document.querySelector(`[data-menu-key="${CSS.escape(key)}"]`)?.focus();
        else if (!document.activeElement.matches(`[data-menu-key="${CSS.escape(key)}"]`)) return;  // something else took focus
        if (performance.now() < until) requestAnimationFrame(keep);
      };
      requestAnimationFrame(keep);
    }
  }, true);
  return node;
}

function closeMenuOutside(e) {
  if (!document.getElementById("menu")?.contains(e.target)) closeMenu();
}

function closeMenu(refocus = false) {
  const menu = document.getElementById("menu");
  menu?.remove();
  document.removeEventListener("mousedown", closeMenuOutside);
  menuOpener?.setAttribute?.("aria-expanded", "false");
  if (menu && refocus && menuOpener?.isConnected) menuOpener.focus();
  menuOpener = null;
}

// The open menu: arrows, Home and End move, Escape closes it and returns to its button, Tab leaves it.
// Capture phase, so Escape closes the menu before a dialog behind it sees the key.
document.addEventListener("keydown", (e) => {
  const menu = document.getElementById("menu");
  if (!menu) return;
  const items = [...menu.querySelectorAll("[role=menuitem], [role=menuitemcheckbox]")];
  const at = items.indexOf(document.activeElement);
  const move = isShortcut(e, "menu") ? { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: items.length - 1 }[e.key] : undefined;
  if (isShortcut(e, "close")) { e.preventDefault(); e.stopImmediatePropagation(); closeMenu(true); }
  else if (isShortcut(e, "focus")) closeMenu(true);  // back on its button, so Tab goes on to the next control
  else if (move !== undefined && items.length) {
    e.preventDefault();
    items[(move + items.length) % items.length].focus();
  }
}, true);

// Rows and cards with the "click" class (focusable, see h()): Enter or Space opens them.
// Tabs (role="tab" in a role="tablist"): Left, Right, Home and End move to the next tab and select it.
document.addEventListener("keydown", (e) => {
  const el = e.target;
  if (!(el instanceof Element) || e.altKey || e.ctrlKey || e.metaKey) return;
  if (isShortcut(e, "open") && el.classList.contains("click") && el === document.activeElement
      && !["BUTTON", "A", "INPUT", "SELECT", "TEXTAREA"].includes(el.tagName)) {
    e.preventDefault();
    el.click();
    return;
  }
  if (el.getAttribute("role") !== "tab") return;
  const tabs = [...(el.closest("[role=tablist]")?.querySelectorAll("[role=tab]") || [])];
  const at = tabs.indexOf(el);
  const next = isShortcut(e, "tabs") ? { ArrowRight: at + 1, ArrowLeft: at - 1, Home: 0, End: tabs.length - 1 }[e.key] : undefined;
  if (next === undefined || at < 0) return;
  e.preventDefault();
  const tab = tabs[(next + tabs.length) % tabs.length];
  const label = tab.textContent;
  const list = el.closest("[role=tablist]")?.getAttribute("aria-label");
  tab.click();
  // Selecting a tab often re-renders the tab list, sometimes after an engine call: focus the new copy
  // of the selected tab, in the same tablist when it's named (a count in the label may have changed),
  // for up to a second, while focus is still nowhere else (#961).
  const selected = () => (list
    ? [...document.querySelectorAll("[role=tablist]")].find((l) => l.getAttribute("aria-label") === list)?.querySelector("[role=tab][aria-selected=true]")
    : [...document.querySelectorAll("[role=tab]")].find((t) => t.textContent === label && t.getAttribute("aria-selected") === "true"));
  const refocus = (tries) => {
    const found = selected();
    const now = found === el ? null : found;  // a page that redraws later still shows the old selection
    const lost = [document.body, el, tab].includes(document.activeElement) || !document.activeElement?.isConnected;
    if (now && lost) now.focus();
    else if (!now && tab.isConnected && lost) tab.focus();
    if (tries > 0 && (!now || document.activeElement !== now) && lost) setTimeout(() => refocus(tries - 1), 50);
  };
  refocus(20);
});

// ---------- account and data ----------

async function signIn() {
  const result = await attempt(() => rpc("sign_in"), "Sign-in failed");
  if (!result) return false;
  state.user = result.user;
  state.tenant = result.tenant;
  renderChrome();
  await loadWorkspaces();
  return true;
}

async function ensureSignedIn() {
  return state.user ? true : signIn();
}

async function loadWorkspaces() {
  if (!(await ensureSignedIn())) return [];
  if (!state.workspaces) {
    // Listing workspaces only reads, so the error toast offers Retry; it fills the list and redraws (UX-011).
    const again = async () => { state.workspaces = await rpc("list_workspaces"); render(); };
    state.workspaces = (await attempt(() => rpc("list_workspaces"), "Could not list workspaces", { retry: again })) || null;
  }
  return state.workspaces || [];
}

function lakehouseRef(ws, lh) {
  return { workspace_id: ws.id, workspace_name: ws.displayName, lakehouse_id: lh.id, name: lh.displayName };
}

// The Warehouse page's ref (WH-01) for a Warehouse item, or for a SQL analytics endpoint: the engine
// finds an endpoint through its lakehouse, which Fabric gives the same name in the same workspace.
// null for other items, and for an endpoint whose lakehouse isn't in the list.
function warehouseRefFor(ws, it, items) {
  const base = { workspace_id: ws.id, workspace_name: ws.displayName, name: it.displayName };
  if (it.type === "Warehouse") return { ...base, kind: "warehouse", item_id: it.id };
  if (it.type !== "SQLEndpoint") return null;
  const lake = items.find((x) => x.type === "Lakehouse" && x.displayName === it.displayName);
  return lake ? { ...base, kind: "endpoint", item_id: lake.id } : null;
}

// ---------- navigation and chrome ----------

// ---------- navigation history (UX-008) ----------

// Back and Forward walk a list of {view, values}: the page, plus the one field each page keeps its
// tab in (or the item it shows). The entry you're on is kept up to date on every render, so a tab
// clicked on a page comes back with it. The last page is remembered on this PC and opened on start.
const NAV_LIMIT = 50;
const NAV_KEY = "fd.nav.last";
const nav = { stack: [], index: -1, restored: false };  // nothing is saved until the last page is read back
// The fields that make up each page's place: [object, key] pairs, read when the entry is saved and
// written back when you go Back or Forward to it. The first pair of a "tab" page is its tab.
const NAV_FIELDS = {
  metadata: () => [[state.meta, "tab"]],
  monitor: () => [[monitorPage, "panel"]],
  onelake: () => [[catalogPage, "tab"]],
  optimize: () => [[optimizePage, "tab"]],
  platform: () => [[platformPage, "tab"]],
  source: () => [[gitPage, "tab"]],
  settings: () => [[state, "settingsTab"]],
  workspace: () => [[state, "openWorkspace"]],
  lakehouse: () => [[state, "lakehouseView"], [state, "openWorkspace"]],
  entity: () => [[state.meta, "entity"]],
  workload: () => [[wlPage, "id"]],
  incident: () => [[incPage, "id"], [incPage, "env_of"]],
  agent: () => [[agPage, "name"]],
};
// Pages whose tab is remembered across a restart; the rest reopen on their rail page.
const NAV_TAB_VIEWS = ["metadata", "monitor", "onelake", "optimize", "platform", "source", "settings"];

function navFields(view) {
  try { return (NAV_FIELDS[view] || (() => []))(); } catch { return []; }
}

function navEntry(view) {
  return { view, values: navFields(view).map(([o, k]) => o?.[k]) };
}

function navSame(a, b) {
  return !!a && !!b && a.view === b.view && JSON.stringify(a.values) === JSON.stringify(b.values);
}

// The feature a view belongs to, and whether it can be shown now (registered, and not a preview
// feature while preview features are hidden).
function navFeature(view) {
  return features.list.find((f) => f.views && Object.prototype.hasOwnProperty.call(f.views, view));
}
function navShown(view) {
  const f = navFeature(view);
  return !!f && (!f.preview || previewShown());
}

// Whether an entry can be opened again: its page is shown, and what it showed is still there.
function navAvailable(entry) {
  if (!entry || !navShown(entry.view)) return false;
  if (entry.view === "notebook") return !!state.notebook;
  if (entry.view === "query") return !!sqlPage.ref;
  if (NAV_FIELDS[entry.view] && !NAV_TAB_VIEWS.includes(entry.view)) return entry.values[0] != null;
  return true;
}

// Keeps the entry you're on in step with the page as drawn, and remembers it on this PC.
function navSync() {
  const here = navEntry(state.view);
  if (nav.index < 0) { nav.stack = [here]; nav.index = 0; } else nav.stack[nav.index] = here;
  if (nav.restored) navSave(here);
}

function navSave(entry) {
  const tab = NAV_TAB_VIEWS.includes(entry.view) && typeof entry.values[0] === "string" ? entry.values[0] : null;
  try { localStorage.setItem(NAV_KEY, JSON.stringify({ view: entry.view, tab })); } catch { /* not remembered */ }
}

// Called by go() with the page about to open: the page being left is already saved by navSync.
function navPush(view) {
  if (nav.index < 0) navSync();
  const next = navEntry(view);
  if (navSame(nav.stack[nav.index], next)) return;
  nav.stack = nav.stack.slice(0, nav.index + 1);
  nav.stack.push(next);
  if (nav.stack.length > NAV_LIMIT) nav.stack.shift();
  nav.index = nav.stack.length - 1;
}

// The nearest entry Back (step -1) or Forward (step 1) opens, or -1.
function navTarget(step) {
  for (let i = nav.index + step; i >= 0 && i < nav.stack.length; i += step) {
    if (navAvailable(nav.stack[i])) return i;
  }
  return -1;
}
function navCan(step) {
  return navTarget(step) >= 0;
}

function navMove(step) {
  const i = navTarget(step);
  if (i < 0) return false;
  if (nav.index >= 0) nav.stack[nav.index] = navEntry(state.view);
  nav.index = i;
  const entry = nav.stack[i];
  navFields(entry.view).forEach(([o, k], n) => { if (o) o[k] = entry.values[n]; });
  closeMenu();
  state.view = entry.view;
  render();
  return true;
}
function navBack() { return navMove(-1); }
function navForward() { return navMove(1); }

// The page to open on start: the last one, its tab too, or its rail page when it showed an item
// that isn't open any more; Home when that page no longer exists or is hidden.
function navRestore() {
  let last = null;
  try { last = JSON.parse(localStorage.getItem(NAV_KEY) || "null"); } catch { /* start on Home */ }
  const view = navStartView(last?.view);
  if (view === last?.view && NAV_TAB_VIEWS.includes(view) && typeof last.tab === "string") {
    const [pair] = navFields(view);
    if (pair?.[0]) pair[0][pair[1]] = last.tab;
  }
  state.view = view;
  nav.stack = [navEntry(view)];
  nav.index = 0;
  nav.restored = true;
  return view;
}
function navStartView(view) {
  if (typeof view !== "string" || !navShown(view)) return "home";
  const f = navFeature(view);
  const main = Object.keys(f.views)[0];
  if (f.rail && main === view) return view;
  // An item page (a workspace, a run, a notebook) reopens on the rail page it sits under.
  const parent = features.list.find((x) => x.rail && (x.rail.also || []).includes(view));
  if (parent) return Object.keys(parent.views)[0];
  return f.rail ? main : "home";
}

// Alt+Left and Alt+Right, except while typing or with a dialog open. Keys pressed inside the embedded
// Fabric portal never reach this page (it is a separate child webview, main.rs add_child); there they
// move within Fabric's own history, so the top-bar buttons are the way out.
function navKey(e) {
  if (!isShortcut(e, "history") || e.ctrlKey || e.metaKey || e.shiftKey) return false;
  const t = e.target;
  if (t?.closest?.("input, textarea, select, [contenteditable], .cm-editor")) return false;
  if (document.getElementById("dialog-root")?.childElementCount) return false;
  e.preventDefault();
  if (e.key === "ArrowLeft") navBack(); else navForward();
  return true;
}

function renderNavButtons() {
  const back = document.getElementById("nav-back");
  const fwd = document.getElementById("nav-forward");
  if (back) back.disabled = !navCan(-1);
  if (fwd) fwd.disabled = !navCan(1);
}

function go(view) {
  // A pop-out window has no Fabric view of its own for the portal: Fabric links open in the main window.
  if (POP_OUT && view === "fabric") { popOutOpenInFabric(fabricPage.page); return; }
  closeMenu();
  navPush(view);
  state.view = view;
  render();
}

function renderChrome() {
  renderNavButtons();
  renderEnvBar();
  renderBell();
  if (typeof renderGitPill === "function") renderGitPill();  // git.js loads last
  const account = document.getElementById("account");
  account.textContent = state.user ? state.user[0].toUpperCase() : "?";
  account.classList.toggle("signed-out", !state.user);
  account.title = state.user ? `Signed in as ${state.user}` : "Sign in";
  account.setAttribute("aria-label", account.title);

  document.getElementById("rail-main").replaceChildren(...railFeatures().map(railItem));
  document.getElementById("rail-bottom").replaceChildren(...railFeatures(true).map(railItem));
  syncSearchBox();
  // Items opened recently, each with a close button.
  const open = [];
  const recent = (view, iconName, label, onOpen, onClose) => h("div", { class: "rail-open-item" },
    h("button", { class: `rail-item${state.view === view ? " active" : ""}`, "aria-current": state.view === view ? "page" : null,
      onclick: onOpen }, icon(iconName, 22), label),
    h("button", { class: "rail-close", title: `Close ${label}`, "aria-label": `Close ${label}`,
      onclick: async (e) => {
        e.stopPropagation();
        if (await onClose() === false) return;  // e.g. a notebook that couldn't be saved stays open
        if (state.view === view) go("home"); else renderChrome();
      } }, icon("dismiss", 12)));
  if (state.openWorkspace) {
    open.push(recent("workspace", "workspace", state.openWorkspace.displayName, () => openWorkspace(state.openWorkspace),
      () => { state.openWorkspace = null; }));
  }
  if (sqlPage.ref) {
    open.push(recent("query", "code", "SQL query", () => go("query"), () => { sqlPage.ref = null; }));
  }
  if (state.notebook) {
    open.push(recent("notebook", "notebook", state.notebook.name, () => go("notebook"), async () => {
      if (state.notebook?.dirty && !(await saveNotebook())) return false;  // closing never loses edits
      state.notebook = null;
    }));
  }
  document.getElementById("rail-open").replaceChildren(...open);

  const crumbs = [];
  if (state.view === "notebook" && state.notebook) {
    const lh = nbLakehouse(state.notebook);
    crumbs.push(h("span", {}, state.notebook.place ? state.notebook.place.label : lh ? lh.workspace_name || "Workspace" : "My notebooks"), h("span", { "aria-hidden": "true" }, "/"));
    crumbs.push(h("button", { class: "current", title: "Rename notebook", onclick: renameNotebook },
      state.notebook.name + (state.notebook.dirty ? " •" : "")));
  } else if (state.view === "workspace" && state.openWorkspace) {
    crumbs.push(h("button", { class: "crumb", onclick: () => go("workspaces") }, "Workspaces"), h("span", {}, "/"),
      h("span", { class: "current" }, state.openWorkspace.displayName));
  } else if (state.view === "workload" && wlPage.detail) {
    crumbs.push(h("button", { class: "crumb", onclick: () => { monitorPage.panel = "runs"; go("monitor"); } }, "Monitor"), h("span", {}, "/"),
      h("button", { class: "crumb", onclick: backToWorkloads }, "Workloads"), h("span", {}, "/"),
      h("span", { class: "current" }, wlPage.detail.name));
  } else if (state.view === "incident" && incPage.detail) {
    crumbs.push(h("button", { class: "crumb", onclick: () => { monitorPage.panel = "runs"; go("monitor"); } }, "Monitor"), h("span", {}, "/"),
      h("button", { class: "crumb", onclick: backToIncidents }, "Incidents"), h("span", {}, "/"),
      h("span", { class: "current" }, incPage.detail.title));
  } else if (state.view === "agent" && agPage.detail) {
    crumbs.push(h("button", { class: "crumb", onclick: () => { monitorPage.panel = "runs"; go("monitor"); } }, "Monitor"), h("span", {}, "/"),
      h("button", { class: "crumb", onclick: backToAgents }, "Agents"), h("span", {}, "/"),
      h("span", { class: "current" }, agPage.detail.agent));
  } else if (state.view === "query" && isWarehouseRef(sqlPage.ref)) {
    const wh = sqlPage.ref;
    crumbs.push(h("button", { class: "crumb", onclick: () => openWorkspace({ id: wh.workspace_id, displayName: wh.workspace_name }) },
      wh.workspace_name || "Workspace"), h("span", {}, "/"), h("span", {}, wh.name), h("span", {}, "/"),
    h("span", { class: "current" }, "SQL query"));
  } else if (state.view === "query" && sqlPage.ref) {
    const lh = sqlPage.ref;
    crumbs.push(h("span", {}, lh.workspace_name || "Workspace"), h("span", {}, "/"),
      h("button", { class: "crumb", onclick: () => openLakehouse(lh) }, lh.name), h("span", {}, "/"),
      h("span", { class: "current" }, "SQL query"));
  } else if (state.view === "lakehouse" && state.lakehouseView) {
    const lh = state.lakehouseView;
    crumbs.push(h("button", { class: "crumb", onclick: () => openWorkspace({ id: lh.workspace_id, displayName: lh.workspace_name }) },
      lh.workspace_name), h("span", {}, "/"), h("span", { class: "current" }, lh.name));
  }
  document.getElementById("breadcrumb").replaceChildren(...crumbs);
}

function render() {
  navSync();
  renderChrome();
  if (state.view !== fabricView.owner) placeFabricView();
  (features.views[state.view] || renderHome)();
}

function setView(...children) {
  document.body.classList.remove("fabric-full");  // only the Fabric portal tab fills the window
  const view = document.getElementById("view");
  view.replaceChildren(...children);
  viewTransition(view);  // motion.js: only when the page itself changed
}

// The shared look for a part of a page that is still being read (UX-020): a small spinner and the
// label, announced once to screen readers. cls adds classes a page already lays out with; live: false
// leaves out role="status" where one line elsewhere already speaks for the whole page.
function loading(label = "Loading...", cls = "", { live = true } = {}) {
  return h("div", { class: `loading muted-text${cls ? ` ${cls}` : ""}`, role: live ? "status" : null },
    h("span", { class: "spinner", "aria-hidden": "true" }), label);
}

// The shared look for a list with nothing in it yet (UX-020): an optional icon, a title, a line
// saying what goes here, and an optional button (or several) to add the first one.
function emptyState({ icon: name, title, text, action } = {}) {
  return h("div", { class: "empty" }, name ? icon(name, 32) : null,
    title ? h("span", { class: "title" }, title) : null,
    text ? h("span", { class: "text" }, text) : null, action);
}

function signInCallout() {
  if (state.user) return null;
  return h("div", { class: "callout" }, icon("person", 20),
    h("span", {}, "Sign in to see your Fabric workspaces and lakehouses. Notebooks run on this PC either way."),
    h("span", { class: "spacer" }), btn("Sign in", null, async () => { if (await signIn()) render(); }, "tool primary"));
}

// ---------- Home, Create, Monitor ----------

// A section that folds to its header, remembered on this PC. head is what stays visible.
// start: whether it opens folded the first time; after that it opens as it was left on this PC.
function foldable(id, head, body, { cls = "card pad", always = null, start = false } = {}) {
  let folded = start;
  try {
    const saved = localStorage.getItem(`fd.fold.${id}`);
    if (saved === "1" || saved === "0") folded = saved === "1";
  } catch { /* as it starts */ }
  const box = h("section", { class: `${cls} foldable${folded ? " folded" : ""}` });
  const toggle = h("button", { class: "fold-toggle", "aria-expanded": String(!folded), title: folded ? "Show" : "Hide",
    onclick: () => {
      folded = !box.classList.contains("folded");
      try { localStorage.setItem(`fd.fold.${id}`, folded ? "1" : "0"); } catch { /* this session only */ }
      box.classList.toggle("folded", folded);
      toggle.setAttribute("aria-expanded", String(!folded));
      toggle.title = folded ? "Show" : "Hide";
    } }, h("span", { class: "chev", "aria-hidden": "true" }, "▾"), head);
  box.append(h("div", { class: "fold-head" }, toggle), ...(always ? [always] : []), h("div", { class: "fold-body" }, body));
  return box;
}

// Coming back to Home within a few minutes shows the page as it was, with no calls (#192), once it
// had finished loading; a sign-in, a search or a run starting or finishing draws it again, as does Refresh.
const HOME_REUSE_MS = 5 * 60 * 1000;
const homeCache = { page: null, key: "", at: 0, stamp: null, done: false };
function homeKey() {
  return JSON.stringify([state.user, state.query, previewShown(), state.meta?.project?.path, activity.items.filter((i) => i.status === "running" && !i.stale).map((i) => i.id)]);
}
function homeUpdatedText() {
  const min = Math.floor((Date.now() - homeCache.at) / 60000);
  return min < 1 ? "Updated just now" : `Updated ${min} min ago`;
}

// Home while the component and update check runs (UX-020): drawn at once, with placeholders where
// the engine's answers go. It calls nothing, since nothing may start the sidecar until the check is
// done; the real Home replaces it when the check returns. Nothing here is kept in homeCache.
function startingHome() {
  // One line speaks for the page; the other placeholders only show, so a screen reader says it once.
  const quiet = (label) => loading(label, "", { live: false });
  const later = (label, iconName) => btn(label, iconName, null, "tool outline",
    { disabled: true, title: "Ready once the local engine has started" });
  return h("div", { class: "page home", "data-starting": "" },
    h("div", { class: "row home-head" }, h("h1", { class: "grow" }, "Welcome to KratosForge")),
    loading("Starting the local engine...", "sub"),
    h("div", { class: "home-two" },
      h("section", { class: "card pad" }, h("h2", {}, "Running now"), quiet()),
      h("section", { class: "card pad" }, h("h2", {}, "Coming up"), quiet())),
    h("section", { class: "row wrap home-start" }, h("h2", {}, "Start something"),
      later("New notebook", "notebook"), later("New lakehouse", "lakehouse"), later("OneLake catalog", "onelake")),
    h("section", { class: "card pad" }, h("h2", {}, "Recent notebooks"), quiet("Loading notebooks...")));
}

// Home answers "what happened since I was last here": problems first, then what is running, what
// runs next and each environment's state, then ways to start something. Each part draws as soon as
// its own data is in, quickest first; the Estate X-ray reads only after all of them.
async function renderHome() {
  if (state.starting) { setView(startingHome()); return; }
  if (homeCache.done && homeCache.key === homeKey() && Date.now() - homeCache.at < HOME_REUSE_MS) {
    homeCache.stamp.textContent = homeUpdatedText();
    setView(homeCache.page);
    return;
  }
  const name = state.user ? state.user.split("@")[0] : "";
  const summary = h("p", { class: "sub" }, "Checking what ran...");
  const attention = h("div", { class: "home-attention" });
  const running = loading();
  const upcoming = loading();
  const envCards = h("div", { class: "home-envs" });
  const list = loading("Loading notebooks...");
  const xray = previewShown() ? xrayCard() : null;
  const yours = yourWorkloadsCard();
  const envSection = h("section", {}, h("h2", {}, "Environments"), envCards);
  let notebookCount = 0;
  const stamp = h("span", { class: "muted-text small" }, "Updated just now");
  // Continue opens the notebook changed last; it appears once the list is read and there is one.
  const resume = h("span", { class: "home-continue" });
  const start = h("section", { class: "row wrap home-start" }, h("h2", {}, "Start something"), resume,
    btn("New notebook", "notebook", newNotebook, "tool outline"),
    btn("Import from Fabric or Databricks", "arrowUpload", nbfImportDialog, "tool outline"),
    btn("New lakehouse", "lakehouse", () => newLakehouse(), "tool outline"),
    btn("OneLake catalog", "onelake", () => go("onelake"), "tool outline"));
  const recent = foldable("home.notebooks", h("h2", {}, "Recent notebooks"), h("div", {}, list,
    btn("All notebooks", "notebook", () => go("notebooks"), "tool link small")));
  const ready = readinessCard();
  const started = gettingStarted();
  const page = h("div", { class: "page home" },
    h("div", { class: "row home-head" },
      h("h1", { class: "grow" }, name ? `Welcome back, ${name}` : "Welcome to KratosForge"),
      stamp, btn("Refresh", null, () => { homeCache.done = false; render(); }, "tool link small"), learnMore("home")),
    summary,
    // What people come to Home for first (AF-2, docs/ux/action-first.md): pick up the last notebook or
    // start something; then readiness, problems and what is running.
    start,
    started.el,
    homePinnedCard(),
    recent,
    ready,
    ready ? null : signInCallout(),  // the readiness card asks to sign in; only one place asks
    attention,
    h("div", { class: "home-attention", id: "home-fresh" }),
    yours,
    h("div", { class: "home-two" },
      h("section", { class: "card pad" }, h("h2", {}, "Running now"), running),
      h("section", { class: "card pad" }, h("h2", {}, "Coming up"), upcoming)),
    meshMapCard(),
    envSection,
    xray);
  Object.assign(homeCache, { page, key: homeKey(), at: Date.now(), stamp, done: false });
  setView(page);
  const onHome = () => state.view === "home" && homeCache.page === page;

  // What is running is already known in the app: no call needed.
  const live = activity.items.filter((i) => i.status === "running" && !i.stale);
  running.replaceWith(live.length ? h("div", { class: "home-list" }, live.map((i) => h("div", { class: "home-item" },
    h("div", { class: "row" }, h("b", { class: "grow" }, i.title), h("span", { class: "muted-text small" }, `started ${relTime(i.at)}`)),
    remoteActivity(i) ? remoteProgress(i.title) : h("div", { class: "progress" }, h("div", { class: "bar indeterminate" })),
    i.text ? h("div", { class: "muted-text small" }, i.text) : null)))
    : h("p", { class: "muted-text" }, "Nothing is running."));

  // The sidecar answers one call at a time, so the quick local ones go first.
  const notebooksDone = rpc("list_notebooks").catch(() => null).then((notebooks) => {
    if (!onHome()) return;
    notebookCount = (notebooks || []).length;
    const last = (notebooks || []).reduce((a, n) => (!a || n.modified > a.modified ? n : a), null);
    if (last) resume.replaceWith(btn(`Continue: ${last.name}`, "notebook", () => openNotebook(last.name), "tool primary"));
    const shown = (notebooks || []).filter((n) => matches(n.name)).slice(0, 8);
    // Each notebook's workspace, lakehouse, Runs on and last run, and Run several (home-notebooks.js).
    const recentList = shown.length ? homeRecentList(shown) : null;
    list.replaceWith(recentList ? recentList.el : h("p", { class: "muted-text" },
      notebooks === null ? "Couldn't list notebooks." : state.query ? "No notebooks match your search." : "No notebooks yet. Create one to get started."));
    return recentList?.done;
  });
  const projectCall = rpc("meta_project").catch(() => null);
  const cloudCall = rpc("fabric_runs", { limit: 50, refresh: false }).catch(() => null);
  const pcCall = rpc("pc_schedules_for", { kind: "all" }).catch(() => []);
  const metaCall = rpc("meta_runs_all", { limit: 300 }).catch(() => ({ runs: [], unavailable: {}, failed: true }));

  // Coming up: this PC's schedules straight away, Fabric's added once read (only when signed in).
  const upcomingDone = (async () => {
    const [project, pcList] = await Promise.all([projectCall, pcCall]);
    if (!onHome()) return;
    const draw = (lists, fabricEnvs) => {
      const items = [
        ...(pcList || []).filter((sc) => sc.enabled && sc.next_run).sort((a, b) => a.next_run.localeCompare(b.next_run))
          .map((sc) => ({ what: sc.label, where: "This PC", text: nextRunText(sc.next_run) })),
        ...lists.flatMap((res, i) => (res?.schedules || []).filter((sc) => sc.enabled)
          .map((sc) => ({ what: `Run all · ${ENV_LABEL[fabricEnvs[i]]}`, where: "Fabric", text: sc.text }))),
      ].slice(0, 6);
      const next = h("div", {}, items.length ? h("div", { class: "home-list" }, items.map((x) => h("div", { class: "home-item row" },
        h("span", { class: "grow" }, x.what, h("span", { class: "muted-text small" }, ` · ${x.where}`)), h("span", { class: "muted-text small" }, x.text))))
        : h("p", { class: "muted-text" }, "Nothing scheduled. Use Schedule on a notebook or on Data mesh, on this PC or in Fabric."),
        // Opens the page with the schedules listed here: Fabric > Schedules when every one of them is in Fabric.
        btn("All schedules", "history", () => {
          monitorPage.panel = items.length && items.every((x) => x.where === "Fabric") ? "fabric_schedules" : "schedules"; go("monitor");
        }, "tool link small"));
      upcoming.replaceWith(next);
      return next;
    };
    let shown = draw([], []);
    const fabricEnvs = project?.path && state.user ? Object.keys(ENV_LABEL).filter((env) => envWhere(project, env).fabric) : [];
    if (!fabricEnvs.length) return;
    await metaCall;  // Fabric's schedules come after the run history, which fills more of the page
    const lists = await Promise.all(fabricEnvs.map((env) => rpc("meta_schedules", { env }).catch(() => null)));
    if (!onHome()) return;
    const next = draw(lists, fabricEnvs);
    shown.replaceWith(next);
  })();

  const runsDone = (async () => {
    const [meta, cloud, project] = await Promise.all([metaCall, cloudCall, projectCall]);
    if (!onHome()) return;
    if (project) state.meta.project = project;
    const hasProject = !!project?.path;
    // Environments mean nothing before there's a metadata project; Getting started says where to begin.
    if (!hasProject) envSection.remove();
    // A notebook list that failed to draw (allSettled below) still ticks what it can, with no stray rejection.
    notebooksDone.catch(() => {}).then(() => onHome() && started.mark(project, notebookCount));

    // The latest run of each table in each environment says how that table stands now.
    const latest = new Map();
    for (const r of meta.runs || []) {
      const key = `${r.env}:${r.entity}`;
      if (!latest.has(key)) latest.set(key, r);
    }
    const failing = [...latest.values()].filter((r) => r.status === "failed" || r.status === "needs_decision");
    const cloudFailed = (cloud || []).filter((r) => r.status === "Failed" && Date.now() - new Date(r.started_at) < 86400000);

    const problems = failing.length + cloudFailed.length;
    // What couldn't be read is unknown, not fine: say so instead of "Nothing needs your attention".
    const unread = meta.failed ? ["run history"] : Object.keys(meta.unavailable || {}).map((env) => ENV_LABEL[env]);
    if (meta.project_error) unread.unshift("the metadata project");
    if (cloud === null) unread.push("Fabric jobs");
    // Late tables (dataops.js) count too; they may arrive after the runs, so the line is redrawn then.
    state.homeSummary = () => {
      const late = (fresh.items || []).filter((x) => x.status === "late").length;
      const total = problems + late;
      summary.textContent = [
        total ? `${total} ${total === 1 ? "thing needs" : "things need"} your attention`
          // Signed out, Fabric wasn't checked at all: say how to change that, not that all is well.
          : !state.user ? "Sign in so KratosForge can check Fabric. Notebooks run on this PC either way"
          : unread.length ? "Nothing found that needs your attention" : "Nothing needs your attention",
        live.length ? `${live.length} running` : "",
        unread.length && state.user ? `couldn't check ${unread.join(", ")}` : "",
      ].filter(Boolean).join(" · ") + ".";
    };
    state.homeSummary();
    renderFreshHome();

    attention.replaceChildren(...(problems ? [h("h2", {}, "Needs your attention"),
      ...failing.slice(0, 5).map((r) => h("div", { class: "card attention" },
        statusPill(r.status),
        h("div", { class: "grow" },
          h("div", {}, h("b", { class: "mono" }, r.entity), ` in ${ENV_LABEL[r.env]} · ${ranOn(r)}`),
          h("div", { class: "reason" }, runReason(r) || "Did not finish"),
          h("div", { class: "muted-text small" }, when(r.started_at))),
        btn("Open entity", null, () => { setGlobalEnv(r.env); openEntity(r.entity); }, "tool outline"),
        btn("Open run", null, () => showRun(r.run_id, r.env), "tool primary"))),
      ...cloudFailed.slice(0, 3).map((r) => h("div", { class: "card attention" },
        statusPill("failed"),
        h("div", { class: "grow" }, h("div", {}, h("b", {}, r.name), ` · ${r.kind} in Fabric`),
          h("div", { class: "reason" }, r.message || "Failed in Fabric"), h("div", { class: "muted-text small" }, when(r.started_at))),
        r.url ? btn("Open in Fabric", null, () => openInFabric(r.url), "tool primary") : null))] : []));

    envCards.replaceChildren(...Object.entries(ENV_LABEL).map(([env, label]) => {
      const where = envWhere(project, env);
      const runs = [...latest.values()].filter((r) => r.env === env);
      const failed = runs.filter((r) => r.status === "failed" || r.status === "needs_decision").length;
      const last = (meta.runs || []).find((r) => r.env === env);
      const busy = live.some((i) => i.env === env);
      const [cls, text] = busy ? ["info", "Running"] : meta.unavailable?.[env] ? ["neu", "Not checked"]
        : !runs.length ? ["neu", "Never run"] : failed ? ["fail", `${failed} failed`] : ["pass", "All green"];
      return h("button", { class: "card env-tile", onclick: () => { setGlobalEnv(env); goMeta("runs"); } },
        h("div", { class: "row" }, h("b", { class: "grow" }, label), h("span", { class: `pill-s ${cls}` }, text)),
        h("div", { class: "muted-text small" }, hasProject ? where.short : "No metadata project yet"),
        h("div", { class: "muted-text small" }, meta.unavailable?.[env] || (last ? ["Last run ", when(last.started_at)] : "")));
    }));
  })();

  // The X-ray's saved scan is read last, so it never holds up the rest of Home.
  await Promise.allSettled([notebooksDone, upcomingDone, runsDone]);
  if (onHome() && yours?.isConnected) await yours.load();  // reads every source, so after the quick parts
  if (onHome() && xray?.isConnected) await loadXray(xray, false);
  // The engine answers in order, so the cards that read on their own (Ready to work, the mesh map) are in too.
  if (onHome()) homeCache.done = true;
}

function tile(iconName, title, desc, onClick) {
  return h("button", { class: "tile", onclick: onClick },
    h("span", { class: "ti" }, icon(iconName, 28)), h("span", { class: "tt" }, title), h("span", { class: "td" }, desc));
}

function renderCreate() {
  setView(h("div", { class: "page" },
    h("h1", {}, "Create"), pageIntro("Items you create here run on this PC and store their data in OneLake. ", learnMore("create")),
    h("h2", {}, "Data engineering"),
    h("div", { class: "tiles" },
      tile("notebook", "Notebook", "Explore, transform and write Delta tables with Python", newNotebook),
      preview("wizard", tile("notebook", "Notebook from a template", "Ten templates to load, clean, check, model and compare data, with checks and a proof contract", openWizard)),
      preview("replay", tile("history", "Open replay bundle", "Run a notebook that failed again, up to the failed cell, against the same table versions", replayOpenDialog)),
      tile("lakehouse", "Lakehouse", "Store tables and files in OneLake, organized by schema", () => newLakehouse()),
      preview("pipelines", tile("flow", "Pipeline", "Open a Fabric pipeline, check what can run here, and run it on this PC", pipelineOpenDialog)))));
}

// Filters on Monitor, kept for the session.
const monitorPage = { env: "all", where: "any", problemsFirst: true, panel: "runs", opsView: "workloads",
  refreshing: false };
// Pop-out windows (Preview, popout.js). In a pop-out window, POP_OUT is the one section it shows ("runs"
// or "fabric"); in the main window it is null and popOuts holds the sections open in windows of their own.
const POP_OUT = ["runs", "fabric"].find((s) => new URLSearchParams(location.search).get("popout") === s) || null;
const popOuts = new Set();
// Monitor > Operations holds the workload views (UX-010). Each keeps its own panel id, so links to it still work.
const MONITOR_OPS = [
  { panel: "workloads", feature: "workloads", label: "Workloads", anchor: "workloads-preview",
    text: "Everything you build and run: on this PC, on agents and in Fabric, with its health and owner" },
  { panel: "queue", feature: "queue", label: "Queue", anchor: "queue-preview",
    text: "What is running and what runs next, on this PC, on agents and in Fabric, and who owns it" },
  { panel: "agents", feature: "agents", label: "Agents", anchor: "agents-preview",
    text: "KratosForge on your own servers, from the status they report" },
  { panel: "runas", feature: "runAs", label: "Run as", anchor: "run-as-preview",
    text: "Which identity runs your workloads in each place, and with what credential" },
  { panel: "incidents", feature: "incidents", label: "Incidents", anchor: "incidents-preview",
    text: "One record per problem, shared by everyone who can read the environment" },
];
// Monitor's tabs are by source, the way Data mesh groups its pages: what KratosForge ran and schedules, and
// Fabric's own Monitoring hub and schedules. Another source (Databricks, Snowflake) is a tab beside them.
// Each tab's pages are on the view row's switcher; "fabric" is the hub's panel.
const MONITOR_SOURCES = [["kratosforge", "KratosForge", "runs"], ["fabric", "Fabric", "fabric"]];
const MONITOR_FABRIC_PANELS = ["fabric", "fabric_schedules"];

function monitorSource(panel) {
  return MONITOR_FABRIC_PANELS.includes(panel) ? "fabric" : "kratosforge";
}

// What each Monitor tab shows, as the line under the title with its Learn more link. Operations' views
// say theirs in MONITOR_OPS.
const MONITOR_ABOUT = {
  runs: ["Everything this app ran or started, in every environment, on this PC and in Fabric", "monitor"],
  schedules: ["Schedules Windows starts on this PC for KratosForge, even with the app closed, with how their last run went", "monitor"],
  fabric_schedules: ["Schedules in Fabric's own job scheduler, on capacity, with how their last run went", "monitor"],
  fabric: ["Fabric's own Monitoring hub, for everything in your workspaces", "monitor"],
};
// The view row's places a tab fills: its own switcher (views) and status (tools), left of Refresh.
const monitorSlots = {};

function monitorAbout(text, anchor) {
  return h("span", { class: "monitor-about" }, text, ". ", learnMore(anchor));
}

async function renderMonitor() {
  // Cost moved to Optimize > Cost and savings (testing item 36); an old link to it lands there.
  if (monitorPage.panel === "cost") { monitorPage.panel = "runs"; optimizePage.tab = "cost"; go("optimize"); return; }
  if (!previewShown() && !["runs", "fabric"].includes(monitorPage.panel)) monitorPage.panel = "runs";
  if (POP_OUT === "fabric") { renderFabricPopOut(); return; }
  // The runs window shows only what KratosForge ran.
  if (POP_OUT === "runs" && monitorSource(monitorPage.panel) === "fabric") monitorPage.panel = "runs";
  // A pop-out window's refresh keeps the list it has, and your place in it, until the new one is read.
  const kept = monitorPage.refreshing ? document.querySelector(".page.monitor .monitor-scroll") : null;
  const keptTop = kept?.scrollTop || 0;
  monitorPage.refreshing = false;
  const local = kept || loading("Loading runs...", "monitor-empty");
  const slot = h("div", { class: "fabric-slot" },
    h("p", { class: "muted-text" }, "Opening the Fabric Monitoring hub. Sign in once and the app remembers it."));
  // One switcher per filter, like every page's view row: the pressed button is the one in force.
  const seg = (key, label, options) => h("div", { class: "seg", role: "group", "aria-label": label }, options.map(([value, text]) =>
    h("button", { class: monitorPage[key] === value ? "on" : null, "aria-pressed": String(monitorPage[key] === value),
      onclick: () => { monitorPage[key] = value; render(); } }, text)));
  const note = h("div", { class: "muted-text small monitor-note" });
  const ops = MONITOR_OPS.filter((o) => !PREVIEW_FEATURES[o.feature] || previewShown());
  const op = ops.find((o) => o.panel === monitorPage.panel);
  if (op) monitorPage.opsView = op.panel;
  const show = (panel) => () => { monitorPage.panel = panel; render(); };
  // A section in a window of its own leaves a band here.
  const runsOut = popOuts.has("runs");
  const fabricOut = popOuts.has("fabric");
  const source = monitorSource(monitorPage.panel);
  const hub = monitorPage.panel === "fabric";
  const hubShown = hub && !POP_OUT && !fabricOut;
  // The same layout as every page with tabs (docs/ui-guidelines.md, Page layout): the title with what
  // this tab shows and its button, the tabs, the view row (pages and filters left, Refresh right), the working area.
  const about = op ? [op.text, op.anchor] : MONITOR_ABOUT[monitorPage.panel] || MONITOR_ABOUT.runs;
  const tabs = POP_OUT ? null : h("div", { class: "subtabs monitor-tabs", role: "tablist", "aria-label": "Monitor" }, MONITOR_SOURCES.map(([id, label, first]) =>
    h("button", { class: `tab${source === id ? " active" : ""}`, role: "tab", "aria-selected": String(source === id), onclick: show(first) }, label)));
  // This source's pages: Operations opens the view it was last on.
  const pages = source === "fabric"
    ? [["fabric", "Monitoring hub", hub], previewShown() ? ["fabric_schedules", "Schedules", monitorPage.panel === "fabric_schedules"] : null]
    : [["runs", "Runs", monitorPage.panel === "runs"], previewShown() ? ["schedules", "Schedules", monitorPage.panel === "schedules"] : null,
      ops.length ? [ops.some((o) => o.panel === monitorPage.opsView) ? monitorPage.opsView : ops[0].panel,
        ["Operations", ops.every((o) => PREVIEW_FEATURES[o.feature]) ? previewTag() : null], !!op] : null];
  const shown = pages.filter(Boolean);
  const pageSeg = shown.length > 1 ? h("div", { class: "seg monitor-pages", role: "group", "aria-label": `${source === "fabric" ? "Fabric" : "KratosForge"} pages` },
    shown.map(([panel, label, on]) => h("button", { class: on ? "on" : null, "aria-pressed": String(on), onclick: show(panel) }, label))) : null;
  Object.assign(monitorSlots, { views: h("div", { class: "row tight mesh-views" }), tools: h("div", { class: "row tight mesh-tools" }) });
  const refresh = () => {
    monitorPage.fabricPipelinesRefresh = true;
    schedulesPage.refresh = monitorPage.panel === "fabric_schedules";
    forgetFabricActivities();
    render();
  };
  const viewRow = h("div", { class: "row mesh-view-row monitor-view-row" }, pageSeg,
    monitorPage.panel !== "runs" ? null : [
      seg("env", "Environment", [["all", "All"], ...Object.entries(ENV_LABEL)]),
      seg("where", "Where it ran", [["any", "Anywhere"], ["local", "This PC"], ["fabric", "Fabric"]]),
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: monitorPage.problemsFirst ? true : null,
        onchange: (e) => { monitorPage.problemsFirst = e.target.checked; render(); } }), "Problems first")],
    op ? h("select", { class: "group-select", "aria-label": "Operations view", title: "Which Operations view to show",
      onchange: (e) => show(e.target.value)() }, ops.map((o) => h("option", { value: o.panel, selected: o === op }, o.label))) : null,
    monitorSlots.views, h("span", { class: "spacer" }), monitorSlots.tools,
    hubShown ? monitorHubButtons() : null,
    op || hub ? null : btn("Refresh", "sync", refresh, "tool outline"));
  // The page's button: the KratosForge tab's runs, or the Fabric hub, in a window of its own.
  const popOutHere = hub ? (fabricOut ? null : popOutButton("fabric", "Pop out hub")) : runsOut ? null : popOutButton("runs", "Pop out runs");
  // The runs window holds the KratosForge tab (Runs, Schedules, Operations); the Fabric tab stays here.
  const runsBand = source === "kratosforge" && runsOut && !POP_OUT;
  setView(h("div", { class: `page monitor${hubShown ? "" : " no-fabric"}` },
    POP_OUT ? null : h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Monitor"), h("p", { class: "sub monitor-context" }, monitorAbout(...about))),
      h("div", { class: "head-actions" }, popOutHere)),
    tabs, runsBand ? null : viewRow,
    hub ? (fabricOut ? poppedOutBand("fabric", "Fabric's Monitoring hub", "monitor-fabric")
      : h("section", { class: "card monitor-fabric" }, slot))
      : runsBand ? poppedOutBand("runs", "From KratosForge", "monitor-local")
        : [note, h("section", { class: "card monitor-local" }, local)]));
  if (hubShown) dockFabricView(slot, "monitor", "monitor");
  else if (!POP_OUT) invoke("fabric_view_hide").catch(() => {});
  if (hub || runsBand) return;
  if (monitorPage.panel === "schedules" || monitorPage.panel === "fabric_schedules") { local.replaceWith(schedulesPanel()); return; }
  if (monitorPage.panel === "workloads") { local.replaceWith(workloadsPanel()); return; }
  if (monitorPage.panel === "incidents") { local.replaceWith(incidentsPanel()); return; }
  if (monitorPage.panel === "agents") { local.replaceWith(agentsPanel()); return; }
  if (monitorPage.panel === "queue") { local.replaceWith(queuePanel()); return; }
  if (monitorPage.panel === "runas") { local.replaceWith(runAsPanel()); return; }

  const refreshFabricPipelines = monitorPage.fabricPipelinesRefresh;
  monitorPage.fabricPipelinesRefresh = false;
  const [writes, meta, cloud, scheduled, pipelines, fabricPipelines] = await Promise.all([
    attempt(() => rpc("history")),
    rpc("meta_runs_all", { limit: 200 }).catch(() => ({ runs: [], unavailable: {} })),
    rpc("fabric_runs", { limit: 50 }).catch(() => []),  // not signed in yet
    rpc("pc_schedule_runs", { limit: 100 }).catch(() => []),
    previewShown() ? rpc("pipeline_runs", { limit: 50 }).catch((err) => ({ runs: [], error: friendlyError(err).text })) : { runs: [] },
    previewShown() ? rpc("fabric_pipeline_runs", { refresh: refreshFabricPipelines })
      .catch((err) => ({ runs: [], error: friendlyError(err).text })) : null,
  ]);
  if (state.view !== "monitor") return;
  const unavailable = Object.entries(meta.unavailable || {});
  note.textContent = [
    meta.project_error ? `Metadata runs not shown: the metadata project couldn't be read (${meta.project_error})` : "",
    ...unavailable.map(([env, why]) => `${ENV_LABEL[env]}: ${why}`),
    pipelines.error ? `Pipeline runs not shown: they couldn't be read (${pipelines.error.replace(/\.$/, "")})` : "",
    fabricPipelineRunNote(fabricPipelines),
  ].filter(Boolean).join(" · ");
  // The Fabric pipeline scan runs in the background: ask only for it until it's done, then draw Runs again.
  clearTimeout(monitorPage.fabricPipelinesTimer);
  const wait = monitorPage.fabricPipelinesWait = (monitorPage.fabricPipelinesWait || 0) + 1;  // a newer draw takes over
  const onRuns = () => wait === monitorPage.fabricPipelinesWait && state.view === "monitor" && monitorPage.panel === "runs";
  const waitForScan = () => {
    monitorPage.fabricPipelinesTimer = setTimeout(async () => {
      if (!onRuns()) return;
      const out = await rpc("fabric_pipeline_runs", {}).catch(() => ({ pending: false }));
      if (!onRuns()) return;
      if (out.pending) waitForScan();
      else { monitorPage.refreshing = true; render(); }
    }, 2500);
  };
  if (fabricPipelines?.pending) waitForScan();
  const rows = [
    ...(meta.runs || []).map((r) => ({
      at: r.started_at, status: r.status, activity: r.entity, kind: `Metadata run${r.backfill ? ` · backfill ${r.backfill}` : ""}`,
      env: r.env, where: r.compute || "local", ranOn: ranOn(r), seconds: r.seconds, rows: r.rows_written,
      note: r.quarantined ? `${r.quarantined} quarantined` : "", reason: runReason(r),
      open: () => showRun(r.run_id, r.env),
    })),
    ...(cloud || []).map((r) => ({
      at: r.started_at, status: fabricStatus(r.status), pill: fabricPill(r), activity: r.name, kind: r.kind, env: r.env || "", where: "fabric",
      seconds: r.finished_at ? (new Date(r.finished_at) - new Date(r.started_at)) / 1000 : "", rows: "",
      note: r.workspace_name || "", reason: r.status === "Failed" ? r.message || "" : "",
      open: r.url ? () => openInFabric(r.url) : null,
      cancel: cancelFabricRunShown() && !FABRIC_DONE.has(r.status) && !r.cancel_requested && r.workspace_id && r.item_id ? r.job_id : null,
    })),
    ...(scheduled || []).map((r) => ({
      at: r.started, status: r.status === "ok" ? "ok" : r.status === "skipped" ? "skipped" : "failed", activity: r.label,
      kind: r.trigger === "home" ? "Run from Home" : r.trigger === "manual" ? "Schedule, run now" : "Scheduled run", env: "", where: "local", seconds: r.seconds,
      rows: r.rows ?? "", note: r.status === "ok" ? r.message : "",
      reason: r.status === "ok" ? "" : [r.message?.replace(/\.$/, ""), r.alert_error].filter(Boolean).join(". "),
      open: r.trigger === "home" ? () => openNotebook(r.label) : () => { monitorPage.panel = "schedules"; render(); },
    })),
    ...pipelineRunRows(pipelines.runs),
    ...fabricPipelineRunRows(fabricPipelines, cloud),
    ...(writes || []).map((r) => ({
      at: r.finished_at, status: "ok", activity: r.job, kind: historyKind(r), env: "", where: "local",
      seconds: r.seconds, rows: r.rows, note: `${r.table_uri.split("/Tables/")[1] || r.table_uri} (${r.mode}, v${r.delta_version})`, reason: "",
    })),
  ].filter((r) => (monitorPage.env === "all" || r.env === monitorPage.env)
      && (monitorPage.where === "any" || r.where === monitorPage.where)
      && matches(`${r.activity} ${r.kind} ${r.note} ${r.reason}`))
    .sort((a, b) => String(b.at).localeCompare(String(a.at)));
  // Unknown and Stale come after real failures and aren't counted with the healthy rows.
  const bad = (r) => (r.status === "failed" || r.status === "needs_decision" ? 0 : r.status === "running" || r.status === "unavailable" ? 1 : 2);
  if (monitorPage.problemsFirst) rows.sort((a, b) => bad(a) - bad(b));  // stable: newest first within each group
  const list = rows.length ? h("div", { class: "monitor-scroll" }, h("table", { class: "list" },
    h("thead", {}, h("tr", {}, ["Status", "Activity", "Env", "Ran on", "When", "Took", "Rows", ""].map((c) => h("th", ["Took", "Rows"].includes(c) ? { class: "n" } : {}, c)))),
    h("tbody", {}, rows.map((r) => h("tr", r.open ? { class: "click", onclick: r.open }
      : r.pipelineRun ? { class: "click pl-run", "data-pl-run": r.pipelineRun, onclick: (e) => togglePipelineRun(e.currentTarget, r) } : {},
      h("td", {}, r.pill || statusPill(r.status)),
      h("td", {}, h("div", {}, r.pipelineRun ? pipelineToggle(r) : null, h("b", {}, r.activity)),
        h("div", { class: "muted-text small" }, [r.kind, r.note].filter(Boolean).join(" · "),
          r.pipelineRun ? h("span", { class: "pl-summary" }) : null, r.tags?.length ? [" ", r.tags] : null),
        r.reason ? h("div", { class: "reason" }, r.reason) : null),
      h("td", {}, runEnvCell(r)), h("td", {}, r.ranOn || (r.where === "fabric" ? "Fabric" : "This PC")),
      h("td", {}, when(r.at)), h("td", { class: "n" }, r.tookText ?? took(r.seconds)), h("td", { class: "n" }, r.rows ?? ""),
      h("td", { class: "n" }, r.cancel ? btn("Cancel", null, async (e) => {
        e.stopPropagation();
        if (await cancelFabricJob(r.cancel)) { monitorPage.refreshing = true; render(); }
      }, "tool small outline danger") : null,
      r.open ? h("span", { class: "link-text" }, r.openLabel || "Details") : r.pipelineRun ? pipelineRunDetails(r) : null)))))) :
    h("p", { class: "muted-text monitor-empty" }, monitorPage.env !== "all" || monitorPage.where !== "any"
      ? "Nothing matches these filters." : "Nothing has run yet. Run a metadata entity or write a table from a notebook.");
  local.replaceWith(list);
  if (rows.length) reopenPipelineRuns(list, rows);
  list.scrollTop = keptTop;
}

// Back, Home, Reload and Open in browser for the Monitoring hub, on Monitor and in its pop-out window.
function monitorHubButtons() {
  const action = (name) => attempt(() => invoke("fabric_view_action", { action: name, page: "monitor" }), "Fabric view");
  return [
    btn("Back", null, () => action("back"), "tool outline"),
    btn("Home", "home", () => action("home"), "tool outline"),
    btn("Reload", "restart", () => action("reload"), "tool outline"),
    btn("Open in browser", "publish", () => action("browser"), "tool outline")];
}

// This PC's write history holds two kinds of record: a notebook's table writes (named "Notebook
// write to <lakehouse>") and runs of a job file (`fabric-desktop run JOB`, named after the job).
function historyKind(r) {
  return String(r.job || "").startsWith("Notebook write to ") ? "Notebook write" : "Job run";
}

// Runs > Env for a row with no environment, saying why there isn't one (testing item 32).
function runEnvCell(r) {
  if (r.env) return ENV_LABEL[r.env] || r.env;
  const why = r.envWhy || (r.where === "fabric"
    ? "Started in a Fabric workspace (named under the activity), not from one of the project's environments"
    : "Notebook writes, job runs and this PC's schedules aren't tied to an environment");
  return h("span", { class: "muted-text", title: why }, "No environment");
}

// Fabric job states, shown with the same pills as local runs.
function fabricStatus(status) {
  return { Completed: "ok", Failed: "failed", Cancelled: "skipped", Deduped: "skipped" }[status] || "running";
}

// Runs > Status for a Fabric job: Cancelled and Cancelling say so; the rest use the shared pills.
function fabricPill(r) {
  if (r.status === "Cancelled") return h("span", { class: "pill-s neu" }, "Cancelled");
  if (r.cancel_requested && !FABRIC_DONE.has(r.status)) return h("span", { class: "pill-s wrn" }, "Cancelling…");
  return null;
}

// Fabric's own pages (Monitoring hub, OneLake catalog) are one second webview the shell lays
// over the slot's box. It is a native view, so it is hidden whenever the slot isn't showing or a
// dialog is open over it. owner is the app view showing it; page is "monitor", "catalog" or a
// Fabric item link.
const fabricView = { slot: null, observer: null, frame: 0, owner: null, page: "monitor", force: false };

function dockFabricView(slot, owner, page) {
  fabricView.observer?.disconnect();
  fabricView.slot = slot;
  fabricView.owner = owner;
  fabricView.page = page;
  fabricView.observer = new ResizeObserver(placeFabricView);
  fabricView.observer.observe(slot);
}

function placeFabricView() {
  cancelAnimationFrame(fabricView.frame);
  fabricView.frame = requestAnimationFrame(() => {
    const slot = fabricView.slot;
    const covered = document.getElementById("dialog-root").childElementCount > 0 || activity.open;
    if (state.view !== fabricView.owner || !slot?.isConnected || covered) {
      invoke("fabric_view_hide").catch(() => {});
      return;
    }
    const r = slot.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return;
    const force = fabricView.force;
    fabricView.force = false;
    invoke("fabric_view_show", { x: r.left, y: r.top, width: r.width, height: r.height, tenant: state.tenant || null,
      page: fabricView.page, force }).catch((err) => {
      slot.replaceChildren(h("p", { class: "muted-text" }, `The Fabric view could not open: ${friendlyError(err).text}`));
    });
  });
}

window.addEventListener("resize", placeFabricView);
new MutationObserver(placeFabricView).observe(document.getElementById("dialog-root"), { childList: true });

// ---------- Workspaces, OneLake, lakehouse ----------

// Favorites and pins are kept on this PC; pins sit at the top in the order they were pinned.
const workspacesPage = { marks: null, filter: "all", open: new Set(), items: {} };

async function renderWorkspaces() {
  const wp = workspacesPage;
  const body = h("div", {}, state.user ? loading("Loading workspaces...") : "");
  const chips = h("div", { class: "ws-filters" });
  const previewing = itemPreviewOn();
  if (itemPreview.target?.kind !== "item" && !itemPreview.parent) itemPreview.target = null;
  setView(h("div", { class: "page" }, h("h1", {}, "Workspaces"), workspacesTabs("workspaces"),
    pageIntro("Your Fabric workspaces. Expand one to see its items, star your favorites and pin the ones you use most. ",
      previewing ? "Click an item to preview it. " : "", learnMore("workspaces")),
    signInCallout(), withHowItWorks([chips, previewing ? previewLayout(body, () => draw()) : body], howItWorks("workspaces", {
      reads: "Your Fabric workspaces and their items, from Fabric with your sign-in, when the page opens and when you expand a workspace. Item previews read only the item you click.",
      changes: "Nothing in Fabric or OneLake. Open in notebook, in an item's preview, makes a new notebook on this PC that reads the table. Stars and pins are kept on this PC, not in Fabric.",
      results: "Only you see your stars, pins and new notebooks. Clicking a workspace opens its own page, where New lakehouse creates a lakehouse in Fabric after you name it.",
      anchor: "workspaces",
    }))));
  if (!state.user) return;
  const [workspaces, marks] = await Promise.all([loadWorkspaces(),
    wp.marks ? wp.marks : rpc("workspace_marks").catch(() => ({ favorites: [], pins: [] })),
    PREVIEW_FEATURES.itemPins && previewShown() ? itemPinsLoaded() : null]);
  if (state.view !== "workspaces") return;
  wp.marks = marks;
  const here = () => state.view === "workspaces";

  async function mark(ws, which, e) {
    e.stopPropagation();
    const on = !wp.marks[which].includes(ws.id);
    const result = await attempt(() => rpc("set_workspace_mark", { workspace_id: ws.id, mark: which, on }), "Could not save the workspace");
    if (result) { wp.marks = result; draw(); }
  }

  async function toggle(ws, e) {
    e.stopPropagation();
    if (wp.open.has(ws.id)) { wp.open.delete(ws.id); draw(); return; }
    wp.open.add(ws.id);
    draw();
    if (!wp.items[ws.id]) {
      const items = await attempt(() => rpc("list_items", { workspace_id: ws.id }), `Could not list the items in ${ws.displayName}`);
      wp.items[ws.id] = items || [];
      if (here()) draw();
    }
  }

  function itemRows(ws) {
    const items = wp.items[ws.id];
    if (!items) return h("p", { class: "muted-text ws-items-note" }, "Loading items...");
    if (!items.length) return h("p", { class: "muted-text ws-items-note" }, "This workspace has no items yet.");
    const sorted = [...items].sort((a, b) => typeLabel(a.type).localeCompare(typeLabel(b.type)) || a.displayName.localeCompare(b.displayName));
    return h("table", { class: "list ws-items" }, h("tbody", {}, sorted.map((it) => {
      const r = { workspace_id: ws.id, item_id: it.id, type: it.type, name: it.displayName };
      const lh = it.type === "Lakehouse" ? lakehouseRef(ws, it) : null;
      const whRef = warehouseRefFor(ws, it, items);
      const target = { kind: "item", item: { ...r, workspace_name: ws.displayName } };
      const stop = (fn) => (e) => { e.stopPropagation(); fn(); };
      return h("tr", { class: `${previewing ? "click" : ""}${isPreviewed(target) ? " selected" : ""}`,
        title: previewing ? `Preview ${it.displayName}` : null, onclick: () => selectPreview(target) },
        h("td", {}, h("span", { class: "item" }, itemIcon(it.type),
          whRef && whOpensHere()
            ? h("button", { class: "name-link", title: "Open on the Warehouse page", onclick: stop(() => openWarehouseQuery(whRef)) }, it.displayName)
            : h("button", { class: "name-link", title: "Open in Fabric", onclick: stop(() => openInFabric(fabricItemLink(r))) }, it.displayName))),
        h("td", { class: "ws-type" }, typeLabel(it.type)),
        PREVIEW_FEATURES.itemPins && previewShown() ? h("td", { class: "tick" }, itemPinButton({ kind: it.type, id: it.id,
          name: it.displayName, workspace_id: ws.id, workspace_name: ws.displayName }, () => draw())) : null,
        h("td", { class: "actions" },
          lh ? btn("Open", null, stop(() => openLakehouse(lh)), "tool small link") : null,
          lh ? btn("SQL query", null, stop(() => openSqlQuery(lh)), "tool small link") : null,
          whRef && !whOpensHere() ? preview("warehouse", btn("SQL query", null, stop(() => openWarehouseQuery(whRef)), "tool small link",
            { title: "Query it with T-SQL in Fabric (uses capacity)" })) : null,
          btn("Open in Fabric", "open", stop(() => openInFabric(fabricItemLink(r))), "tool small link")));
    })));
  }

  function draw() {
    if (!here()) return;
    const { favorites, pins } = wp.marks;
    const chip = (key, label, n) => h("button", { class: `chip${wp.filter === key ? " on" : ""}`,
      onclick: () => { wp.filter = key; draw(); } }, `${label} ${n}`);
    const byId = new Map(workspaces.map((w) => [w.id, w]));
    const count = (ids) => ids.filter((id) => byId.has(id)).length;
    chips.replaceChildren(chip("all", "All", workspaces.length), chip("favorites", "Favorites", count(favorites)),
      chip("pins", "Pinned", count(pins)));
    const pinned = pins.map((id) => byId.get(id)).filter(Boolean);
    const ordered = [...pinned, ...workspaces.filter((w) => !pins.includes(w.id))];
    const shown = ordered.filter((w) => matches(w.displayName) && (wp.filter === "all" || wp.marks[wp.filter].includes(w.id)));
    const rows = [];
    shown.forEach((w, i) => {
      const open = wp.open.has(w.id);
      const pin = pins.includes(w.id);
      const fav = favorites.includes(w.id);
      const lastPin = pin && !pins.includes(shown[i + 1]?.id);
      rows.push(h("tr", { class: `click${lastPin && i < shown.length - 1 ? " last-pin" : ""}`, onclick: () => openWorkspace(w) },
        h("td", {}, h("button", { class: "star", title: open ? "Hide items" : "Show items", "aria-label": open ? "Hide items" : "Show items",
          "aria-expanded": String(open), onclick: (e) => toggle(w, e) }, open ? "−" : "+")),
        h("td", {}, h("button", { class: `star pin${pin ? " on" : ""}`, title: pin ? "Unpin" : "Pin to the top",
          "aria-label": pin ? "Unpin" : "Pin to the top", onclick: (e) => mark(w, "pins", e) }, icon(pin ? "pinFilled" : "pin", 16))),
        h("td", {}, h("button", { class: `star${fav ? " on" : ""}`, title: fav ? "Remove from favorites" : "Add to favorites",
          "aria-label": fav ? "Remove from favorites" : "Add to favorites", onclick: (e) => mark(w, "favorites", e) }, fav ? "★" : "☆")),
        h("td", {}, h("span", { class: "item" }, icon("workspace", 18), w.displayName)),
        h("td", {}, w.type || "Workspace"),
        h("td", { class: "actions" }, btn("Open in Fabric", "open", (e) => { e.stopPropagation(); openInFabric(fabricItemLink({ workspace_id: w.id })); }, "tool small link"))));
      if (open) rows.push(h("tr", { class: "sub-row" }, h("td", {}), h("td", { colspan: 5 }, itemRows(w))));
    });
    const table = shown.length ? h("table", { class: "list ws-list" },
      h("thead", {}, h("tr", {}, h("th", { style: "width:32px" }), h("th", { style: "width:32px" }), h("th", { style: "width:32px" }),
        h("th", {}, "Name"), h("th", {}, "Type"), h("th", {}))),
      h("tbody", {}, rows)) : h("p", { class: "muted-text", style: "padding:16px 0" },
      wp.filter === "favorites" ? "No favorite workspaces yet. Click the star next to one." :
      wp.filter === "pins" ? "No pinned workspaces yet. Click the pin next to one." : "Nothing matches.");
    body.replaceChildren(table);
  }
  draw();
}

// The Fabric page: Fabric's own portal on the whole page. Links across the app open their
// item here; the page keeps whatever was shown last.
const FABRIC_HOME = "https://app.fabric.microsoft.com/home?experience=fabric-developer";
const fabricPage = { page: FABRIC_HOME };

function openInFabric(page) {
  fabricPage.page = page;
  fabricView.force = true;  // a click always goes to its page, even the one shown last
  if (state.view === "fabric") {
    fabricView.force = false;
    invoke("fabric_view_action", { action: "home", page }).catch(() => {});
  } else {
    go("fabric");
  }
}

// Workspaces has two tabs: your workspaces in the app, and Fabric's own portal.
function workspacesTabs(active) {
  return h("div", { class: "subtabs", role: "tablist", "aria-label": "Workspaces views" },
    [["workspaces", "Your workspaces"], ["fabric", "Fabric portal"]].map(([view, label]) =>
      h("button", { class: `tab${active === view ? " active" : ""}`, role: "tab", "aria-selected": active === view ? "true" : "false",
        onclick: () => go(view) }, label)));
}

// Opening a warehouse or SQL analytics endpoint by its name: the Warehouse page unless Settings says
// Fabric's own editor (wh_open_in). The Warehouse page is a Preview feature, so with preview features
// hidden a name opens Fabric as before.
const whOpensHere = () => previewShown() && (state.settings?.wh_open_in?.value || "kratosforge") === "kratosforge";
const fabricFullScreen = () => state.settings?.fabric_full_screen?.value === true;

// The warehouse or SQL analytics endpoint a Fabric portal address shows, as {workspace_id, item_id,
// type}, or null for any other page.
function fabricWarehouseOf(address) {
  const m = /^https:\/\/app\.fabric\.microsoft\.com\/groups\/([0-9a-f-]{36})\/(datawarehouses|lakewarehouses)\/([0-9a-f-]{36})(?:[/?#]|$)/i
    .exec(String(address || ""));
  return m ? { workspace_id: m[1].toLowerCase(), type: m[2].toLowerCase() === "datawarehouses" ? "Warehouse" : "SQLEndpoint",
    item_id: m[3].toLowerCase() } : null;
}

// Open in KratosForge: the warehouse the Fabric portal shows, on the Warehouse page.
async function openFabricPageHere() {
  const address = await invoke("fabric_view_url").catch(() => null);
  const at = fabricWarehouseOf(address);
  if (!at) { toast("Open a warehouse or SQL analytics endpoint in Fabric first, then this opens it here.", true); return; }
  const workspaces = state.workspaces || await attempt(() => rpc("list_workspaces"), "Could not list workspaces");
  const ws = (workspaces || []).find((w) => w.id.toLowerCase() === at.workspace_id);
  const items = ws && await attempt(() => rpc("list_items", { workspace_id: ws.id }), `Could not list the items in ${ws.displayName}`);
  const it = (items || []).find((x) => x.id.toLowerCase() === at.item_id && x.type === at.type);
  const ref = it && warehouseRefFor(ws, it, items);
  if (!ref) { toast("That warehouse isn't in a workspace you can open here.", true); return; }
  openWarehouseQuery(ref);
}

async function setFabricFullScreen(on) {
  if (await saveSettings({ fabric_full_screen: on })) renderFabric();
}

function renderFabric() {
  const slot = h("div", { class: "fabric-slot" },
    h("p", { class: "muted-text" }, "Opening Fabric. Sign in once and the app keeps it between launches."));
  const action = (name) => attempt(() => invoke("fabric_view_action", { action: name, page: fabricPage.page }), "Fabric view");
  const full = fabricFullScreen();
  setView(h("div", { class: "page monitor fabric-page" }, workspacesTabs("fabric"), withHowItWorks(
    h("section", { class: "card monitor-fabric" },
      h("div", { class: "monitor-band" },
        h("h2", {}, "Fabric"),
        h("span", { class: "spacer" }),
        preview("warehouse", btn("Open in KratosForge", "database", openFabricPageHere, "tool outline",
          { title: "Open the warehouse or SQL analytics endpoint Fabric shows on the Warehouse page" })),
        btn(full ? "Exit full screen" : "Full screen", null, () => setFabricFullScreen(!full), "tool outline"),
        btn("Back", null, () => action("back"), "tool outline"),
        btn("Home", "home", () => openInFabric(FABRIC_HOME), "tool outline"),
        btn("OneLake catalog", "onelake", () => openInFabric("catalog"), "tool outline"),
        btn("Monitoring hub", "monitor", () => openInFabric("monitor"), "tool outline"),
        btn("Reload", "restart", () => action("reload"), "tool outline"),
        btn("Open in browser", "publish", () => action("browser"), "tool outline")),
      slot),
    howItWorks("workspaces.fabric", {
      reads: "This is Fabric's own portal, app.fabric.microsoft.com, signed in as you. It shows what Fabric shows you; "
        + "KratosForge doesn't read anything from it.",
      changes: "Nothing by itself. What you do in the portal writes to Fabric and OneLake exactly as it would in a browser, "
        + "with Fabric's own prompts; this app adds no confirmation of its own there.",
      results: "Everything you change stays in Fabric. Your portal sign-in is kept on this PC, encrypted for your Windows "
        + "account; delete fabric-view-signin.bin to make it forget.",
      anchor: "fabric",
    }))));
  document.body.classList.toggle("fabric-full", full);
  dockFabricView(slot, "fabric", fabricPage.page);
  placeFabricView();
}

// Esc leaves full screen while the app has the keys; inside Fabric the bar's button does.
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || !document.body.classList.contains("fabric-full")) return;
  if (document.getElementById("dialog-root")?.childElementCount) return;
  e.preventDefault();
  setFabricFullScreen(false);
});

function openWorkspace(ws) {
  state.openWorkspace = ws;
  go("workspace");
}

async function renderWorkspace() {
  const ws = state.openWorkspace;
  const body = loading("Loading items...");
  setView(h("div", { class: "page" }, h("h1", {}, ws.displayName),
    pageIntro("This workspace's lakehouses. ", learnMore("workspaces")),
    h("div", { class: "toolbar", style: "padding:0;border:0;margin-bottom:16px" },
      btn("New lakehouse", "add", () => newLakehouse(ws), "tool outline"),
      btn("New notebook", "add", newNotebook, "tool outline")),
    body));
  const lakehouses = (await attempt(() => rpc("list_lakehouses", { workspace_id: ws.id }), "Could not list lakehouses")) || [];
  if (state.view !== "workspace" || state.openWorkspace !== ws) return;
  const shown = lakehouses.filter((l) => matches(l.displayName));
  body.replaceWith(shown.length ? h("table", { class: "list" },
    h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Type"), h("th", {}, ""))),
    h("tbody", {}, shown.map((lh) => h("tr", { class: "click", onclick: () => openLakehouse(lakehouseRef(ws, lh)) },
      h("td", {}, h("span", { class: "item" }, itemIcon("Lakehouse"), lh.displayName)),
      h("td", {}, "Lakehouse"),
      h("td", {}, btn("Open in notebook", "notebook", (e) => { e.stopPropagation(); newNotebook(lakehouseRef(ws, lh)); }, "tool small link")))))) :
    h("p", { class: "muted-text" }, "No lakehouses in this workspace yet."));
}

// ---------- OneLake catalog page ----------

// Fabric item types as people say them: SemanticModel -> Semantic model, KQLDatabase -> KQL database.
function typeLabel(type) {
  const words = String(type).replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2").replace(/([a-z])([A-Z])/g, "$1 $2").split(" ");
  return words.map((w, i) => (i && w !== w.toUpperCase() ? w.toLowerCase() : w)).join(" ");
}

// A workspace's environment as a small colored pill; nothing for one with no environment
// unless always (a group header).
function envPill(env, always = false) {
  if (env === "none" && !always) return null;
  return h("span", { class: `env-pill env-${env}` }, ENV_NAMES[env]);
}

// An icon for each Fabric item type, tinted by workload the way the Fabric portal groups them.
// Fluent UI icons, not Fabric's own artwork; other types get a plain document.
const ITEM_ICONS = {
  Lakehouse: ["lakehouse", "eng"], Notebook: ["notebook", "eng"], SparkJobDefinition: ["code", "eng"], Environment: ["boxToolbox", "eng"],
  Warehouse: ["building", "wh"], WarehouseSnapshot: ["camera", "wh"], SQLEndpoint: ["databaseSearch", "wh"], SQLDatabase: ["database", "wh"],
  Datamart: ["windowDatabase", "wh"], MirroredDatabase: ["databaseArrowRight", "wh"], MirroredWarehouse: ["databaseArrowRight", "wh"],
  MirroredAzureDatabricksCatalog: ["databaseArrowRight", "wh"],
  DataPipeline: ["flow", "fac"], Dataflow: ["dataFunnel", "fac"], CopyJob: ["copyArrowRight", "fac"], DataBuildToolJob: ["buildingFactory", "fac"],
  Report: ["dataBarVertical", "bi"], PaginatedReport: ["documentTable", "bi"], SemanticModel: ["cube", "bi"], Dashboard: ["gauge", "bi"],
  OrgApp: ["apps", "bi"], App: ["apps", "bi"],
  Eventhouse: ["tableLightning", "rti"], KQLDatabase: ["database", "rti"], KQLQueryset: ["databaseSearch", "rti"], KQLDashboard: ["gauge", "rti"],
  Eventstream: ["flashFlow", "rti"], Reflex: ["flash", "rti"],
  MLModel: ["brainCircuit", "ds"], MLExperiment: ["beaker", "ds"], DataAgent: ["bot", "ds"],
  UserDataFunction: ["braces", "eng"], GraphQLApi: ["plugConnected", "eng"],
};

function itemIcon(type, size = 18) {
  const [name, family] = ITEM_ICONS[type] || ["document", "other"];
  const svg = icon(name, size);
  svg.classList.add("type-icon", `wl-${family}`);
  return svg;
}

// Paths of item pages in the Fabric portal, by item type; other types open their workspace,
// since a wrong guess shows Fabric's "not found" page instead.
const FABRIC_ITEM_PATHS = {
  Lakehouse: "lakehouses", Warehouse: "datawarehouses", SQLEndpoint: "lakewarehouses", Notebook: "synapsenotebooks",
  SparkJobDefinition: "sparkjobdefinitions", SemanticModel: "datasets", Report: "reports", PaginatedReport: "rdlreports",
  DataPipeline: "pipelines", Dashboard: "dashboards", Datamart: "datamarts", Eventhouse: "eventhouses", KQLDatabase: "databases",
  KQLQueryset: "queryworkbenches", KQLDashboard: "kustodashboards", Eventstream: "eventstreams", MirroredDatabase: "mirroreddatabases",
  SQLDatabase: "sqldatabases", MLModel: "mlmodels", MLExperiment: "mlexperiments",
};

function fabricItemLink(r) {
  const path = FABRIC_ITEM_PATHS[r.type];
  const tail = path ? `${path}/${r.item_id}` : "list";
  return `https://app.fabric.microsoft.com/groups/${r.workspace_id}/${tail}?experience=fabric-developer`;
}

// Endorsement and table names come from slow background reads in the sidecar; ask until done.
async function pollSidecar(method, params, done, keepGoing) {
  for (let i = 0; i < 90 && keepGoing(); i++) {
    if (i) await new Promise((r) => setTimeout(r, 2000));
    const result = await rpc(method, params).catch((err) => ({ pending: false, available: false, items: {}, reason: String(err) }));
    if (!result.pending) { done(result); return; }
  }
}

const catalogPage = { data: null, tables: null, layers: {}, scope: "all", type: "all", query: "",
                      wsQuery: "", wsAll: false, sort: "name", dir: 1, open: new Set(), tab: "catalog", typeOpen: new Set(),
                      // Grouping (catalog-groups.js): none | env | solution | both, remembered on this PC.
                      groupBy: "none", env: "all", envWs: {}, groupOpen: new Map() };
try { catalogPage.groupBy = localStorage.getItem("fd.catalog.groupBy") || "none"; } catch { /* not remembered: no grouping */ }
const GROUP_BY = [["none", "None"], ["env", "Environment"], ["solution", "Solution"], ["both", "Solution, then environment"]];

// Item types grouped the way Fabric's OneLake catalog groups its Type filter. A type not listed
// falls under Other types.
const TYPE_GROUPS = [
  ["data", "Data", "database", ["Lakehouse", "Warehouse", "WarehouseSnapshot", "SQLEndpoint", "SQLDatabase", "Datamart", "MirroredDatabase",
    "MirroredWarehouse", "MirroredAzureDatabricksCatalog", "Eventhouse", "KQLDatabase", "SemanticModel"]],
  ["insights", "Insights", "dataBarVertical", ["Report", "PaginatedReport", "Dashboard", "KQLDashboard", "KQLQueryset", "Scorecard"]],
  ["processes", "Processes", "flow", ["Notebook", "SparkJobDefinition", "DataPipeline", "Dataflow", "CopyJob", "Eventstream", "Reflex",
    "DataBuildToolJob", "MLExperiment", "UserDataFunction"]],
  ["solutions", "Solutions", "apps", ["OrgApp", "App", "DataAgent", "MLModel", "GraphQLApi"]],
  ["configurations", "Configurations", "boxToolbox", ["Environment", "VariableLibrary", "MountedDataFactory", "ApacheAirflowJob"]],
];
const TYPE_GROUP_OF = Object.fromEntries(TYPE_GROUPS.flatMap(([id, , , types]) => types.map((t) => [t, id])));
const typeGroup = (type) => TYPE_GROUP_OF[type] || "other";
// A click anywhere outside the open Type panel closes it.
document.addEventListener("mousedown", (e) => {
  if (catalogPage.typePanel && !e.target.closest?.(".type-filter")) {
    catalogPage.typePanel = false;
    document.querySelector(".type-panel")?.remove();
  }
});
const TYPE_GROUP_LABEL = { ...Object.fromEntries(TYPE_GROUPS.map(([id, label]) => [id, label])), other: "Other types" };

// The OneLake page's tabs: the catalog here; Shortcuts and Access are in estate.js.
function onelakeTabs() {
  const tab = (id, label) => h("button", { class: `tab${catalogPage.tab === id ? " active" : ""}`, role: "tab",
    "aria-selected": String(catalogPage.tab === id), onclick: () => { catalogPage.tab = id; render(); } }, label);
  // The tablist holds only this page's tabs (#961). Data products moved to Data mesh > Products: it
  // looks like a tab but opens that page, so it sits after the list, where arrows don't reach it.
  return h("div", { class: "subtabs onelake-tabs" }, h("div", { class: "tab-group", role: "tablist", "aria-label": "OneLake" },
    tab("catalog", "Catalog"), preview("shortcuts", tab("shortcuts", "Shortcuts")), preview("access", tab("access", "Access"))),
    preview("mesh", h("button", { class: "tab tab-jump", title: "Opens Data mesh > Products", onclick: () => openProducts() }, "Data products")),
    h("span", { class: "spacer" }), learnMore("onelake"));
}

async function renderOneLake() {
  if (!previewShown()) catalogPage.tab = "catalog";
  if (catalogPage.tab === "shortcuts") return renderShortcuts();
  if (catalogPage.tab === "access") return renderAccess();
  if (catalogPage.tab === "products") { catalogPage.tab = "catalog"; return openProducts(productsPage.open); }  // an older link
  const cp = catalogPage;
  const nav = h("nav", { class: "catalog-nav" });
  const head = h("div", { class: "catalog-head" });
  const list = h("div", { class: "catalog-list" },
    state.user ? loading("Loading items from all workspaces...") : "");
  const note = h("span", { class: "muted-text" });
  const search = h("input", { class: "catalog-search", placeholder: "Search by name, workspace or table", value: cp.query, "aria-label": "Search the catalog" });
  search.addEventListener("input", () => { cp.query = search.value; draw(); });

  setView(h("div", { class: "page monitor onelake" }, signInCallout(), onelakeTabs(),
    h("section", { class: "card onelake-catalog" },
      h("div", { class: "monitor-band" }, h("h2", {}, "OneLake catalog"),
        h("span", { class: "muted-text" }, "Every item in every workspace you can open"), h("span", { class: "spacer" }), note, search,
        btn("Refresh", "sync", () => { cp.data = null; render(); }, "tool outline"),
        btn("Open Fabric's catalog", "open", () => openInFabric("catalog"), "tool outline")),
      h("div", { class: "catalog-body" }, nav, h("section", { class: "catalog-main" }, head, list))),
    // Under the catalog, not beside it: the table needs the page's full width for its row actions.
    howItWorks("onelake.catalog", {
      reads: "Every item in every workspace you can open, and each lakehouse's table names, from Fabric with your sign-in. It reads when the page opens and when you press Refresh.",
      changes: "Nothing in Fabric or OneLake. Open in notebook makes a new notebook on this PC that reads the table; SQL query reads it without changing it. Stars are kept on this PC.",
      results: "Only you see your stars. The list shows only what your own Fabric permissions let you open.",
      anchor: "catalog",
    })));
  if (!state.user) return;

  const here = () => state.view === "onelake" && list.isConnected;
  const key = (r) => r.item_id;
  const favorites = () => new Set(cp.data?.favorites || []);
  const endorsed = () => state.endorsements?.items || {};
  const tablesOf = (r) => cp.tables?.[r.item_id] || [];

  // cp.type is "all", a group ("g:data") or one item type ("Lakehouse").
  const typeMatches = (r) => cp.type === "all" || (cp.type.startsWith("g:") ? typeGroup(r.type) === cp.type.slice(2) : r.type === cp.type);
  const personal = () => new Set((cp.data?.workspaces || []).filter((w) => w.type === "Personal").map((w) => w.id));
  const envOf = (r) => workspaceEnv(r.workspace_id, r.workspace_name, cp.envWs);

  function inScope(r) {
    if (cp.env !== "all" && envOf(r) !== cp.env) return false;
    if (cp.scope === "fav") return favorites().has(key(r));
    if (cp.scope === "mine") return personal().has(r.workspace_id);
    if (cp.scope === "endorsed") return !!endorsed()[key(r)];
    if (cp.scope.startsWith("ws:")) return r.workspace_id === cp.scope.slice(3);
    return true;
  }
  const sortValue = { name: (r) => r.name, type: (r) => typeLabel(r.type), ws: (r) => r.workspace_name,
    endorsement: (r) => endorsed()[key(r)] || "~", layer: (r) => (r.type === "Lakehouse" && cp.layers[r.name]) || "~" };

  function shown() {
    const q = cp.query.toLowerCase();
    return cp.data.items.filter((r) => inScope(r) && typeMatches(r)
      && (!q || `${r.name} ${r.workspace_name} ${typeLabel(r.type)} ${r.description} ${tablesOf(r).join(" ")}`.toLowerCase().includes(q)))
      .sort((a, b) => cp.dir * String(sortValue[cp.sort](a)).localeCompare(String(sortValue[cp.sort](b)), undefined, { sensitivity: "base" }));
  }

  async function toggleFavorite(r, e) {
    e.stopPropagation();
    const result = await attempt(() => rpc("set_favorite", { lakehouse_id: key(r), favorite: !favorites().has(key(r)) }), "Could not save the favorite");
    if (result) { cp.data.favorites = result; draw(); }
  }

  function scopeButton(scope, label, iconName, count, disabled, title) {
    return h("button", { class: `catalog-scope${cp.scope === scope ? " on" : ""}`, disabled, title,
      onclick: () => { cp.scope = scope; draw(); } },
    h("span", { class: "glyph" }, icon(iconName, 16)), h("span", { class: "label" }, label), h("span", { class: "count" }, count));
  }

  // Fabric's Type filter: a button naming the choice, and a panel of groups that open to their types.
  function typeFilter(items) {
    const counts = {};
    items.forEach((r) => { counts[r.type] = (counts[r.type] || 0) + 1; });
    const groups = [...TYPE_GROUPS.map(([id, label, ic]) => [id, label, ic]), ["other", "Other types", "document"]]
      .map(([id, label, ic]) => [id, label, ic, Object.keys(counts).filter((t) => typeGroup(t) === id).sort((a, b) => counts[b] - counts[a])])
      .filter(([, , , types]) => types.length);
    const total = (types) => types.reduce((n, t) => n + counts[t], 0);
    const chosen = cp.type === "all" ? `All (${items.length})`
      : cp.type.startsWith("g:") ? `${TYPE_GROUP_LABEL[cp.type.slice(2)]} (${total(Object.keys(counts).filter((t) => typeGroup(t) === cp.type.slice(2)))})`
      : `${typeLabel(cp.type)} (${counts[cp.type] || 0})`;
    const pick = (type) => { cp.type = type; closeTypePanel(); draw(); };
    const check = (on) => h("span", { class: "tick", "aria-hidden": "true" }, on ? "✓" : "");
    const panel = h("div", { class: "type-panel", role: "menu" },
      h("button", { class: "type-row", role: "menuitemradio", "aria-checked": String(cp.type === "all"), onclick: () => pick("all") },
        check(cp.type === "all"), h("span", { class: "label" }, "All types"), h("span", { class: "count" }, items.length)),
      ...groups.map(([id, label, ic, types]) => {
        const open = cp.typeOpen.has(id) || types.includes(cp.type);
        return h("div", { class: "type-group" },
          h("div", { class: "type-row-wrap" },
            h("button", { class: "type-row", role: "menuitemradio", "aria-checked": String(cp.type === `g:${id}`), onclick: () => pick(`g:${id}`) },
              check(cp.type === `g:${id}`), icon(ic, 16), h("span", { class: "label" }, label), h("span", { class: "count" }, total(types))),
            h("button", { class: "type-expand", title: open ? `Hide ${label} types` : `Show ${label} types`, "aria-expanded": String(open),
              onclick: (e) => { e.stopPropagation(); open ? cp.typeOpen.delete(id) : cp.typeOpen.add(id); drawTypePanel(); } },
            icon(open ? "chevronDown" : "chevronRight", 14))),
          open ? h("div", { class: "type-sub" }, types.map((t) => h("button", { class: "type-row", role: "menuitemradio",
            "aria-checked": String(cp.type === t), onclick: () => pick(t) },
          check(cp.type === t), itemIcon(t, 16), h("span", { class: "label" }, typeLabel(t)), h("span", { class: "count" }, counts[t])))) : null);
      }));
    const wrap = h("div", { class: "type-filter" },
      h("button", { class: `tool outline type-button${cp.type !== "all" ? " on" : ""}`, "aria-haspopup": "menu",
        onclick: (e) => { e.stopPropagation(); cp.typePanel = !cp.typePanel; drawTypePanel(); } },
      icon("database", 16), `Type: ${chosen}`, icon("chevronDown", 14)),
      cp.typePanel ? panel : null);
    return wrap;
  }
  function envChips(items) {
    const count = (env) => items.filter((r) => envOf(r) === env).length;
    const chip = (env, label) => h("button", { class: `chip${cp.env === env ? " on" : ""}`, "aria-pressed": String(cp.env === env),
      title: env === "all" ? "Every environment" : `Only ${label} workspaces`, onclick: () => { cp.env = env; draw(); } },
    env === "all" ? label : `${label} ${count(env)}`);
    return h("div", { class: "catalog-envs" }, chip("all", "All"), ...ENVS.map((e) => chip(e, ENV_NAMES[e])));
  }

  function groupBySelect() {
    const select = h("select", { class: "group-select", "aria-label": "Group by",
      onchange: () => {
        cp.groupBy = select.value;
        cp.groupOpen = new Map();
        try { localStorage.setItem("fd.catalog.groupBy", cp.groupBy); } catch { /* this session only */ }
        draw();
      } }, GROUP_BY.map(([v, label]) => h("option", { value: v, selected: cp.groupBy === v }, label)));
    return h("label", { class: `group-by${cp.groupBy !== "none" ? " on" : ""}` }, icon("apps", 16), "Group by", select);
  }

  // Group headers in the table; the first group starts open, the rest closed, so a long
  // catalog reads as a list of solutions or environments.
  function drawGroups(groups, body, itemRow, depth = 0) {
    groups.forEach((g, i) => {
      const open = cp.groupOpen.has(g.key) ? cp.groupOpen.get(g.key) : depth > 0 || i === 0;
      const toggle = () => { cp.groupOpen.set(g.key, !open); draw(); };
      const wsCount = new Set(g.rows.map((r) => r.workspace_id)).size;
      const bits = [`${g.rows.length} item${g.rows.length === 1 ? "" : "s"}`];
      if (!g.env) bits.unshift(`${wsCount} workspace${wsCount === 1 ? "" : "s"}`);
      if (g.children && g.children.every((c) => c.env === "none")) bits.push("no environment");
      body.push(h("tr", { class: `group-row depth-${depth}` }, h("td", { colspan: 8 },
        h("button", { class: "group-head", "aria-expanded": String(open), onclick: toggle },
          icon(open ? "chevronDown" : "chevronRight", 14),
          g.env ? envPill(g.env, true) : null,
          h("span", { class: "group-label" }, g.env && depth > 0 ? (g.workspaces || []).join(", ") : g.env ? "" : g.label),
          h("span", { class: "muted-text" }, bits.join("  ·  "))))));
      if (!open) return;
      if (g.children) drawGroups(g.children, body, itemRow, depth + 1);
      else g.rows.forEach(itemRow);
    });
  }

  function closeTypePanel() { cp.typePanel = false; }
  function drawTypePanel() { draw(); }

  function draw() {
    if (!cp.data || !here()) return;
    const items = cp.data.items;
    const end = state.endorsements;
    const wsFilter = h("input", { class: "catalog-search small", placeholder: "Find a workspace", value: cp.wsQuery, "aria-label": "Find a workspace" });
    wsFilter.addEventListener("input", () => { cp.wsQuery = wsFilter.value; drawWorkspaces(); wsFilter.focus(); });
    const wsList = h("div", { class: "catalog-ws" });
    // Workspaces as Fabric lists them: My workspace first, then the first few, then "More workspaces...".
    const WS_SHOWN = 6;
    const drawWorkspaces = () => {
      const q = cp.wsQuery.toLowerCase();
      const all = cp.data.workspaces.filter((w) => w.type !== "Personal" && w.displayName.toLowerCase().includes(q));
      const list = q || cp.wsAll ? all : all.slice(0, WS_SHOWN);
      wsList.replaceChildren(...[
        ...list.map((w) => scopeButton(`ws:${w.id}`, w.displayName, "workspace", items.filter((r) => r.workspace_id === w.id).length)),
        !q && all.length > WS_SHOWN ? h("button", { class: "catalog-more", onclick: () => { cp.wsAll = !cp.wsAll; drawWorkspaces(); } },
          cp.wsAll ? "Fewer workspaces" : `More workspaces (${all.length - WS_SHOWN})...`) : null].filter(Boolean));
    };
    drawWorkspaces();
    const keepFocus = document.activeElement === nav.querySelector(".catalog-search");
    const mine = personal();
    nav.replaceChildren(...[
      scopeButton("all", "All items", "apps", items.length),
      mine.size ? scopeButton("mine", "My workspace", "person", items.filter((r) => mine.has(r.workspace_id)).length) : null,
      scopeButton("endorsed", "Endorsed items", "shield", end?.available ? items.filter((r) => endorsed()[key(r)]).length : "",
        !end?.available, !end ? "Checking endorsement..." : end.reason || ""),
      scopeButton("fav", "Favorites", "star", items.filter((r) => favorites().has(key(r))).length),
      h("div", { class: "sec" }, "Environments"), envChips(items),
      h("div", { class: "sec" }, "Workspaces"), wsFilter, wsList].filter(Boolean));
    if (keepFocus) wsFilter.focus();

    const rows = shown();
    // The Type filter counts what the left panel and search leave, as Fabric's does.
    const q = cp.query.toLowerCase();
    const scoped = items.filter((r) => inScope(r)
      && (!q || `${r.name} ${r.workspace_name} ${typeLabel(r.type)} ${r.description} ${tablesOf(r).join(" ")}`.toLowerCase().includes(q)));
    head.replaceChildren(...[typeFilter(scoped),
      cp.type !== "all" ? btn("Clear", "dismiss", () => { cp.type = "all"; draw(); }, "tool link small") : null,
      groupBySelect(),
      h("span", { class: "spacer" }),
      h("span", { class: "muted-text" }, `${rows.length} item${rows.length === 1 ? "" : "s"}`)].filter(Boolean));

    const sortHead = (col, label) => h("th", {}, h("button", { class: `sort${cp.sort === col ? " on" : ""}`,
      onclick: () => { cp.dir = cp.sort === col ? -cp.dir : 1; cp.sort = col; draw(); } },
    label + (cp.sort === col ? (cp.dir > 0 ? " ▲" : " ▼") : "")));
    const body = [];
    const itemRow = (r) => {
      const fav = favorites().has(key(r));
      const isLh = r.type === "Lakehouse";
      const open = isLh && cp.open.has(key(r));
      const e = endorsed()[key(r)];
      body.push(h("tr", {},
        h("td", {}, isLh ? h("button", { class: "star", title: open ? "Hide tables" : "Show tables", "aria-label": open ? "Hide tables" : "Show tables",
          onclick: () => { open ? cp.open.delete(key(r)) : cp.open.add(key(r)); draw(); } }, open ? "▾" : "▸") : ""),
        h("td", {}, h("button", { class: `star${fav ? " on" : ""}`, title: fav ? "Remove from favorites" : "Add to favorites",
          "aria-label": fav ? "Remove from favorites" : "Add to favorites", onclick: (ev) => toggleFavorite(r, ev) }, fav ? "★" : "☆")),
        h("td", {}, h("span", { class: "item" }, itemIcon(r.type),
          r.type === "Warehouse" && whOpensHere()
            ? h("button", { class: "name-link", title: "Open on the Warehouse page",
              onclick: () => openWarehouseQuery({ kind: "warehouse", workspace_id: r.workspace_id, workspace_name: r.workspace_name, item_id: r.item_id, name: r.name }) }, r.name)
            : h("button", { class: "name-link", title: "Open in Fabric", onclick: () => openInFabric(fabricItemLink(r)) }, r.name))),
        h("td", {}, typeLabel(r.type)),
        h("td", {}, h("button", { class: "name-link", title: "Open the workspace in Fabric",
          onclick: () => openInFabric(fabricItemLink({ workspace_id: r.workspace_id })) }, r.workspace_name),
        envPill(envOf(r))),
        h("td", {}, e ? h("span", { class: `pill-s ${e === "Certified" ? "pass" : "info"}` }, e) : ""),
        h("td", {}, (isLh && cp.layers[r.name]) || ""),
        h("td", { class: "actions" },
          btn("Show in Fabric", null, () => openInFabric(fabricItemLink(r)), "tool small link"),
          isLh ? btn("SQL query", null, () => openSqlQuery(r), "tool small link") : null,
          isLh ? btn("Open in notebook", null, () => newNotebook(r), "tool small link") : null)));
      if (open) {
        const tree = h("div", { class: "explorer-content", style: "padding:0" });
        body.push(h("tr", { class: "sub-row" }, h("td", {}), h("td", {}), h("td", { colspan: 6 }, tree)));
        renderTree(tree, r, null);
      }
    };
    if (cp.groupBy === "none") rows.forEach(itemRow);
    else drawGroups(groupCatalog(rows, cp.groupBy, envOf), body, itemRow);
    list.replaceChildren(rows.length ? h("table", { class: "list" },
      h("thead", {}, h("tr", {}, h("th", { style: "width:28px" }), h("th", { style: "width:32px" }),
        sortHead("name", "Name"), sortHead("type", "Type"), sortHead("ws", "Location"),
        sortHead("endorsement", "Endorsement"), sortHead("layer", "Layer"), h("th", {}))),
      h("tbody", {}, body)) : h("p", { class: "muted-text", style: "padding:16px" },
      cp.scope === "fav" && !cp.query ? "No favorites yet. Click the star next to an item." : "Nothing matches."));

    const parts = [];
    if (cp.data.skipped.length) parts.push(`Skipped ${cp.data.skipped.length} workspace${cp.data.skipped.length === 1 ? "" : "s"}: ${cp.data.skipped.join(", ")}.`);
    if (!cp.tables) parts.push("Reading table names for search...");
    note.textContent = parts.join(" ");
  }

  if (cp.data) { draw(); return; }
  rpc("meta_project").then((p) => {
    (p.layers || []).forEach((l) => { cp.layers[l.lakehouse] = l.name; });
    cp.envWs = envWorkspaces(p.environments);
    draw();
  }).catch(() => {});
  const data = await attempt(() => rpc("catalog", { all_types: true }), "Could not list the catalog");
  if (!data || !here()) return;
  cp.data = data;
  cp.tables = null;
  state.workspaces = data.workspaces;
  draw();
  const lakehouses = data.items.filter((r) => r.type === "Lakehouse")
    .map((r) => ({ workspace_id: r.workspace_id, lakehouse_id: r.item_id }));
  pollSidecar("catalog_tables", { lakehouses }, (result) => { cp.tables = result.items || {}; draw(); }, () => cp.data === data);
  if (!state.endorsements) {
    pollSidecar("endorsements", { workspace_ids: data.workspaces.map((w) => w.id) },
      (result) => { state.endorsements = result; draw(); }, () => cp.data === data);
  }
}

function openLakehouse(ref) {
  markDone("lakehouse");
  state.lakehouseView = ref;
  state.openWorkspace = { id: ref.workspace_id, displayName: ref.workspace_name };
  go("lakehouse");
}

function renderLakehouse() {
  const ref = state.lakehouseView;
  const tree = h("div", { class: "explorer-content", style: "padding:0" });
  if (itemPreview.target && itemPreview.target.ref?.lakehouse_id !== ref.lakehouse_id) itemPreview.target = null;
  const pick = itemPreviewOn() ? (p) => selectPreview({ ...p, ref }) : null;
  const draw = () => renderTree(tree, ref, null, pick);
  const box = h("div", { class: "lakehouse-tree" }, tree,
    pick ? h("p", { class: "muted-text tree-hint" }, "Click a table or file to preview it.") : null);
  setView(h("div", { class: "page" }, h("h1", {}, ref.name),
    h("p", { class: "sub" }, `Lakehouse in ${ref.workspace_name}. `, learnMore("a-lakehouse")),
    h("div", { class: "toolbar", style: "padding:0;border:0;margin-bottom:16px" },
      btn("Open in new notebook", "notebook", () => newNotebook(ref), "tool primary"),
      btn("New SQL query", "code", () => openSqlQuery(ref), "tool outline")),
    pick ? previewLayout(box, draw) : box));
  draw();
}

// Fabric's rule for lakehouse names (the engine checks the same): letters, numbers and
// underscores, starting with a letter, at most 256 characters.
function lakehouseNameProblem(name) {
  if (/^[A-Za-z][A-Za-z0-9_]{0,255}$/.test(name)) return "";
  if (name.length > 256) return "A lakehouse name can be at most 256 characters.";
  let suggestion = name.replace(/[^A-Za-z0-9_]/g, "_");
  if (!/^[A-Za-z]/.test(suggestion)) suggestion = `LH_${suggestion}`;
  return `Use only letters, numbers and underscores, starting with a letter. For example ${suggestion}.`;
}

async function newLakehouse(ws) {
  if (!(await ensureSignedIn())) return;
  if (!ws) {
    const workspaces = await loadWorkspaces();
    ws = await pickWorkspace(workspaces);
    if (!ws) return;
  }
  const name = await askText("New lakehouse", `Name (created in ${ws.displayName} with schemas enabled)`, "", "Create",
    lakehouseNameProblem);
  if (!name) return;
  toast(`Creating ${name}...`);
  const lh = await attempt(() => rpc("create_lakehouse", { workspace_id: ws.id, name }), "Could not create the lakehouse");
  if (lh) {
    toast(`Created lakehouse ${name}`);
    openLakehouse(lakehouseRef(ws, lh));
  }
  return lh ? lakehouseRef(ws, lh) : null;
}

function pickWorkspace(workspaces, { title = "Choose a workspace", selected = null, okLabel = "Next", note = null } = {}) {
  return new Promise((resolve) => {
    const select = h("select", {}, workspaces.map((w) => h("option", { value: w.id, selected: w.id === selected }, w.displayName)));
    showDialog(title, h("div", {}, note ? h("p", { class: "text" }, note) : null, h("label", { class: "field" }, "Workspace", select)), [
      btn("Cancel", null, () => { resolve(null); closeDialog(); }, "tool outline"),
      btn(okLabel, null, () => { resolve(workspaces.find((w) => w.id === select.value)); closeDialog(); }, "tool primary"),
    ], () => resolve(null));
  });
}

// ---------- Explorer tree (tables and files of a lakehouse) ----------

function treeKey(ref, part) {
  return `${ref.lakehouse_id}:${part}`;
}

async function toggleNode(key, loader, rerender) {
  const node = state.tree[key] || (state.tree[key] = { open: false });
  node.open = !node.open;
  if (node.open && !node.items) {
    node.loading = true;
    rerender();
    node.items = (await attempt(loader, "Could not load")) || null;
    node.loading = false;
    if (!node.items) node.open = false;
  }
  rerender();
}

function chevron(open) {
  return icon(open ? "chevronDown" : "chevronRight", 12);
}

// onPick({kind: "table", schema, table} | {kind: "file", path, size}): a click previews instead.
function renderTree(container, ref, onInsert, onPick = null) {
  const rerender = () => renderTree(container, ref, onInsert, onPick);
  const rows = [];
  const tablesKey = treeKey(ref, "tables");
  const tables = state.tree[tablesKey] || {};
  rows.push(h("button", { class: "tree-row", style: "padding-left:6px",
    onclick: () => toggleNode(tablesKey, () => rpc("list_tables", { workspace_id: ref.workspace_id, lakehouse_id: ref.lakehouse_id }), rerender) },
  chevron(tables.open), h("span", { class: "label" }, "Tables")));
  if (tables.open) {
    if (tables.loading) rows.push(h("div", { class: "tree-row muted", style: "padding-left:28px" }, "Loading..."));
    for (const [schema, names] of Object.entries(tables.items || {})) {
      const sKey = treeKey(ref, `schema:${schema}`);
      const sOpen = state.tree[sKey]?.open ?? true;
      rows.push(h("button", { class: "tree-row", style: "padding-left:24px",
        onclick: () => { state.tree[sKey] = { open: !sOpen }; rerender(); } },
      chevron(sOpen), h("span", { class: "label", style: "color:var(--muted)" }, schema)));
      if (sOpen) {
        if (!names.length) rows.push(h("div", { class: "tree-row muted", style: "padding-left:46px" }, "No tables"));
        names.forEach((t) => {
          const pick = { kind: "table", schema, table: t };
          rows.push(h("button", {
            class: `tree-row${onPick && isPreviewed({ ...pick, ref }) ? " selected" : ""}`, style: "padding-left:44px",
            title: onInsert ? "Click to add a cell that loads this table. Drag it into a cell, or right-click for other ways to load it." : onPick ? `Preview ${schema}.${t}` : `${schema}.${t}`,
            onclick: () => onInsert ? onInsert(`df = lakehouse.read_table(${JSON.stringify(`${schema}.${t}`)})\ndf`) : onPick && onPick(pick),
            ...(onInsert ? dataItemAttrs(pick) : {}),  // nbdrag.js
          }, icon("table", 15), h("span", { class: "label" }, t)));
        });
      }
    }
  }
  rows.push(...fileRows(ref, "Files", 6, onInsert, rerender, onPick));
  container.replaceChildren(...rows);
}

function fileRows(ref, path, indent, onInsert, rerender, onPick = null) {
  const key = treeKey(ref, `files:${path}`);
  const node = state.tree[key] || {};
  const isRoot = path === "Files";
  const rows = [h("button", { class: "tree-row", style: `padding-left:${indent}px;${isRoot ? "margin-top:4px" : ""}`,
    onclick: () => toggleNode(key, () => rpc("list_files", { workspace_id: ref.workspace_id, lakehouse_id: ref.lakehouse_id, directory: path }), rerender) },
  chevron(node.open), isRoot ? null : icon("folder", 15), h("span", { class: "label" }, isRoot ? "Files" : path.split("/").pop()))];
  if (!node.open) return rows;
  if (node.loading) rows.push(h("div", { class: "tree-row muted", style: `padding-left:${indent + 22}px` }, "Loading..."));
  if (node.items && !node.items.length) rows.push(h("div", { class: "tree-row muted", style: `padding-left:${indent + 22}px` }, "Empty"));
  for (const item of node.items || []) {
    if (item.is_dir) {
      rows.push(...fileRows(ref, item.path, indent + 20, onInsert, rerender, onPick));
    } else {
      const pick = { kind: "file", path: item.path, size: item.size };
      rows.push(h("button", { class: `tree-row${onPick && isPreviewed({ ...pick, ref }) ? " selected" : ""}`, style: `padding-left:${indent + 38}px`,
        title: onInsert ? "Click to add a cell that reads this file. Drag it into a cell, or right-click for other ways to load it." : onPick ? `Preview ${item.path}` : item.path,
        onclick: () => onInsert ? onInsert(readSnippet(item.path)) : onPick && onPick(pick),
        ...(onInsert ? dataItemAttrs(pick) : {}) },  // nbdrag.js
      icon("file", 15), h("span", { class: "label" }, item.name), h("span", { class: "hint" }, item.size ? sizeText(item.size) : "")));
    }
  }
  return rows;
}

function readSnippet(path) {
  const ext = path.split(".").pop().toLowerCase();
  const reader = { parquet: "read_parquet", json: "read_json" }[ext] || "read_csv";
  return `df = lakehouse.${reader}(${JSON.stringify(path)})\ndf`;
}

// File and data sizes, the same on every page (#929): 1024-based, with one decimal below 100
// (17 B, 2.0 KB, 99.5 MB, 212 MB, 1.1 GB). Nothing for a missing or negative size.
function sizeText(n) {
  if (n == null || n === "") return "";
  n = Number(n);
  if (!Number.isFinite(n) || n < 0) return "";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  if (!i) return `${Math.round(n)} B`;
  let v = +n.toFixed(n >= 100 ? 0 : 1);
  if (v >= 1024 && i < units.length - 1) { v = +(v / 1024).toFixed(1); i++; }  // 1023.97 KB reads 1.0 MB
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${units[i]}`;
}

// ---------- Notebook files ----------

// A cell's stable id, saved in the notebook (nbformat 4.5): copies get a new one, the cell keeps
// it through edits, moves and undoing a delete. `id` is only this screen's handle.
function newCellId() {
  return (crypto.randomUUID?.() || `${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`).replace(/-/g, "").slice(0, 12);
}

// A notebook saved on Windows can hold \r\n line ends. The editor counts \n as one character, so a
// cell keeps \n only: cursor positions (split, run selection) then line up with its text.
function cellFrom(c) {
  return { id: nextCellId++, cell_id: c.cell_id || newCellId(), type: c.type, source: (c.source || "").replace(/\r\n?/g, "\n"), meta: c.meta || {}, outputs: [], status: null, count: null, editing: !c.source };
}

function loadIntoState(nb) {
  disposeEditors();
  state.variables = null;
  const kept = openNotebooks.get(nb.name);
  if (kept && nbRunning(kept)) {  // still running in its session: show it as it is
    state.notebook = kept;
  } else {
    state.notebook = { name: nb.name, cells: nb.cells.map(cellFrom), lakehouse: nb.lakehouse || null, place: nb.place || null, locations: nb.locations || null, dirty: false,
      compute: nb.compute || "default", workspace: nb.workspace || null, check: nb.check || null, replay: nb.replay || null,
      onelake_writes: nb.onelake_writes || null, diskStamp: nb.disk_stamp || null };
    if (!state.notebook.cells.length) state.notebook.cells.push(cellFrom({ type: "code" }));
    state.notebook.cells.forEach((c) => { if (c.type === "markdown" && c.source) c.editing = false; });
    if (kept) keepOutputs(kept, state.notebook);
  }
  rememberNotebook(state.notebook);
  state.selected = state.notebook.cells[0].id;
  state.ribbonTab = "home";
}

function nbRunning(nb) {
  return nb.kernelStatus === "busy" || nb.cells.some((c) => c.status === "running" || c.status === "queued");
}

// Reopened from disk: cells whose source is unchanged keep the outputs they had, as the session
// that made them (and its variables) is still there.
function keepOutputs(from, to) {
  const before = new Map(from.cells.filter((c) => c.cell_id).map((c) => [c.cell_id, c]));
  for (const c of to.cells) {
    const old = before.get(c.cell_id);
    if (!old || old.source !== c.source) continue;
    Object.assign(c, { outputs: old.outputs, count: old.count, status: old.status, seconds: old.seconds, finishedAt: old.finishedAt });
  }
  to.kernelStatus = from.kernelStatus;
  if (from.debug) to.debug = from.debug;
}

function rememberNotebook(nb) {
  openNotebooks.delete(nb.name);
  openNotebooks.set(nb.name, nb);
  for (const [name, other] of openNotebooks) {
    if (openNotebooks.size <= KEPT_NOTEBOOKS) break;
    if (other !== state.notebook && !nbRunning(other)) openNotebooks.delete(name);
  }
}

async function openNotebook(name) {
  if (state.notebook?.dirty && !(await saveNotebook())) return;
  const nb = await attempt(() => rpc("open_notebook", { name }), "Could not open the notebook");
  if (!nb) return;
  loadIntoState(nb);
  go("notebook");
}

// ``into`` ({folder, place}): a new notebook in that folder of that place (All notebooks > a folder).
async function newNotebook(lakehouse, into = null) {
  if (state.notebook?.dirty && !(await saveNotebook())) return;
  const place = into ? into.place : await nbChoosePlace();  // only asks when there are notebooks repositories
  if (place === undefined) return;
  const nb = await attempt(() => rpc("new_notebook", into?.folder ? { place, folder: into.folder } : { place }), "Could not create a notebook");
  if (!nb) return;
  closeDialog();
  loadIntoState(nb);
  const ref = lakehouse && lakehouse.lakehouse_id ? lakehouse : null;
  if (ref) {
    state.notebook.lakehouse = ref;
    state.notebook.cells[0].source = "import polars as pl\nfrom fabric_desktop import lakehouse\n";
    state.notebook.cells[0].editing = true;
    markDirty();
  }
  go("notebook");
}

function notebookPayload() {
  return { name: state.notebook.name, cells: state.notebook.cells.map((c) => ({ type: c.type, source: c.source, meta: c.meta, cell_id: c.cell_id })),
    lakehouse_ref: state.notebook.lakehouse, compute: state.notebook.compute, workspace: state.notebook.workspace };
}

// For publishing and Fabric runs: also the repository location's lakehouse, which Fabric gets
// when the notebook has none of its own. It isn't saved into the notebook.
function publishPayload() {
  const data = nbData(state.notebook);
  return { ...notebookPayload(), location_ref: data.from === "locations" ? data.ref : null };
}

// True when the notebook is saved (or there is none). On a failure the error toast stays, the
// notebook stays dirty, and callers that would close or replace it stop.
async function saveNotebook() {
  if (!state.notebook) return true;
  clearTimeout(saveTimer);
  const nb = state.notebook;
  const ok = await attempt(() => rpc("save_notebook", { ...notebookPayload(), disk_stamp: nb.diskStamp || null }), "Could not save");
  if (!ok) return false;
  if (ok.changed_on_disk) {
    nb.diskConflict = true;  // autosave waits until the person decides
    nbDiskConflict(nb, ok);
    return false;
  }
  nb.diskStamp = ok.disk_stamp || null;
  nb.diskConflict = false;
  if (state.notebook === nb) {
    nb.dirty = false;
    if (ok.check) setComputeCheck(ok.check);
    renderChrome();
  }
  return true;
}

// An explicit save (the Save button, Ctrl+S) gets a Forge Spark once it is written, at most one per
// SAVE_FX_MS; autosave and the saves other flows make first (publish, run in Fabric, close) never do.
const SAVE_FX_MS = 5000;
let saveFxAt = -Infinity;
async function saveExplicitly(anchor) {
  const ok = await saveNotebook();
  const el = ok ? anchor() : null;
  if (el && Date.now() - saveFxAt >= SAVE_FX_MS && forgeEvent("notebook.saved", el)) saveFxAt = Date.now();
  return ok;
}

function markDirty() {
  if (!state.notebook) return;
  const wasDirty = state.notebook.dirty;
  state.notebook.dirty = true;
  if (!wasDirty) renderChrome();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => { if (state.kernel !== "busy" && !state.notebook?.diskConflict) saveNotebook(); }, 1500);
}

// Another program (VS Code, a Git pull, a sync tool) saved the notebook's file after the app read
// it. Nothing was saved over it, and the engine kept both sides under Versions first (the file's
// version, or a byte copy when it isn't a notebook any more, and the cells on screen as "not saved"),
// so either choice, or closing the app without one, loses nothing.
function nbDiskConflict(nb, out) {
  if (nb.diskConflictOpen) return;
  nb.diskConflictOpen = true;
  const running = nbRunning(nb);
  const done = () => { nb.diskConflictOpen = false; closeDialog(); };
  const load = async () => {
    if (nbRunning(nb)) return;  // the screen can't be replaced under a running cell
    done();
    const fresh = await attempt(() => rpc("open_notebook", { name: nb.name }), "Could not open the notebook");
    if (!fresh || state.notebook !== nb) return;
    nb.dirty = false;
    loadIntoState(fresh);
    if (state.view === "notebook") renderNotebook();
    toast(`Loaded ${fresh.name} as it is on disk. Your edits here are under Versions, as Not saved.`);
  };
  const keep = async () => {
    done();
    nb.diskStamp = out.disk_stamp;
    nb.diskConflict = false;
    if (await saveNotebook()) toast(`Saved ${nb.name}. The other version is ${out.unreadable ? "copied beside its versions" : "under Versions"}.`);
  };
  const text = out.unreadable
    ? [`${nb.name} was changed by another program and isn't a notebook KratosForge can read any more, for example a Git merge left conflict markers in it. `
      + "Nothing was saved over it. A copy of the file is kept, and your edits here are under Versions.",
      out.outside_copy ? h("p", { class: "muted-text small" }, `Copy: ${out.outside_copy}`) : null]
    : [`${nb.name} was saved by another program, such as VS Code or a Git pull, after you opened it here. `
      + "Nothing was saved over it. Its version and yours are both kept under Versions."];
  const loadBtn = out.unreadable ? null : btn("Load its version", null, load, "tool primary");
  if (loadBtn && running) loadBtn.disabled = true;
  showDialog("This notebook changed outside KratosForge",
    h("div", {}, h("p", { class: "text" }, text[0]), text[1] || null,
      running && loadBtn ? h("p", { class: "muted-text small" }, "A cell is running. Stop it to load the other version.") : null),
    [btn("Keep mine", null, keep, loadBtn ? "tool outline" : "tool primary"), loadBtn].filter(Boolean),
    () => { nb.diskConflictOpen = false; });
}

async function renameNotebook() {
  const nb = state.notebook;
  const name = await askText("Rename notebook", "Name", nb.name, "Rename");
  if (!name || name === nb.name) return;
  if (!(await saveNotebook())) return;
  const ok = await attempt(() => rpc("rename_notebook", { name: nb.name, new_name: name }), "Could not rename");
  if (ok) {
    openNotebooks.delete(nb.name);
    nb.name = name;
    openNotebooks.set(name, nb);
    renderChrome();
  }
}

// ---------- Publish to OneLake ----------

// Each notebook remembers the workspace it is published to (saved in the .ipynb). The first
// Publish asks for it, starting from the attached lakehouse's workspace; later ones reuse it.
// Run in Fabric and schedules use the same workspace.
function publishWorkspace(nb) {
  if (nb?.workspace?.workspace_id) return nb.workspace;
  const lh = nbLakehouse(nb);
  if (lh) return { workspace_id: lh.workspace_id, workspace_name: lh.workspace_name };
  return null;
}

function publishWorkspaceName(nb) {
  return publishWorkspace(nb)?.workspace_name || "the workspace";
}

async function chooseNotebookWorkspace(nb, okLabel = "Save") {
  const workspaces = await loadWorkspaces();
  if (!workspaces.length) return null;
  const current = publishWorkspace(nb);
  const lh = nb.lakehouse ? ` Its lakehouse, ${nb.lakehouse.name}, stays attached wherever it is published.` :
    nbLakehouse(nb) ? ` It uses ${nbLakehouse(nb).name}, its ${nbData(nb).envLabel} location.` : "";
  const ws = await pickWorkspace(workspaces, { title: `Publish ${nb.name} to`, selected: current?.workspace_id, okLabel,
    note: `The notebook is created or updated as a Fabric notebook item in this workspace. Publish, Run in Fabric and schedules keep using it until you change it here.${lh}` });
  if (!ws || state.notebook !== nb) return null;
  nb.workspace = { workspace_id: ws.id, workspace_name: ws.displayName };
  await saveNotebook();
  if (state.view === "notebook") renderNotebook();
  return nb.workspace;
}

function publishMenu(nb) {
  const ws = nb.workspace?.workspace_id ? nb.workspace.workspace_name : null;
  return [
    { info: ws ? `Publishes to ${ws}.` : "Publish asks which workspace to use the first time." },
    { label: ws ? `Publish to ${ws}` : "Publish...", icon: "publish", action: publishNotebook },
    { label: "Change workspace...", icon: "folder", action: () => ensureSignedIn().then((ok) => ok && chooseNotebookWorkspace(nb)) },
  ];
}

async function publishNotebook() {
  const nb = state.notebook;
  if (!(await ensureSignedIn())) return;
  if (!nb.workspace?.workspace_id && !(await chooseNotebookWorkspace(nb, "Publish"))) return;
  const ws = nb.workspace.workspace_name;
  const gate = await nbFabricChangeGate(nb);  // someone changed it in Fabric: they decide first
  if (!gate) return;
  toast(`Publishing ${nb.name} to ${ws}...`);
  let failed = false;
  const result = await attempt(() => rpc("publish_notebook", { ...publishPayload(), replace_fabric_changes: gate.replace })
    .catch((err) => { failed = !friendlyError(err).cancelled; throw err; }), "Publish failed");
  if (failed) forgeEvent("publish.failed", document.getElementById("toast"));  // an ember beside the error (motion.js)
  if (result) {
    nb.dirty = false;
    renderChrome();
    toast(`Published ${nb.name} to ${ws} as a Fabric notebook item.${result.note ? ` ${result.note}` : ""}`);
    // Fabric gave back the item it saved, so the publish is confirmed: the toast cools (motion.js).
    if (result.id) forgeEvent("publish.verified", document.getElementById("toast"));
  }
}

// ---------- Where a notebook runs: this PC or Fabric ----------

// Each notebook has a "Runs on" choice: This PC, Fabric, or the default, which follows where the
// environment last picked on the Data mesh pages runs (Data mesh > Environments). Run all goes there. Fabric
// runs the whole published notebook, so single cells only run on this PC.
function nbWhere(nb) {
  const choice = nb?.compute || "default";
  const ws = nbLakehouse(nb) ? publishWorkspace(nb).workspace_name : null;
  const envFabric = envWhere(state.meta?.project, state.meta?.env).fabric;
  const fabric = choice === "fabric" || (choice === "default" && envFabric);
  const short = fabric ? (ws ? `Fabric · ${ws}` : "Fabric") : "This PC";
  return { fabric, choice, envFabric, short };
}

function computeMenu(nb) {
  const where = nbWhere(nb);
  const env = ENV_LABEL[state.meta?.env] || "Dev";
  const option = (value, label, iconName) => ({ label, icon: where.choice === value ? "ok" : iconName,
    action: () => setNotebookCompute(value) });
  return [
    { info: "Where Run all runs this notebook." },
    option("local", "This PC (no Fabric job)", "desktop"),
    previewShown() || where.choice === "fabric"
      ? option("fabric", `Fabric${nbLakehouse(nb) ? ` (${publishWorkspaceName(nb)})` : " (attach a lakehouse first)"} · Preview`, "cloud") : null,
    option("default", `Same as ${env} (${where.envFabric ? "Fabric" : "this PC"})`, "sync"),
    { info: `Python ${state.python || "3"} on this PC. Fabric runs publish the notebook first.` },
    ...(where.fabric ? [] : oneLakeWritesMenu(nb)),
    { label: "Restart kernel", icon: "restart", action: restartKernel },
  ].filter(Boolean);
}

// How this notebook's cells on this PC write lakehouse tables (lakehouse.write_table): its own choice,
// kept in the notebook file, or the Settings default.
function oneLakeWritesMenu(nb) {
  const fallback = state.settings?.onelake_writes?.value || "local_first";
  const label = (mode) => (ONELAKE_WRITES.find(([v]) => v === mode) || [])[1] || mode;
  const option = (value, text, iconName) => ({ label: text, icon: (nb.onelake_writes || null) === value ? "ok" : iconName,
    action: () => setNotebookOneLakeWrites(value) });
  return [
    { info: "Table writes to OneLake from this PC." },
    option("local_first", "Write here, then upload in the background", "save"),
    option("direct", "Straight to OneLake (fast upload line)", "cloud"),
    option(null, `Same as Settings (${label(fallback)})`, "sync"),
    { label: "Open the folder of local writes", icon: "folderOpen", action: () => openLocalWrites() },
  ];
}

// Where writes kept on this PC live (settings.js, local_writes_service.py): to look, or to clear up after a failed run.
async function openLocalWrites(folder) {
  const res = await attempt(() => rpc("local_writes_reveal", folder ? { folder } : {}), "Couldn't open the folder");
  if (res) toast(`Opened ${res.shown} in File Explorer.`);
}

async function setNotebookOneLakeWrites(mode) {
  const nb = state.notebook;
  if (nb.dirty && !(await saveNotebook())) return;
  const res = await attempt(() => rpc("notebook_onelake_writes_set", { name: nb.name, mode }), "Not saved");
  if (!res) return;
  nb.onelake_writes = res.onelake_writes;
  toast(res.effective === "direct" ? `${nb.name} now writes straight to OneLake. Each write returns once it's uploaded.`
    : `${nb.name} now writes here first. Uploads run in the background and each cell ends when its writes are in OneLake.`);
}

async function setNotebookCompute(value) {
  const nb = state.notebook;
  const before = nbWhere(nb).fabric;
  nb.compute = value;
  const saved = await saveNotebook();
  if (state.notebook !== nb) return;
  const now = nbWhere(nb);
  if (saved && now.fabric !== before) toast(now.fabric ? `${nb.name} now runs in Fabric. Run all publishes it and starts it there.`
    : `${nb.name} now runs on this PC.`);
  renderNotebook();
}

// After a save: whether the notebook can still run where it is set to. Only the note is redrawn,
// so typing in a cell isn't interrupted.
function setComputeCheck(check) {
  const nb = state.notebook;
  const changed = JSON.stringify(nb.check) !== JSON.stringify(check);
  nb.check = check;
  if (!changed || state.view !== "notebook") return;
  const old = document.querySelector(".compute-note-slot");
  if (old) old.replaceWith(computeNote(nb));
}

function computeReasons(reasons) {
  return h("ul", { class: "gate-list" }, reasons.map((r) => h("li", {},
    r.cell ? [`Cell ${r.cell} ${r.text}`, r.code ? [": ", h("code", {}, r.code)] : null] : `The notebook ${r.text}`)));
}

// A note at the top of the notebook when it can't run where it is set to, or how a Fabric run works.
function computeNote(nb) {
  const slot = h("div", { class: "compute-note-slot" });
  const where = nbWhere(nb);
  const check = nb.check;
  const here = check?.[where.fabric ? "fabric" : "local"];
  const other = check?.[where.fabric ? "local" : "fabric"];
  if (here?.reasons?.length) {  // Unknown cells (here.unknown) aren't shown as blockers
    const switchTo = where.fabric ? "local" : "fabric";
    slot.append(h("div", { class: "problems" },
      h("div", { class: "row" }, icon("warning", 16),
        h("b", { class: "grow" }, where.fabric ? "This notebook can't run in Fabric yet" : "This notebook only runs in Fabric"),
        other?.ok ? btn(where.fabric ? "Run on this PC" : "Run in Fabric", where.fabric ? "desktop" : "cloud",
          () => setNotebookCompute(switchTo), "tool small") : null),
      computeReasons(here.reasons)));
  } else if (where.fabric) {
    const run = nb.fabricRun;
    slot.append(h("div", { class: "callout compute-callout" }, icon("cloud", 18),
      h("span", { class: "grow" }, `Run all publishes this notebook and runs it on ${publishWorkspaceName(nb)}'s Fabric capacity. `,
        "Cell output shows in Fabric, and the bell tells you when it finishes."),
      run ? h("a", { href: "#", onclick: (e) => { e.preventDefault(); openInFabric(run.url); } },
        `Last run: ${run.status === "NotStarted" ? "starting" : run.status}`) : null));
  }
  return slot;
}

// ---------- Run in Fabric and schedules ----------

// The notebook's status bar while its run in Fabric goes: the navy wolf trotting in place (motion.js
// remoteProgress; no fill or percentage, since Fabric reports none), then how the last run ended.
function nbFabricRunStatus(nb) {
  const run = nb.fabricRun;
  if (!run) return null;
  const word = run.status === "NotStarted" ? "starting" : run.status;
  if (FABRIC_DONE.has(run.status)) return h("span", { class: "nb-fabric-last" }, `Last Fabric run: ${word}`);
  return h("span", { class: "nb-fabric-run" }, remoteProgress(`${nb.name} running in Fabric`, `Running in Fabric: ${word}`));
}

// The same notebook, run on the workspace's Fabric capacity: published first, then started
// with Fabric's job scheduler. It shows in Monitor (both lists) while it runs.
const FABRIC_DONE = new Set(["Completed", "Failed", "Cancelled", "Deduped"]);

async function runInFabric() {
  const nb = state.notebook;
  if (!nbLakehouse(nb)) {
    toast("Attach a lakehouse first. The notebook runs in that lakehouse's workspace.", true);
    return;
  }
  if (!(await ensureSignedIn())) return;
  // The risk check (FINOPS-004) asks only when something should stop or slow the run. A notebook
  // not yet linked to its Fabric item can't be checked and runs as before.
  if (!(await riskGate({ id: `notebook:${nb.name}`, action: "run_in_fabric", title: `Run in Fabric ${nb.name}?`,
    runLabel: "Run in Fabric", alt: { label: "Run on this PC", run: () => runCells(nb.cells) } }))) return;
  const gate = await nbFabricChangeGate(nb);
  if (!gate) return;
  await nbInputsSaved();  // nbinputs.js: the run gets the values the Parameters bar shows
  toast(`Publishing ${nb.name} and starting it in ${publishWorkspaceName(nb)}...`);
  const run = await attempt(() => rpc("run_notebook_in_fabric", { ...publishPayload(), replace_fabric_changes: gate.replace }), "Could not start the run in Fabric");
  if (!run) return;
  nb.dirty = false;
  nb.fabricRun = run;
  renderChrome();
  if (state.view === "notebook") renderNotebook();
  toast(`${nb.name} is running in Fabric. The bell follows it until it finishes.`);
  if (state.view === "notebook" && state.notebook === nb) forgeEvent("fabric.run.started", document.querySelector("button[data-run]"));
  logActivity({ id: `fabric:${run.job_id}`, status: "running", title: `${nb.name} · Notebook run`, text: `In Fabric on ${run.workspace_name || publishWorkspaceName(nb)}`, url: run.url, monitor: true });
  trackFabricJobs();
  watchFabricRun(nb, run);
}

// How often a run in Fabric is read again while its notebook is open (a test shortens it).
let fabricWatchMs = 10000;
async function watchFabricRun(nb, run) {
  for (let i = 0; i < 360; i++) {  // up to an hour while the app is open
    await new Promise((r) => setTimeout(r, fabricWatchMs));
    const runs = await rpc("fabric_runs", { limit: 50 }).catch(() => null);
    const now = runs?.find((r) => r.job_id === run.job_id);
    if (!now) continue;
    nb.fabricRun = now;
    const here = state.view === "notebook" && state.notebook === nb;
    if (here) renderNotebook();
    if (FABRIC_DONE.has(now.status)) {
      // The activity tray reports how it ended; the status bar glows once on success or shows the
      // ember on failure (motion.js). Cancelled and deduplicated runs play nothing.
      const last = here && document.querySelector(".statusbar .nb-fabric-last");
      if (last && now.status === "Completed") forgeEvent("fabric.run.succeeded", last);
      if (last && now.status === "Failed") forgeEvent("fabric.run.failed", last);
      return;
    }
  }
}

// Windows time zone ids, which Fabric schedules use, for the zones people pick most.
const TIME_ZONES = {
  "UTC": "UTC", "America/New_York": "Eastern Standard Time", "America/Chicago": "Central Standard Time",
  "America/Denver": "Mountain Standard Time", "America/Phoenix": "US Mountain Standard Time",
  "America/Los_Angeles": "Pacific Standard Time", "Europe/London": "GMT Standard Time",
  "Europe/Paris": "Romance Standard Time", "Europe/Berlin": "W. Europe Standard Time",
  "Asia/Kolkata": "India Standard Time", "Asia/Singapore": "Singapore Standard Time",
  "Australia/Sydney": "AUS Eastern Standard Time",
};
// Every Windows time zone and this PC's, read once from the registry (PB-048). Off Windows, or when the
// engine can't read them, the short list above stays; a failed read is tried again next time.
let windowsTimeZones = null;
function loadTimeZones() {
  windowsTimeZones ||= rpc("time_zones").catch(() => { windowsTimeZones = null; return { zones: [], here: null }; });
  return windowsTimeZones;
}
const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

// The repeat, times, days and time zone fields of a Fabric schedule, shared by notebooks and metadata runs.
function scheduleForm() {
  const kind = h("select", {}, h("option", { value: "daily" }, "Every day"), h("option", { value: "weekly" }, "Some days of the week"),
    h("option", { value: "cron" }, "Every few minutes"));
  const times = h("input", { value: "06:00", placeholder: "06:00, 18:00" });
  const minutes = h("input", { type: "number", min: "1", value: "60" });
  const days = WEEKDAYS.map((d, i) => h("label", { class: "check" }, h("input", { type: "checkbox", value: d, checked: i < 5 ? true : null }), d.slice(0, 3)));
  const here = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const zone = h("select", { size: 8, "aria-label": "Time zone" });
  const search = h("input", { type: "search", placeholder: "Search time zones", "aria-label": "Search time zones" });
  let chosen = null;  // a zone set by set() or picked; until then the PC's own zone
  // Rebuild the options from [{id, name}], the PC's zone first, keeping the chosen zone even when the list lacks it.
  const fill = (zones, pcZone) => {
    const pick = chosen || pcZone || zone.value;
    const label = (z) => (z.name === z.id ? z.id : `${z.name} · ${z.id}`);
    const pc = zones.find((z) => z.id === pcZone);
    const other = chosen && !zones.some((z) => z.id === chosen) ? [{ id: chosen, name: chosen }] : [];
    zone.replaceChildren(
      pc ? h("optgroup", { label: "On this PC" }, h("option", { value: pc.id }, label(pc))) : null,
      h("optgroup", { label: "All time zones" }, [...other, ...zones.filter((z) => z !== pc)].map((z) => h("option", { value: z.id }, label(z)))));
    zone.value = pick;
    if (!zone.value && zone.options.length) zone.selectedIndex = 0;
    filter();
  };
  // Show the zones matching every word typed; if the chosen one is filtered out, pick the first match.
  const filter = () => {
    const words = search.value.toLowerCase().split(/\s+/).filter(Boolean);
    for (const o of zone.options) o.hidden = !words.every((w) => o.textContent.toLowerCase().includes(w));
    for (const g of zone.querySelectorAll("optgroup")) g.hidden = ![...g.children].some((o) => !o.hidden);
    if (zone.selectedOptions[0]?.hidden) {
      const first = [...zone.options].find((o) => !o.hidden);
      if (first) { zone.value = first.value; chosen = first.value; zone.dispatchEvent(new Event("change", { bubbles: true })); }
    }
  };
  search.addEventListener("input", filter);
  zone.addEventListener("change", () => { chosen = zone.value; });
  fill(Object.entries(TIME_ZONES).map(([iana, win]) => ({ id: win, name: iana === "UTC" ? win : `${win} (${iana})` })), TIME_ZONES[here]);
  loadTimeZones().then((r) => { if (r?.zones?.length) fill(r.zones, r.here); });
  const timesField = h("label", { class: "field" }, "Times of day (24-hour, comma between)", times);
  const minutesField = h("label", { class: "field", hidden: true }, "Minutes between runs", minutes);
  const daysField = h("div", { class: "field", hidden: true }, "Days", h("div", { class: "row wrap" }, days));
  const zoneField = h("div", { class: "field zone-field" }, h("span", {}, "Time zone"), search, zone);
  const sync = () => {
    minutesField.hidden = kind.value !== "cron";
    timesField.hidden = zoneField.hidden = kind.value === "cron";
    daysField.hidden = kind.value !== "weekly";
  };
  kind.addEventListener("change", sync);
  return {
    fields: [h("label", { class: "field" }, "Repeat", kind), timesField, daysField, minutesField, zoneField],
    sync,
    value: () => ({ kind: kind.value, times: times.value.split(","), every_minutes: Number(minutes.value),
      weekdays: days.map((l) => l.firstChild).filter((c) => c.checked).map((c) => c.value), time_zone: zone.value }),
    // Fill the form with a stored schedule (scheduleValues), to edit it (PB-048).
    set: (v) => {
      kind.value = v.kind;
      times.value = v.times.length ? v.times.join(", ") : "06:00";
      minutes.value = String(v.every_minutes || 60);
      days.forEach((l) => { l.firstChild.checked = v.weekdays.includes(l.firstChild.value); });
      if (v.time_zone) {
        chosen = v.time_zone;
        if (![...zone.options].some((o) => o.value === v.time_zone)) zone.lastChild.prepend(h("option", { value: v.time_zone }, v.time_zone));
        zone.value = v.time_zone;
      }
      sync();
    },
  };
}

async function scheduleNotebook() {
  const nb = state.notebook;
  // Where it runs by default: the notebook's own choice, else the environment's compute.
  const fabricDefault = nb.compute === "fabric" || (nb.compute !== "local" && state.meta?.project?.environments?.[state.meta?.env]?.compute === "fabric");
  openScheduleDialog({
    title: `Schedule ${nb.name}`, what: nb.name,
    mode: fabricDefault && nbLakehouse(nb) ? "fabric" : "pc",
    pc: { target: { kind: "notebook", name: nb.name }, query: { kind: "notebook", name: nb.name },
          intro: "This PC runs the saved notebook in a fresh kernel, every code cell in order except frozen ones, with its attached lakehouse. Save your changes first." },
    fabric: {
      unavailable: nbLakehouse(nb) ? "" : "Attach a lakehouse first. Fabric runs the notebook in that lakehouse's workspace.",
      intro: `Fabric runs the published notebook in ${publishWorkspaceName(nb)} on its own capacity, even when this PC is off. Adding a schedule publishes what is on screen first.`,
      adding: `Publishing ${nb.name} and adding the schedule...`,
      added: "Scheduled. Fabric runs the published notebook on its own capacity.",
      list: () => rpc("notebook_schedules", { name: nb.name, lakehouse_ref: nbLakehouse(nb), workspace: nb.workspace }),
      add: async (schedule) => {
        if (!(await saveNotebook())) return null;  // never publishes over a file changed outside the app
        const res = await rpc("add_notebook_schedule", { ...publishPayload(), schedule });
        nb.dirty = false; renderChrome();
        return res;
      },
      remove: (id) => rpc("delete_notebook_schedule", { name: nb.name, lakehouse_ref: nbLakehouse(nb), workspace: nb.workspace, schedule_id: id }),
    },
  });
}

// ---------- Add data items ----------

// Lakehouses across every workspace, like Fabric's OneLake catalog. Favorites are stars kept on
// this PC (Fabric has no public favorites API); endorsement comes from Fabric's admin scan when
// the account may read it.
async function openDataItems() {
  if (!(await ensureSignedIn())) return;
  const pick = { scope: "all", query: "", selected: null, catalog: null, layers: {} };
  const nav = h("nav", { class: "catalog-nav" });
  const list = h("div", { class: "catalog-list" }, loading("Loading lakehouses from all workspaces..."));
  const head = h("div", { class: "catalog-head" });
  const detail = h("aside", { class: "catalog-detail" });
  const note = h("span", { class: "muted-text" });
  const search = h("input", { class: "catalog-search", placeholder: "Filter by name or workspace", "aria-label": "Filter lakehouses" });
  const attachBtn = btn("Attach", null, () => pick.selected && attachLakehouse(pick.selected), "tool primary", { disabled: true });
  const newBtn = btn("New lakehouse", "add", async () => {
    const ws = pick.scope.startsWith("ws:") ? workspaceById(pick.scope.slice(3))
      : pick.selected ? workspaceById(pick.selected.workspace_id) : null;
    closeDialog();
    const view = state.view;
    const ref = await newLakehouse(ws);
    if (ref) { state.view = view; attachLakehouse(ref); }
  }, "tool link");
  const workspaceById = (id) => pick.catalog?.workspaces.find((w) => w.id === id) || null;
  const key = (r) => r.item_id;
  const favorites = () => new Set(pick.catalog?.favorites || []);
  const endorsed = () => state.endorsements?.items || {};

  const inScope = (r) => pick.scope === "all" ? true
    : pick.scope === "fav" ? favorites().has(key(r))
    : pick.scope === "endorsed" ? !!endorsed()[key(r)]
    : r.workspace_id === pick.scope.slice(3);
  const shown = () => (pick.catalog?.items || []).filter((r) => inScope(r)
    && `${r.name} ${r.workspace_name}`.toLowerCase().includes(pick.query.toLowerCase()));

  const navButton = (scope, label, glyph, count, disabled, title) => h("button", {
    class: `catalog-scope${pick.scope === scope ? " on" : ""}`, disabled, title,
    onclick: () => { pick.scope = scope; draw(); },
  }, h("span", { class: "glyph" }, glyph), h("span", { class: "label" }, label), h("span", { class: "count" }, count));

  const endorsementPill = (r) => {
    const e = endorsed()[key(r)];
    return e ? h("span", { class: `pill-s ${e === "Certified" ? "pass" : "info"}` }, e) : "";
  };

  async function toggleFavorite(r, e) {
    e.stopPropagation();
    const on = !favorites().has(key(r));
    const result = await attempt(() => rpc("set_favorite", { lakehouse_id: key(r), favorite: on }), "Could not save the favorite");
    if (result) { pick.catalog.favorites = result; draw(); }
  }

  function select(r, row) {
    pick.selected = r;
    // Mark the row in place: redrawing the list would swallow the second click of a double-click.
    list.querySelectorAll("tr.selected").forEach((tr) => tr.classList.remove("selected"));
    row?.classList.add("selected");
    attachBtn.disabled = false;
    const tree = h("div", { class: "explorer-content", style: "padding:0" });
    detail.replaceChildren(h("div", { class: "n" }, r.name), h("div", { class: "muted-text" }, `${r.workspace_name} workspace`),
      endorsementPill(r), h("div", { class: "sec" }, "Contents"), tree);
    renderTree(tree, r, null);
  }

  function draw() {
    const c = pick.catalog;
    if (!c) return;
    const items = c.items;
    const end = state.endorsements;
    const endorsedTitle = !end || end.pending ? "Checking endorsement..." : end.available ? "" : end.reason;
    nav.replaceChildren(
      navButton("all", "All items", "▦", items.length),
      navButton("fav", "Favorites", "★", items.filter((r) => favorites().has(key(r))).length),
      navButton("endorsed", "Endorsed", "✓", end?.available ? items.filter((r) => endorsed()[key(r)]).length : "", !end?.available, endorsedTitle),
      h("div", { class: "sec" }, "Workspaces"),
      ...c.workspaces.map((w) => navButton(`ws:${w.id}`, w.displayName, "▢", items.filter((r) => r.workspace_id === w.id).length)));
    const rows = shown();
    const title = { all: "All items", fav: "Favorites", endorsed: "Endorsed" }[pick.scope] || workspaceById(pick.scope.slice(3))?.displayName;
    head.replaceChildren(h("span", { class: "t" }, title), h("span", { class: "muted-text" }, `${rows.length} lakehouse${rows.length === 1 ? "" : "s"}`));
    const attachedId = state.notebook?.lakehouse?.lakehouse_id;
    list.replaceChildren(rows.length ? h("table", { class: "list" },
      h("thead", {}, h("tr", {}, h("th", { style: "width:32px" }, ""), h("th", {}, "Name"), h("th", {}, "Location"),
        h("th", {}, "Endorsement"), h("th", {}, "Layer"), h("th", {}, ""))),
      h("tbody", {}, rows.map((r) => {
        const fav = favorites().has(key(r));
        return h("tr", { class: `click${pick.selected && key(pick.selected) === key(r) ? " selected" : ""}`, onclick: (e) => select(r, e.currentTarget), ondblclick: () => attachLakehouse(r) },
          h("td", {}, h("button", { class: `star${fav ? " on" : ""}`, title: fav ? "Remove from favorites" : "Add to favorites",
            "aria-label": fav ? "Remove from favorites" : "Add to favorites", onclick: (e) => toggleFavorite(r, e) }, fav ? "★" : "☆")),
          h("td", {}, h("span", { class: "item" }, itemIcon("Lakehouse"), r.name)),
          h("td", {}, r.workspace_name),
          h("td", {}, endorsementPill(r)),
          h("td", {}, pick.layers[r.name] || ""),
          h("td", {}, r.lakehouse_id === attachedId ? h("span", { class: "pill-s pass" }, "Attached") : ""));
      }))) : h("p", { class: "muted-text", style: "padding:16px" },
      pick.scope === "fav" && !pick.query ? "No favorites yet. Click the star next to a lakehouse." : "No lakehouses match."));
    const skipped = c.skipped.length ? `Skipped ${c.skipped.length} workspace${c.skipped.length === 1 ? "" : "s"} that could not be read: ${c.skipped.join(", ")}.` : "";
    note.textContent = [skipped, end && !end.pending && !end.available ? end.reason : ""].filter(Boolean).join(" ");
  }

  search.addEventListener("input", () => { pick.query = search.value; draw(); });
  const overlay = showDialog("OneLake catalog", h("div", { class: "catalog" },
    h("div", { class: "catalog-top" }, h("span", { class: "text" }, "Choose a lakehouse. The notebook reads its files and writes tables to it through OneLake."), search),
    h("div", { class: "catalog-body" }, nav, h("section", { class: "catalog-main" }, head, list), detail),
    h("div", { class: "dialog-actions catalog-foot" }, newBtn, h("span", { style: "flex:1" }), note,
      btn("Cancel", null, closeDialog, "tool outline"), attachBtn)));
  overlay.querySelector(".dialog").classList.add("wide");
  search.focus();
  detail.replaceChildren(h("p", { class: "muted-text" }, "Select a lakehouse to see its tables and files."));

  rpc("meta_project").then((p) => (p.layers || []).forEach((l) => { pick.layers[l.lakehouse] = l.name; draw(); })).catch(() => {});
  const catalog = await attempt(() => rpc("catalog"), "Could not list lakehouses");
  if (!catalog || !overlay.isConnected) return;
  pick.catalog = catalog;
  state.workspaces = catalog.workspaces;
  const attached = state.notebook?.lakehouse;
  draw();
  if (attached) {
    const r = catalog.items.find((i) => i.lakehouse_id === attached.lakehouse_id);
    if (r) select(r, null);
    draw();
  }
  if (!state.endorsements) {
    await pollSidecar("endorsements", { workspace_ids: catalog.workspaces.map((w) => w.id) },
      (result) => { state.endorsements = result; }, () => overlay.isConnected);
  }
  if (overlay.isConnected) draw();
}

function attachLakehouse(ref) {
  closeDialog();
  state.notebook.lakehouse = ref;
  state.explorerTab = "data";
  state.showExplorer = true;
  markDirty();
  go("notebook");
}

function detachLakehouse() {
  state.notebook.lakehouse = null;
  markDirty();
  renderNotebook();
}

// ---------- Markdown ----------

// Quotes too: inlineMd puts link targets in an attribute, and notebooks come from other people.
function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function inlineMd(text) {
  return esc(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<u title="$2">$1</u>');
}

function markdown(src) {
  const wrap = h("div");
  if (!src.trim()) {
    wrap.innerHTML = '<p class="placeholder">Empty Markdown cell. Double-click to edit.</p>';
    return wrap;
  }
  const html = [];
  let list = null;
  let para = [];
  const flushPara = () => { if (para.length) { html.push(`<p>${inlineMd(para.join(" "))}</p>`); para = []; } };
  const flushList = () => { if (list) { html.push(`<ul>${list.map((li) => `<li>${inlineMd(li)}</li>`).join("")}</ul>`); list = null; } };
  for (const line of src.split("\n")) {
    const heading = line.match(/^(#{1,3})\s+(.*)$/);
    const item = line.match(/^\s*[-*]\s+(.*)$/);
    if (heading) { flushPara(); flushList(); html.push(`<h${heading[1].length}>${inlineMd(heading[2])}</h${heading[1].length}>`); }
    else if (item) { flushPara(); (list ||= []).push(item[1]); }
    else if (!line.trim()) { flushPara(); flushList(); }
    else { flushList(); para.push(line.trim()); }
  }
  flushPara();
  flushList();
  wrap.innerHTML = html.join("");
  return wrap;
}

// ---------- pages ----------

// The core pages, in rail order. Pages from other files register themselves the same way
// (meta.js, settings.js); features.js has the rules.
registerFeature({ id: "home", rail: { label: "Home", icon: "home", order: 10, also: ["notebooks"] }, views: { home: renderHome } });
registerFeature({
  id: "create", rail: { label: "Create", icon: "create", order: 20, also: ["pipelines", "pipelineRun"] }, views: { create: renderCreate },
  commands: [
    { label: "New notebook", icon: "notebook", keywords: "create python", run: () => newNotebook() },
    { label: "Move this notebook to another place", icon: "folder", keywords: "repository git save in",
      run: () => state.notebook ? moveNotebookDialog(state.notebook) : toast("Open a notebook first.") },
    { label: "New lakehouse", icon: "lakehouse", keywords: "create", run: () => newLakehouse() },
  ],
});
registerFeature({
  id: "workspaces", rail: { label: "Workspaces", icon: "workspaces", order: 30, also: ["workspace", "lakehouse", "query", "fabric"] },
  views: { workspaces: renderWorkspaces, workspace: renderWorkspace, lakehouse: renderLakehouse, query: renderQuery },
});
registerFeature({
  id: "onelake", rail: { label: "OneLake", icon: "onelake", order: 40 }, views: { onelake: renderOneLake },
  commands: [
    { label: "OneLake catalog", icon: "onelake", keywords: "find tables items", run: () => { catalogPage.tab = "catalog"; go("onelake"); } },
    { label: "OneLake shortcuts", icon: "copyArrowRight", keywords: "link command center", preview: true, run: () => { catalogPage.tab = "shortcuts"; scPage.view = "all"; go("onelake"); } },
    { label: "Broken shortcuts", icon: "warning", keywords: "shortcut health check", preview: true, run: () => { catalogPage.tab = "shortcuts"; scPage.view = "health"; go("onelake"); } },
    { label: "Shortcut changes", icon: "history", keywords: "shortcut history audit undo recreate", preview: true, run: () => { catalogPage.tab = "shortcuts"; scPage.view = "changes"; go("onelake"); } },
    { label: "Request a shortcut", icon: "databaseLink", keywords: "shortcut request ask", preview: true, run: () => { catalogPage.tab = "shortcuts"; scPage.view = "requests"; go("onelake"); shortcutDialog("request"); } },
    { label: "OneLake access", icon: "shield", keywords: "permissions roles security", preview: true, run: () => { catalogPage.tab = "access"; go("onelake"); } },
  ],
});
// Fabric's own portal is a tab of Workspaces; its rail slot went to Optimize (optimize.js).
registerFeature({
  id: "fabric", views: { fabric: renderFabric },
  commands: [{ label: "Fabric portal", icon: "cloud", keywords: "fabric web browser app.fabric.microsoft.com", run: () => go("fabric") }],
});
registerFeature({
  id: "monitor", rail: { label: "Monitor", icon: "monitor", order: 70, also: ["workload"] }, views: { monitor: renderMonitor },
  commands: [
    { label: "Runs", icon: "monitor", keywords: "monitor history failed", run: () => { monitorPage.panel = "runs"; go("monitor"); } },
    { label: "KratosForge schedules", icon: "history", keywords: "monitor scheduled schedules this pc", preview: true, run: () => { monitorPage.panel = "schedules"; go("monitor"); } },
    { label: "Fabric schedules", icon: "history", keywords: "monitor scheduled schedules fabric", preview: true, run: () => { monitorPage.panel = "fabric_schedules"; go("monitor"); } },
    { label: "Fabric Monitoring hub", icon: "monitor", keywords: "monitor fabric hub jobs capacity", run: () => { monitorPage.panel = "fabric"; go("monitor"); } },
    { label: "Cost", icon: "gauge", keywords: "monitor capacity cu spend", preview: true, run: () => { optimizePage.tab = "cost"; go("optimize"); } },
  ],
});

// ---------- startup ----------

document.getElementById("bell").addEventListener("click", () => toggleTray());
document.addEventListener("keydown", (e) => { if (isShortcut(e, "close") && activity.open && !document.getElementById("dialog-root").childElementCount) toggleTray(false); });
document.getElementById("account").addEventListener("click", (e) => accountMenu(e.currentTarget));
// The top bar's box opens the command bar; clearing it removes the page filter.
const searchBox = document.getElementById("search");
searchBox.addEventListener("keydown", (e) => {
  if (e.key.length === 1 || e.key === "Enter" || e.key === "ArrowDown") { e.preventDefault(); openPalette(e.key.length === 1 ? e.key : ""); }
});
searchBox.addEventListener("mousedown", (e) => { if (!state.query) { e.preventDefault(); openPalette(); } });
searchBox.addEventListener("input", () => {
  if (!searchBox.value && state.query) { state.query = ""; render(); }
});
document.addEventListener("keydown", (e) => {
  if (isShortcut(e, "palette")) { e.preventDefault(); openPalette(); }
  // "?" lists the shortcuts, unless it's a character typed in a box or a dialog or the command bar is open.
  else if (isShortcut(e, "shortcuts") && !e.ctrlKey && !e.metaKey && !e.altKey && !typingIn(e.target)
      && !document.getElementById("dialog-root")?.childElementCount && !document.getElementById("palette")) {
    e.preventDefault();
    shortcutsDialog();
  }
});
document.addEventListener("keydown", navKey);
document.getElementById("nav-back").addEventListener("click", navBack);
document.getElementById("nav-forward").addEventListener("click", navForward);
// Listed in the Keyboard shortcuts dialog while a notebook is open (handled just below).
registerShortcuts("Notebook", [
  { keys: ["Ctrl+S"], label: "Save the notebook", when: () => state.view === "notebook" },
]);
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s" && state.view === "notebook") {
    e.preventDefault();
    saveExplicitly(() => { toast("Saved"); return document.getElementById("toast"); });
  }
});
window.addEventListener("beforeunload", () => { if (state.notebook?.dirty) saveNotebook(); });

// ---------- startup component check ----------

// What to do when Windows' application control refuses the engine's DLLs (from the
// startup check's "blocked" report: files by package, who blocked them, where they are).
function blockedHelp(blocked) {
  const names = Object.entries(blocked.packages)
    .map(([pkg, files]) => `${pkg} (${files.length} file${files.length === 1 ? "" : "s"})`).join(", ");
  const why = "Nothing is wrong with the install, so reinstalling won't fix it.";
  if (blocked.by === "smart_app_control") {
    return [
      h("p", {}, "Smart App Control stops program files that aren't signed or that Microsoft doesn't recognize yet. " +
        `It stopped files the local engine needs: ${names}. ${why}`),
      h("p", {}, "Smart App Control can't allow just one app. To run the engine on this PC, turn it off in " +
        "Windows Security > App & browser control > Smart App Control settings, then click Retry. " +
        "Some versions of Windows can't turn it back on without a reset, so decide that first."),
    ];
  }
  return [
    h("p", {}, "An App Control policy on this PC, usually set by your organization, stops program files it doesn't trust. " +
      `It stopped files the local engine needs: ${names}. ${why}`),
    h("p", {}, `Ask your IT admin to allow the Python packages in ${blocked.location}. ` +
      "They can see each block in Event Viewer under Microsoft > Windows > CodeIntegrity > Operational. Then click Retry."),
  ];
}

// Before the sidecar starts, make sure this exe, Python and the sidecar are
// current and match each other, updating whatever is behind. Release builds run a
// bundled engine; after installing one, the check runs once more (afterInstall) with it.
async function checkComponents(afterInstall = false) {
  const short = (commit) => commit.slice(0, 7);
  const rows = {};
  const row = (key, label) => (rows[key] = h("div", { class: "startup-row", "data-status": "busy" },
    h("span", { class: "startup-mark" }), h("span", { class: "startup-name" }, label),
    h("span", { class: "startup-detail" }, "Checking…")));
  const set = (key, status, detail) => {
    rows[key].dataset.status = status;
    rows[key].lastChild.textContent = detail;
    if (status === "busy" || status === "error") show();
    if (status === "warn") logActivity({ status: "info", title: "Update check", text: detail });
  };
  const log = h("pre", { class: "startup-log", hidden: true });
  const help = h("div", { class: "startup-help", hidden: true });
  const actions = h("div", { class: "dialog-actions" });
  const choose = (...labels) => new Promise((resolve) => actions.replaceChildren(...labels.map((label, i) =>
    btn(label, null, () => { actions.replaceChildren(); resolve(i); }, i === 0 ? "tool primary" : "tool outline"))));
  const listen = (fn) => window.__TAURI__.event.listen("component-log", (e) => fn(e.payload));

  // Shown only when something is updating or wrong, or the check takes more than a moment;
  // a normal start goes straight to Home.
  const dialog = h("div", { class: "overlay" },
    h("div", { class: "dialog", role: "dialog", "aria-modal": "true", "aria-label": "Checking components" },
      h("div", { class: "dialog-head" }, "Checking components"),
      h("div", { class: "startup-rows" }, row("app", "KratosForge"), row("python", "Python"), row("sidecar", "Local engine")),
      help, log, actions));
  let dialogShown = false;
  function show() {
    if (dialogShown) return;
    dialogShown = true;
    clearTimeout(slow);
    document.getElementById("dialog-root").replaceChildren(dialog);
  }
  const slow = setTimeout(show, 2500);

  let report;
  try {
    report = await invoke("check_components");
  } catch (err) {
    set("app", "skip", "Needs Python");
    set("python", "error", `${err} Install Python 3.12 or later from python.org, or set FABRIC_DESKTOP_PYTHON to its full path.`);
    set("sidecar", "skip", "Needs Python");
    if (await choose("Retry", "Continue anyway") === 0) return checkComponents();
    return closeDialog();
  }
  const { app, sidecar, latest } = report;
  state.commit = app.commit;
  state.release = latest;
  if (latest.note) logActivity({ status: "info", title: "Updates", text: latest.note });

  if (report.bundled) {
    set("python", report.python ? "ok" : "skip", report.python ? `${report.python}, part of the engine` : "Comes with the engine");
  } else {
    set("python", report.python_ok ? "ok" : "error",
      report.python_ok ? report.python : `${report.python} is too old. KratosForge needs Python 3.12 or later.`);
  }

  state.build = app.commit ? short(app.commit) : `${app.version} (local build)`;
  if (!app.commit) set("app", "ok", `${app.version}, local build (update check off)`);
  else if (report.bundled && !report.python) set("app", "ok", `Build ${short(app.commit)}; checked for updates once the engine is installed`);
  else if (app.updated_to && app.updated_to !== app.commit) {
    set("app", "error", `The update didn't take: this is still build ${short(app.commit)}, not ${short(app.updated_to)}. ` +
      "Download kratosforge.exe again from the latest release.");
  } else if (report.auto_update === false) {
    // The organization rolls out builds itself (policy auto_update: false).
    set("app", "ok", latest.commit && latest.commit !== app.commit
      ? `Build ${short(app.commit)}. Build ${short(latest.commit)} is out; your organization rolls out updates`
      : `Build ${short(app.commit)}. Your organization rolls out updates`);
  } else if (!latest.commit) set("app", "warn", `Build ${short(app.commit)} (${latest.channel || "stable"} channel). Could not check for a newer build: ${latest.error}`);
  // The shell refused the channel's version.json: out of date, or no checksum (HD-022).
  else if (latest.refused) set("app", "warn", `Build ${short(app.commit)}. ${latest.refused}`);
  else if (latest.commit === app.commit) set("app", "ok", `Up to date (build ${short(app.commit)})`);
  else {
    // A newer build is installed straight away, so nobody keeps running an old one.
    set("app", "busy", `Updating to build ${short(latest.commit)}…`);
    const unlisten = await listen((line) => {
      const m = /^progress (\d+)/.exec(line);
      if (m) set("app", "busy", `Downloading build ${short(latest.commit)}… ${m[1]}%`);
    });
    try {
      await invoke("update_app", { commit: latest.commit });
      set("app", "ok", "Restarting…");
      return new Promise(() => {});  // the new exe takes over
    } catch (err) {
      set("app", "error", `Update to build ${short(latest.commit)} failed: ${err} You are still on ${short(app.commit)}.`);
    } finally {
      unlisten();
    }
  }

  // Windows refusing to load the engine's DLLs is not something pip can repair, so
  // explain it in place of the pip output.
  const showBlocked = (blocked) => {
    set("sidecar", "error", blocked.by === "smart_app_control"
      ? "Windows Smart App Control blocked it." : "An App Control policy on this PC blocked it.");
    log.hidden = true;
    help.replaceChildren(...blockedHelp(blocked));
    help.hidden = false;
  };

  if (!report.python_ok) set("sidecar", "skip", "Needs Python 3.12 or later");
  else if (sidecar.editable || !app.commit) {
    const kind = sidecar.editable ? "developer install (not managed)" : "not checked in a local build";
    if (sidecar.version && sidecar.error) set("sidecar", "error", `${sidecar.version} does not start: ${sidecar.error}`);
    else if (sidecar.version) set("sidecar", "ok", `${sidecar.version}, ${kind}`);
    else set("sidecar", "error", "Not installed. In the sidecar folder, run: python -m pip install -e .");
  } else if (sidecar.commit === app.commit && !sidecar.error) {
    set("sidecar", "ok", `${sidecar.version}, matches this build`);
  } else if (sidecar.commit === app.commit && sidecar.blocked) {
    showBlocked(sidecar.blocked);
  } else {
    if (afterInstall && report.bundled) {
      // The engine just installed is still not this build's: the exe update above failed.
      set("sidecar", "error", `The latest engine is build ${sidecar.commit ? short(sidecar.commit) : "unknown"}, ` +
        `but this app is ${short(app.commit)}. ` + (report.auto_update === false
        ? "Your organization rolls out updates: ask IT for the current build of kratosforge.exe."
        : "Restart KratosForge to update it."));
      if (await choose("Retry", "Continue anyway") === 0) return checkComponents();
      return closeDialog();
    }
    set("sidecar", "busy", sidecar.commit === app.commit ? `Repairing (it would not start: ${sidecar.error})…`
      : report.bundled ? (sidecar.version ? "Updating the engine…" : "Installing the engine…")
      : sidecar.version ? `Updating to build ${short(app.commit)}…` : `Installing build ${short(app.commit)}…`);
    log.hidden = false;
    let blocked = null;
    const unlisten = await listen((line) => {
      if (line.startsWith("blocked {")) { blocked = JSON.parse(line.slice(8)); return; }
      log.textContent += `${line}\n`;
      log.scrollTop = log.scrollHeight;
    });
    try {
      const result = await invoke("update_sidecar");
      if (result?.engine) {
        // Check again with the new engine: it may be a newer build than this exe, and
        // it may be blocked from loading. Its first start is slow (Windows scans each new file).
        set("sidecar", "busy", "Starting the new engine for the first time…");
        return checkComponents(true);
      }
      set("sidecar", "ok", `Updated to build ${short(app.commit)}`);
      log.hidden = true;
    } catch (err) {
      if (blocked) showBlocked(blocked);
      else set("sidecar", "error", report.bundled ? "The engine could not be installed. Details are below."
        : "Update failed. The pip output is below.");
      // The error repeats the last lines of output, which the log already shows.
      const shown = new Set(log.textContent.split("\n"));
      const extra = String(err).split("\n").filter((l) => !shown.has(l) && !l.startsWith("blocked {"));
      if (extra.length) log.textContent += `${extra.join("\n")}\n`;
    } finally {
      unlisten();
    }
  }

  if (Object.values(rows).some((r) => r.dataset.status === "error")) {
    if (await choose("Retry", "Continue anyway") === 0) return checkComponents();
  }
  clearTimeout(slow);
  closeDialog();  // also an earlier attempt's, after Retry
}

hydrateIcons();
renderChrome();
// A pop-out window starts from the engine the main window checked and started (popout.js).
if (POP_OUT) addEventListener("DOMContentLoaded", () => startPopOut());
else startMain();

function startMain() {
  // Nothing may start the sidecar until the check is done: pip cannot replace files it has open.
  // Home draws at once with placeholders while the check runs (UX-020), then fills in when it returns.
  state.starting = true;
  if (state.view === "home") renderHome();
  checkComponents().finally(() => {
    state.starting = false;
    listenForPopOuts();  // popout.js
    listenForLinks();  // vscode.js: kratosforge:// links, once the engine is up
    navRestore();  // the page you were on last time, or Home
    render();
    attempt(async () => {
      const env = await rpc("environment");
      state.python = env.python;
      state.user = env.user;
      state.tenant = env.tenant;
      await loadSettings();
      if (!navShown(state.view)) state.view = "home";  // a preview page reopened while preview features are now hidden
      showRenameSplash(() => showWhatsNew()) || showUpdateSplash();
      // The environment pill needs the metadata project; there may not be one yet.
      state.meta.project = await rpc("meta_project").catch(() => state.meta.project);
      render();
      trackFabricJobs();
      checkFreshness();
      scheduleUpdateWatch();  // settings.js: the new build banner, every 4 hours while open
      // After an update the engine lives in a new folder: point this PC's scheduled runs at it.
      rpc("sync_pc_schedules").then((r) => r.error && toast(`Scheduled runs on this PC: ${friendlyError(r.error).text}`, true)).catch(() => {});
    }, "Could not start the Python sidecar");
  });
}
