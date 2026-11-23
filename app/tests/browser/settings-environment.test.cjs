// Settings > Environment (Preview, kf-environment.js, CP-010): the status card with the one status and
// its next step; Create shows the engine's plan and runs it by its token after a confirm (a Prod
// workspace's name typed); Connect looks inside a workspace; Adopt shows the manifest first; Upgrade is
// the same plan-and-confirm; Disconnect is a danger confirm; with the policy off only Disconnect stays.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const SHOTS = process.env.SCREENSHOT_DIR;
const WS = "11111111-1111-4111-8111-111111111111";
const LH = "22222222-2222-4222-8222-222222222222";
const ENV = { environment_id: "kfenv_2f0c0c1e9c8a4c6f9b1d7a3e5f4b2a10", name: "Contoso data platform", provider: "fabric", writable: true,
  workspace_id: WS, workspace_name: "KratosForge", lakehouse_id: LH, lakehouse_name: "KratosForgeLakehouse",
  has_operational_store: false, operational_name: "", families: { fdopt_: 2 }, updated_at: "2026-11-19T09:00:00Z" };
const OTHER = { ...ENV, environment_id: "kfenv_" + "a".repeat(32), name: "Finance sandbox", lakehouse_id: "lh-fin", lakehouse_name: "lh_finance_kf" };
const PROVIDERS = [
  { id: "fabric", label: "Microsoft Fabric", status: "Preview", available: true, preview: true, planned: "" },
  { id: "databricks", label: "Azure Databricks", status: "Not available yet", available: false, preview: true, planned: "x" },
  { id: "snowflake", label: "Snowflake", status: "Not available yet", available: false, preview: true, planned: "y" },
  { id: "local", label: "This PC", status: "Preview", available: true, preview: true, planned: "" },
];
const PERM = "Write access to the lakehouse (Contributor or higher on the workspace).";
const STEPS = (blocked) => [
  { action: "create", target: "workspace KratosForge", creates: true, permission: "Your organization lets you create workspaces.",
    cost: "No new purchase.", blocked: blocked ? "A workspace called KratosForge already exists. Choose it as an existing workspace, or pick another name." : "", note: "" },
  { action: "create", target: "lakehouse KratosForgeLakehouse", creates: true, permission: "Contributor, Member or Admin role on the workspace.",
    cost: "OneLake storage for what it holds.", blocked: "", note: "" },
  { action: "initialize", target: "Optimize tables (fdopt_)", creates: true, permission: PERM, cost: "", blocked: "", note: "" },
  { action: "write_manifest", target: "Files/_kratosforge/environment.json", creates: true, permission: PERM, cost: "", blocked: "",
    note: "Ids and versions only, never a secret." },
];

let app;
let world;
const page = () => app.page;

function fresh() {
  return { allowed: true, active: ENV, environments: [ENV, OTHER], check: { status: "Connected", next: "Shared results are saved to the environment as they're made.",
    detail: "", waiting: 0, compatibility: { state: "ready", can_upgrade: false } }, blocked: false, prod: "", createOutcome: "ok", nothingToAdopt: false };
}

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1280, height: 1000 },
    engine: ["app_settings"],
    mocks: {
      cp_status: () => ({ allowed: world.allowed, providers: PROVIDERS, active: world.active, environments: world.environments }),
      cp_check: () => ({ ...world.check, environment: world.active }),
      cp_discover: (p) => p.workspace_id ? { workspace_id: p.workspace_id, truncated: false, found: [
        { workspace_id: WS, lakehouse_id: LH, display_name: "KratosForgeLakehouse", state: "environment", reason: "", environment: ENV },
        { workspace_id: WS, lakehouse_id: "lh-fin", display_name: "lh_finance_kf", state: "environment", reason: "", environment: { ...OTHER, writable: false } },
        { workspace_id: WS, lakehouse_id: "lh-central", display_name: "FabricDesktop_Central", state: "adoptable", reason: "", environment: null },
        { workspace_id: WS, lakehouse_id: "lh-hr", display_name: "lh_hr_restricted", state: "unreadable", reason: "You can't read this lakehouse.", environment: null }] }
        : { namespaces: [{ id: WS, display_name: "KratosForge" }, { id: "ws-prod", display_name: "Sales Prod" }] },
      list_capacities: () => [{ id: "cap-1", displayName: "Contoso Analytics", sku: "F8", region: "North Europe", state: "Active" }],
      list_lakehouses: () => [],
      optimize_status: () => ({ active: { lakehouse_id: "lh-central" } }),
      cp_plan: (p) => ({ provider: "fabric", steps: STEPS(world.blocked), blocked: world.blocked ? ["taken"] : [], request: p.request,
        typed_name: world.prod, token: world.blocked ? "" : "tok-create" }),
      cp_create: (p) => {
        if (world.createOutcome === "changed") throw new Error("Something changed since this was shown, so nothing was done. Look at it again and confirm.");
        if (world.createOutcome === "stopped") return { ok: false, workspace_id: WS, lakehouse_id: "", manifest: null, steps: [
          { step: "Workspace", status: "done", detail: "Created KratosForge" }, { step: "Lakehouse", status: "failed", detail: "capacity paused" }] };
        if (p.confirm_name !== world.prod) throw new Error("Sales Prod is a Prod workspace, so this runs only after you type its name exactly to confirm. Nothing was changed.");
        world.active = ENV;
        return { ok: true, steps: [{ step: "Workspace", status: "done", detail: "Created KratosForge" }, { step: "Manifest", status: "done", detail: "Wrote the manifest" }],
          workspace_id: WS, lakehouse_id: LH, manifest: {}, environment: ENV };
      },
      cp_connect: (p) => {
        if (p.lakehouse_id === "lh-fin") throw new Error("This manifest names another lakehouse, so it wasn't trusted. Connect to the environment where its manifest says it is.");
        world.active = ENV;
        return { environment: ENV, writable: true };
      },
      cp_adopt_preview: (p) => world.nothingToAdopt ? (() => { throw new Error("Connect this PC to a central lakehouse first (Settings > Optimize lakehouses), or create an environment."); })() : ({ token: "tok-adopt", typed_name: "", environment: { ...ENV, name: p.name, lakehouse_name: "FabricDesktop_Central" },
        manifest: { environment_id: ENV.environment_id, provider: "fabric", namespace: { workspace_id: WS, display_name: "KratosForge" },
          analytical_store: { lakehouse_id: "lh-central", display_name: "FabricDesktop_Central" },
          operational_store: world.prodStore ? { workspace_id: "ws-platform", functions_id: "fn-1", display_name: "SQL_Platform" } : null, services: [], owner: null },
        operational_prod: !!world.prodStore }),
      cp_adopt: () => ({ ok: true, manifest: {}, environment: ENV }),
      cp_upgrade_plan: () => ({ provider: "fabric", blocked: [], typed_name: world.prod, token: "tok-up", environment: ENV, request: {},
        steps: [{ action: "initialize", target: "Access tables (fdacc_)", creates: true, permission: PERM, cost: "", blocked: "",
          note: "Upgrades the shared tables. KratosForge on other PCs can only read them until it's updated to this version." },
        { action: "write_manifest", target: "Files/_kratosforge/environment.json", creates: false, permission: PERM, cost: "", blocked: "", note: "" }] }),
      cp_upgrade: () => { world.check = fresh().check; return { ok: true, steps: [], manifest: {}, environment: ENV }; },
      cp_switch: (p) => { world.active = world.environments.find((e) => e.environment_id === p.environment_id); return { active: world.active }; },
      cp_disconnect: (p) => { world.environments = world.environments.filter((e) => e.environment_id !== p.environment_id);
        if (world.active?.environment_id === p.environment_id) world.active = null; return { forgotten: true, active: world.active }; },
    },
  });
  await page().waitForFunction(() => typeof go === "function" && typeof renderEnvironmentTab === "function" && !!document.querySelector(".rail-item"));
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });
beforeEach(() => { world = fresh(); if (app) app.calls.length = 0; });

async function openTab(wait = ".kfe-status") {
  await page().evaluate(() => { go("home"); Object.assign(kfe, { mode: "status", check: null, checkedAt: null, plan: null, form: null,
    workspaces: null, capacities: null, inside: null, adopt: null, adoptName: "", done: null, error: "", upgrade: null });
  state.settingsTab = "environment"; go("settings"); });
  await page().locator(wait).first().waitFor({ timeout: 10000 });
}
const card = () => page().locator(".kf-environment");
async function waitCall(method) {
  for (let i = 0; i < 200 && !app.calls.some((c) => c.method === method); i++) await page().waitForTimeout(25);
  return app.calls.find((c) => c.method === method);
}

test("the status card shows the environment, its one status and next step, and the providers", { skip }, async () => {
  await openTab();
  assert.match(await page().locator('.page.settings [role="tab"][aria-selected="true"]').textContent(), /^Environment/);
  const text = await card().textContent();
  assert.match(text, /KratosForge Environment.*Preview.*Where KratosForge keeps what your team shares/);
  assert.match(text, /Contoso data platform.*Provider.*Microsoft Fabric.*Workspace.*KratosForge.*Lakehouse.*KratosForgeLakehouse/);
  assert.match(text, /Operational database.*None yet\. Added when a feature that needs it is turned on\./);
  assert.match(await page().locator(".kfe-status").textContent(), /Connected.*Shared results are saved to the environment as they're made\..*No results wait on this PC\./);
  assert.match(text, /Azure Databricks.*Not available yet.*Snowflake.*Not available yet/);
  assert.doesNotMatch(text, /This PC \(one person/);
  assert.strictEqual(await card().locator("button", { hasText: "Upgrade..." }).count(), 0);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "settings-environment.png"), fullPage: true });
});

test("Upgrade required offers Upgrade, which shows a plan and runs it by its token after a confirm", { skip }, async () => {
  world.check = { status: "Upgrade required", next: "Choose Upgrade. Every record is kept.", detail: "", waiting: 3, compatibility: { state: "upgrade", can_upgrade: true } };
  await openTab();
  assert.match(await page().locator(".kfe-status").textContent(), /Upgrade required.*Choose Upgrade\. Every record is kept\..*3 results wait on this PC\./);
  await card().locator("button", { hasText: "Upgrade..." }).click();
  await page().locator(".kfe-plan").waitFor();
  assert.match(await card().textContent(), /Create Access tables \(fdacc_\).*other PCs can only read them/);
  await card().locator(".kfe-buttons button", { hasText: "Upgrade..." }).click();
  await page().locator("#dialog-root .dialog").waitFor();
  assert.match(await page().locator("#dialog-root .dialog").textContent(), /Upgrade Contoso data platform\?.*Every record is kept/);
  await page().locator("#dialog-root .dialog button", { hasText: "Upgrade" }).click();
  const call = await waitCall("cp_upgrade");
  assert.deepStrictEqual(call.params, { token: "tok-up", confirmed: true, confirm_name: "" });
  await page().locator(".kfe-status", { hasText: "Upgraded" }).waitFor();
});

test("not connected offers Create, Connect and Adopt", { skip }, async () => {
  world.active = null;
  await openTab(".kfe-card h3");
  const text = await card().textContent();
  assert.match(text, /This PC isn't connected to an environment.*Create an environment\.\.\..*Connect to one\.\.\..*Adopt this PC's central lakehouse\.\.\./);
  assert.ok(!app.calls.some((c) => c.method === "cp_check"));
});

test("Create shows the plan, stays off while a step is blocked, then creates by token after a confirm", { skip }, async () => {
  world.active = null;
  world.blocked = true;
  await openTab(".kfe-card h3");
  await card().locator("button", { hasText: "Create an environment..." }).click();
  await card().getByRole("textbox", { name: "Name", exact: true }).fill("Contoso data platform");
  const providers = await card().getByRole("combobox", { name: "Provider" }).locator("option").allTextContents();
  assert.deepStrictEqual(providers, ["Microsoft Fabric (Preview)", "Azure Databricks (not available yet)", "Snowflake (not available yet)"]);
  await card().getByRole("combobox", { name: "Capacity for the new workspace" }).locator("option").first().waitFor({ state: "attached" });
  await card().locator("button", { hasText: "Show what will happen" }).click();
  await page().locator(".kfe-plan").waitFor();
  assert.match(await page().locator(".kfe-plan").textContent(), /A workspace called KratosForge already exists/);
  assert.strictEqual(await page().locator(".kfe-plan tr.dim").count(), 3);
  assert.ok(await card().locator("button", { hasText: "Create environment..." }).isDisabled());
  assert.deepStrictEqual((await waitCall("cp_plan")).params.request,
    { name: "Contoso data platform", workspace_name: "KratosForge", capacity_id: "cap-1", lakehouse_name: "KratosForgeLakehouse" });

  world.blocked = false;
  await card().getByRole("textbox", { name: "Name", exact: true }).fill("Contoso data platform ");
  await card().locator("button", { hasText: "Show what will happen" }).click();
  await page().locator(".kfe-plan").waitFor();
  const rows = await page().locator(".kfe-plan tbody tr").allTextContents();
  assert.match(rows[2], /Create Optimize tables \(fdopt_\).*Included above\./);
  assert.match(rows[3], /Write the manifest.*Included above\..*Ids and versions only, never a secret\./);
  await card().locator("button", { hasText: "Create environment..." }).click();
  await page().locator("#dialog-root .dialog").waitFor();
  assert.match(await page().locator("#dialog-root .dialog").textContent(), /Create Contoso data platform in Fabric\?.*a workspace and a lakehouse.*Nothing is deleted/);
  await page().locator("#dialog-root .dialog button", { hasText: "Cancel" }).click();
  assert.ok(!app.calls.some((c) => c.method === "cp_create"));
  await card().locator("button", { hasText: "Create environment..." }).click();
  await page().locator("#dialog-root .dialog button", { hasText: "Create" }).click();
  assert.deepStrictEqual((await waitCall("cp_create")).params, { token: "tok-create", confirmed: true, confirm_name: "" });
  await page().locator(".kfe-status", { hasText: "Created" }).waitFor();
  assert.match(await card().textContent(), /Contoso data platform is ready, and this PC is connected to it\./);
});

test("a Prod workspace asks for its exact name before Create", { skip }, async () => {
  world.active = null;
  world.prod = "Sales Prod";
  await openTab(".kfe-card h3");
  await card().locator("button", { hasText: "Create an environment..." }).click();
  await card().getByRole("textbox", { name: "Name", exact: true }).fill("Ours");
  await card().getByRole("combobox", { name: "Workspace", exact: true }).locator("option", { hasText: "Sales Prod" }).waitFor({ state: "attached" });
  await card().getByRole("combobox", { name: "Workspace", exact: true }).selectOption("ws-prod");
  await card().locator("button", { hasText: "Show what will happen" }).click();
  await page().locator(".kfe-plan").waitFor();
  await card().locator("button", { hasText: "Create environment..." }).click();
  const dialog = page().locator("#dialog-root .dialog");
  await dialog.waitFor();
  assert.match(await dialog.textContent(), /Create Ours in Sales Prod\?.*Sales Prod is a Prod workspace/);
  const create = dialog.locator("button", { hasText: "Create" });
  await dialog.locator("input").fill("Sales Pro");
  assert.ok(await create.isDisabled());
  await dialog.locator("input").fill("Sales Prod");
  await create.click();
  assert.strictEqual((await waitCall("cp_create")).params.confirm_name, "Sales Prod");
});

test("Connect looks inside a workspace and shows what it found; a copied manifest is refused", { skip }, async () => {
  world.active = null;
  await openTab(".kfe-card h3");
  await card().getByRole("button", { name: "Connect to one..." }).click();
  await card().locator("button", { hasText: "Look inside" }).first().click();
  await card().locator("h4", { hasText: "Couldn't read" }).waitFor();
  const text = await card().textContent();
  assert.match(text, /Finance sandbox.*Read only.*A newer KratosForge wrote this environment/);
  assert.match(text, /Central lakehouses this PC can adopt.*FabricDesktop_Central/);
  assert.match(text, /lh_hr_restricted.*You can't read this lakehouse\./);
  await card().getByRole("button", { name: "Connect to Finance sandbox" }).click();
  await card().locator(".kfe-status", { hasText: "Not connected" }).waitFor();
  assert.match(await card().textContent(), /names another lakehouse, so it wasn't trusted/);
  await card().getByRole("button", { name: "Connect to Contoso data platform" }).click();
  await page().locator(".kfe-status", { hasText: "Connected" }).waitFor();
});

test("Adopt shows the manifest it would write, then adopts by token after a confirm", { skip }, async () => {
  world.active = null;
  await openTab(".kfe-card h3");
  await card().locator("button", { hasText: "Adopt this PC's central lakehouse..." }).click();
  await card().locator(".kfe-adopt-name input").fill("Contoso data platform");
  await card().locator("button", { hasText: "Show the manifest" }).click();
  await card().locator("h4", { hasText: "The manifest it would write" }).waitFor();
  assert.match(await card().textContent(), /environment_id.*kfenv_.*Ids and versions only/);
  await card().locator("button", { hasText: "Adopt..." }).click();
  await page().locator("#dialog-root .dialog").waitFor();
  assert.match(await page().locator("#dialog-root .dialog").textContent(), /Adopt FabricDesktop_Central\?.*No table is created or changed/);
  await page().locator("#dialog-root .dialog button", { hasText: "Adopt" }).click();
  assert.deepStrictEqual((await waitCall("cp_adopt")).params, { token: "tok-adopt", confirmed: true, confirm_name: "" });
});

test("Adopt says plainly when it links the Prod Platform's database", { skip }, async () => {
  world.active = null;
  world.prodStore = true;
  try {
    await openTab(".kfe-card h3");
    await card().locator("button", { hasText: "Adopt this PC's central lakehouse..." }).click();
    await card().locator(".kfe-adopt-name input").fill("Contoso data platform");
    await card().locator("button", { hasText: "Show the manifest" }).click();
    await card().locator("h4", { hasText: "The manifest it would write" }).waitFor();
    assert.match(await card().textContent(), /operational_store\s*SQL_Platform \(Prod\)/);
    await card().locator("button", { hasText: "Adopt..." }).click();
    const dialog = page().locator("#dialog-root .dialog");
    await dialog.waitFor();
    assert.match(await dialog.textContent(), /links your Prod Platform's database as the environment's operational store\. Only its ids are recorded; nothing is written to it\./);
    await dialog.locator("button", { hasText: "Cancel" }).click();
  } finally {
    world.prodStore = false;
  }
});

test("Switch and Disconnect change only this PC; Disconnect is a danger confirm", { skip }, async () => {
  world.check = { ...world.check, waiting: 14 };
  await openTab();
  await card().locator("button", { hasText: "Switch or disconnect..." }).click();
  await card().locator("h3", { hasText: "Environments on this PC" }).waitFor();
  assert.match(await card().textContent(), /Contoso data platform.*Active.*Finance sandbox/);
  await card().getByRole("button", { name: "Disconnect Contoso data platform" }).click();
  const dialog = page().locator("#dialog-root .dialog");
  await dialog.waitFor();
  assert.match(await dialog.textContent(), /Disconnect this PC from Contoso data platform\?.*Nothing in Fabric is changed or deleted.*14 results still wait on this PC\. They stay on this PC, and nothing in the environment changes\./);
  assert.ok(await dialog.locator("button.danger-solid", { hasText: "Disconnect" }).count());
  await dialog.locator("button", { hasText: "Disconnect" }).click();
  assert.deepStrictEqual((await waitCall("cp_disconnect")).params, { environment_id: ENV.environment_id });
});

test("with the policy off, only Disconnect stays", { skip }, async () => {
  world.allowed = false;
  await openTab();
  const text = await card().textContent();
  assert.match(text, /Turned off.*allow_central_environment.*Work still runs on this PC\./);
  assert.deepStrictEqual(await card().locator("button").allTextContents(), ["Disconnect this PC..."]);
  assert.ok(!app.calls.some((c) => c.method === "cp_check"));
});

test("Settings > Environment has no serious axe findings", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  await openTab();
  assert.deepStrictEqual(await axeFindings(page(), "#view .kf-environment"), []);
  world.active = null;
  await openTab(".kfe-card h3");
  await card().locator("button", { hasText: "Create an environment..." }).click();
  await card().getByRole("textbox", { name: "Name", exact: true }).fill("Ours");
  await card().locator("button", { hasText: "Show what will happen" }).click();
  await page().locator(".kfe-plan").waitFor();
  assert.deepStrictEqual(await axeFindings(page(), "#view .kf-environment"), []);
});

// ---- failure paths: what the page says when the environment or a step doesn't go as planned

test("offline and failed statuses say what to do, with the reason and the results waiting", { skip }, async () => {
  world.check = { status: "Working offline", next: "Keep working. Results wait on this PC and are sent when the environment can be reached.",
    detail: "14 results waiting on this PC", waiting: 14, compatibility: null };
  await openTab();
  assert.match(await page().locator(".kfe-status").textContent(), /Working offline.*Keep working\..*14 results wait on this PC\./);
  world.check = { status: "Connection failed", next: "Check the sign-in and network, then choose Validate connection.",
    detail: "The environment's manifest isn't in its lakehouse any more.", waiting: 0 };
  await card().locator("button", { hasText: "Validate connection" }).click();
  await page().locator(".kfe-status[role=alert]", { hasText: "Connection failed" }).waitFor();
  assert.match(await card().textContent(), /manifest isn't in its lakehouse any more/);
  assert.strictEqual(await card().locator("button", { hasText: "Upgrade..." }).count(), 0);
});

test("an environment a newer app upgraded offers no Upgrade, only the update hint", { skip }, async () => {
  world.check = { status: "Upgrade required", next: "Update KratosForge: another installation upgraded this environment. You can still read it.",
    detail: "", waiting: 0, compatibility: { state: "read_only", can_upgrade: false } };
  await openTab();
  assert.match(await page().locator(".kfe-status").textContent(), /Update KratosForge/);
  assert.strictEqual(await card().locator("button", { hasText: "Upgrade..." }).count(), 0);
});

async function planAndCreate() {
  world.active = null;
  await openTab(".kfe-card h3");
  await card().locator("button", { hasText: "Create an environment..." }).click();
  await card().getByRole("textbox", { name: "Name", exact: true }).fill("Ours");
  await card().locator("button", { hasText: "Show what will happen" }).click();
  await page().locator(".kfe-plan").waitFor();
  await card().locator("button", { hasText: "Create environment..." }).click();
  await page().locator("#dialog-root .dialog button", { hasText: "Create" }).click();
  await waitCall("cp_create");
}

test("a create that stops part way says which step and how to finish", { skip }, async () => {
  world.createOutcome = "stopped";
  await planAndCreate();
  await page().locator(".kfe-status", { hasText: "Stopped" }).waitFor();
  assert.match(await card().textContent(), /Lakehouse didn't finish\. capacity paused\. Nothing after it ran\. Make the plan again to finish\./);
});

test("a create refused because something changed shows why and asks for a new plan", { skip }, async () => {
  world.createOutcome = "changed";
  await planAndCreate();
  await card().locator(".kfe-status", { hasText: "Not created" }).waitFor();
  assert.match(await card().textContent(), /Something changed since this was shown/);
  assert.strictEqual(await page().locator(".kfe-plan").count(), 0, "the old plan and its token are gone");
  assert.ok(await card().locator("button", { hasText: "Show what will happen" }).isEnabled());
});

test("Adopt with no central lakehouse says there's nothing to adopt", { skip }, async () => {
  world.active = null;
  world.nothingToAdopt = true;
  await openTab(".kfe-card h3");
  await card().locator("button", { hasText: "Adopt this PC's central lakehouse..." }).click();
  await card().locator(".kfe-adopt-name input").fill("Ours");
  await card().locator("button", { hasText: "Show the manifest" }).click();
  await card().locator(".kfe-status", { hasText: "Nothing to adopt" }).waitFor();
  assert.match(await card().textContent(), /Connect this PC to a central lakehouse first/);
  assert.ok(!app.calls.some((c) => c.method === "cp_adopt"));
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
