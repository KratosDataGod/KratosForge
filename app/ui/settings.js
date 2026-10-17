// Settings, the account menu, Report a problem, What's new and
// Get started. The values live in the sidecar's app_settings.py: this person's choices, or
// what their organization fixed in its policy file.

const SOURCE_TEXT = {
  policy: "Set by your organization",
  environment: "Set on this PC (FABRIC_DESKTOP_CLIENT_ID)",
  user: "Set by you",
  built_in: "Comes with Fabric Desktop by KratosBI",
  default: "Default",
};
// Shown under the app ID when nothing set one.
const MICROSOFT_SIGN_IN = "Microsoft's Fabric sign-in (no app registration)";

async function loadSettings() {
  state.settings = await rpc("app_settings").catch(() => state.settings || null);
  return state.settings;
}

async function saveSettings(changes, label = "Not saved") {
  const res = await attempt(() => rpc("save_app_settings", { changes }), label);
  if (res) state.settings = res;
  return res;
}

function clientId() {
  return state.settings?.client_id?.value || "";
}

// The link an Entra admin opens once to approve Fabric Desktop for everyone in their organization.
function adminConsentUrl(id = clientId()) {
  return `https://login.microsoftonline.com/organizations/adminconsent?client_id=${encodeURIComponent(id)}`;
}

async function copyText(text, what) {
  try {
    await navigator.clipboard.writeText(text);
    toast(`${what} copied`);
    return true;
  } catch {
    return false;
  }
}

function openLink(url) {
  return attempt(() => invoke("open_link", { url }), "Could not open the link");
}

// ---------- sign-out ----------

async function signOut() {
  if (!(await confirmDialog("Sign out?", "Fabric Desktop by KratosBI forgets your sign-in on this PC, in the app and in the Fabric views. " +
    "Scheduled runs on this PC wait until someone signs in again. Notebooks and settings stay.", "Sign out"))) return;
  await attempt(() => rpc("sign_out"), "Sign-out failed");
  await invoke("fabric_view_sign_out").catch(() => {});
  Object.assign(state, { user: null, tenant: null, workspaces: null, openWorkspace: null });
  toast("Signed out");
  go("home");
}

// ---------- account menu ----------

function accountMenu(anchor) {
  const items = state.user
    ? [{ info: `Signed in as ${state.user}` }]
    : [{ label: "Sign in", icon: "person", action: () => signIn().then((ok) => ok && render()) }];
  items.push(
    { label: "Settings", icon: "settings", action: () => go("settings") },
    { label: "Help and user guide", icon: "help", action: () => invoke("open_doc", { page: "user-guide.md" }).catch(() => {}) },
    { label: "Report a problem", icon: "bug", action: reportProblem },
    { label: "What's new", icon: "rocket", action: () => showWhatsNew(true) },
    { info: `Fabric Desktop by KratosBI build ${state.build || "unknown"} · ${channelLabel()}` },
  );
  if (state.user) items.push({ label: "Sign out", icon: "signOut", action: signOut });
  showMenu(anchor, items);
}

function channelLabel() {
  return state.settings?.channel?.value === "preview" ? "Preview channel" : "Stable channel";
}

// ---------- Report a problem ----------

// Takes out what a public issue mustn't carry: tokens, keys, passwords, email addresses, the
// Windows user name in paths, and tenant, workspace and item ids. The same rules as the engine's
// redact.py; both are tested against app/tests/redact-cases.json.
function redactReport(text, level = "support") {
  const slash = "(?:\\\\\\\\|\\\\|/)+";
  const out = String(text)
    .replace(/eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]*/g, "[removed]")
    .replace(/(\bbearer\s+)[A-Za-z0-9._~+/=\-]{8,}/gi, "$1[removed]")
    .replace(/([?&]sig=)[^&\s"'<>]+/gi, "$1[removed]")
    .replace(/\b(client_secret|password|passwd|pwd|accountkey|sharedaccesskey|access_token|refresh_token|id_token|api[_-]?key|secret)("?\s*[=:]\s*"?)[^\s"'&;,]+/gi, "$1$2[removed]");
  if (level !== "support") return out;
  return out
    .replace(/[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+/g, "[email]")
    .replace(new RegExp(`([a-z]:${slash}(?:users|documents and settings)${slash})([^\\\\/"'<>\\r\\n]+)`, "gi"), "$1[user]")
    .replace(/(\/home\/|\/Users\/)([^\\/\s"'<>]+)/g, "$1[user]")
    .replace(/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi, "[id]");
}

function diagnostics() {
  const errors = activity.items.filter((i) => i.status === "error" || i.status === "failed").slice(0, 10);
  return [
    `Fabric Desktop by KratosBI build: ${state.build || "unknown"} (${channelLabel()})`,
    `Engine Python: ${state.python || "unknown"}`,
    `Signed in: ${state.tenant ? "yes" : "no"}`,
    `Page: ${state.view}`,
    `Windows: ${navigator.userAgent}`,
    "",
    errors.length ? "Recent problems (newest first):" : "No recent problems in Activity.",
    ...errors.map((i) => `- ${new Date(i.at).toISOString()} ${i.title}${i.text ? `: ${i.text}` : ""}${i.details ? `\n    ${String(i.details).slice(0, 400).replace(/\n/g, "\n    ")}` : ""}`),
  ].map((line) => redactReport(line)).join("\n");
}

function reportProblem() {
  const what = h("textarea", { rows: 3, placeholder: "What were you doing, and what went wrong?" });
  const info = h("textarea", { rows: 9, class: "mono", readonly: true }, diagnostics());
  const support = state.settings?.support_url?.value || "";
  const report = () => redactReport(`${what.value.trim() || "(no description)"}\n\n${info.value}`);
  const send = async () => {
    const text = report();
    if (support.startsWith("mailto:")) {
      // Mail apps cap a link's length; the full text is also on the clipboard.
      await copyText(text, "Report");
      openLink(`${support}${support.includes("?") ? "&" : "?"}subject=${encodeURIComponent("Fabric Desktop by KratosBI problem")}&body=${encodeURIComponent(text.slice(0, 1500))}`);
    } else {
      await copyText(text, "Report");
      openLink(support || "https://github.com/KratosDataGod/fabric-desktop/issues/new");
    }
    closeDialog();
  };
  showDialog("Report a problem", h("div", { class: "setup" },
    h("label", { class: "field" }, "What happened", what),
    h("label", { class: "field" }, "Sent with it (no data, file contents, passwords, email addresses or ids)", info),
    h("p", { class: "muted-text small" }, support
      ? "Send copies all of this and opens your organization's support contact. Paste it there if it isn't filled in."
      : "Send copies all of this and opens a new issue on GitHub. Paste it there.")),
  [btn("Copy only", null, () => copyText(report(), "Report"), "tool outline"), h("span", { class: "spacer" }),
    btn("Cancel", null, closeDialog, "tool outline"), btn("Send", null, send, "tool primary")]);
}

// ---------- What's new ----------

// After an update, the release notes of the build now running, once. From the account menu,
// whenever asked.
function showWhatsNew(asked = false) {
  const notes = state.release?.commit && state.release.commit === state.commit ? state.release.notes : "";
  let seen = "";
  try { seen = localStorage.getItem("fd.whatsnew.seen") || ""; } catch { /* show it */ }
  if (!asked && (!notes || !state.commit || seen === state.commit)) return;
  try { localStorage.setItem("fd.whatsnew.seen", state.commit || ""); } catch { /* shown again next time */ }
  const lines = String(notes || "").split(/\r?\n/).map((l) => l.replace(/^[-*]\s*/, "").trim()).filter(Boolean);
  showDialog("What's new in Fabric Desktop by KratosBI", h("div", { class: "setup" },
    lines.length ? h("ul", { class: "whatsnew" }, lines.map((l) => h("li", {}, l)))
      : h("p", {}, "No notes for this build."),
    h("p", { class: "muted-text small" }, `Build ${state.build || "unknown"} · ${channelLabel()}. `, learnMore("updates", "About updates"))),
  [btn("Got it", null, closeDialog, "tool primary")]);
}

// ---------- Settings page ----------

function settingRow(title, help, control, source) {
  return h("div", { class: "setting" },
    h("div", { class: "setting-text" }, h("b", {}, title), help ? h("div", { class: "muted-text small" }, help) : null,
      source ? h("div", { class: "setting-source" }, SOURCE_TEXT[source] || source) : null),
    h("div", { class: "setting-control" }, control));
}

async function renderSettings() {
  setView(h("div", { class: "page settings" }, h("h1", {}, "Settings"), h("p", { class: "muted-text" }, "Loading...")));
  const s = await loadSettings();
  if (state.view !== "settings") return;
  if (!s) {
    setView(h("div", { class: "page settings" }, h("h1", {}, "Settings"),
      h("p", { class: "muted-text" }, "Settings load once the local engine is running.")));
    return;
  }
  const locked = (key) => s[key].source === "policy";

  // Account
  const idInput = h("input", { value: s.client_id.source === "user" ? s.client_id.value : "", spellcheck: "false",
    placeholder: s.client_id.source === "default" ? "Empty: Microsoft's Fabric sign-in" : s.client_id.value, disabled: locked("client_id") ? true : null });
  const account = h("section", { class: "card pad" }, h("h2", {}, "Account"),
    settingRow(state.user ? `Signed in as ${state.user}` : "Not signed in",
      state.user ? (state.tenant ? `Directory ${state.tenant}` : "") : "Sign in to see your Fabric workspaces and lakehouses.",
      state.user ? btn("Sign out", "signOut", signOut, "tool outline")
        : btn("Sign in", "person", async () => { if (await signIn()) render(); }, "tool primary")),
    settingRow("App (client) ID", "Leave it empty: Fabric Desktop by KratosBI signs in through Microsoft's own Fabric sign-in, so nobody has to register an app. Only paste an ID if your organization gave you its own app.",
      h("div", { class: "row" }, idInput,
        locked("client_id") ? null : btn("Save", null, async () => {
          if (await saveSettings({ client_id: idInput.value.trim() })) { toast("Saved. Sign in again to use it."); render(); }
        }, "tool outline")),
      s.client_id.source === "default" ? MICROSOFT_SIGN_IN : s.client_id.source),
    s.client_id.source !== "default" ? settingRow("Admin approval",
      "If sign-in says your organization needs to approve Fabric Desktop by KratosBI, send this link to a Microsoft Entra admin. They open it once for everyone.",
      btn("Copy the link", "copyArrowRight", async () => {
        if (!(await copyText(adminConsentUrl(), "Link"))) toast(adminConsentUrl());
      }, "tool outline")) : null);

  // Updates
  const channel = (value, label, help) => h("label", { class: `choice-row${s.channel.value === value ? " selected" : ""}` },
    h("input", { type: "radio", name: "channel", value, checked: s.channel.value === value ? true : null, disabled: locked("channel") ? true : null,
      onchange: async () => { if (await saveSettings({ channel: value })) { toast(`${label}: takes effect the next time Fabric Desktop by KratosBI starts`); render(); } } }),
    h("span", {}, h("b", {}, label), h("span", { class: "muted-text small" }, help)));
  const updates = h("section", { class: "card pad" }, h("h2", {}, "Updates"),
    h("p", { class: "muted-text small" }, s.auto_update?.value === false
      ? "Your organization rolls out new builds, so Fabric Desktop by KratosBI doesn't update itself. "
      : "Fabric Desktop by KratosBI checks for an update each time it starts and installs it before opening. ",
      `This is build ${state.build || "unknown"}. `, learnMore("updates")),
    h("div", { class: "choices" },
      channel("stable", "Stable", "Builds that have been checked and released. Recommended."),
      channel("preview", "Preview", "Every new build as soon as it's ready. For testers; things may change or break.")),
    s.channel.source === "default" ? null : h("div", { class: "setting-source" }, SOURCE_TEXT[s.channel.source]),
    h("div", { class: "row" }, btn("What's new in this build", "rocket", () => showWhatsNew(true), "tool link small")));

  // Preview features
  const previews = h("section", { class: "card pad" }, h("h2", {}, "Preview features"),
    settingRow("Show features in preview",
      `These work but haven't been tried on many Fabric tenants yet, so they may change: ${Object.values(PREVIEW_FEATURES).join(", ")}.`,
      h("label", { class: "switch" }, h("input", { type: "checkbox", checked: previewShown() ? true : null, disabled: locked("show_preview") ? true : null,
        onchange: async (e) => { if (await saveSettings({ show_preview: e.target.checked })) render(); } }), h("span", {}, previewShown() ? "On" : "Off")),
      s.show_preview.source === "policy" ? "policy" : null),
    s.kernel_process ? settingRow("Run notebooks in their own process (Preview)",
      "Cells run in a Python process of their own instead of inside the app's engine. Stop and a crash then end only that process, and the rest of the app keeps working. Variables are cleared when you switch.",
      h("label", { class: "switch" }, h("input", { type: "checkbox", checked: s.kernel_process.value ? true : null, disabled: locked("kernel_process") ? true : null,
        onchange: async (e) => { await saveSettings({ kernel_process: e.target.checked }); render(); } }),
      h("span", {}, s.kernel_process.value ? "On" : "Off")),
      s.kernel_process.source === "policy" ? "policy" : null) : null,
    s.kernel_process?.value && s.cell_time_limit_minutes ? limitRow("cell_time_limit_minutes", "Cell time limit",
      "A cell that runs longer is interrupted. Variables are kept. If it doesn't stop within 30 seconds, its process is ended and variables are cleared. Proofs and side by side use the same limit.",
      [[0, "Off"], [15, "15 minutes"], [60, "1 hour"], [240, "4 hours"], [720, "12 hours"]]) : null,
    s.kernel_process?.value && s.kernel_memory_limit_gb ? limitRow("kernel_memory_limit_gb", "Notebook memory limit",
      "The notebook's process is ended if it uses more, so one notebook can't slow the whole PC. Variables are cleared.",
      [[0, "Off"], [4, "4 GB"], [8, "8 GB"], [16, "16 GB"], [32, "32 GB"]]) : null);

  // A limit from Settings: a fixed list, plus the current value when the policy file set another.
  function limitRow(key, title, help, options) {
    const current = Number(s[key].value || 0);
    if (!options.some(([v]) => v === current)) options = [...options, [current, key.endsWith("_gb") ? `${current} GB` : `${current} minutes`]];
    return settingRow(h("span", {}, title, " ", previewTag()), help,
      h("select", { "aria-label": title, disabled: locked(key) ? true : null,
        onchange: async (e) => { if (await saveSettings({ [key]: Number(e.target.value) })) render(); } },
        options.map(([v, label]) => h("option", { value: v, selected: v === current ? true : null }, label))),
      s[key].source === "policy" ? "policy" : null);
  }

  // Notifications
  const notify = h("section", { class: "card pad" }, h("h2", {}, "Notifications"),
    settingRow("Teams alerts", "Post late and failed tables to a Teams channel. The bell always shows them.",
      btn("Set up", "alert", () => alertsDialog(), "tool outline")));

  // Help and about
  const about = h("section", { class: "card pad" }, h("h2", {}, "Help and about"),
    h("div", { class: "row wrap" },
      btn("User guide", "help", () => invoke("open_doc", { page: "user-guide.md" }).catch(() => {}), "tool outline"),
      btn("Report a problem", "bug", reportProblem, "tool outline"),
      btn("Guide for IT admins", "shield", () => invoke("open_doc", { page: "permissions.md" }).catch(() => {}), "tool outline")),
    h("dl", { class: "about" },
      h("dt", {}, "Build"), h("dd", {}, state.build || "unknown"),
      h("dt", {}, "Engine Python"), h("dd", {}, state.python || "unknown"),
      h("dt", {}, "Organization settings file"), h("dd", { class: "mono" }, s.policy_file),
      s.allow_fabric_runs?.value === false ? [h("dt", {}, "Runs in Fabric"),
        h("dd", {}, "Off: your organization keeps work on this PC")] : null,
      s.allowed_tenants?.value?.length ? [h("dt", {}, "Sign-in"),
        h("dd", {}, "Only your organization's own work accounts")] : null,
      s.policy_problems?.length ? [h("dt", {}, "Ignored in that file"),
        h("dd", {}, h("ul", { class: "plain" }, s.policy_problems.map((p) => h("li", {}, p))))] : null));

  // Integrations (external AI tools, integrations.js) is a tab: the left rail is full.
  const tab = previewShown() && state.settingsTab === "integrations" ? "integrations" : "general";
  const tabs = previewShown() ? h("div", { class: "subtabs" },
    [["general", "General"], ["integrations", "Integrations"]].map(([id, label]) =>
      h("button", { class: `tab${tab === id ? " active" : ""}`, onclick: () => { state.settingsTab = id; render(); } },
        label, id === "integrations" ? previewTag() : null))) : null;
  const integrations = h("div", { class: "integrations" });
  setView(h("div", { class: "page settings" },
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Settings"),
      h("p", { class: "sub" }, "How Fabric Desktop by KratosBI signs in, updates and talks to you. ", learnMore("settings")))),
    tabs, tab === "integrations" ? integrations : [account, updates, previews, notify, about]));
  if (tab === "integrations") {
    renderIntegrationsTab(integrations);
    return;
  }
  // The platform's status comes from the engine; it fills in after the page shows.
  const platform = await platformSection();
  if (state.view === "settings" && previewShown()) notify.before(platform);
}

// ---------- Ready to work (Home): can this PC do real work yet? ----------

const READY_MARK = { ok: ["ok", "Ready"], fail: ["error", "Needs attention"], warn: ["warning", "Worth a look"],
  wait: ["info", "Not checked yet"] };

function readinessFix(row, rerun) {
  const fixes = {
    sign_in: ["Sign in", async () => { if (await signIn()) render(); }],
    retry: ["Check again", rerun],
    workspaces: ["Open Workspaces", () => go("workspaces")],
    capacity: ["How to get capacity", () => invoke("open_doc", { page: row.doc }).catch(() => {})],
    settings: ["Open Settings", () => go("settings")],
  };
  const fix = fixes[row.fix];
  return fix ? btn(fix[0], null, fix[1], row.status === "fail" ? "tool primary small" : "tool outline small") : null;
}

function readinessRow(row, rerun, extra = null) {
  const [mark, label] = READY_MARK[row.status] || READY_MARK.wait;
  return h("li", { class: `ready-row ${row.status}` },
    h("span", { class: "ready-mark", role: "img", "aria-label": label }, icon(mark, 18)),
    h("div", { class: "grow" }, h("b", {}, row.title), h("div", { class: "muted-text small" }, row.text),
      row.status !== "ok" && row.status !== "wait" ? h("div", { class: "row" }, readinessFix(row, rerun)) : null),
    extra);
}

function writeTestDialog(done) {
  const wsSelect = h("select", {}, h("option", { value: "" }, "Loading workspaces..."));
  const lhSelect = h("select", { disabled: true }, h("option", { value: "" }, "Pick a workspace first"));
  const why = h("div", { class: "muted-text small" });
  const start = btn("Write and delete the test file", null, async () => {
    const ws = wsSelect.selectedOptions[0];
    start.disabled = true;
    const result = await attempt(() => rpc("readiness_write_test",
      { workspace_id: ws.value, lakehouse_id: lhSelect.value, workspace_name: ws.textContent }), "Write test");
    closeDialog();
    if (result) done(result);
  }, "tool primary", { disabled: true });
  const canGo = () => { start.disabled = !(wsSelect.value && lhSelect.value); };
  wsSelect.addEventListener("change", async () => {
    lhSelect.disabled = true;
    lhSelect.replaceChildren(h("option", { value: "" }, "Loading lakehouses..."));
    const lakehouses = await attempt(() => rpc("list_lakehouses", { workspace_id: wsSelect.value }), "Lakehouses") || [];
    lhSelect.replaceChildren(...(lakehouses.length ? lakehouses.map((l) => h("option", { value: l.id }, l.displayName))
      : [h("option", { value: "" }, "No lakehouses in this workspace")]));
    lhSelect.disabled = !lakehouses.length;
    canGo();
  });
  lhSelect.addEventListener("change", canGo);
  showDialog("Test a write to OneLake", h("div", { class: "stack" },
    h("p", {}, "This writes one small file to the lakehouse you pick, reads it back and deletes it. No table is touched."),
    h("label", { class: "field" }, "Workspace", wsSelect),
    h("label", { class: "field" }, "Lakehouse", lhSelect),
    h("div", { class: "callout small" }, "Writes Files/_fabric_desktop_check/write-test.txt, then deletes that folder. ",
      "Workspaces whose name says Prod aren't offered."), why),
    [btn("Cancel", null, closeDialog, "tool outline"), start]);
  rpc("list_workspaces").then((spaces) => {
    const usable = spaces.filter((w) => w.capacityId && !/(^|[^a-z])(prod|prd|production)([^a-z]|$)/i.test(w.displayName));
    wsSelect.replaceChildren(h("option", { value: "" }, usable.length ? "Pick a workspace" : "No Dev or Test workspace on a capacity"),
      ...usable.map((w) => h("option", { value: w.id }, w.displayName)));
  }).catch((err) => { why.textContent = friendlyError(err).text; });
}

function readinessCard() {
  let hidden = false;
  try { hidden = localStorage.getItem("fd.ready.hidden") === "1"; } catch { /* shown */ }
  if (hidden) return null;
  const box = h("section", { class: "card pad ready", "aria-labelledby": "ready-title" });
  let writeRow = null;
  const draw = (rows, checkedAt) => {
    const all = writeRow ? [...rows, writeRow] : rows;
    const good = all.filter((r) => r.status === "ok").length;
    const head = h("div", { class: "row" }, h("h2", { class: "grow", id: "ready-title" }, "Ready to work"),
      h("span", { class: "muted-text small" }, `${good} of ${rows.length + 1} ready`),
      btn("Check again", null, run, "tool outline small"),
      btn("Hide", null, () => { try { localStorage.setItem("fd.ready.hidden", "1"); } catch { /* */ } box.remove(); }, "tool link small"));
    if (rows.every((r) => r.status === "ok") && writeRow?.status === "ok") {
      box.replaceChildren(h("div", { class: "row" }, icon("ok", 18), h("b", { class: "grow" }, "Ready to work."),
        h("span", { class: "muted-text small" }, `Checked ${checkedAt}`), btn("Check again", null, run, "tool link small")));
      return;
    }
    const capacityOk = rows.find((r) => r.key === "capacity")?.status === "ok";
    const write = writeRow || { key: "write", title: "Writing to OneLake", status: "wait",
      text: capacityOk ? "Not checked. It writes a small test file, so it waits until you ask."
        : "Not checked. It needs a lakehouse you can write to, so it waits for the check above." };
    const tester = btn("Test a write", null, () => writeTestDialog((r) => { writeRow = r; draw(rows, "just now"); }),
      "tool outline small", { disabled: capacityOk ? null : true });
    box.replaceChildren(head,
      h("p", { class: "muted-text small" }, "Each check reads only. Nothing in Fabric is changed unless you choose Test a write."),
      h("ol", {}, rows.map((r) => readinessRow(r, run)), readinessRow(write, run, write.status === "ok" ? null : tester)),
      h("p", { class: "muted-text small" }, learnMore("ready-to-work", "What each check means")));
  };
  async function run() {
    box.replaceChildren(h("h2", { id: "ready-title" }, "Ready to work"), h("div", { class: "muted-text" }, "Checking..."));
    const rows = await attempt(() => rpc("readiness_check"), "Ready to work");
    if (rows) draw(rows, new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
  }
  run();
  return box;
}

// ---------- Get started (Home, until there's a metadata project) ----------

function markDone(step) {
  try { localStorage.setItem(`fd.done.${step}`, "1"); } catch { /* not remembered */ }
}

function getStarted(project, notebookCount) {
  let hidden = false;
  const done = (step) => { try { return localStorage.getItem(`fd.done.${step}`) === "1"; } catch { return false; } };
  try { hidden = localStorage.getItem("fd.getstarted.hidden") === "1"; } catch { /* shown */ }
  if (hidden || project?.path) return null;
  const steps = [
    [!!state.user, "Sign in with your work account", "So the app can see your Fabric workspaces.", () => signIn().then((ok) => ok && render())],
    [done("lakehouse"), "Open a lakehouse", "Browse its tables and files, or query them with SQL.", () => go("onelake")],
    [notebookCount > 0, "Run a notebook on this PC", "Python and Spark-style code, run on your own processor.", () => newNotebook()],
    [false, "Set up a metadata project", "Describe your medallion tables once and let Fabric Desktop by KratosBI build and run them.", () => go("metadata")],
  ];
  const box = h("section", { class: "card pad get-started" },
    h("div", { class: "row" }, h("h2", { class: "grow" }, "Get started"),
      btn("Hide", null, () => { try { localStorage.setItem("fd.getstarted.hidden", "1"); } catch { /* */ } box.remove(); }, "tool link small")),
    h("ol", {}, steps.map(([ok, title, help, run]) => h("li", { class: ok ? "done" : null },
      h("span", { class: "step-mark" }, ok ? icon("ok", 16) : null),
      h("div", { class: "grow" }, h("b", {}, title), h("div", { class: "muted-text small" }, help)),
      ok ? null : btn("Start", null, run, "tool outline small")))),
    h("p", { class: "muted-text small" }, learnMore("get-started", "Read the user guide")));
  return box;
}

registerFeature({
  id: "settings",
  rail: { label: "Settings", icon: "settings", order: 100, bottom: true },
  views: { settings: renderSettings },
  commands: [
    { label: "Open Settings", icon: "settings", keywords: "preferences options channel app id", run: () => go("settings") },
    { label: "Sign out", icon: "signOut", keywords: "log out account", run: signOut, when: () => !!state.user },
    { label: "Sign in", icon: "person", keywords: "log in account", run: () => signIn().then((ok) => ok && render()), when: () => !state.user },
    { label: "Report a problem", icon: "bug", keywords: "bug support help feedback", run: reportProblem },
    { label: "What's new", icon: "rocket", keywords: "release notes update changelog", run: () => showWhatsNew(true) },
    { label: "Open the user guide", icon: "help", keywords: "help docs documentation", run: () => invoke("open_doc", { page: "user-guide.md" }).catch(() => {}) },
  ],
});
