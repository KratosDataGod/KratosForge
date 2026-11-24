// Monitor > KratosForge > Operations > Queue (queue.js) and Run as (runas.js) beyond the main panel test
// (operations-panels.test.cjs): an environment whose agent records couldn't be read, This PC's
// scheduler not answering, Fabric schedules being read, failed or not read yet (and the link to read
// them), a row outside any environment, a row with a Fabric link, the Queue's Running, Queued and Next
// chips, Run as for you yourself and for an agent that doesn't report its credential's dates, and the
// command bar's Queue and Run as. queue_list and run_as_list are mocked per test.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const at = (s) => new Date(Date.now() + s * 1000).toISOString();

const unreadable = { env: "test", count: null, observation: { label: "Can't read", detail: "LH_Meta in Test isn't reachable" } };
const live = { env: "prod", count: 1, observation: { label: "Live", state: "live", detail: "" } };
let fabric;  // sources.fabric_schedules, per test

const queueRows = () => [
  { state: "running", what: "silver_orders", env: "prod", where: { kind: "agent", name: "etl-01" }, owner: "etl-01's queue", at: at(-120), started_by: "schedule hourly" },
  { state: "queued", what: "Load sales", env: "prod", where: { kind: "fabric", name: "Sales Prod" }, owner: "Fabric", at: null,
    started_by: "this app", url: "https://app.fabric.microsoft.com/groups/w1/pipelines/p1" },
  { state: "unknown", what: "1 job", env: "prod", where: { kind: "agent", name: "etl-02" }, owner: "etl-02's queue", at: null, as_of: at(-3600) },
  { state: "next", what: "scratch_export", env: null, where: { kind: "pc", name: "This PC" }, owner: "Windows Task Scheduler", at: null, text: "" },
];

const runAsRows = () => [
  { place: { kind: "pc", name: "This PC" }, env: null, runs_as: { kind: "you", name: "chris@contoso.com" },
    credential: { kind: "user", state: "ok", expires_at: null, last_token_at: at(-60) },
    boundary: "Data is read and written on this PC", detail: "Runs and schedules you start here.", live: true },
  { place: { kind: "agent", name: "etl-05" }, env: null, runs_as: { kind: "service_principal", name: "Service principal" },
    credential: { kind: "certificate", state: "unknown" }, boundary: "Data on etl-05's server", as_of: at(-30), live: true },
  { place: { kind: "agent", name: "etl-06" }, env: "prod", runs_as: { kind: "unknown" },
    credential: { kind: "mystery", state: "unknown" }, boundary: "Data on etl-06's server", as_of: at(-30), live: true },
  { place: { kind: "agent", name: "etl-07" }, env: "prod", runs_as: { kind: "managed_identity", name: "Managed identity" },
    credential: { kind: "managed_identity", state: "not_visible" }, boundary: "Data on etl-07's server", as_of: at(-30), live: true },
];

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    readiness_check: () => [],
    queue_list: () => ({ rows: queueRows(), sources: { agents: [live, unreadable], pc: { state: "error", detail: "Task Scheduler didn't answer" }, fabric_schedules: fabric }, hours: 12 }),
    run_as_list: () => ({ rows: runAsRows(), attention: [], expiring_days: 30, sources: { agents: [live, unreadable], fabric_schedules: fabric } }),
  } });
  await startupReads(app);
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  fabric = { state: "reading" };
  await page().evaluate(() => { quPage.filter = "all"; window.__fdShellCalls.length = 0; go("home"); });
});

const panel = () => page().locator(".page.monitor .inc-panel");
const open = async (name) => {
  await page().evaluate((p) => { monitorPage.panel = p; go("monitor"); }, name);
  await panel().locator(".inc-watchers").waitFor();
};
const sourcesText = () => panel().locator(".inc-watchers").innerText();
const whats = () => panel().locator("tbody tr:not(.qu-group) td:nth-child(2)").allInnerTexts();

test("Queue says which sources couldn't be read, and its chips show running, queued or next only", { skip }, async () => {
  await open("queue");
  const text = await sourcesText();
  assert.match(text, /This PC: Task Scheduler didn't answer/);
  assert.match(text, /Can't read Test: LH_Meta in Test isn't reachable/);
  assert.match(text, /Fabric schedules being read on Schedules/);
  assert.deepStrictEqual(await panel().locator(".qu-chips button").allInnerTexts(), ["All · 4", "Running · 1", "Queued · 1", "Next 12 h · 1"]);
  assert.deepStrictEqual(await panel().locator(".qu-group").allInnerTexts(), ["Running and queued", "Next 12 hours"]);
  const pcRow = panel().locator("tbody tr", { hasText: "scratch_export" });
  assert.strictEqual(await pcRow.locator("td").nth(2).innerText(), "Not in an environment");
  const fabricRow = panel().locator("tbody tr", { hasText: "Load sales" });
  assert.strictEqual(await fabricRow.locator("td").nth(3).innerText(), "Fabric · Sales Prod");
  assert.strictEqual(await fabricRow.locator("td").nth(5).innerText(), "waiting to start");

  await fabricRow.getByRole("button", { name: "Open in Fabric" }).click();
  await page().waitForFunction(() => window.__fdShellCalls.some((c) => c.cmd === "open_link"));
  assert.deepStrictEqual(await page().evaluate(() => window.__fdShellCalls.find((c) => c.cmd === "open_link").args),
    { url: "https://app.fabric.microsoft.com/groups/w1/pipelines/p1" });

  await panel().getByRole("button", { name: "Queued · 1" }).click();
  await panel().getByRole("button", { name: "Queued · 1" }).and(page().locator(".on")).waitFor();
  assert.deepStrictEqual(await whats(), ["Load sales"]);
  await panel().getByRole("button", { name: "Running · 1" }).click();
  await panel().getByRole("button", { name: "Running · 1" }).and(page().locator(".on")).waitFor();
  assert.deepStrictEqual(await whats(), ["silver_orders"]);
  await panel().getByRole("button", { name: "Next 12 h · 1" }).click();
  await panel().getByRole("button", { name: "Next 12 h · 1" }).and(page().locator(".on")).waitFor();
  assert.deepStrictEqual(await whats(), ["scratch_export"]);
  assert.deepStrictEqual(app.errors, []);
});

test("Queue and Run as say why Fabric schedules failed, and when they aren't read link to where they are", { skip }, async () => {
  fabric = { state: "error", detail: "The schedules list was cut short." };
  await open("queue");
  assert.match(await sourcesText(), /Fabric schedules: The schedules list was cut short\./);
  await open("runas");
  assert.match(await sourcesText(), /Fabric schedules: The schedules list was cut short\./);

  fabric = {};
  await open("runas");
  assert.match(await sourcesText(), /Fabric schedules not read yet\./);
  await panel().getByRole("button", { name: "Read them on Fabric > Schedules" }).click();
  await page().waitForFunction(() => state.view === "monitor" && monitorPage.panel === "fabric_schedules");
  await page().evaluate(() => go("home"));
  await open("queue");
  await panel().getByRole("button", { name: "Read them on Fabric > Schedules" }).click();
  await page().waitForFunction(() => state.view === "monitor" && monitorPage.panel === "fabric_schedules");
});

test("Run as names you on this PC, and says when an agent doesn't report its credential", { skip }, async () => {
  await open("runas");
  const text = await sourcesText();
  assert.match(text, /Can't read Test: LH_Meta in Test isn't reachable/);
  assert.match(text, /Fabric schedules being read on Schedules/);
  const row = (name) => panel().locator("tbody tr", { hasText: name });
  assert.strictEqual(await row("This PC").locator("td").nth(1).innerText(), "You, chris@contoso.com");
  assert.match(await row("etl-05").locator("td").nth(0).innerText(), /Several environments · reported/);
  assert.match(await row("etl-05").locator("td").nth(3).innerText(), /^Unknown\nThe agent couldn't read its certificate's dates$/);
  assert.strictEqual(await row("etl-06").locator("td").nth(1).innerText(), "Unknown", "an unknown identity has no agent.toml note");
  assert.strictEqual(await row("etl-06").locator("td").nth(2).innerText(), "Unknown");
  assert.match(await row("etl-06").locator("td").nth(3).innerText(), /An older version that doesn't report it/);
  assert.match(await row("etl-07").locator("td").nth(3).innerText(), /^Not visible\nIts expiry isn't visible to the agent$/);
  assert.deepStrictEqual(app.errors, []);
});

test("the command bar opens Queue and Run as", { skip }, async () => {
  for (const [label, panelId] of [["Queue", "queue"], ["Run as", "runas"]]) {
    await page().keyboard.press("Control+K");
    await page().getByRole("combobox", { name: "Command bar" }).fill(label);
    await page().locator("#palette-list").getByRole("option", { name: label, exact: true }).click();
    await page().waitForFunction((p) => state.view === "monitor" && monitorPage.panel === p, panelId);
    await panel().locator(".inc-watchers").waitFor();
    await page().evaluate(() => go("home"));
  }
});
