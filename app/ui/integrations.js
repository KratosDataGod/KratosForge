// Settings > Integrations: lets AI tools a developer already uses (Claude Code, Codex, Cursor, VS Code,
// any MCP client) connect to KratosForge over MCP or the local HTTP API. There is no AI
// in the app: no chat, no model picker, no AI keys. Everything here calls the engine's
// integrations_* methods (sidecar integrations/app_service.py); approvals are decided only here.
// Mockup: https://claude.ai/artifact/VbVijJzz6XRbdjg7sH1MRG

const PERMISSION_LABEL = {
  READ_METADATA: "Read metadata",
  READ_DATA: "Read data",
  WRITE_LOCAL_FILES: "Write notebooks",
  EXECUTE_LOCAL_CODE: "Run code on this PC",
  WRITE_ONELAKE: "Write to OneLake",
  MODIFY_FABRIC_ARTIFACTS: "Change Fabric items",
  DEPLOY_TO_FABRIC: "Deploy",
  MANAGE_CONNECTIONS: "Manage connections",
};
const PERMISSION_HELP = {
  READ_METADATA: "See workspaces, lakehouses, tables, notebooks and project settings. No rows.",
  READ_DATA: "Read rows from tables, a few at a time.",
  WRITE_LOCAL_FILES: "Create and change notebooks in your notebook folder.",
  EXECUTE_LOCAL_CODE: "Python and notebooks on this PC. You approve every run.",
};
// None of these has been tried with Fabric Desktop yet (docs/product/ai-integration-compatibility.md).
const TESTED_TOOLS = new Set();
const DECISION_TEXT = { pending: "Waiting", approved: "Approved, not used yet", used: "Approved by you", denied: "Denied",
  expired: "Not answered" };
const OUTCOME_PILL = { succeeded: "pass", failed: "fail", cancelled: "neu", timed_out: "wrn", running: "info", queued: "info",
  started: "wrn", unfinished: "wrn" };
// An approved call writes a "started" line before it runs; the engine drops it once the result is in.
const OUTCOME_TITLE = { started: "Started after your approval. No result yet: it is still running, or the tool or KratosForge stopped before it finished." };

const integ = { status: null, activity: null, watching: false, shown: new Set() };

function integWhen(seconds) {
  return seconds ? relTime(seconds * 1000) : "Never";
}

function permissionPills(ids) {
  return ids.map((p) => h("span", { class: `pill-s ${p === "EXECUTE_LOCAL_CODE" ? "wrn" : "info"}` }, PERMISSION_LABEL[p] || p));
}

function untestedTag(kind) {
  return TESTED_TOOLS.has(kind) ? null : h("span", { class: "pill-s neu", title: "Nobody has tried this tool with KratosForge yet." }, "Untested");
}

// ---------- the tab ----------

async function renderIntegrationsTab(box) {
  box.replaceChildren(loading());
  const [status, activity, editor] = await Promise.all([
    rpc("integrations_status").catch((err) => ({ error: friendlyError(err).text })),
    rpc("integrations_activity", { limit: 50 }).catch(() => []),
    rpc("editor_status").catch(() => null),
  ]);
  if (!box.isConnected) return;
  integ.status = status;
  integ.activity = activity;
  if (status.error) {
    box.replaceChildren(h("p", { class: "muted-text" }, `Integrations load once the local engine is running. ${status.error}`));
    return;
  }
  const refresh = () => renderIntegrationsTab(box);
  box.replaceChildren(...[
    switchCard(status, refresh),
    editor ? vscodeRunCard(editor, refresh) : null,
    status.enabled ? connectCard(status, refresh) : null,
    status.enabled ? toolsCard(status, refresh) : null,
    status.enabled && (status.extensions || []).length ? extensionsCard(status, refresh) : null,
    status.enabled && status.pending.length ? pendingCard(status.pending, refresh) : null,
    status.enabled ? activityCard(activity, status) : null,
    troubleshootingCard(status)].filter(Boolean));
}

function switchCard(status, refresh) {
  const locked = status.set_by_organization;
  const toggle = h("label", { class: "switch" },
    h("input", { type: "checkbox", checked: status.enabled ? true : null, disabled: locked ? true : null,
      onchange: async (e) => {
        const res = await attempt(() => rpc("integrations_set_enabled", { enabled: e.target.checked }), "Not changed");
        if (res) { toast(res.enabled ? "AI tools can connect" : "AI tools are turned off"); refresh(); watchApprovals(); } else refresh();
      } }), h("span", {}, status.enabled ? "On" : "Off"));
  return h("section", { class: "card pad" },
    h("div", { class: "row" }, h("h2", {}, "External AI tools"), previewTag()),
    settingRow("Let AI tools connect",
      status.enabled ? "AI tools you allow below can read your projects and, with your approval, run work on this PC."
        : "KratosForge works the same without it. Turn this on to let AI tools you already use read your projects and, with your approval, run work on this PC.",
      toggle, locked ? "policy" : null),
    status.enabled ? null : h("dl", { class: "about" },
      h("dt", {}, "Works with"), h("dd", {}, "Claude Code, Claude Desktop, Codex, Cursor, VS Code with GitHub Copilot, and any MCP client (none tried yet)"),
      h("dt", {}, "How they connect"), h("dd", {}, "MCP on this PC, or a local HTTP API that only this PC can reach"),
      h("dt", {}, "What they can do"), h("dd", {}, "Only what you allow for each tool. Anything that runs code asks you here first.")),
    h("p", { class: "muted-text small" }, "KratosForge has no AI of its own and sends nothing to an AI service. Your tools talk to it, not the other way round. ",
      learnMore("external-ai-tools")));
}

function connectCard(status, refresh) {
  const http = status.http;
  const offered = status.capabilities.filter((c) => offeredPermission(status, c.permission));
  const counts = {};
  for (const c of offered) counts[c.permission] = (counts[c.permission] || 0) + 1;
  const later = status.capabilities.length - offered.length;
  const httpToggle = h("label", { class: "switch" },
    h("input", { type: "checkbox", checked: http.switch ? true : null, disabled: http.allowed ? null : true,
      onchange: async (e) => {
        // Turning it on opens a port other programs on this PC can call, so it asks first.
        if (e.target.checked && !(await confirmDialog("Turn on the local HTTP API?",
          `Programs on this PC can then call KratosForge at ${http.url}. Each one still needs a token you make here, and gets only what you allow it.`, "Turn on"))) {
          e.target.checked = false;
          return;
        }
        const res = await attempt(() => rpc("integrations_set_http", { enabled: e.target.checked }), "The local API didn't change");
        if (res) toast(res.http.running ? `The local API is on at ${res.http.url}` : "The local API is off");
        refresh();
      } }), h("span", {}, http.running ? "On" : "Off"));
  return h("section", { class: "card pad" }, h("h2", {}, "How tools connect"),
    settingRow("MCP (stdio)", h("span", {}, "Tools start KratosForge's MCP server themselves with this command. ",
      h("span", { class: "mono" }, status.launcher)),
      h("div", { class: "row" }, h("span", { class: "pill-s pass" }, "Ready"),
        btn("Copy", "copyArrowRight", () => copyText(status.launcher, "Command"), "tool outline small"))),
    settingRow("Local HTTP API",
      http.allowed ? `REST and MCP over HTTP at ${http.url}, this PC only, with a token per tool. Off unless a tool needs it.`
        : "Your organization doesn't allow the local HTTP API on this PC.",
      httpToggle, http.allowed ? null : "policy"),
    http.switch && !http.running ? h("p", { class: "fail-text small" }, `It isn't running. The log is in ${http.log}.`) : null,
    settingRow("Things tools can do", `${offered.length} capabilities. ${Object.entries(counts)
      .map(([p, n]) => `${PERMISSION_LABEL[p]}: ${n}`).join(" · ")}. ${later ? `${later} more need permissions not offered yet.` : ""}`,
      btn("See all", null, () => capabilitiesDialog(status), "tool outline small")));
}

function offeredPermission(status, id) {
  const p = status.permissions.find((x) => x.id === id);
  return !!(p && p.offered && p.allowed);
}

function capabilitiesDialog(status) {
  showDialog("What tools can do", h("div", { class: "setup integ-scroll" },
    h("table", { class: "list" }, h("thead", {}, h("tr", {}, h("th", {}, "Capability"), h("th", {}, "Needs"), h("th", {}, "Asks you"))),
      h("tbody", {}, status.capabilities.map((c) => h("tr", {},
        h("td", {}, h("b", {}, c.title), h("div", { class: "muted-text small" },
          offeredPermission(status, c.permission) ? c.description : `Not available: ${PERMISSION_LABEL[c.permission]} isn't offered to tools ${status.permissions.find((p) => p.id === c.permission)?.allowed ? "yet" : "by your organization"}.`)),
        h("td", {}, permissionPills([c.permission])),
        h("td", {}, c.approval === "always" ? "Every time" : c.approval === "ask" ? "Unless you allow it" : "No")))))),
  [btn("Close", null, closeDialog, "tool primary")]);
}

function toolsCard(status, refresh) {
  const rows = status.clients.map((c) => h("tr", {},
    h("td", {}, h("b", {}, c.name), h("div", { class: "muted-text small" }, c.kind_label, " ", untestedTag(c.kind))),
    h("td", {}, c.transport === "http" ? "HTTP" : "stdio"),
    h("td", {}, permissionPills(c.grants), c.workspaces.length ? h("div", { class: "muted-text small" }, `${c.workspaces.length} workspaces only`) : null),
    h("td", { class: "nowrap" }, integWhen(c.last_used_at)),
    h("td", { class: "nowrap" },
      btn("Test", null, () => testTool(c), "tool link small"),
      btn("Edit", null, () => editTool(status, c, refresh), "tool link small"),
      btn("Revoke", null, () => revokeTool(c, refresh), "tool link small danger"))));
  return h("section", { class: "card pad" },
    h("div", { class: "row" }, h("h2", { class: "grow" }, "Connected tools"), btn("Add a tool", "add", () => addTool(status, refresh), "tool primary")),
    rows.length ? h("table", { class: "list" },
      h("thead", {}, h("tr", {}, h("th", {}, "Tool"), h("th", {}, "Connects by"), h("th", {}, "Allowed to"), h("th", {}, "Last used"), h("th", {}, ""))),
      h("tbody", {}, rows))
      : h("p", { class: "muted-text" }, "No tools yet. Add one to get the setup to paste into it."),
    h("p", { class: "muted-text small" }, "Revoke stops a tool at once: its next request is refused, and anything waiting for your approval is cancelled."));
}

// Extensions add capabilities that tools can use, with the same permissions and approvals
// (sidecar integrations/extensions.py). Off until switched on here; the IT policy can limit them.
function extensionsCard(status, refresh) {
  const rows = status.extensions.map((e) => {
    const toggle = h("label", { class: "switch" },
      h("input", { type: "checkbox", checked: e.enabled ? true : null, disabled: e.allowed ? null : true,
        onchange: async (ev) => {
          const res = await attempt(() => rpc("integrations_set_extension", { extension_id: e.id, enabled: ev.target.checked }), "Not changed");
          if (res) toast(ev.target.checked ? `${e.name} is on. Connected tools are told in a few seconds.` : `${e.name} is off`);
          refresh();
        } }), h("span", {}, e.enabled ? "On" : "Off"));
    return settingRow(h("span", {}, e.name, " ", h("span", { class: "muted-text small" }, `${e.version} · ${e.publisher}${e.built_in ? " · built in" : ""}`)),
      h("span", {}, e.description, " ", h("span", { class: "muted-text small" },
        `Adds: ${e.capabilities.map((c) => c.title).join(", ") || "nothing"}. Needs `), permissionPills(e.permissions),
        e.error ? h("div", { class: "fail-text small" }, `Didn't load: ${e.error}`) : null),
      toggle, e.allowed ? null : "policy");
  });
  return h("section", { class: "card pad" },
    h("div", { class: "row" }, h("h2", {}, "Extensions"), previewTag()),
    ...rows,
    h("p", { class: "muted-text small" }, "A tool still needs the permission each one asks for, and anything that runs code still asks you here."));
}

async function revokeTool(c, refresh) {
  if (!(await confirmDialog(`Revoke ${c.name}?`, "Its next request is refused and anything it's waiting on is cancelled. You can add it again later.", "Revoke", { danger: true }))) return;
  if (await attempt(() => rpc("integrations_revoke_client", { client_id: c.id }), "Not revoked")) { toast(`${c.name} revoked`); refresh(); }
}

async function testTool(c) {
  toast(`Testing ${c.name}...`);
  const res = await attempt(() => rpc("integrations_test", { client_id: c.id }), "Test failed");
  if (res) toast(res.message, !res.ok);
}

// ---------- add and edit ----------

function permissionChoices(status, granted, auto) {
  const boxes = {};
  const autoBox = h("input", { type: "checkbox", checked: auto.includes("READ_DATA") ? true : null });
  const rows = status.permissions.filter((p) => p.offered).map((p) => {
    const required = p.id === "READ_METADATA";
    const input = h("input", { type: "checkbox", checked: required || granted.includes(p.id) ? true : null,
      disabled: required || !p.allowed ? true : null });
    boxes[p.id] = input;
    return h("label", { class: "choice-row" }, input,
      h("span", {}, h("b", {}, PERMISSION_LABEL[p.id], required ? h("span", { class: "pill-s neu" }, "Required") : null),
        h("span", { class: "muted-text small" }, p.allowed ? PERMISSION_HELP[p.id] : "Your organization doesn't allow this.")));
  });
  const later = status.permissions.filter((p) => !p.offered).map((p) => PERMISSION_LABEL[p.id]);
  const node = h("div", { class: "choices" }, rows,
    h("label", { class: "choice-row" }, autoBox, h("span", {}, h("b", {}, "Don't ask me before it reads data"),
      h("span", { class: "muted-text small" }, "Running code always asks, whatever you choose."))),
    h("p", { class: "muted-text small" }, `Not available yet: ${later.join(", ")}.`));
  const value = () => {
    const grants = Object.entries(boxes).filter(([, b]) => b.checked).map(([id]) => id);
    return { grants, auto: autoBox.checked && grants.includes("READ_DATA") ? ["READ_DATA"] : [] };
  };
  return { node, value };
}

function addTool(status, refresh) {
  let kind = "claude-code";
  const tiles = h("div", { class: "integ-tools" });
  const name = h("input", { value: "Claude Code on this PC", spellcheck: "false" });
  const drawTiles = () => tiles.replaceChildren(...Object.entries(status.tools).map(([id, label]) =>
    h("button", { class: `integ-tool${id === kind ? " selected" : ""}`, onclick: () => {
      if (name.value === `${status.tools[kind]} on this PC` || !name.value.trim()) name.value = `${label} on this PC`;
      kind = id; drawTiles();
    } }, h("b", {}, label), untestedTag(id))));
  drawTiles();
  const perms = permissionChoices(status, ["READ_METADATA"], []);
  const next = async () => {
    if (kind === "http" && !status.http.allowed) { toast("Your organization doesn't allow the local HTTP API.", true); return; }
    const { grants, auto } = perms.value();
    const res = await attempt(() => rpc("integrations_add_client", { name: name.value.trim(), kind, grants, auto,
      transport: kind === "http" ? "http" : "stdio" }), "Not added");
    if (!res) return;
    refresh();
    setupDialog(res.client, res.token, kind);
  };
  showDialog("Add a tool", h("div", { class: "setup integ-scroll" },
    h("p", { class: "muted-text small" }, "Step 1 of 2. Which tool are you connecting, and what may it do?"), tiles,
    h("label", { class: "field" }, "Name (so you can tell your tools apart)", name), perms.node),
  [btn("Cancel", null, closeDialog, "tool outline"), btn("Next", null, next, "tool primary")]);
}

// VS Code's own MCP install link adds the server. The shell builds the link from the
// tool's id (add_to_vscode in main.rs), and VS Code shows the entry and asks before adding it.
function addToVsCode(client, insiders = false) {
  return attempt(() => invoke("add_to_vscode", { clientId: client.id, insiders }).then(() =>
    toast(`VS Code is asking whether to add KratosForge for ${client.name}. Then use Copilot Chat in Agent mode.`)),
  "Couldn't open VS Code");
}

function vsCodeBox(client) {
  return h("div", { class: "integ-vscode" },
    h("div", { class: "grow" }, h("b", {}, "Add to VS Code"),
      h("div", { class: "small" }, "Opens VS Code, which shows you the KratosForge server and asks before it adds it to your user settings. Then open Copilot Chat in Agent mode and ask: \u201cWhat lakehouses can you see in KratosForge?\u201d")),
    btn("Add to VS Code", null, () => addToVsCode(client), "tool primary"),
    btn("VS Code Insiders", null, () => addToVsCode(client, true), "tool outline small"));
}

function setupDialog(client, token, kind) {
  const snippet = client.config[kind] || client.config["other-mcp"];
  const result = h("p", { class: "muted-text small" });
  const test = async () => {
    result.textContent = "Testing...";
    const res = await rpc("integrations_test", { client_id: client.id }).catch((err) => ({ ok: false, message: friendlyError(err).text }));
    result.className = res.ok ? "small" : "fail-text small";
    result.textContent = res.message;
  };
  showDialog(`Connect ${client.name}`, h("div", { class: "setup integ-scroll" },
    h("p", { class: "muted-text small" }, kind === "vscode" ? "Step 2 of 2. Add KratosForge to VS Code once." : "Step 2 of 2. Paste this into your tool once."),
    kind === "vscode" ? vsCodeBox(client) : null,
    kind === "vscode" && snippet ? h("div", { class: "muted-text small" }, "Or set it up by hand, for one workspace:") : null,
    snippet ? h("div", {}, h("div", { class: "muted-text small" }, snippet.where),
      h("button", { class: "snippet", onclick: () => copyText(snippet.text, "Setup") }, h("span", { class: "t" }, "Copy"), h("code", {}, snippet.text))) : null,
    token ? h("div", { class: "warn-box" }, h("b", {}, "Token, shown once: "), h("span", { class: "mono" }, token), " ",
      btn("Copy", "copyArrowRight", () => copyText(token, "Token"), "tool outline small"),
      h("div", { class: "small" }, "Send it as Authorization: Bearer <token>. It's stored only as a fingerprint, so it can't be shown again; make a new one from Edit.")) : null,
    h("p", { class: "muted-text small" }, kind === "http" ? "" : `${client.id} tells the app which tool is calling. It isn't a password and only works on this PC.`),
    h("div", { class: "row" }, btn("Test connection", "plugConnected", test, "tool outline")), result,
    h("div", {}, h("b", { class: "small" }, "Allowed to "), permissionPills(client.grants))),
  [btn("Done", null, closeDialog, "tool primary")]);
}

function editTool(status, c, refresh) {
  const name = h("input", { value: c.name });
  const perms = permissionChoices(status, c.grants, c.auto);
  const workspaces = h("input", { value: c.workspaces.join(", "), placeholder: "Empty: every workspace you can open", spellcheck: "false" });
  const save = async () => {
    const { grants, auto } = perms.value();
    const list = workspaces.value.split(",").map((w) => w.trim()).filter(Boolean);
    if (await attempt(() => rpc("integrations_update_client", { client_id: c.id, changes: { name: name.value.trim() || c.name, grants, auto, workspaces: list } }), "Not saved")) {
      closeDialog(); toast("Saved"); refresh();
    }
  };
  const newToken = async () => {
    if (!(await confirmDialog(`Make a new token for ${c.name}?`,
      "Its current token stops working at once, so the tool can't connect until you give it the new one.", "Make a new token", { danger: true }))) return;
    const res = await attempt(() => rpc("integrations_new_token", { client_id: c.id }), "No new token");
    if (res) setupDialog(res.client, res.token, "http");
  };
  showDialog(`Edit ${c.name}`, h("div", { class: "setup integ-scroll" },
    h("label", { class: "field" }, "Name", name), perms.node,
    h("label", { class: "field" }, "Only these workspace ids (comma separated). A tool limited to some workspaces can't run code, and sees only notebooks, proofs and receipts in them.", workspaces),
    c.kind === "vscode" ? vsCodeBox(c) : null,
    c.kind !== "http" && c.config[c.kind] ? h("div", {}, h("div", { class: "muted-text small" }, c.config[c.kind].where),
      h("button", { class: "snippet", onclick: () => copyText(c.config[c.kind].text, "Setup") }, h("span", { class: "t" }, "Copy setup"), h("code", {}, c.config[c.kind].text))) : null),
  [c.transport === "http" ? btn("New token", null, newToken, "tool outline") : null, h("span", { class: "spacer" }),
    btn("Cancel", null, closeDialog, "tool outline"), btn("Save", null, save, "tool primary")]);
}

// ---------- approvals ----------

function approvalBody(a) {
  const s = a.summary || {};
  const target = Object.entries(s.target || {}).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(", ");
  return h("div", { class: "setup" },
    h("dl", { class: "about" },
      h("dt", {}, "Tool"), h("dd", {}, a.client_name),
      h("dt", {}, "Wants to"), h("dd", {}, s.action || a.title, s.destructive ? h("span", { class: "pill-s fail" }, "Can't be undone") : null),
      h("dt", {}, "Permission"), h("dd", {}, permissionPills([a.permission])),
      target ? h("dt", {}, "On") : null, target ? h("dd", {}, target) : null),
    s.content ? h("pre", { class: "mono", style: "max-height:260px;overflow:auto;white-space:pre-wrap;padding:8px;border:1px solid var(--border);border-radius:6px" }, s.content.text) : null,
    h("p", { class: "muted-text small" }, `Asked at ${timeText(a.created_at * 1000)}. Cancelled after 5 minutes if you don't answer. `,
      "Approvals are made only here, never from the AI tool. Text the tool sends can't approve anything."));
}

async function decide(a, approve, after) {
  closeDialog();
  if (await attempt(() => rpc("integrations_decide", { approval_id: a.id, approve }), "Not answered")) {
    toast(approve ? `Allowed once for ${a.client_name}` : `Denied ${a.client_name}`);
    after?.();
  }
}

function showApproval(a, after) {
  showDialog(`${a.client_name} wants to: ${a.title}`, approvalBody(a), [
    btn("Deny", null, () => decide(a, false, after), "tool outline"), h("span", { class: "spacer" }),
    btn("Allow once", null, () => decide(a, true, after), "tool primary")]);
}

function pendingCard(pending, refresh) {
  return h("section", { class: "card pad" }, h("h2", {}, "Waiting for you"),
    pending.map((a) => h("div", { class: "row" }, h("span", { class: "grow" }, h("b", {}, a.client_name), ` wants to ${a.title.toLowerCase()}`),
      btn("Review", null, () => showApproval(a, refresh), "tool primary small"))));
}

// Polls for approval requests while integrations are on, from any page: a tool waits up to a
// minute for an answer, so the request shows over whatever the person is doing.
async function watchApprovals() {
  if (integ.watching) return;
  integ.watching = true;
  let wait = 30000;
  try {
    const res = await rpc("integrations_pending");
    wait = res.enabled ? 3000 : 30000;
    const fresh = res.pending.filter((a) => !integ.shown.has(a.id));
    document.title = res.pending.length ? `(${res.pending.length}) Approval needed - KratosForge` : "KratosForge";
    if (fresh.length) {
      const busy = document.getElementById("dialog-root")?.children.length;
      if (busy) {
        toast(`${fresh[0].client_name} is waiting for your approval. Settings > Integrations shows it.`, false, { log: true });
      } else {
        showApproval(fresh[0], () => { if (state.view === "settings") render(); });
      }
      fresh.forEach((a) => integ.shown.add(a.id));
    }
  } catch { /* the engine isn't up yet */ }
  integ.watching = false;
  clearTimeout(integ.timer);
  integ.timer = setTimeout(watchApprovals, wait);
}

// ---------- activity and troubleshooting ----------

const CALL_DECISION = { allowed: "Allowed", approved: "Approved by you", denied: "Refused", approval_denied: "Denied by you",
  approval_pending: "Not answered", invalid: "Not valid" };

function activityCard(rows, status) {
  const titles = Object.fromEntries((status.capabilities || []).map((c) => [c.id, c.title]));
  const result = (r) => {
    const state = r.outcome === "ok" ? (r.execution_state || "ok") : r.outcome;
    const pill = state === "ok" ? "pass" : OUTCOME_PILL[state] || (r.outcome === "ok" ? "info" : "fail");
    return h("span", { class: `pill-s ${pill}`, title: r.error || OUTCOME_TITLE[state] || "" }, state.replaceAll("_", " "));
  };
  const target = (r) => Object.entries(r.arguments || {}).filter(([, v]) => v !== null && v !== "" && typeof v !== "object")
    .slice(0, 4).map(([k, v]) => `${k} ${v}`).join(" · ");
  const check = async () => {
    const res = await attempt(() => rpc("integrations_audit_check"), "Couldn't check");
    if (res) toast(res.ok ? `All ${res.lines} lines are as they were written` : `${res.problems.length} problems: ${res.problems[0]}`);
  };
  return h("section", { class: "card pad" },
    h("div", { class: "row" }, h("h2", { class: "grow" }, "Recent activity"), btn("Check the trail", null, check, "tool outline small")),
    rows.length ? h("div", { class: "integ-scroll" }, h("table", { class: "list" },
      h("thead", {}, h("tr", {}, h("th", {}, "Time"), h("th", {}, "Tool"), h("th", {}, "Request"), h("th", {}, "Decision"), h("th", {}, "Result"), h("th", { class: "n" }, "Took"))),
      h("tbody", {}, rows.map((r) => h("tr", {},
        h("td", { class: "nowrap" }, integWhen(Date.parse(r.at) / 1000)), h("td", {}, r.client || (r.client_id ? r.client_id : "Unknown tool")),
        h("td", {}, titles[r.capability] || r.capability, h("div", { class: "muted-text small" }, [r.channel, target(r)].filter(Boolean).join(" · "))),
        h("td", {}, CALL_DECISION[r.decision] || r.decision),
        h("td", {}, result(r)),
        h("td", { class: "n" }, r.seconds >= 0.05 ? `${r.seconds.toFixed(1)} s` : ""))))))
      : h("p", { class: "muted-text" }, "Nothing yet."),
    h("p", { class: "muted-text small" }, "Every request a tool made, including refused ones. Code, queries and table data aren't kept. ",
      "The full trail is in KratosForge's data folder (%LOCALAPPDATA%\\FabricDesktop) under integrations\\audit."));
}

function troubleshootingCard(status) {
  const item = (title, ...text) => h("div", {}, h("b", {}, title), h("div", { class: "muted-text small" }, ...text));
  return h("section", { class: "card pad" }, h("h2", {}, "If something doesn't work"),
    item("Your tool can't start the server", "Use the full command shown under How tools connect, in quotes. After an update it stays the same."),
    item("HTTP requests are refused", `Check the local HTTP API is on and you're calling ${status.http?.url || "http://127.0.0.1:47615"}. `,
      "Send the token as Authorization: Bearer <token>. Requests from a web page are always refused."),
    item("The tool connects but sees nothing", "Check you're signed in, and the tool's permissions under Connected tools. A tool that isn't connected sees only the list of capabilities, which says why."),
    item("No approval shows up", "KratosForge must be open. A tool waits a minute, then gets an approval_required answer; it can ask again after you approve. Requests are dropped after 5 minutes."));
}

registerFeature({
  id: "integrations",
  preview: true,
  commands: [
    { label: "Connect an AI tool", icon: "plugConnected", keywords: "mcp claude codex cursor copilot vscode ai integrations api",
      run: () => { state.settingsTab = "integrations"; go("settings"); } },
  ],
});

setTimeout(watchApprovals, 5000);
