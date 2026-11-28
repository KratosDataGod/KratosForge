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
  // A notebook's command mode (notebook.js, nbselect.js: a cell selected, not being edited). A key here can
  // be a letter on its own, and two keys one after the other are written with a space: "d d" is D then D.
  // Up, Down, Esc, Tab and the run keys stay as they are (NOTEBOOK_RESERVED_KEYS).
  { id: "nb_edit", label: "Notebook: edit the selected cell (command mode)", key: "Enter", in: "command" },
  { id: "nb_select_above", label: "Notebook: select the cell above (command mode; Up too)", key: "k", in: "command" },
  { id: "nb_select_below", label: "Notebook: select the cell below (command mode; Down too)", key: "j", in: "command" },
  { id: "nb_extend_above", label: "Notebook: grow the selection up (command mode; Shift+Up too)", key: "Shift-k", in: "command" },
  { id: "nb_extend_below", label: "Notebook: grow the selection down (command mode; Shift+Down too)", key: "Shift-j", in: "command" },
  { id: "nb_insert_above", label: "Notebook: insert a cell above (command mode)", key: "a", in: "command" },
  { id: "nb_insert_below", label: "Notebook: insert a cell below (command mode)", key: "b", in: "command" },
  { id: "nb_delete", label: "Notebook: delete the cell or the selected cells (command mode)", key: "d d", in: "command" },
  { id: "nb_undo_delete", label: "Notebook: put the last deleted cell back (command mode)", key: "z", in: "command" },
  { id: "nb_markdown", label: "Notebook: make the cell Markdown (command mode)", key: "m", in: "command" },
  { id: "nb_code", label: "Notebook: make the cell code (command mode)", key: "y", in: "command" },
  { id: "nb_copy", label: "Notebook: copy the cell or the selected cells (command mode)", key: "c", in: "command" },
  { id: "nb_cut", label: "Notebook: cut the cell or the selected cells (command mode)", key: "x", in: "command" },
  { id: "nb_paste_below", label: "Notebook: paste below (command mode)", key: "v", in: "command" },
  { id: "nb_paste_above", label: "Notebook: paste above (command mode)", key: "Shift-v", in: "command" },
  { id: "nb_merge", label: "Notebook: merge the selected cells (command mode)", key: "Shift-m", in: "command" },
  { id: "nb_output", label: "Notebook: show or hide the cell's output (command mode)", key: "o", in: "command" },
  { id: "nb_line_numbers", label: "Notebook: show or hide line numbers (command mode)", key: "Shift-l", in: "command" },
];

// Keys a command-mode command can't take, because command mode or the app keeps them, and why.
// Enter is nb_edit's own.
const NOTEBOOK_RESERVED_KEYS = {
  "Escape": "leaves a cell and clears the selection", "Tab": "moves to the next control",
  "ArrowUp": "selects the cell above", "ArrowDown": "selects the cell below",
  "Shift-ArrowUp": "grows the selection up", "Shift-ArrowDown": "grows the selection down",
  "Alt-ArrowUp": "moves the selected cells up", "Alt-ArrowDown": "moves the selected cells down",
  "Alt-ArrowLeft": "goes back", "Alt-ArrowRight": "goes forward",
  "Enter": "edits the selected cell", "Shift-Enter": "runs the cell and selects the next one", "Mod-Enter": "runs the cell",
  "Alt-Enter": "runs the cell and adds one below", "Mod-Shift-Enter": "runs the selected text", "Delete": "deletes the selected cells",
  "Mod-a": "selects every cell", "Mod-h": "opens find and replace", "Mod-Shift-f": "opens find and replace",
  "Shift-/": "shows the keyboard shortcuts",
};

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
// Two keys one after the other ("d d") are each made the same way.
function editorKeyCanon(key) {
  if (/\S \S/.test(String(key))) return String(key).split(" ").map(editorKeyCanon).join(" ");
  const parts = String(key).split(/-(?!$)/);
  let name = parts.pop();
  const mods = new Set(parts.map((m) => (m === "Ctrl" || m === "Meta" || m === "Cmd" ? "Mod" : m)));
  if (name.length === 1) name = name.toLowerCase();
  return [...["Mod", "Alt", "Shift"].filter((m) => mods.has(m)), name].join("-");
}

// As people read it: Ctrl+Shift+F. Two letters one after the other read as one word, DD, as Jupyter writes it.
function editorKeyLabel(key) {
  if (/\S \S/.test(String(key))) {
    const each = String(key).split(" ").map(editorKeyLabel);
    return each.join(each.every((k) => k.length === 1) ? "" : " ");
  }
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
  if (EDITOR_COMMANDS.find((c) => c.id === id)?.in === "command") return notebookKeyProblem(id, key);
  const canon = editorKeyCanon(key);
  const label = editorKeyLabel(key);
  if (EDITOR_RESERVED_KEYS[canon] && !(canon === "Mod-s" && id === "sql_save")) return `${label} ${EDITOR_RESERVED_KEYS[canon]}, so it can't be used here.`;
  const mods = canon.split(/-(?!$)/).slice(0, -1);
  const name = canon.split(/-(?!$)/).pop();
  if (!mods.includes("Mod") && !mods.includes("Alt") && !/^F\d{1,2}$/.test(name)) {
    return `On its own, ${label} types or moves the cursor. Add Ctrl or Alt.`;
  }
  const cmd = EDITOR_COMMANDS.find((c) => c.id === id);
  const other = EDITOR_COMMANDS.find((c) => c.id !== id && c.in !== "command" && editorKeyCanon(editorKey(c.id)) === canon
    && (c.in === "both" || cmd.in === "both" || c.in === cmd.in));
  return other ? `${label} is already the key for "${other.label.replace(/ \(.*\)$/, "")}". Change that one first.` : "";
}

// The same for a command-mode command. Any key will do, a letter too, but not one command mode or the app
// keeps, another command-mode command's, or one that starts or is the first key of another's two keys.
function notebookKeyProblem(id, key) {
  const canon = editorKeyCanon(key);
  const label = editorKeyLabel(key);
  const strokes = canon.split(" ");
  for (const stroke of strokes) {
    const why = (stroke !== "Enter" || id !== "nb_edit") && (NOTEBOOK_RESERVED_KEYS[stroke] || EDITOR_RESERVED_KEYS[stroke]);
    if (why) return `${editorKeyLabel(stroke)} ${why}, so it can't be used here.`;
  }
  const other = EDITOR_COMMANDS.find((c) => {
    if (c.id === id || c.in !== "command") return false;
    const theirs = editorKeyCanon(editorKey(c.id));
    return theirs === canon || theirs.startsWith(`${canon} `) || canon.startsWith(`${theirs} `);
  });
  if (!other) return "";
  const theirs = editorKeyLabel(editorKey(other.id));
  const name = other.label.replace(/ \(.*\)$/, "");
  return editorKeyCanon(editorKey(other.id)) === canon ? `${label} is already the key for "${name}". Change that one first.`
    : `${label} and ${theirs} (the key for "${name}") start the same way. Change that one first.`;
}

// A command-mode key press: a letter by the character it types (as the keys always were: A on any
// keyboard layout), with Shift written out; anything else as editorKeyFromEvent names it.
function notebookStrokeFromEvent(e) {
  const k = String(e.key || "");
  if (!/^[A-Za-z]$/.test(k)) return editorKeyFromEvent(e);
  return [e.ctrlKey || e.metaKey ? "Mod" : null, e.altKey ? "Alt" : null, e.shiftKey ? "Shift" : null, k.toLowerCase()].filter(Boolean).join("-");
}

// The command-mode command a key press is: its id, "" when it is the first of a command's two keys (the
// second must follow within 600 ms, as D D always has), or null. notebook.js and nbselect.js both ask for
// the same press, so the answer is kept for that press.
const notebookChord = { stroke: null, at: 0, event: null, result: null };
function notebookCommandFor(e) {
  if (notebookChord.event === e) return notebookChord.result;
  const stroke = notebookStrokeFromEvent(e);
  let result = null;
  if (stroke) {  // a modifier on its own keeps a first key waiting
    const now = Date.now();
    const canon = editorKeyCanon(stroke);
    const first = notebookChord.stroke && now - notebookChord.at < 600 ? notebookChord.stroke : null;
    notebookChord.stroke = null;
    const cmds = EDITOR_COMMANDS.filter((c) => c.in === "command").map((c) => [c.id, editorKeyCanon(editorKey(c.id))]);
    if (first) result = cmds.find(([, k]) => k === `${first} ${canon}`)?.[0] || null;
    if (!result) result = cmds.find(([, k]) => k === canon)?.[0] || null;
    if (!result && cmds.some(([, k]) => k.startsWith(`${canon} `))) {
      Object.assign(notebookChord, { stroke: canon, at: now });
      result = "";
    }
  }
  Object.assign(notebookChord, { event: e, result });
  return result;
}

// A command-mode command's key now, as the Keyboard shortcuts dialog shows it.
const notebookKeyLabel = (id) => editorKeyLabel(editorKey(id));

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
  // A command-mode command can take two keys one after the other: a second key within 800 ms of the
  // first makes the pair (D then D), otherwise the first key is the key.
  let first = null;
  let firstTimer = 0;
  const stopFirst = () => { clearTimeout(firstTimer); first = null; };
  const take = (id, key) => {
    const why = editorKeyProblem(id, key);
    if (why) {
      problem.textContent = why;
      const capture = body.querySelector(".editor-key-capture");
      if (capture) capture.textContent = "Press the new key...";
      return;
    }
    problem.textContent = "";
    capturing = null;
    const cmd = EDITOR_COMMANDS.find((c) => c.id === id);
    const next = { ...editorOverrides() };
    if (editorKeyCanon(key) === editorKeyCanon(cmd.key)) delete next[id];
    else next[id] = key;
    saveEditorKeys(next).then(draw);
  };
  const listen = (id) => (e) => {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); stopFirst(); capturing = null; problem.textContent = ""; draw(); return; }
    if (e.key === "Tab") return;
    const command = EDITOR_COMMANDS.find((c) => c.id === id)?.in === "command";
    const key = command ? notebookStrokeFromEvent(e) : editorKeyFromEvent(e);
    e.preventDefault();
    e.stopPropagation();
    if (!key) return;
    if (!command) { take(id, key); return; }
    if (first) { const pair = `${first} ${key}`; stopFirst(); take(id, pair); return; }
    first = key;
    e.currentTarget.textContent = `${editorKeyLabel(key)}, then...`;
    firstTimer = setTimeout(() => { const one = first; first = null; if (one && capturing === id) take(id, one); }, 800);
  };
  function draw() {
    const changed = editorOverrides();
    const rows = EDITOR_COMMANDS.filter((c) => !filter || `${c.label} ${editorKeyLabel(editorKey(c.id))}`.toLowerCase().includes(filter));
    fill(body, rows.length ? rows.map((c) => {
      const isChanged = typeof changed[c.id] === "string";
      const where = c.in === "both" ? "Notebooks and SQL" : c.in === "sql" ? "SQL" : c.in === "command" ? "Notebooks, command mode" : "Notebooks";
      const keyCell = capturing === c.id
        ? h("button", { class: "tool outline small editor-key-capture", "aria-label": c.in === "command"
          ? `Press the new key for ${c.label}, or two keys one after the other. Esc cancels.` : `Press the new key for ${c.label}. Esc cancels.`,
          onkeydown: listen(c.id), onblur: () => { if (capturing === c.id && !first) { capturing = null; draw(); } } }, "Press the new key...")
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
    settingRow("Shortcuts", `Change the key for any editor command or notebook command-mode key.${changed ? ` ${changed} changed.` : ""}`,
      btn("Change keys...", null, () => editorKeysDialog(locked("editor_key_overrides")), "tool outline"), source("editor_key_overrides")));
}
