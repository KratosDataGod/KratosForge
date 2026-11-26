// Settings > Integrations (integrations.js) with the engine mocked, for what integrations-tab.test.cjs and
// integrations-clients.test.cjs (real engine) leave out: an engine that isn't up, the switch while off and
// when the organization sets it, What tools can do, the local API not allowed or not running, an extension
// that didn't load, Add a tool for VS Code (Add to VS Code, Test connection) and for the HTTP API when it's
// not allowed, Edit with workspaces, a request that can't be undone, a request that arrives while a dialog
// is open, every Recent activity result, and the Connect an AI tool command (see harness.cjs).
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const LAUNCHER = "C:\\Users\\chris\\AppData\\Local\\FabricDesktop\\bin\\fabric-desktop-mcp.exe";
const PERMS = [
  { id: "READ_METADATA", offered: true, allowed: true }, { id: "READ_DATA", offered: true, allowed: true },
  { id: "WRITE_LOCAL_FILES", offered: true, allowed: false }, { id: "EXECUTE_LOCAL_CODE", offered: true, allowed: true },
  { id: "WRITE_ONELAKE", offered: false, allowed: true }, { id: "DEPLOY_TO_FABRIC", offered: false, allowed: false },
];
const CAPS = [
  { id: "list_ws", title: "List workspaces", description: "Names of the workspaces you can open.", permission: "READ_METADATA", approval: "never" },
  { id: "preview", title: "Preview a table", description: "A few rows.", permission: "READ_DATA", approval: "ask" },
  { id: "run_py", title: "Run Python", description: "On this PC.", permission: "EXECUTE_LOCAL_CODE", approval: "always" },
  { id: "write_nb", title: "Write a notebook", description: "In your folder.", permission: "WRITE_LOCAL_FILES", approval: "never" },
  { id: "deploy", title: "Deploy", description: "To Fabric.", permission: "DEPLOY_TO_FABRIC", approval: "always" },
];
const TOOLS = { "claude-code": "Claude Code", codex: "OpenAI Codex CLI", vscode: "VS Code (GitHub Copilot agent mode)", http: "An HTTP client (REST API)" };
const config = (id) => ({ "claude-code": { where: "Run in a terminal", text: `claude mcp add fabric-desktop -- "${LAUNCHER}" --client ${id}` },
  vscode: { where: ".vscode/mcp.json in a workspace", text: `{"servers":{"fabric-desktop":{"command":"${LAUNCHER}","args":["--client","${id}"]}}}` } });
const CODE = { id: "cl_code", name: "Code", kind: "vscode", kind_label: "VS Code", transport: "stdio", grants: ["READ_METADATA", "EXECUTE_LOCAL_CODE"],
  auto: [], workspaces: ["ws-1", "ws-2"], last_used_at: null, config: config("cl_code") };

let status;
let pending;
let activity;
let app;
const page = () => app.page;
const calls = (method) => app.calls.filter((c) => c.method === method).map((c) => c.params);
const base = (extra = {}) => ({ enabled: true, set_by_organization: false, launcher: LAUNCHER, tools: TOOLS, permissions: PERMS, capabilities: CAPS,
  http: { switch: false, allowed: true, running: false, url: "http://127.0.0.1:47615", log: "C:\\logs\\http.log" },
  clients: [CODE], extensions: [], pending: [], ...extra });

before(async () => {
  if (skip) return;
  // Settings itself reads the engine's app settings; everything on the Integrations tab is mocked.
  app = await openApp({
    engine: ["app_settings"],
    mocks: {
      readiness_check: () => [],
      integrations_status: () => { if (status instanceof Error) throw status; return status; },
      integrations_activity: () => activity,
      integrations_pending: () => ({ enabled: true, pending }),
      editor_status: () => null,
      integrations_set_enabled: ({ enabled }) => { status = { ...status, enabled }; return { enabled }; },
      integrations_add_client: ({ name, kind, grants }) => ({ client: { id: "cl_new", name, kind, grants, config: config("cl_new") }, token: kind === "http" ? "tok_123" : null }),
      integrations_update_client: () => ({ ok: true }),
      integrations_test: ({ client_id }) => (client_id === "cl_new" ? { ok: false, message: "The tool hasn't called yet." } : { ok: true, message: "Code called 2 minutes ago." }),
      integrations_decide: () => ({ ok: true }),
    },
  });
  await page().waitForFunction(() => state.settings && "show_preview" in state.settings);
});

after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  status = base();
  pending = [];
  activity = [];
  await page().evaluate(() => {
    closeDialog(); integ.shown.clear(); clearTimeout(integ.timer);
    window.__fdShellCalls.length = 0;
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (t) => { window.__copied = t; } } });
    go("home");
  });
  app.calls.length = 0;
  app.errors.length = 0;
});

async function openTab(heading = "External AI tools") {
  await page().evaluate(() => { state.settingsTab = "integrations"; go("settings"); });
  await page().getByRole("heading", { name: heading, exact: true }).waitFor();
  await page().waitForFunction(() => !document.querySelector(".page.settings .integrations .loading"));
}
const card = (heading) => page().locator(".page.settings section.card").filter({ has: page().getByRole("heading", { name: heading, exact: true }) });
const toastIs = (text) => page().waitForFunction((t) => document.getElementById("toast").textContent === t, text);

test("an engine that isn't up yet says the tab loads once it is", { skip }, async () => {
  status = new Error("The engine isn't running");
  await page().evaluate(() => { state.settingsTab = "integrations"; go("settings"); });
  await page().getByText("Integrations load once the local engine is running. The engine isn't running").waitFor();
});

test("while off it says what turning it on allows, and turning it on shows the tools", { skip }, async () => {
  status = base({ enabled: false });
  await openTab();
  const text = await card("External AI tools").innerText();
  assert.match(text, /Works with\s+Claude Code, Claude Desktop, Codex, Cursor/);
  assert.strictEqual(await card("Connected tools").count(), 0);
  await card("External AI tools").locator("input[type=checkbox]").check();
  await toastIs("AI tools can connect");
  assert.deepStrictEqual(calls("integrations_set_enabled"), [{ enabled: true }]);
  await page().getByRole("heading", { name: "Connected tools" }).waitFor();
});

test("the organization can set the switch, and turn the local API off", { skip }, async () => {
  status = base({ set_by_organization: true, http: { switch: false, allowed: false, running: false, url: "http://127.0.0.1:47615" } });
  await openTab();
  assert.strictEqual(await card("External AI tools").locator("input[type=checkbox]").isDisabled(), true);
  const how = await card("How tools connect").innerText();
  assert.match(how, /Your organization doesn't allow the local HTTP API on this PC\./);
  assert.strictEqual(await card("How tools connect").locator("input[type=checkbox]").isDisabled(), true);
  assert.match(await page().locator(".page.settings").innerText(), /HTTP requests are refused\s+Check the local HTTP API is on and you're calling http:\/\/127\.0\.0\.1:47615/);
});

test("a local API that is switched on but not running says where its log is; Copy copies the command", { skip }, async () => {
  status = base({ http: { switch: true, allowed: true, running: false, url: "http://127.0.0.1:47615", log: "C:\\logs\\http.log" } });
  await openTab();
  await card("How tools connect").getByText("It isn't running. The log is in C:\\logs\\http.log.").waitFor();
  assert.match(await card("How tools connect").innerText(), /3 capabilities\. Read metadata: 1 · Read data: 1 · Run code on this PC: 1\. 2 more need permissions not offered yet\./);
  await card("How tools connect").getByRole("button", { name: "Copy" }).click();
  await toastIs("Command copied");
  assert.strictEqual(await page().evaluate(() => window.__copied), LAUNCHER);
});

test("What tools can do lists each capability, what it needs and whether it asks", { skip }, async () => {
  await openTab();
  await card("How tools connect").getByRole("button", { name: "See all" }).click();
  const dialog = page().getByRole("dialog", { name: "What tools can do" });
  const rows = await dialog.locator("tbody tr").allInnerTexts();
  assert.match(rows[0], /List workspaces\s+Names of the workspaces you can open\.\s+Read metadata\s+No/);
  assert.match(rows[1], /Preview a table[\s\S]*Unless you allow it/);
  assert.match(rows[2], /Run Python[\s\S]*Every time/);
  assert.match(rows[3], /Not available: Write notebooks isn't offered to tools by your organization\./);
  assert.match(rows[4], /Not available: Deploy isn't offered to tools by your organization\./);
  assert.strictEqual(await dialog.locator("tbody tr").nth(2).locator(".pill-s.wrn").count(), 1);
  await dialog.getByRole("button", { name: "Close" }).last().click();
});

test("an extension that didn't load says why, and one the organization stops can't be switched", { skip }, async () => {
  status = base({ extensions: [
    { id: "x1", name: "Labels", version: "1.2", publisher: "Contoso", built_in: false, enabled: true, allowed: true, description: "Reads labels.",
      capabilities: [], permissions: ["READ_METADATA"], error: "labels.py: no module named requests" },
    { id: "x2", name: "Lineage", version: "0.9", publisher: "KratosForge", built_in: true, enabled: false, allowed: false, description: "Draws lineage.",
      capabilities: [{ title: "Show lineage" }], permissions: ["READ_DATA"] }] });
  await openTab();
  const text = await card("Extensions").innerText();
  assert.match(text, /Labels 1\.2 · Contoso/);
  assert.match(text, /Adds: nothing\. Needs/);
  assert.match(text, /Didn't load: labels\.py: no module named requests/);
  assert.match(text, /Lineage 0\.9 · KratosForge · built in/);
  assert.match(text, /Adds: Show lineage\./);
  assert.strictEqual(await card("Extensions").locator("input[type=checkbox]").nth(1).isDisabled(), true);
});

test("Add a tool for VS Code names it after the tool and offers Add to VS Code", { skip }, async () => {
  await openTab();
  await card("Connected tools").getByRole("button", { name: "Add a tool" }).click();
  const add = page().getByRole("dialog", { name: "Add a tool" });
  assert.strictEqual(await add.getByLabel("Name (so you can tell your tools apart)").inputValue(), "Claude Code on this PC");
  await add.getByRole("button", { name: /^VS Code \(GitHub Copilot agent mode\)/ }).click();
  assert.strictEqual(await add.getByLabel("Name (so you can tell your tools apart)").inputValue(), "VS Code (GitHub Copilot agent mode) on this PC");
  // Write notebooks isn't allowed; Read metadata is required.
  assert.strictEqual(await add.locator(".choice-row", { hasText: "Write notebooks" }).locator("input").isDisabled(), true);
  assert.match(await add.locator(".choice-row", { hasText: "Write notebooks" }).innerText(), /Your organization doesn't allow this\./);
  assert.match(await add.innerText(), /Not available yet: Write to OneLake, Deploy\./);
  await add.locator(".choice-row", { hasText: "Read data" }).locator("input").check();
  await add.locator(".choice-row", { hasText: "Don't ask me before it reads data" }).locator("input").check();
  await add.getByRole("button", { name: "Next" }).click();
  const setup = page().getByRole("dialog", { name: "Connect VS Code (GitHub Copilot agent mode) on this PC" });
  await setup.getByText("Step 2 of 2. Add KratosForge to VS Code once.").waitFor();
  assert.deepStrictEqual(calls("integrations_add_client"), [{ name: "VS Code (GitHub Copilot agent mode) on this PC", kind: "vscode",
    grants: ["READ_METADATA", "READ_DATA"], auto: ["READ_DATA"], transport: "stdio" }]);
  assert.match(await setup.innerText(), /Or set it up by hand, for one workspace:\s+\.vscode\/mcp\.json in a workspace/);
  await setup.getByRole("button", { name: "VS Code Insiders" }).click();
  await toastIs("VS Code is asking whether to add KratosForge for VS Code (GitHub Copilot agent mode) on this PC. Then use Copilot Chat in Agent mode.");
  assert.deepStrictEqual(await page().evaluate(() => window.__fdShellCalls.filter((c) => c.cmd === "add_to_vscode").map((c) => c.args)),
    [{ clientId: "cl_new", insiders: true }]);
  await setup.getByRole("button", { name: "Test connection" }).click();
  await setup.getByText("The tool hasn't called yet.").waitFor();
  assert.match(await setup.locator("p.fail-text").textContent(), /hasn't called yet/);
  await setup.locator("button.snippet").click();
  await toastIs("Setup copied");
  assert.match(await page().evaluate(() => window.__copied), /"--client","cl_new"/);
  assert.deepStrictEqual(app.errors, []);
});

test("an HTTP tool can't be added when the organization turns the local API off", { skip }, async () => {
  status = base({ tools: TOOLS, http: { switch: false, allowed: false, running: false, url: "http://127.0.0.1:47615" } });
  await openTab();
  await card("Connected tools").getByRole("button", { name: "Add a tool" }).click();
  const add = page().getByRole("dialog", { name: "Add a tool" });
  await add.getByLabel("Name (so you can tell your tools apart)").fill("");
  await add.getByRole("button", { name: /^An HTTP client/ }).click();
  assert.strictEqual(await add.getByLabel("Name (so you can tell your tools apart)").inputValue(), "An HTTP client (REST API) on this PC");
  await add.getByRole("button", { name: "Next" }).click();
  await toastIs("Your organization doesn't allow the local HTTP API. Ask IT to change integrations_http in Group Policy or the policy file.");
  assert.deepStrictEqual(calls("integrations_add_client"), []);
});

test("Test on a tool says what the engine found; Edit sends the workspaces typed", { skip }, async () => {
  await openTab();
  const row = card("Connected tools").locator("tbody tr", { hasText: "Code" });
  assert.match(await row.innerText(), /2 workspaces only/);
  assert.match(await row.innerText(), /Never/);
  await row.getByRole("button", { name: "Test" }).click();
  await toastIs("Code called 2 minutes ago.");
  await row.getByRole("button", { name: "Edit" }).click();
  const edit = page().getByRole("dialog", { name: "Edit Code" });
  assert.strictEqual(await edit.getByRole("button", { name: "Add to VS Code" }).count(), 1);
  assert.strictEqual(await edit.getByRole("button", { name: "New token" }).count(), 0);
  const ws = edit.getByLabel(/Only these workspace ids/);
  assert.strictEqual(await ws.inputValue(), "ws-1, ws-2");
  await ws.fill(" ws-3 , ,ws-4");
  await edit.getByLabel("Name", { exact: true }).fill("");
  await edit.getByRole("button", { name: "Save" }).click();
  await toastIs("Saved");
  assert.deepStrictEqual(calls("integrations_update_client"), [{ client_id: "cl_code",
    changes: { name: "Code", grants: ["READ_METADATA", "EXECUTE_LOCAL_CODE"], auto: [], workspaces: ["ws-3", "ws-4"] } }]);
  assert.deepStrictEqual(app.errors, []);
});

test("a request that can't be undone shows its target and content, and Allow once answers it", { skip }, async () => {
  const ask = { id: "ap1", client_name: "Code", title: "Delete a notebook", permission: "WRITE_LOCAL_FILES", created_at: Date.now() / 1000,
    summary: { action: "Delete the notebook Daily sales", destructive: true, target: { notebook_name: "Daily sales", workspace_id: "ws-1" },
      content: { text: "cells: 4" } } };
  status = base({ pending: [ask] });
  await openTab();
  assert.match(await card("Waiting for you").innerText(), /Code wants to delete a notebook/);
  await card("Waiting for you").getByRole("button", { name: "Review" }).click();
  const dialog = page().getByRole("dialog", { name: "Code wants to: Delete a notebook" });
  const text = await dialog.innerText();
  assert.match(text, /Wants to\s+Delete the notebook Daily sales\s*Can't be undone/);
  assert.match(text, /On\s+notebook name Daily sales, workspace id ws-1/);
  assert.match(text, /cells: 4/);
  await dialog.getByRole("button", { name: "Allow once" }).click();
  await toastIs("Allowed once for Code");
  assert.deepStrictEqual(calls("integrations_decide"), [{ approval_id: "ap1", approve: true }]);
});

test("a request that arrives while a dialog is open is a toast instead, and shown only once", { skip }, async () => {
  pending = [{ id: "ap2", client_name: "Codex", title: "Run Python", permission: "EXECUTE_LOCAL_CODE", created_at: Date.now() / 1000, summary: {} }];
  await page().evaluate(() => showDialog("Something else", h("p", {}, "busy"), []));
  await page().evaluate(() => watchApprovals());
  await toastIs("Codex is waiting for your approval. Settings > Integrations shows it.");
  assert.strictEqual(await page().evaluate(() => document.title), "(1) Approval needed - KratosForge");
  await page().evaluate(() => closeDialog());
  await page().evaluate(() => watchApprovals());
  assert.strictEqual(await page().getByRole("dialog", { name: /Codex wants to/ }).count(), 0);
  // A new one with nothing open shows its dialog; Deny answers it.
  pending.push({ id: "ap3", client_name: "Codex", title: "Read data", permission: "READ_DATA", created_at: Date.now() / 1000, summary: {} });
  await page().evaluate(() => watchApprovals());
  const dialog = page().getByRole("dialog", { name: "Codex wants to: Read data" });
  await dialog.getByRole("button", { name: "Deny" }).click();
  await toastIs("Denied Codex");
  assert.deepStrictEqual(calls("integrations_decide"), [{ approval_id: "ap3", approve: false }]);
  await page().evaluate(() => { clearTimeout(integ.timer); document.title = "KratosForge"; });
});

test("Recent activity shows each call's decision, result and time taken", { skip }, async () => {
  const at = new Date(Date.now() - 60000).toISOString();
  activity = [
    { at, client: "Code", capability: "list_ws", channel: "mcp-stdio", arguments: { workspace_id: "ws-1", deep: { a: 1 }, empty: "" }, decision: "allowed", outcome: "ok", seconds: 0.42 },
    { at, client_id: "cl_old", capability: "run_py", decision: "approved", outcome: "ok", execution_state: "timed_out", seconds: 0.01 },
    { at, capability: "mystery", decision: "weird", outcome: "error", error: "No such capability." },
    { at, client: "Code", capability: "run_py", decision: "approved", outcome: "ok", execution_state: "started" },
  ];
  await openTab();
  const rows = await card("Recent activity").locator("tbody tr").evaluateAll((trs) => trs.map((tr) => [...tr.children].map((td) => td.textContent.trim())));
  assert.deepStrictEqual(rows.map((r) => r.slice(1)), [
    ["Code", "List workspacesmcp-stdio · workspace_id ws-1", "Allowed", "ok", "0.4 s"],
    ["cl_old", "Run Python", "Approved by you", "timed out", ""],
    ["Unknown tool", "mystery", "weird", "error", ""],
    ["Code", "Run Python", "Approved by you", "started", ""],
  ]);
  assert.strictEqual(await card("Recent activity").locator("tbody tr").nth(2).locator(".pill-s.fail").getAttribute("title"), "No such capability.");
  assert.match(await card("Recent activity").locator("tbody tr").nth(3).locator(".pill-s.wrn").getAttribute("title"), /^Started after your approval/);
});

test("Connect an AI tool from the command bar opens Settings > Integrations", { skip }, async () => {
  await page().keyboard.press("Control+k");
  await page().locator("#palette").waitFor();
  await page().keyboard.type("Connect an AI tool");
  await page().waitForFunction(() => document.querySelector("#palette .palette-item.on")?.textContent.includes("Connect an AI tool"));
  await page().keyboard.press("Enter");
  await page().getByRole("heading", { name: "Connected tools" }).waitFor();
  assert.deepStrictEqual(await page().evaluate(() => [state.view, state.settingsTab]), ["settings", "integrations"]);
});
