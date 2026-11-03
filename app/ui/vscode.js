// VS Code alongside KratosForge (docs/vscode.md): open a notebook, or the folder it's in, in VS
// Code, and load the edits saved there when the app comes back into focus. Edits on both sides go
// through the changed-on-disk question in app.js (nbDiskConflict) instead.

function vscodeButton(nb) {
  return h("div", { class: "split" },
    btn("Open in VS Code", "code", () => openInVsCode(nb, "notebook"), "tool",
      { title: "Saves the notebook, then opens it in VS Code. Edits made there show here when you come back." }),
    h("button", { class: "tool split-more", "aria-label": "More VS Code options", title: "More VS Code options",
      onclick: (e) => showMenu(e.currentTarget, vscodeMenu(nb)) }, icon("chevronDown", 14)));
}

function vscodeMenu(nb) {
  return [
    { info: nb.place ? `${nb.name} is in the ${nb.place.label} repository.` : `${nb.name} is in the app's notebooks folder.` },
    { label: "Open this notebook in VS Code", icon: "code", action: () => openInVsCode(nb, "notebook") },
    { label: nb.place ? "Open the repository folder in VS Code" : "Open the notebooks folder in VS Code", icon: "folder",
      action: () => openInVsCode(nb, "folder") },
    { label: "Open this notebook in VS Code Insiders", icon: "code", action: () => openInVsCode(nb, "notebook", true) },
    nb.place ? { label: "Set up this repository for VS Code…", icon: "settings", action: () => vscodeSetupDialog(nb.place.root, nb.place.label) } : null,
  ].filter(Boolean);
}

// The shell opens only an existing .ipynb file or folder (open_in_vscode in main.rs).
async function openInVsCode(nb, what, insiders = false) {
  if (nb.dirty && !(await saveNotebook())) return;
  const where = await attempt(() => rpc("notebook_file_path", { name: nb.name }), "Couldn't find the notebook's file");
  if (!where) return;
  const path = what === "notebook" ? where.path : (where.repo_root || where.notebooks_folder);
  const ok = await attempt(() => invoke("open_in_vscode", { path, insiders }).then(() => true), "Couldn't open VS Code");
  if (ok) toast(what === "notebook" ? `Opened ${nb.name} in VS Code. Edits saved there show here when you come back.`
    : `Opened ${where.repo_label || "the notebooks folder"} in VS Code.`);
}

// Back in the app: when the open notebook's file changed outside it and nothing here is unsaved or
// running, show the file as it is now. Cells whose code didn't change keep their outputs.
async function nbReloadIfChangedOutside() {
  const nb = state.notebook;
  if (state.view !== "notebook" || !nb || nb.dirty || nb.diskConflict || !nb.diskStamp || nbRunning(nb)) return;
  const now = await rpc("notebook_disk_stamp", { name: nb.name }).catch(() => null);
  if (!now?.disk_stamp || now.disk_stamp === nb.diskStamp || state.notebook !== nb || nb.dirty || nbRunning(nb)) return;
  const fresh = await rpc("open_notebook", { name: nb.name }).catch(() => null);
  if (!fresh || state.notebook !== nb || nb.dirty || nbRunning(nb)) return;
  loadIntoState(fresh);
  renderNotebook();
  toast(`${fresh.name} changed outside KratosForge, for example in VS Code. Showing the file as it is now.`);
}

window.addEventListener("focus", () => { nbReloadIfChangedOutside(); });

// Set up a repository for VS Code (vscode_setup.py): adds the files a developer who opens the
// repository in VS Code needs. Files already there are never replaced; for Copilot's instructions
// the text to paste is shown instead. Nothing is committed.
async function vscodeSetupDialog(root, label, after) {
  const plan = await attempt(() => rpc("git_vscode_plan", { repo: root }), "Couldn't read the repository");
  if (!plan) return;
  const chosen = new Set(plan.files.filter((f) => f.state === "new").map((f) => f.path));
  const add = btn("", null, () => run(), "tool primary");
  const count = () => {
    add.textContent = chosen.size ? `Add ${chosen.size} file${chosen.size === 1 ? "" : "s"}` : "Nothing to add";
    add.disabled = !chosen.size;
  };
  const row = (f) => {
    const box = h("input", { type: "checkbox", checked: f.state === "new" ? true : null, disabled: f.state === "new" ? null : true,
      "aria-label": f.path, onchange: (e) => { e.target.checked ? chosen.add(f.path) : chosen.delete(f.path); count(); } });
    const state = { new: ["New", "info"], exists: ["Already there", "neu"], same: ["Already set up", "pass"] }[f.state];
    const paste = f.state === "exists" && f.path.endsWith(".md")
      ? h("details", {}, h("summary", {}, "Show the text to add"), h("pre", { class: "vscode-setup-text" }, f.text),
        btn("Copy", "document", () => attempt(() => navigator.clipboard.writeText(f.text).then(() => toast("Copied. Paste it into the file.")), "Could not copy"), "tool outline small"))
      : null;
    return h("div", { class: "vscode-setup-file" }, box,
      h("div", { class: "grow" }, h("label", {}, h("code", {}, f.path), h("div", { class: "muted-text small" }, `${f.label}. ${f.why}`
        + (f.state === "exists" ? " It's already in the repository, so it's left as it is." : ""))), paste),
      h("span", { class: `pill-s ${state[1]}` }, state[0]));
  };
  const run = async () => {
    const out = await attempt(() => rpc("git_vscode_apply", { repo: root, files: [...chosen] }), "Couldn't add the files");
    if (!out) return;
    closeDialog();
    toast(out.added.length ? `Added ${out.added.length} file${out.added.length === 1 ? "" : "s"} to ${label}. Commit them in Source control to share them.`
      : "Nothing was added: the files are already there.");
    if (out.skipped.length) toast(`Left as they are: ${out.skipped.join(", ")}.`, true);
    if (after) after(out.status);
  };
  count();
  showDialog(`Set up ${label} for VS Code`, h("div", { class: "scroll" },
    h("p", {}, "Adds files that make VS Code ready for anyone who clones this repository: a task that validates the open notebook, the KratosForge server for Copilot, and the extensions to install. Files already there are never replaced."),
    h("div", { class: "vscode-setup-files" }, plan.files.map(row)),
    h("div", { class: "callout" }, icon("info", 20), h("span", {},
      h("b", {}, "How this works. "), "Writes the ticked files in this repository's folder on this PC, and the kratosforge command the task runs. Nothing is committed or pushed; they show under Changes for you to review. ",
      "Doesn't write Fabric, OneLake or VS Code's own settings. The Validate task reads the notebook file and runs nothing; Copilot's tools still ask you here before they run code."))),
  [btn("Cancel", null, closeDialog, "tool outline"), add]);
}

// kratosforge:// links (deep_link.rs, docs/vscode.md): VS Code, a terminal or a browser asks the
// app to show a notebook or a page. A link only shows something; nothing in it is run, and unsaved
// edits here are saved first, as when you open another notebook yourself.
async function showLink(link) {
  if (link?.kind === "refused") {
    toast(`KratosForge didn't open that link.${link.reason ? ` ${link.reason}` : ""}`, true);
    return;
  }
  if (link?.kind === "page") {
    const page = railFeatures().concat(railFeatures(true)).map((f) => Object.keys(f.views || {})[0]).find((v) => v === link.page);
    if (page) go(page); else toast(`KratosForge has no page called ${link.page}.`, true);
    return;
  }
  if (link?.kind !== "notebook") return;
  const name = link.name ?? (await rpc("notebook_name_for_path", { path: link.path }).catch(() => ({}))).name;
  const known = name && (await rpc("list_notebooks").catch(() => [])).some((n) => n.name === name);
  if (!known) {
    toast(link.path ? `${link.path} isn't in the notebooks folder or a notebooks repository KratosForge knows. Import it, or open its repository in Source control.`
      : `There's no notebook named ${name}.`, true);
    return;
  }
  if (state.notebook?.name === name && state.view === "notebook") return;
  await openNotebook(name);
}

async function listenForLinks() {
  if (!window.__TAURI__?.event) return;
  await window.__TAURI__.event.listen("deep-link", ({ payload }) => {
    try { showLink(JSON.parse(payload)); } catch { /* not a link the shell made */ }
  });
  const pending = await invoke("take_pending_link").catch(() => null);
  if (pending) showLink(JSON.parse(pending));
}


// Run cells from VS Code (editor_service.py, docs/vscode.md): a VS Code window asks to pair, the
// person pairs it here, and its cells then run in each notebook's own session on this PC without
// asking each time. Writing to OneLake from such a cell still asks here, every time.

const editorPair = { status: null, shown: new Set(), watching: false, timer: 0 };
const KEEP_LABEL = { session: "Until KratosForge closes", "8h": "8 hours", "7d": "7 days" };

function vscodeRunCard(status, refresh) {
  const toggle = h("label", { class: "switch" },
    h("input", { type: "checkbox", checked: status.enabled ? true : null, disabled: status.blocked ? true : null,
      "aria-label": "Let VS Code run notebooks",
      onchange: async (e) => {
        const res = await attempt(() => rpc("editor_set_enabled", { enabled: e.target.checked }), "Not changed");
        if (res) toast(res.enabled ? "VS Code windows can ask to pair" : "VS Code can't run notebooks here now. Every pairing ended.");
        refresh();
        watchEditorRequests();
      } }), h("span", {}, status.enabled ? "On" : "Off"));
  const end = async (p) => {
    if (await attempt(() => rpc("editor_end_pairing", { pairing_id: p.id }), "Couldn't end it")) toast(`${p.window} isn't paired any more`);
    refresh();
  };
  const rows = status.pairings || [];
  return h("section", { class: "card pad" },
    // Always shown: a pairing that is on must stay where it can be turned off.
    previewMarked("vscode", h("div", { class: "row" }, h("h2", {}, "VS Code"))),
    settingRow("Let VS Code run notebooks",
      status.blocked || "A VS Code window you pair runs cells in each notebook's own session on this PC, without asking each time. Writing to OneLake still asks here, every time.",
      toggle, status.blocked ? "policy" : null),
    status.enabled ? (rows.length
      ? h("table", { class: "list" },
        h("thead", {}, h("tr", {}, h("th", {}, "Paired window"), h("th", {}, "Kept"), h("th", {}, "Last used"), h("th", {}, ""))),
        h("tbody", {}, rows.map((p) => h("tr", {},
          h("td", {}, h("code", {}, p.window), h("div", { class: "muted-text small" }, p.client)),
          h("td", {}, p.expires ? `Until ${dateTimeText(p.expires * 1000)}` : KEEP_LABEL.session),
          h("td", {}, p.last_used ? integWhen(p.last_used) : "Not yet"),
          h("td", { class: "n" }, btn("End", null, () => end(p), "tool outline small", { "aria-label": `End pairing with ${p.window}` }))))))
      : h("p", { class: "muted-text" }, "No VS Code window is paired. In VS Code, pick KratosForge as a notebook's kernel to pair it.")) : null,
    h("p", { class: "muted-text small" }, "AI tools such as Copilot still ask before every run; pairing is only for the notebooks you run yourself. Every run from VS Code is in the audit trail. ",
      learnMore("run-cells-from-vs-code")));
}

function pairingDialog(req) {
  const keep = h("select", { "aria-label": "Keep this pairing for" },
    Object.entries(KEEP_LABEL).map(([v, label]) => h("option", { value: v }, v === "session" ? "until KratosForge closes" : label)));
  const answer = async (approve) => {
    closeDialog();
    const res = await attempt(() => rpc("editor_decide_pairing", { request_id: req.id, approve, keep: keep.value }), "Couldn't answer");
    if (res) toast(approve ? `Paired ${req.window}. Its cells run here now.` : "Denied. That VS Code window isn't paired.");
  };
  const list = (title, items) => h("div", { class: "editor-pair-list" }, h("b", {}, title), h("ul", {}, items.map((t) => h("li", {}, t))));
  showDialog("Let VS Code run notebooks on this PC?", h("div", { class: "scroll editor-pair" },
    h("dl", { class: "about" },
      h("dt", {}, "Asked by"), h("dd", {}, req.client, h("span", { class: "muted-text small" }, " (as it names itself)")),
      h("dt", {}, "Window"), h("dd", {}, h("code", {}, req.window)),
      h("dt", {}, "Pairing code"), h("dd", {}, h("code", { class: "editor-pair-code" }, req.code))),
    h("p", { class: "muted-text small" }, "Check VS Code shows the same code. If you didn't just pick KratosForge in VS Code, deny."),
    h("div", { class: "editor-pair-lists" },
      list("Without asking again", ["Run cells on this PC in each notebook's own session, as you", "Read lakehouse tables the notebook can open", "Show results, variables and errors"]),
      list("Still asks you here, every time", ["Write tables or files to OneLake", "Publish to Fabric and Run in Fabric are done here, not from VS Code"])),
    h("label", { class: "row" }, "Keep this pairing ", keep),
    h("p", { class: "muted-text small" }, "AI tools such as Copilot still ask before every run. Paired windows are listed in Settings > Integrations, where you can end them.")),
  [btn("Deny", null, () => answer(false), "tool outline"), h("span", { class: "spacer" }), btn("Pair this window", null, () => answer(true), "tool primary")]);
}

function writeDialog(ask) {
  const answer = async (approve) => {
    closeDialog();
    await attempt(() => rpc("editor_decide_write", { ask_id: ask.id, approve }), "Couldn't answer");
  };
  showDialog("VS Code wants to write to OneLake", h("div", {},
    h("p", {}, "A cell in ", h("b", {}, ask.notebook), ", run from the VS Code window ", h("code", {}, ask.window), ", writes ", h("code", {}, ask.target), "."),
    h("p", { class: "muted-text small" }, "Allow once lets this cell write that table or file. The cell waits up to 5 minutes for your answer.")),
  [btn("Deny", null, () => answer(false), "tool outline"), h("span", { class: "spacer" }), btn("Allow once", null, () => answer(true), "tool primary")]);
}

// Polls while VS Code may pair, from any page: a request shows over whatever the person is doing.
async function watchEditorRequests() {
  if (editorPair.watching) return;
  editorPair.watching = true;
  let wait = 30000;
  try {
    const res = await rpc("editor_pending");
    wait = res.enabled ? 2000 : 30000;
    const fresh = [...res.pairing.map((r) => ["pair", r]), ...res.writes.map((w) => ["write", w])].filter(([, r]) => !editorPair.shown.has(r.id));
    if (fresh.length) {
      const [kind, r] = fresh[0];
      if (document.getElementById("dialog-root")?.children.length) {
        toast(kind === "pair" ? "A VS Code window is asking to pair. Close this dialog to answer." : "VS Code is waiting to write to OneLake. Close this dialog to answer.", false, { log: true });
      } else {
        editorPair.shown.add(r.id);
        (kind === "pair" ? pairingDialog : writeDialog)(r);
      }
    }
  } catch { /* the engine isn't up yet */ }
  editorPair.watching = false;
  clearTimeout(editorPair.timer);
  editorPair.timer = setTimeout(watchEditorRequests, wait);
}

setTimeout(watchEditorRequests, 5000);
