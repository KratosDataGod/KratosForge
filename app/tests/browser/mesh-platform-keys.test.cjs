// #1473: Platform > Settings, the platform and keys writes in app/ui/mesh.js (see harness.cjs for how to
// run it). Create, Finish or repair, Join existing and Forget for each environment's Platform workspace;
// Create, Join existing and Move keys in for the one Keys workspace; and cancelling a move that didn't
// switch. Each write is sent with exactly the environment, name, capacity or workspace picked. Creating
// a workspace on a capacity asks first, naming the workspace and the capacity. Move keys in and Cancel
// the move read the engine's plan first (no approve) and send approve: true only once the person
// confirms it, both on a danger-styled button. Cancel or Close at every dialog sends nothing. The
// engine's refusals (not a platform admin, the organization's policy sets the platform) read as the
// engine's sentence. Opening the page sends no write, and axe finds nothing serious on the page and on
// each dialog.
//
// The mocks are shaped on the engine: central/service.py (platform_status, platform_setup, platform_join,
// platform_forget, platform_keys_setup, platform_keys_join, platform_share_keys,
// platform_cancel_share_keys), central/setup.py setup() and central/keys_home.py setup(), plan() and
// share() for the step lists and key reports, and central/core.py whoami(), list_admins() and
// require_admin() for who may change things.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const ME = "chris@contoso.com";
const READS = ["platform_whoami", "platform_status", "platform_admins", "platform_note_seen", "list_capacities", "list_workspaces"];
const WRITES = ["platform_setup", "platform_join", "platform_forget", "platform_keys_setup", "platform_keys_join"];
// Moving and cancelling keys read a plan without approve; only approve: true changes anything.
const APPROVED = ["platform_share_keys", "platform_cancel_share_keys"];
const ENVS = ["dev", "test", "prod"];  // meta/project.py ENVIRONMENTS
const DEFAULT_NAMES = { dev: "KratosForge Platform (Dev)", test: "KratosForge Platform (Test)", prod: "KratosForge Platform" };  // central/setup.py default_name
const KEYS_DEFAULT = "KratosForge Keys";  // central/keys_home.py DEFAULT_NAME
const CAPACITIES = [{ id: "cap-f64", displayName: "Sales F64", sku: "F64" }, { id: "cap-trial", displayName: "Trial", sku: "FT1" }];
const WORKSPACES = [{ id: "ws-plat-dev", displayName: "Platform Dev" }, { id: "ws-keys", displayName: "Keys (central)" }];
// core.require_admin's refusal, as server.py sends a PlatformError: "<type>: <message>".
const NOT_ADMIN = "PlatformError: Only platform admins can add platform admins. Send a request in Data mesh > Requests, or ask a platform admin.";
// service._save_platform and service._check_can_share when IT's policy.json sets the platform.
const POLICY_SETS = "PermissionError: Your organization sets the platform, so it can't be changed here";
const POLICY_KEYS = "PermissionError: Your organization sets the platform, so keys can't be moved from this PC";

const copy = (x) => JSON.parse(JSON.stringify(x));

// service.platform_status: Test has a Platform workspace in Fabric, Dev and Prod are on this PC, and
// the keys database is on this PC and not shared yet.
let cfg, source, fail, moveOk;
function reset() {
  cfg = { dev: {}, prod: {},
    test: { workspace_id: "ws-plat-test", workspace_name: "Platform Test", meta_id: "m1", landing_id: "l1", sql_id: "s1", functions_id: "f1", varlib_id: "v1" } };
  cfg.keys = {};
  source = "user";
  fail = {};
  moveOk = true;
}
reset();
const status = () => ({
  source, default_names: DEFAULT_NAMES,
  environments: Object.fromEntries(ENVS.map((e) => [e, { kind: cfg[e].functions_id ? "fabric" : "local", ...copy(cfg[e]) }])),
  keys: { kind: cfg.keys.functions_id ? "fabric" : "local", shared: !!cfg.keys.shared, default_name: KEYS_DEFAULT, ...copy(cfg.keys) },
  landing: { central: false, default_name: "KratosForge Landing" }, local_path: "C:/Users/chris/AppData/Local/KratosForge/platform",
});
// setup.setup / keys_home.setup: one step per item, each "done" with what it did.
const steps = (labels, verb) => labels.map((step) => ({ step, status: "done", detail: `${verb} ${step}` }));
// keys_home.plan: Prod first, Dev's keys that Prod numbers differently change number.
const PLAN_TABLES = [{ env: "prod", table: "customer", keys: 1200, renumbered: 0 }, { env: "dev", table: "customer", keys: 40, renumbered: 12 }];

const guarded = (method, fn) => (p) => { if (fail[method]) throw new Error(fail[method]); return fn(p); };
const mocks = {
  platform_whoami: () => ({ user: ME, admin: true, admins: 1, can_claim: false, domains_owned: [], signed_in: true, catalog_env: "prod" }),
  platform_status: () => status(),
  platform_admins: () => [{ user_name: ME, added_at: "2026-10-01T09:00:00+00:00", added_by: ME }],
  platform_note_seen: () => ({ noted: false }),
  list_capacities: () => CAPACITIES,
  list_workspaces: () => WORKSPACES,
  platform_setup: guarded("platform_setup", ({ env, name }) => {
    const kept = !!cfg[env].workspace_id;
    const ws = kept ? cfg[env] : { workspace_id: `ws-new-${env}`, workspace_name: name || DEFAULT_NAMES[env] };
    cfg[env] = { ...ws, meta_id: "m", landing_id: "l", sql_id: "s", functions_id: "f", varlib_id: "v" };
    return { env, config: copy(cfg[env]), ready: true, status: status(),
      steps: [{ step: "Platform workspace", status: "done", detail: `${kept ? "Kept" : "Created"} ${cfg[env].workspace_name}` },
        ...steps(["LH_Meta", "LH_Landing", "SQL_Platform", "UDF_Platform", "Platform tables", "VL_Platform"], "Created")] };
  }),
  platform_join: guarded("platform_join", ({ env, workspace_id }) => {
    cfg[env] = { workspace_id, workspace_name: WORKSPACES.find((w) => w.id === workspace_id).displayName, meta_id: "m", landing_id: "l", sql_id: "s", functions_id: "f" };
    return status();
  }),
  platform_forget: guarded("platform_forget", ({ env }) => { cfg[env] = {}; return status(); }),
  platform_keys_setup: guarded("platform_keys_setup", ({ name }) => {
    cfg.keys = { workspace_id: "ws-keys-new", workspace_name: name || KEYS_DEFAULT, sql_id: "ks", functions_id: "kf" };
    return { config: copy(cfg.keys), ready: true, status: status(),
      steps: [{ step: "Keys workspace", status: "done", detail: `Created ${cfg.keys.workspace_name}` }, ...steps(["SQL_Keys", "UDF_Keys", "Key tables"], "Created")] };
  }),
  platform_keys_join: guarded("platform_keys_join", ({ workspace_id }) => {
    cfg.keys = { workspace_id, workspace_name: WORKSPACES.find((w) => w.id === workspace_id).displayName, sql_id: "ks", functions_id: "kf" };
    return status();
  }),
  platform_share_keys: ({ approve = false }) => {
    if (fail.platform_share_keys && approve) throw new Error(fail.platform_share_keys);
    if (fail.share_plan) throw new Error(fail.share_plan);
    if (!approve) {
      return { tables: copy(PLAN_TABLES), renumbered: 12, ok: false, preview: true, message:
        "Every environment's keys close while they move into the keys database on this PC, so runs that need new keys wait. Prod's keys keep their numbers; Dev and Test keys that change need one re-run of their tables. Confirm to continue." };
    }
    const tables = [{ env: "prod", table: "customer", copied: 1200, renumbered: 0, match: true, differ: [] },
      { env: "dev", table: "customer", copied: 40, renumbered: 12, match: moveOk, differ: moveOk ? [] : ["C-0042"] }];
    if (!moveOk) return { tables, ok: false, renumbered: 12, status: status(),
      message: "Nothing switched, and every environment's keys stay closed so they can't change. Move the keys again, or cancel the move to reopen them." };
    cfg.keys = { ...cfg.keys, shared: true, moved_at: new Date().toISOString() };
    return { tables, ok: true, renumbered: 12, status: status() };
  },
  platform_cancel_share_keys: ({ approve = false }) => {
    if (!approve) return { ok: false, preview: true, tables: ["customer"], message:
      "The move never switched, so nothing uses these key tables yet: customer. They're removed from the keys database, and every environment issues its own keys again. Confirm to continue." };
    return { ok: true, cleared: ["customer"], status: status() };
  },
};

before(async () => {
  if (skip) return;
  app = await openApp({ mocks });
  await startupReads(app);
});
after(async () => { if (app) await app.close(); });

const writes = () => app.calls.filter((c) => WRITES.includes(c.method) || (APPROVED.includes(c.method) && c.params?.approve));
const sent = (method) => app.calls.filter((c) => c.method === method);
const dialogTitle = () => page().getAttribute(".dialog", "aria-label");
const settingRow = (text) => page().locator(".setting", { has: page().locator(".setting-text b", { hasText: text }) });
const dialogButton = (name) => page().locator(".dialog .dialog-actions").getByRole("button", { name, exact: true });
const waitDialog = (title) => page().waitForFunction((t) => document.querySelector(".dialog")?.getAttribute("aria-label") === t, title);
const noDialog = () => page().waitForFunction(() => !document.querySelector(".dialog"));
const toastText = async (re) => {
  await page().waitForFunction((src) => new RegExp(src).test(document.getElementById("toast")?.textContent || ""), re.source);
  return page().textContent("#toast");
};

async function openSettings() {
  await page().evaluate((me) => { closeDialog(); state.user = me; goPlatform("settings"); }, ME);
  await page().waitForSelector(".setting:has-text('Test: Platform Test')");
}

beforeEach(async () => {
  if (skip) return;
  reset();
  await openSettings();
  app.calls.length = 0;
});

test("opening Platform > Settings sends no write and shows each environment's and the keys' actions", { skip }, async () => {
  app.calls.length = 0;
  await page().evaluate(() => goPlatform("overview"));
  await openSettings();
  await page().waitForSelector(".setting:has-text('Keys:')");
  assert.deepStrictEqual(writes(), [], "opening the page sends no write");
  assert.ok(app.calls.every((c) => READS.includes(c.method) || c.method === "git_info" || c.method === "environment"
    || c.method.startsWith("platform_") && !WRITES.includes(c.method) && !APPROVED.includes(c.method)),
  `only reads: ${app.calls.map((c) => c.method).join(", ")}`);
  assert.ok(!sent("platform_share_keys").length, "the keys plan is read only when Move keys in is chosen");
  assert.deepStrictEqual(await settingRow("Dev: on this PC").getByRole("button").allTextContents(), ["Create", "Join existing"]);
  assert.deepStrictEqual(await settingRow("Test: Platform Test").getByRole("button").allTextContents(), ["Finish or repair", "Forget"]);
  assert.deepStrictEqual(await settingRow("Keys:").getByRole("button").allTextContents(), ["Move keys in", "Create", "Join existing"]);
  assert.deepStrictEqual(app.errors, []);
});

test("Create asks before making the Dev workspace on the capacity picked, then sends exactly that", { skip }, async () => {
  await settingRow("Dev: on this PC").getByRole("button", { name: "Create" }).click();
  await waitDialog("Create the Dev platform");
  await page().waitForSelector(".dialog select option[value='cap-trial']", { state: "attached" });
  assert.strictEqual(await page().inputValue(".dialog input"), DEFAULT_NAMES.dev);
  // Close at the setup dialog sends nothing.
  await dialogButton("Close").click();
  await noDialog();
  await settingRow("Dev: on this PC").getByRole("button", { name: "Create" }).click();
  await page().waitForSelector(".dialog select option[value='cap-trial']", { state: "attached" });
  await page().fill(".dialog input", "  Sales Platform Dev  ");
  await page().selectOption(".dialog select", "cap-trial");
  await dialogButton("Create platform").click();
  await waitDialog("Create Sales Platform Dev?");
  const ask = await page().textContent(".dialog");
  assert.match(ask, /creates the workspace Sales Platform Dev in Fabric on Trial \(FT1\)/);
  assert.deepStrictEqual(sent("platform_setup"), [], "nothing is created before the person confirms");
  // Cancel at the question goes back to the setup dialog with what was typed, and sends nothing.
  await dialogButton("Cancel").click();
  await waitDialog("Create the Dev platform");
  assert.strictEqual(await page().inputValue(".dialog input"), "  Sales Platform Dev  ");
  assert.strictEqual(await page().inputValue(".dialog select"), "cap-trial");
  assert.deepStrictEqual(sent("platform_setup"), []);
  await dialogButton("Create platform").click();
  await waitDialog("Create Sales Platform Dev?");
  await dialogButton("Create").click();
  await page().waitForSelector(".dialog .steps-list");
  assert.deepStrictEqual(sent("platform_setup").map((c) => c.params), [{ env: "dev", name: "Sales Platform Dev", capacity_id: "cap-trial" }]);
  assert.match(await page().textContent(".dialog"), /Created Sales Platform Dev[\s\S]*The platform is ready\./);
  assert.deepStrictEqual(writes().map((c) => c.method), ["platform_setup"]);
  await dialogButton("Close").click();
  await page().waitForSelector(".setting:has-text('Dev: Sales Platform Dev')");
  assert.deepStrictEqual(app.errors, []);
});

test("Create with no capacity says so before it asks Fabric", { skip }, async () => {
  await settingRow("Dev: on this PC").getByRole("button", { name: "Create" }).click();
  await page().waitForSelector(".dialog select option[value='cap-trial']", { state: "attached" });
  await page().selectOption(".dialog select", "");
  await dialogButton("Create platform").click();
  await waitDialog(`Create ${DEFAULT_NAMES.dev}?`);
  assert.match(await page().textContent(".dialog"), /in Fabric with no capacity/);
  await dialogButton("Create").click();
  await page().waitForSelector(".dialog .steps-list");
  assert.deepStrictEqual(sent("platform_setup").map((c) => c.params), [{ env: "dev", name: DEFAULT_NAMES.dev, capacity_id: "" }]);
});

test("Finish or repair keeps the workspace there, so it sends at once with its name", { skip }, async () => {
  await settingRow("Test: Platform Test").getByRole("button", { name: "Finish or repair" }).click();
  await waitDialog("Finish the Test platform");
  await page().waitForSelector(".dialog select option[value='cap-f64']", { state: "attached" });
  assert.ok(await page().isDisabled(".dialog input"), "an existing workspace's name can't change");
  await dialogButton("Finish setup").click();
  await page().waitForSelector(".dialog .steps-list");
  assert.deepStrictEqual(sent("platform_setup").map((c) => c.params), [{ env: "test", name: "Platform Test", capacity_id: "cap-f64" }]);
  assert.match(await page().textContent(".dialog"), /Kept Platform Test/);
});

test("Join existing sends the environment and the workspace picked; Cancel sends nothing", { skip }, async () => {
  const join = () => settingRow("Dev: on this PC").getByRole("button", { name: "Join existing" }).click();
  await join();
  await waitDialog("Join the Dev platform");
  await dialogButton("Cancel").click();
  await noDialog();
  assert.deepStrictEqual(sent("platform_join"), []);
  await join();
  await waitDialog("Join the Dev platform");
  await page().selectOption(".dialog select", "ws-plat-dev");
  await dialogButton("Join").click();
  await toastText(/Dev now uses Platform Dev/);
  assert.deepStrictEqual(sent("platform_join").map((c) => c.params), [{ env: "dev", workspace_id: "ws-plat-dev" }]);
  await page().waitForSelector(".setting:has-text('Dev: Platform Dev')");
});

test("Forget asks first and sends only the environment; Cancel sends nothing", { skip }, async () => {
  const forget = () => settingRow("Test: Platform Test").getByRole("button", { name: "Forget" }).click();
  await forget();
  await waitDialog("Stop using Platform Test for Test?");
  assert.match(await page().textContent(".dialog"), /Nothing in Fabric is deleted/);
  await dialogButton("Cancel").click();
  await noDialog();
  assert.deepStrictEqual(sent("platform_forget"), []);
  await forget();
  await waitDialog("Stop using Platform Test for Test?");
  await dialogButton("Forget").click();
  await page().waitForSelector(".setting:has-text('Test: on this PC')");
  assert.deepStrictEqual(sent("platform_forget").map((c) => c.params), [{ env: "test" }]);
});

test("the Keys workspace's Create asks first and sends the name and capacity picked; Cancel and Close send nothing", { skip }, async () => {
  const create = () => settingRow("Keys:").getByRole("button", { name: "Create" }).click();
  await create();
  await waitDialog("Create the Keys workspace");
  await page().waitForSelector(".dialog select option[value='cap-f64']", { state: "attached" });
  assert.strictEqual(await page().inputValue(".dialog input"), KEYS_DEFAULT);
  await dialogButton("Close").click();
  await noDialog();
  await create();
  await page().waitForSelector(".dialog select option[value='cap-f64']", { state: "attached" });
  await page().fill(".dialog input", "Central Keys");
  await dialogButton("Create workspace").click();
  await waitDialog("Create Central Keys?");
  assert.match(await page().textContent(".dialog"), /creates the workspace Central Keys in Fabric on Sales F64 \(F64\)[\s\S]*SQL_Keys and UDF_Keys/);
  await dialogButton("Cancel").click();
  await waitDialog("Create the Keys workspace");
  assert.deepStrictEqual(sent("platform_keys_setup"), []);
  await dialogButton("Create workspace").click();
  await waitDialog("Create Central Keys?");
  await dialogButton("Create").click();
  await page().waitForSelector(".dialog .steps-list");
  assert.deepStrictEqual(sent("platform_keys_setup").map((c) => c.params), [{ name: "Central Keys", capacity_id: "cap-f64" }]);
  assert.match(await page().textContent(".dialog"), /Nothing has moved yet: choose Move keys in\./);
  assert.ok(!sent("platform_share_keys").length, "creating the Keys workspace moves no keys");
});

test("the Keys workspace's Join existing sends only the workspace picked; Cancel sends nothing", { skip }, async () => {
  const join = () => settingRow("Keys:").getByRole("button", { name: "Join existing" }).click();
  await join();
  await waitDialog("Join the Keys workspace");
  await dialogButton("Cancel").click();
  await noDialog();
  assert.deepStrictEqual(sent("platform_keys_join"), []);
  await join();
  await waitDialog("Join the Keys workspace");
  await page().selectOption(".dialog select", "ws-keys");
  await dialogButton("Join").click();
  await toastText(/Joined Keys \(central\); keys haven't moved in yet/);
  assert.deepStrictEqual(sent("platform_keys_join").map((c) => c.params), [{ workspace_id: "ws-keys" }]);
});

test("Move keys in reads the plan, and sends approve: true only after the danger-styled confirm", { skip }, async () => {
  const move = () => settingRow("Keys:").getByRole("button", { name: "Move keys in" }).click();
  await move();
  await waitDialog("Move keys in");
  assert.deepStrictEqual(sent("platform_share_keys").map((c) => c.params), [{}], "the plan is read without approve");
  const text = await page().textContent(".dialog");
  assert.match(text, /Every environment's keys close while they move/);
  assert.doesNotMatch(text, /Confirm to continue/);
  assert.match(text, /12 Dev and Test keys change number/);
  assert.deepStrictEqual(await page().$$eval(".dialog tbody tr", (rows) => rows.map((r) => [...r.cells].map((c) => c.textContent))),
    [["Prod", "sk.customer", "1,200", "0 (kept)"], ["Dev", "sk.customer", "40", "12"]]);
  const go = dialogButton("Move keys in");
  assert.match(await go.getAttribute("class"), /danger-solid/, "moving keys is danger-styled");
  assert.strictEqual(await page().evaluate(() => document.activeElement?.textContent), "Cancel", "Cancel has focus");
  await dialogButton("Cancel").click();
  await noDialog();
  assert.deepStrictEqual(writes(), [], "Cancel sends nothing");
  await move();
  await waitDialog("Move keys in");
  await dialogButton("Move keys in").click();
  await waitDialog("Keys moved");
  assert.deepStrictEqual(sent("platform_share_keys").map((c) => c.params), [{}, {}, { approve: true }]);
  assert.match(await page().textContent(".dialog"), /Re-run Dev and Test/);
  await dialogButton("Close").click();
  await page().waitForSelector(".setting:has-text('Dev, Test and Prod use it')");
});

test("a move that didn't switch offers Cancel the move, which reads its plan and asks with a danger button first", { skip }, async () => {
  moveOk = false;
  await settingRow("Keys:").getByRole("button", { name: "Move keys in" }).click();
  await waitDialog("Move keys in");
  await dialogButton("Move keys in").click();
  await waitDialog("Keys didn't switch");
  assert.match(await page().textContent(".dialog"), /Nothing switched[\s\S]*Dev customer: didn't check out/);
  const cancelMove = async () => {
    await dialogButton("Cancel the move").click();
    await waitDialog("Cancel the move?");
  };
  await cancelMove();
  assert.deepStrictEqual(sent("platform_cancel_share_keys").map((c) => c.params), [{}], "the plan is read without approve");
  assert.match(await page().textContent(".dialog"), /They're removed from the keys database/);
  assert.doesNotMatch(await page().textContent(".dialog"), /Confirm to continue/);
  assert.match(await dialogButton("Cancel the move").getAttribute("class"), /danger-solid/);
  await dialogButton("Cancel").click();
  await noDialog();
  assert.ok(!sent("platform_cancel_share_keys").some((c) => c.params.approve), "Cancel sends no approve");
  // Back to the failed move to say yes this time.
  await settingRow("Keys:").getByRole("button", { name: "Move keys in" }).click();
  await waitDialog("Move keys in");
  await dialogButton("Move keys in").click();
  await waitDialog("Keys didn't switch");
  await cancelMove();
  await dialogButton("Cancel the move").click();
  await toastText(/Move cancelled; every environment issues its own keys again/);
  assert.deepStrictEqual(sent("platform_cancel_share_keys").map((c) => c.params), [{}, {}, { approve: true }]);
});

test("refusals read as the engine's sentence: not a platform admin, and the organization's policy", { skip }, async () => {
  fail.platform_share_keys = NOT_ADMIN;
  await settingRow("Keys:").getByRole("button", { name: "Move keys in" }).click();
  await waitDialog("Move keys in");
  await dialogButton("Move keys in").click();
  const said = await toastText(/Keys not moved/);
  assert.strictEqual(said.split("\n")[0], "Keys not moved: Only platform admins can add platform admins. Send a request in Data mesh > Requests, or ask a platform admin.");
  assert.doesNotMatch(said, /PlatformError|Something went wrong/);

  fail.share_plan = POLICY_KEYS;
  await settingRow("Keys:").getByRole("button", { name: "Move keys in" }).click();
  assert.strictEqual((await toastText(/Could not read the keys/)).split("\n")[0],
    "Could not read the keys: Your organization sets the platform, so keys can't be moved from this PC");

  fail.platform_forget = POLICY_SETS;
  await settingRow("Test: Platform Test").getByRole("button", { name: "Forget" }).click();
  await waitDialog("Stop using Platform Test for Test?");
  await dialogButton("Forget").click();
  const forgot = await toastText(/Not changed/);
  assert.strictEqual(forgot.split("\n")[0], "Not changed: Your organization sets the platform, so it can't be changed here");
  assert.doesNotMatch(forgot, /PermissionError|undefined|\[object Object\]/);
  assert.ok(await page().$(".setting:has-text('Test: Platform Test')"), "the platform stays as it was");
  assert.deepStrictEqual(app.errors, []);
});

test("when the organization's policy sets the platform, Settings offers no platform or keys writes", { skip }, async () => {
  source = "policy";
  await openSettings();
  await page().waitForSelector(".setting:has-text('Keys:')");
  for (const row of ["Dev: on this PC", "Test: Platform Test", "Keys:"]) {
    assert.deepStrictEqual(await settingRow(row).locator(".setting-control button").count(), 0, `${row} has no buttons`);
  }
  assert.deepStrictEqual(writes(), []);
});

test("axe finds nothing serious on Platform > Settings and its dialogs", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  assert.deepStrictEqual(await axeFindings(page(), ".page"), []);
  await settingRow("Dev: on this PC").getByRole("button", { name: "Create" }).click();
  await page().waitForSelector(".dialog select option[value='cap-f64']", { state: "attached" });
  assert.deepStrictEqual(await axeFindings(page(), "#dialog-root"), [], "the setup dialog");
  await dialogButton("Create platform").click();
  await waitDialog(`Create ${DEFAULT_NAMES.dev}?`);
  assert.deepStrictEqual(await axeFindings(page(), "#dialog-root"), [], "the question before a workspace is made");
  await dialogButton("Cancel").click();
  await dialogButton("Close").click();
  await noDialog();
  await settingRow("Keys:").getByRole("button", { name: "Move keys in" }).click();
  await waitDialog("Move keys in");
  assert.deepStrictEqual(await axeFindings(page(), "#dialog-root"), [], "the move's plan");
  await dialogButton("Cancel").click();
  assert.deepStrictEqual(writes(), []);
});
