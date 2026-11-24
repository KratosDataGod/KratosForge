// Settings > Environment (kf-environment.js), the paths settings-environment.test leaves out: environments
// that can't be read and Try again; a failed connection's detail, an operational database and a provider
// out of Preview; Switch, and the last known status in the list; Disconnect with the policy off; Create
// in an existing workspace and lakehouse (a plan that refuses one environment, a create that the engine
// refuses or that stops part way, Cancel); Connect's search, a workspace with nothing to connect to and
// Back; Adopt with nothing to adopt, a refused preview, a Prod workspace, a name changed after the
// manifest shows and a refused adopt; Upgrade in a Prod workspace refused and stopped part way, and
// Cancel; and the list with no environment. The tab is drawn into a box of its own with every engine
// call mocked; what the person sees and what was sent is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const WS = "11111111-1111-4111-8111-111111111111";
const LH = "22222222-2222-4222-8222-222222222222";
const ENV = { environment_id: "kfenv_2f0c0c1e9c8a4c6f9b1d7a3e5f4b2a10", name: "Contoso data platform", provider: "fabric", writable: true,
  workspace_id: WS, workspace_name: "KratosForge", lakehouse_id: LH, lakehouse_name: "KratosForgeLakehouse",
  has_operational_store: true, operational_name: "", updated_at: "2026-11-19T09:00:00Z" };
const OTHER = { ...ENV, environment_id: "kfenv_" + "a".repeat(32), name: "Finance sandbox", workspace_name: "", workspace_id: "ws-fin" };
const PROVIDERS = [{ id: "fabric", label: "Microsoft Fabric", available: true, preview: false, planned: "" },
  { id: "databricks", label: "Azure Databricks", available: false, preview: true, planned: "" }, { id: "local", label: "This PC", available: true }];
const PERM = "Write access to the lakehouse.";

const w = {};
function fresh() {
  Object.assign(w, {
    statusFail: false, allowed: true, active: ENV, environments: [ENV, OTHER],
    check: { status: "Connection failed", next: "Check your network, then Validate connection.", detail: "OneLake answered 503.", waiting: 1, compatibility: { state: "ready" } },
    plan: null, create: null, adoptPreview: null, adopt: null, upgradePlan: null, upgrade: null, found: [], truncated: false,
  });
}
fresh();
const answer = (x, p) => { if (x instanceof Error) throw x; return typeof x === "function" ? x(p) : structuredClone(x); };

let app;
const page = () => app.page;
const card = () => page().locator("#t-box");
const sent = (m) => app.calls.filter((c) => c.method === m).map((c) => c.params);
const dialog = () => page().locator("#dialog-root .dialog");

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    cp_status: () => { if (w.statusFail) throw new Error("OSError: the settings file is locked"); return { allowed: w.allowed, providers: PROVIDERS, active: w.active, environments: w.environments }; },
    cp_check: () => ({ ...w.check, environment: w.active }),
    cp_discover: (p) => (p.workspace_id ? { workspace_id: p.workspace_id, truncated: w.truncated, found: w.found }
      : { namespaces: [{ id: WS, display_name: "KratosForge" }, { id: "ws-sales", display_name: "Sales Prod" }] }),
    list_capacities: () => [],
    list_lakehouses: () => [{ id: "lh-gold", displayName: "LH_Gold" }],
    optimize_status: () => ({ active: { lakehouse_id: "lh-central" } }),
    cp_plan: (p) => answer(w.plan, p),
    cp_create: (p) => answer(w.create, p),
    cp_switch: (p) => { w.active = w.environments.find((e) => e.environment_id === p.environment_id); return { active: w.active }; },
    cp_disconnect: (p) => { w.environments = w.environments.filter((e) => e.environment_id !== p.environment_id);
      if (w.active?.environment_id === p.environment_id) w.active = null; return { forgotten: true }; },
    cp_adopt_preview: (p) => answer(w.adoptPreview, p),
    cp_adopt: (p) => answer(w.adopt, p),
    cp_upgrade_plan: () => answer(w.upgradePlan),
    cp_upgrade: (p) => answer(w.upgrade, p),
  } });
  await page().waitForFunction(() => typeof renderEnvironmentTab === "function");
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  fresh();
  await page().evaluate(() => {
    closeDialog(); closeDialog();
    Object.assign(kfe, { mode: "status", status: null, check: null, checkedAt: null, plan: null, form: null, workspaces: null, capacities: null,
      lakehouses: null, planBox: null, upgrade: null, inside: null, filter: "", adopt: null, adoptName: "", done: null, error: "", lastKnown: {} });
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
  });
  app.calls.length = 0;
  app.errors.length = 0;
});

// Draws the tab into #t-box, in the given mode, and waits for `ready`.
async function open(ready, mode = "status") {
  await page().evaluate((mode) => {
    let b = document.getElementById("t-box");
    if (!b) { b = document.createElement("div"); b.id = "t-box"; document.body.append(b); }
    kfe.mode = mode;
    renderEnvironmentTab(b);
  }, mode);
  await card().locator(ready).first().waitFor();
}
const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test(document.getElementById("toast")?.textContent || ""), re.source);

test("environments that can't be read offer Try again; a failed connection shows its detail; a provider out of Preview is Available", { skip }, async () => {
  w.statusFail = true;
  await open("text=This PC's environments couldn't be read.");
  w.statusFail = false;
  await card().getByRole("button", { name: "Try again" }).click();
  await card().locator(".kfe-status", { hasText: "Connection failed" }).waitFor();
  assert.match(await card().innerText(), /Operational database\s+Linked/);
  assert.match(await card().locator(".kfe-status").innerText(), /Check your network, then Validate connection\.\s*1 result wait on this PC\./);
  assert.strictEqual(await card().getByText("OneLake answered 503.").count(), 1);
  assert.match(await card().locator(".kfe-table tbody tr", { hasText: "Microsoft Fabric" }).innerText(), /Available/);
  assert.deepStrictEqual(app.errors, []);
});

test("the list shows the last known status; Switch moves this PC; with no environment the list says so", { skip }, async () => {
  await open(".kfe-status");
  await card().getByRole("button", { name: "Switch or disconnect..." }).click();
  await card().locator("h3", { hasText: "Environments on this PC" }).waitFor();
  const rows = await card().locator(".kfe-table").first().locator("tbody tr").allInnerTexts();
  assert.match(rows[0], /Contoso data platform\s*Active\s+KratosForge\s+Connection failed, today /);
  assert.match(rows[1], /Finance sandbox\s+ws-fin\s+Not checked yet/);
  await card().getByRole("button", { name: "Switch to Finance sandbox" }).click();
  await toastSays(/^This PC now uses Finance sandbox\.$/);
  await card().locator("h3", { hasText: "Finance sandbox" }).waitFor();
  assert.deepStrictEqual(sent("cp_switch"), [{ environment_id: OTHER.environment_id }]);

  w.active = null;
  w.environments = [];
  await open("text=This PC isn't connected to any environment.", "list");
  assert.deepStrictEqual(app.errors, []);
});

test("with the policy off, Disconnect still asks first and forgets the environment", { skip }, async () => {
  w.allowed = false;
  await open(".kfe-card");
  await card().getByRole("button", { name: "Disconnect this PC..." }).click();
  assert.match(await dialog().innerText(), /Disconnect this PC from Contoso data platform\?/);
  await dialog().locator("button.danger-solid").click();
  await toastSays(/^Disconnected this PC from Contoso data platform\.$/);
  assert.deepStrictEqual(sent("cp_disconnect"), [{ environment_id: ENV.environment_id }]);
  assert.deepStrictEqual(app.errors, []);
});

async function startCreate() {
  w.active = null;
  await open("text=Create an environment...");
  await card().getByRole("button", { name: "Create an environment..." }).click();
  await card().getByRole("combobox", { name: "Workspace", exact: true }).locator("option", { hasText: "Sales Prod" }).waitFor({ state: "attached" });
  await card().getByRole("textbox", { name: "Name", exact: true }).fill("Sales team");
  await card().getByRole("combobox", { name: "Workspace", exact: true }).selectOption("ws-sales");
  await card().getByRole("combobox", { name: "Lakehouse", exact: true }).locator("option", { hasText: "Existing: LH_Gold" }).waitFor({ state: "attached" });
  await card().getByRole("combobox", { name: "Lakehouse", exact: true }).selectOption("lh-gold");
}

test("Create in an existing workspace and lakehouse: the plan names them, refuses one environment, and a refused create says why", { skip }, async () => {
  w.plan = (p) => ({ provider: "fabric", request: p.request, typed_name: "", token: "tok", blocked: [], steps: [
    { action: "find", target: "lh-gold", permission: PERM, cost: "", blocked: "", note: "" },
    { action: "refuse", target: "environment", permission: "", cost: "", blocked: "", note: "An environment is already here." },
    { action: "create", target: "lakehouse Staging", permission: PERM, cost: "OneLake storage.", blocked: "", note: "" },
    { action: "initialize", target: "Optimize tables (fdopt_)", permission: PERM, cost: "", blocked: "", note: "" }] });
  w.create = new Error("PermissionError: You need Contributor on Sales Prod.");
  await startCreate();
  await card().getByRole("button", { name: "Show what will happen" }).click();
  await card().locator(".kfe-plan").waitFor();
  assert.deepStrictEqual(sent("cp_plan"), [{ request: { name: "Sales team", workspace_id: "ws-sales", lakehouse_id: "lh-gold" } }]);
  const rows = await card().locator(".kfe-plan tbody tr").allInnerTexts();
  assert.match(rows[0], /^Use lakehouse LH_Gold/);
  assert.match(rows[1], /^This environment/);
  assert.match(rows[3], /Included above\./);
  await card().getByRole("button", { name: "Create environment..." }).click();
  assert.match(await dialog().innerText(), /This creates a lakehouse in Fabric, the shared tables and the manifest\. Nothing is deleted\./);
  await dialog().getByRole("button", { name: "Create" }).click();
  await card().locator(".kfe-status", { hasText: "Not created" }).waitFor();
  assert.match(await card().locator(".kfe-status").innerText(), /You need Contributor on Sales Prod\./);
  await card().getByRole("button", { name: "Cancel" }).click();
  await card().getByRole("button", { name: "Create an environment..." }).waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("a create that stops part way says which step and what to do", { skip }, async () => {
  w.plan = (p) => ({ provider: "fabric", request: p.request, typed_name: "", token: "tok", blocked: [], steps: [
    { action: "initialize", target: "Optimize tables (fdopt_)", permission: PERM, cost: "", blocked: "", note: "" }] });
  w.create = { ok: false, steps: [{ step: "Workspace", status: "done", detail: "Kept" }, { step: "Optimize tables", status: "failed", detail: "The SQL endpoint timed out" }] };
  await startCreate();
  await card().getByRole("button", { name: "Show what will happen" }).click();
  await card().getByRole("button", { name: "Create environment..." }).click();
  assert.match(await dialog().innerText(), /This sets up the shared tables and writes the manifest\. Nothing is deleted\./);
  await dialog().getByRole("button", { name: "Create" }).click();
  await card().locator(".kfe-status", { hasText: "Stopped" }).waitFor();
  assert.match(await card().locator(".kfe-status").first().innerText(),
    /Optimize tables didn't finish\. The SQL endpoint timed out\. Nothing after it ran\. Make the plan again to finish\./);
  assert.deepStrictEqual(app.errors, []);
});

test("Connect: the search narrows the workspaces; a workspace with nothing to connect to says so; Back returns", { skip }, async () => {
  w.active = null;
  w.truncated = true;
  w.found = [{ workspace_id: WS, lakehouse_id: "lh-other", display_name: "FabricDesktop_Old", state: "adoptable", reason: "", environment: null }];
  await open("text=Connect to one...");
  await card().getByRole("button", { name: "Connect to one..." }).click();
  await card().getByRole("button", { name: "Look inside Sales Prod" }).waitFor();
  const find = card().getByRole("textbox", { name: "Find a workspace" });
  await find.pressSequentially("sal");
  await page().waitForFunction(() => document.querySelectorAll('#t-box button[aria-label^="Look inside"]').length === 1);
  assert.strictEqual(await page().evaluate(() => document.activeElement === document.querySelector("#t-box .kfe-narrow input")), true, "typing keeps focus");
  await find.fill("zzz");
  await find.dispatchEvent("input");
  await card().getByText("No workspace has that name.").waitFor();
  await find.fill("");
  await find.dispatchEvent("input");
  await card().getByRole("button", { name: "Look inside KratosForge" }).click();
  await card().getByText("No environments in this workspace.").waitFor();
  assert.match(await card().innerText(), /To adopt it, connect this PC to it in Optimize lakehouses first\./);
  assert.strictEqual(await card().getByRole("button", { name: "Adopt FabricDesktop_Old" }).count(), 0);
  assert.match(await card().innerText(), /This workspace has more lakehouses than were looked at\./);
  await card().getByRole("button", { name: "Back to workspaces" }).click();
  await card().getByRole("button", { name: "Look inside KratosForge" }).waitFor();
  assert.deepStrictEqual(app.errors, []);
});

const MANIFEST = (p) => ({ token: "tok-adopt", typed_name: "Sales Prod", environment: { ...ENV, name: p.name, lakehouse_name: "FabricDesktop_Central" },
  manifest: { environment_id: ENV.environment_id, provider: "fabric", namespace: { workspace_id: WS, display_name: "Sales Prod" },
    analytical_store: { lakehouse_id: "lh-central", display_name: "FabricDesktop_Central" }, operational_store: { display_name: "SQL_Ops" },
    services: ["optimize", "access"], owner: "oid" } });

test("Adopt: nothing to adopt, a refused preview, and a Prod workspace's name typed before a refused adopt", { skip }, async () => {
  w.active = null;
  w.adoptPreview = new Error("ValueError: Connect this PC to a central lakehouse first, in Optimize lakehouses.");
  await open(".kfe-adopt-name", "adopt");
  await card().locator(".kfe-adopt-name input").fill("Sales");
  await card().getByRole("button", { name: "Show the manifest" }).click();
  await card().locator(".kfe-status", { hasText: "Nothing to adopt" }).waitFor();
  assert.match(await card().innerText(), /Connect this PC to a central lakehouse first/);
  await card().getByRole("button", { name: "Back" }).click();
  await card().getByRole("button", { name: "Adopt this PC's central lakehouse..." }).click();

  w.adoptPreview = new Error("RuntimeError: OneLake didn't answer.");
  await card().locator(".kfe-adopt-name input").fill("Sales");
  await card().getByRole("button", { name: "Show the manifest" }).click();
  await card().locator(".kfe-status", { hasText: "Not adopted" }).waitFor();

  w.adoptPreview = MANIFEST;
  await card().getByRole("button", { name: "Show the manifest" }).click();
  await card().locator("h4", { hasText: "The manifest it would write" }).waitFor();
  const kv = await card().locator("dl.kfe-kv").innerText();
  assert.match(kv, /operational_store\s+SQL_Ops/);
  assert.match(kv, /services\s+optimize, access/);
  assert.match(kv, /owner\s+your Entra object id/);
  // Changing the name after the manifest shows takes the manifest away, and keeps typing in the box.
  await card().locator(".kfe-adopt-name input").press("End");
  await card().locator(".kfe-adopt-name input").pressSequentially(" team");
  await card().getByRole("button", { name: "Show the manifest" }).waitFor();
  assert.strictEqual(await card().locator(".kfe-adopt-name input").inputValue(), "Sales team");
  await card().getByRole("button", { name: "Show the manifest" }).click();
  w.adopt = new Error("PermissionError: You can't write to FabricDesktop_Central.");
  await card().getByRole("button", { name: "Adopt..." }).click();
  assert.match(await dialog().innerText(), /Adopt FabricDesktop_Central\?\s+Sales Prod is a Prod workspace/);
  const ok = dialog().getByRole("button", { name: "Adopt" });
  assert.ok(await ok.isDisabled());
  await dialog().locator("input").fill("Sales Prod");
  await ok.click();
  await card().locator(".kfe-status", { hasText: "Not adopted" }).waitFor();
  assert.match(await card().innerText(), /You can't write to FabricDesktop_Central\./);
  assert.deepStrictEqual(sent("cp_adopt"), [{ token: "tok-adopt", confirmed: true, confirm_name: "Sales Prod" }]);
  assert.deepStrictEqual(app.errors, []);
});

const UPGRADE = { provider: "fabric", blocked: [], typed_name: "Sales Prod", token: "tok-up", environment: ENV, request: {},
  steps: [{ action: "upgrade", target: "Access tables (fdacc_)", creates: false, permission: PERM, cost: "", blocked: "", note: "" }] };

test("Upgrade in a Prod workspace: refused, it can't be run again until planned again; stopped part way says which step; Cancel", { skip }, async () => {
  w.check = { status: "Upgrade required", next: "Choose Upgrade.", detail: "", waiting: 0, compatibility: { state: "upgrade", can_upgrade: true } };
  w.upgradePlan = UPGRADE;
  w.upgrade = new Error("RuntimeError: The plan is out of date. Make it again.");
  await open("text=Upgrade...");
  await card().getByRole("button", { name: "Upgrade..." }).click();
  await card().locator("h3", { hasText: "Upgrade Contoso data platform" }).waitFor();
  await card().locator(".kfe-buttons").getByRole("button", { name: "Upgrade..." }).click();
  assert.match(await dialog().innerText(), /Upgrade Contoso data platform in Sales Prod\?\s+Sales Prod is a Prod workspace/);
  await dialog().locator("input").fill("Sales Prod");
  await dialog().getByRole("button", { name: "Upgrade" }).click();
  await card().locator(".kfe-status", { hasText: "Not upgraded" }).waitFor();
  assert.ok(await card().locator(".kfe-buttons").getByRole("button", { name: "Upgrade..." }).isDisabled());
  assert.deepStrictEqual(sent("cp_upgrade"), [{ token: "tok-up", confirmed: true, confirm_name: "Sales Prod" }]);
  await card().getByRole("button", { name: "Cancel" }).click();
  await card().locator(".kfe-status", { hasText: "Upgrade required" }).waitFor();

  w.upgrade = { ok: false, steps: [{ step: "Access tables", status: "failed", detail: "Timed out." }] };
  await card().getByRole("button", { name: "Upgrade..." }).click();
  await card().locator(".kfe-buttons").getByRole("button", { name: "Upgrade..." }).click();
  await dialog().locator("input").fill("Sales Prod");
  await dialog().getByRole("button", { name: "Upgrade" }).click();
  await card().locator(".kfe-status", { hasText: "Stopped" }).waitFor();
  assert.match(await card().locator(".kfe-status", { hasText: "Stopped" }).innerText(), /Access tables didn't finish\. Timed out\. Nothing after it ran\. Choose Upgrade again to finish\./);
  assert.deepStrictEqual(app.errors, []);
});
