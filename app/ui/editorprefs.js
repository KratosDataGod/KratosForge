// Settings > Editor (app_settings editor_word_wrap, editor_font_size, editor_keys, editor_key_overrides): word
// wrap, the code font size, Vim key bindings (Preview) and changed keys, for notebook cells (notebook.js) and
// SQL editors (sqleditor.js). Each editor adds editorPrefsExtension(kind, keys) once; a saved change
// reconfigures every open editor (applyEditorPrefs, called by settings.js after loading or saving settings).
// The defaults are the editors as they were: no wrapping, each editor's own font size and the default keys.

// The editor commands whose keys can be changed: id (the key in editor_key_overrides), what it does, its
// default key (CodeMirror's names: Mod is Ctrl) and which editors have it.
const EDITOR_COMMANDS = [
  { id: "run_cell_next", label: "Run the cell and select the next one", key: "Shift-Enter", in: "notebook" },
  { id: "run_cell", label: "Run the cell", key: "Mod-Enter", in: "notebook" },
  { id: "run_cell_insert", label: "Run the cell and add one below", key: "Alt-Enter", in: "notebook" },
  { id: "split_cell", label: "Split the cell at the cursor", key: "Mod-Shift--", in: "notebook" },
  { id: "breakpoint", label: "Set or clear a breakpoint", key: "F9", in: "notebook" },
  { id: "sql_run", label: "Run the query (F5 runs it too)", key: "Mod-Enter", in: "sql" },
  { id: "sql_run_statement", label: "Run the statement at the cursor", key: "Shift-Mod-Enter", in: "sql" },
  { id: "sql_save", label: "Save the query", key: "Mod-s", in: "sql" },
  { id: "format", label: "Format", key: "Shift-Alt-f", in: "both" },
  { id: "comment", label: "Comment or uncomment lines", key: "Mod-/", in: "both" },
  { id: "sql_results", label: "Show or hide the results", key: "Mod-r", in: "sql" },
  { id: "upper_case", label: "Make the selection upper case", key: "Shift-Mod-u", in: "sql" },
  { id: "lower_case", label: "Make the selection lower case", key: "Shift-Mod-l", in: "sql" },
  { id: "go_to_line", label: "Go to a line", key: "Mod-g", in: "sql" },
  { id: "definition", label: "Show the table, view, procedure or function under the cursor in the Explorer", key: "F12", in: "sql" },
  { id: "peek_definition", label: "Peek at the definition of the object under the cursor", key: "Alt-F12", in: "sql" },
  { id: "bookmark", label: "Set or clear a bookmark", key: "Mod-F2", in: "sql" },
  { id: "bookmark_next", label: "Go to the next bookmark", key: "F2", in: "sql" },
  { id: "bookmark_previous", label: "Go to the previous bookmark", key: "Shift-F2", in: "sql" },
  { id: "bookmark_clear", label: "Clear every bookmark", key: "Shift-Mod-F2", in: "sql" },
];

// Keys an editor command can't take, because the app or every editor already uses them, and why.
const EDITOR_RESERVED_KEYS = {
  "Mod-k": "is the command bar", "Mod-=": "makes everything bigger", "Mod--": "makes everything smaller",
  "Mod-0": "sets the size back to 100%", "F5": "runs the query on the SQL page", "Mod-c": "copies", "Mod-x": "cuts",
  "Mod-v": "pastes", "Mod-z": "undoes", "Mod-y": "redoes", "Mod-Shift-z": "redoes", "Mod-a": "selects everything",
  "Mod-f": "finds in the editor",
};
const EDITOR_FONT_SIZES = [10, 11, 12, 13, 14, 15, 16, 18, 20, 22, 24];

const editorOpen = new Set();  // { kind, keys, prefs, keymap, view } for every editor on screen

function editorSetting(key, fallback) {
  const v = state.settings?.[key]?.value;
  return v === undefined || v === null ? fallback : v;
}

const editorWraps = (kind) => ["both", kind === "sql" ? "sql" : "notebooks"].includes(editorSetting("editor_word_wrap", "off"));
const editorVimOn = () => editorSetting("editor_keys", "default") === "vim" && previewShown();
const editorFontSize = () => Number(editorSetting("editor_font_size", 0)) || 0;

function editorOverrides() {
  const o = editorSetting("editor_key_overrides", {});
  return o && typeof o === "object" && !Array.isArray(o) ? o : {};
}

// A key's one spelling, to compare two: modifiers in a fixed order, a letter in lower case.
function editorKeyCanon(key) {
  const parts = String(key).split(/-(?!$)/);
  let name = parts.pop();
  const mods = new Set(parts.map((m) => (m === "Ctrl" || m === "Meta" || m === "Cmd" ? "Mod" : m)));
  if (name.length === 1) name = name.toLowerCase();
  return [...["Mod", "Alt", "Shift"].filter((m) => mods.has(m)), name].join("-");
}

// As people read it: Ctrl+Shift+F.
function editorKeyLabel(key) {
  const words = { Mod: "Ctrl", ArrowUp: "Up", ArrowDown: "Down", ArrowLeft: "Left", ArrowRight: "Right" };
  return editorKeyCanon(key).split(/-(?!$)/).map((p) => words[p] || (p.length === 1 ? p.toUpperCase() : p)).join("+");
}

// The key a command has now: the one saved for it, or its default.
function editorKey(id) {
  const cmd = EDITOR_COMMANDS.find((c) => c.id === id);
  const saved = editorOverrides()[id];
  return typeof saved === "string" && saved ? saved : cmd?.key;
}

// An editor's own key list with every listed command on its key now. Entries without an id keep theirs.
function editorKeymap(entries) {
  return entries.map(({ id, ...binding }) => (id ? { ...binding, key: editorKey(id) || binding.key } : binding));
}

function editorPrefExtensions(kind) {
  const size = editorFontSize();
  const line = size ? `${Math.round(size * 1.6)}px` : null;
  return [
    editorWraps(kind) ? CM.EditorView.lineWrapping : [],
    size ? CM.Prec.highest(CM.EditorView.theme({ "&": { fontSize: `${size}px` }, ".cm-content": { lineHeight: line },
      ".cm-scroller": { lineHeight: line } })) : [],
    editorVimOn() ? CM.vim() : [],
  ];
}

// Added once to an editor's extensions, before its other keymaps (Vim reads keys first): the settings,
// and the editor's key list (entries with an id follow changed keys) at the highest precedence.
function editorPrefsExtension(kind, keys) {
  const rec = { kind, keys, prefs: new CM.Compartment(), keymap: new CM.Compartment(), view: null };
  return [
    rec.prefs.of(editorPrefExtensions(kind)),
    CM.Prec.highest(rec.keymap.of(CM.keymap.of(editorKeymap(keys)))),
    CM.ViewPlugin.define((view) => {
      rec.view = view;
      editorOpen.add(rec);
      return { destroy() { editorOpen.delete(rec); } };
    }),
  ];
}

// After settings load or change: every open editor takes them at once.
function applyEditorPrefs() {
  // A notebook cell's code shown as plain text while its editor is out of the page (.nb-lazy-code,
  // notebook.js EAGER_EDITORS) takes the same size, line height and wrapping, so it is the same height.
  const root = document.documentElement;
  const size = editorFontSize();
  root.style.setProperty("--nb-code-size", size ? `${size}px` : "13px");
  root.style.setProperty("--nb-code-line", size ? `${Math.round(size * 1.6)}px` : "20px");
  root.classList.toggle("nb-code-wrap", editorWraps("notebook"));
  for (const rec of editorOpen) {
    if (!rec.view) continue;
    rec.view.dispatch({ effects: [rec.prefs.reconfigure(editorPrefExtensions(rec.kind)),
      rec.keymap.reconfigure(CM.keymap.of(editorKeymap(rec.keys)))] });
  }
}

// Whether Vim has a use for Esc in this editor now: insert or visual mode, or keys still to come. A notebook
// cell leaves for command mode on Esc only after that, so Esc in Vim's normal mode still leaves the cell.
function editorVimTakesEscape(view) {
  if (!editorVimOn()) return false;
  const vim = CM.getCM(view)?.state?.vim;
  return !!vim && (vim.insertMode || vim.visualMode || !!vim.inputState?.keyBuffer?.length);
}

// Why a command can't take this key, or "" when it can. Without Ctrl or Alt, only F keys: anything else
// on its own types or moves the cursor.
function editorKeyProblem(id, key) {
  const canon = editorKeyCanon(key);
  const label = editorKeyLabel(key);
  if (EDITOR_RESERVED_KEYS[canon] && !(canon === "Mod-s" && id === "sql_save")) return `${label} ${EDITOR_RESERVED_KEYS[canon]}, so it can't be used here.`;
  const mods = canon.split(/-(?!$)/).slice(0, -1);
  const name = canon.split(/-(?!$)/).pop();
  if (!mods.includes("Mod") && !mods.includes("Alt") && !/^F\d{1,2}$/.test(name)) {
    return `On its own, ${label} types or moves the cursor. Add Ctrl or Alt.`;
  }
  const cmd = EDITOR_COMMANDS.find((c) => c.id === id);
  const other = EDITOR_COMMANDS.find((c) => c.id !== id && editorKeyCanon(editorKey(c.id)) === canon
    && (c.in === "both" || cmd.in === "both" || c.in === cmd.in));
  return other ? `${label} is already the key for "${other.label.replace(/ \(.*\)$/, "")}". Change that one first.` : "";
}

// A key press as CodeMirror names it, by the key's place on the keyboard (so Shift+- stays Shift+-), or null
// for a modifier alone or a key no command can take.
function editorKeyFromEvent(e) {
  const code = String(e.code || "");
  const named = { Minus: "-", Equal: "=", Slash: "/", Backslash: "\\", BracketLeft: "[", BracketRight: "]", Semicolon: ";",
    Quote: "'", Comma: ",", Period: ".", Backquote: "`", Enter: "Enter", NumpadEnter: "Enter", Space: "Space",
    Backspace: "Backspace", Delete: "Delete", Home: "Home", End: "End", PageUp: "PageUp", PageDown: "PageDown",
    Insert: "Insert", ArrowUp: "ArrowUp", ArrowDown: "ArrowDown", ArrowLeft: "ArrowLeft", ArrowRight: "ArrowRight" };
  const name = /^Key[A-Z]$/.test(code) ? code.slice(3).toLowerCase() : /^Digit\d$/.test(code) ? code.slice(5)
    : /^F\d{1,2}$/.test(code) ? code : named[code];
  if (!name) return null;
  return [e.ctrlKey || e.metaKey ? "Mod" : null, e.altKey ? "Alt" : null, e.shiftKey ? "Shift" : null, name].filter(Boolean).join("-");
}

async function saveEditorKeys(overrides) {
  const res = await saveSettings({ editor_key_overrides: Object.keys(overrides).length ? overrides : null });
  if (res) applyEditorPrefs();
  return res;
}

// Settings > Editor > Change keys: every command with its key, to change or put back.
function editorKeysDialog(locked) {
  let filter = "";
  let capturing = null;
  const problem = h("p", { class: "fail-text small", role: "alert", "aria-live": "assertive" });
  const find = h("input", { type: "search", "aria-label": "Find a command", placeholder: "Find a command", class: "grow",
    oninput: () => { filter = find.value.trim().toLowerCase(); draw(); } });
  const body = h("tbody", {});
  const table = h("div", { class: "scroll editor-keys" }, h("table", { class: "list" },
    h("thead", {}, h("tr", {}, h("th", {}, "Command"), h("th", {}, "Key"), h("th", {}, h("span", { class: "sr-only" }, "Change")))), body));
  const listen = (id) => (e) => {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); capturing = null; problem.textContent = ""; draw(); return; }
    if (e.key === "Tab") return;
    const key = editorKeyFromEvent(e);
    e.preventDefault();
    e.stopPropagation();
    if (!key) return;
    const why = editorKeyProblem(id, key);
    if (why) { problem.textContent = why; return; }
    problem.textContent = "";
    capturing = null;
    const cmd = EDITOR_COMMANDS.find((c) => c.id === id);
    const next = { ...editorOverrides() };
    if (editorKeyCanon(key) === editorKeyCanon(cmd.key)) delete next[id];
    else next[id] = key;
    saveEditorKeys(next).then(draw);
  };
  function draw() {
    const changed = editorOverrides();
    const rows = EDITOR_COMMANDS.filter((c) => !filter || `${c.label} ${editorKeyLabel(editorKey(c.id))}`.toLowerCase().includes(filter));
    fill(body, rows.length ? rows.map((c) => {
      const isChanged = typeof changed[c.id] === "string";
      const where = c.in === "both" ? "Notebooks and SQL" : c.in === "sql" ? "SQL" : "Notebooks";
      const keyCell = capturing === c.id
        ? h("button", { class: "tool outline small editor-key-capture", "aria-label": `Press the new key for ${c.label}. Esc cancels.`,
          onkeydown: listen(c.id), onblur: () => { if (capturing === c.id) { capturing = null; draw(); } } }, "Press the new key...")
        : h("span", {}, h("kbd", {}, editorKeyLabel(editorKey(c.id))), isChanged ? h("span", { class: "pill-s info" }, "Changed") : null);
      return h("tr", { "data-command": c.id }, h("td", {}, c.label, h("div", { class: "muted-text small" }, where)), h("td", {}, keyCell),
        h("td", {}, locked ? null : capturing === c.id ? null : isChanged
          ? btn("Reset", null, () => { const next = { ...editorOverrides() }; delete next[c.id]; saveEditorKeys(next).then(draw); }, "tool outline small",
            { "aria-label": `Reset ${c.label} to ${editorKeyLabel(c.key)}` })
          : btn("Change", null, () => { capturing = c.id; problem.textContent = ""; draw(); body.querySelector(".editor-key-capture")?.focus(); },
            "tool outline small", { "aria-label": `Change the key for ${c.label}` })));
    }) : [h("tr", {}, h("td", { colspan: "3", class: "muted-text" }, "No command matches."))]);
  }
  draw();
  showDialog("Change keys", h("div", { class: "editor-keys-body" },
    locked ? h("p", { class: "muted-text small" }, SOURCE_TEXT.policy) : null, find, table, problem), [
    locked ? null : btn("Reset all", null, () => saveEditorKeys({}).then(draw), "tool outline"),
    btn("Done", null, () => { closeDialog(); if (state.view === "settings") render(); }, "tool primary")].filter(Boolean));
}

// The Editor card on Settings, beside Appearance.
function editorSettingsCard(s, locked) {
  const save = async (changes) => { if (await saveSettings(changes)) { applyEditorPrefs(); render(); } };
  const source = (key) => (s[key]?.source === "policy" ? "policy" : null);
  const seg = (key, label, options) => {
    const now = s[key]?.value ?? options[0][0];
    return h("div", { class: "seg", role: "group", "aria-label": label }, options.map(([value, text]) =>
      h("button", { class: now === value ? "on" : "", "aria-pressed": String(now === value), disabled: locked(key) ? true : null,
        onclick: () => now !== value && save({ [key]: value }) }, text)));
  };
  const size = Number(s.editor_font_size?.value || 0);
  const sizes = EDITOR_FONT_SIZES.includes(size) || !size ? EDITOR_FONT_SIZES : [...EDITOR_FONT_SIZES, size].sort((a, b) => a - b);
  const changed = Object.keys(editorOverrides()).filter((id) => EDITOR_COMMANDS.some((c) => c.id === id)).length;
  return h("section", { class: "card pad editor-settings" }, h("h2", {}, "Editor"),
    h("p", { class: "muted-text small" }, "Notebook cells and SQL editors. ", learnMore("editor-settings")),
    settingRow("Word wrap", "Long lines wrap instead of scrolling sideways.",
      seg("editor_word_wrap", "Word wrap", [["notebooks", "Notebooks"], ["sql", "SQL"], ["both", "Both"], ["off", "Off"]]), source("editor_word_wrap")),
    settingRow("Font size", "The code font in cells and SQL editors. The rest of the app follows Ctrl+= and Ctrl+-.",
      h("select", { "aria-label": "Font size", disabled: locked("editor_font_size") ? true : null,
        onchange: (e) => save({ editor_font_size: Number(e.target.value) || null }) },
        h("option", { value: "0", selected: !size ? true : null }, "Each editor's own (default)"),
        sizes.map((n) => h("option", { value: String(n), selected: size === n ? true : null }, `${n} px`))), source("editor_font_size")),
    previewShown() ? settingRow(h("span", {}, "Key bindings ", previewTag()),
      "Vim adds modal editing to cells and SQL editors. Esc still leaves a cell.",
      seg("editor_keys", "Key bindings", [["default", "Default"], ["vim", "Vim"]]), source("editor_keys")) : null,
    settingRow("Shortcuts", `Change the key for any editor command.${changed ? ` ${changed} changed.` : ""}`,
      btn("Change keys...", null, () => editorKeysDialog(locked("editor_key_overrides")), "tool outline"), source("editor_key_overrides")));
}
