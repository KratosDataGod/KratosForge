// Cancel a Fabric run from the app (#850): Monitor > Runs and the activity tray offer Cancel on a running
// Fabric job this app started, ask first (danger confirm, Keep running focused), then show Cancelling until
// Fabric reads Cancelled.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

let app;
const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const cancels = [];
const run = { job_id: "j1", workspace_id: "ws1", item_id: "nb1", name: "Sales_Load", kind: "Notebook run",
  workspace_name: "Sales DE Dev", status: "InProgress", started_at: new Date(Date.now() - 120000).toISOString() };
const done = { job_id: "j0", workspace_id: "ws1", item_id: "nb0", name: "Old_Load", kind: "Notebook run",
  workspace_name: "Sales DE Dev", status: "Completed", started_at: new Date(Date.now() - 7200000).toISOString(),
  finished_at: new Date(Date.now() - 7000000).toISOString() };

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1440, height: 900 },
    mocks: {
      readiness_check: () => [],
      history: () => [],
      fabric_runs: () => [run, done],
      cancel_fabric_run: (p) => { cancels.push(p); run.cancel_requested = new Date().toISOString(); return { cancelled: p.job_id }; },
    },
  });
});
after(async () => { if (app) await app.close(); });

const showRuns = () => page().evaluate(() => { monitorPage.panel = "runs"; monitorPage.where = "any"; monitorPage.env = "all"; go("home"); go("monitor"); });
const row = (name) => page().locator(".monitor-scroll tbody tr", { hasText: name });

test("Runs offers Cancel only on a running Fabric job, and Keep running sends nothing", { skip }, async () => {
  await showRuns();
  await row("Sales_Load").waitFor();
  assert.strictEqual(await row("Sales_Load").getByRole("button", { name: "Cancel" }).count(), 1);
  assert.strictEqual(await row("Old_Load").getByRole("button", { name: "Cancel" }).count(), 0, "a finished run has no Cancel");
  await row("Sales_Load").getByRole("button", { name: "Cancel" }).click();
  const dialog = page().getByRole("dialog");
  await dialog.waitFor();
  assert.match(await dialog.innerText(), /Cancel Sales_Load in Fabric\?[\s\S]*Sales DE Dev[\s\S]*can't be resumed/);
  assert.ok(await dialog.locator("button.danger-solid", { hasText: "Cancel run" }).count(), "danger confirm");
  assert.strictEqual(await page().evaluate(() => document.activeElement.textContent), "Keep running");
  await dialog.getByRole("button", { name: "Keep running" }).click();
  await dialog.waitFor({ state: "detached" });
  assert.deepStrictEqual(cancels, []);
  // Preview until it has been checked on a tenant: with Preview features hidden there is no Cancel.
  await page().evaluate(() => { state.settings = { ...state.settings, show_preview: { value: false } }; });
  await showRuns();
  await row("Sales_Load").waitFor();
  assert.strictEqual(await row("Sales_Load").getByRole("button", { name: "Cancel" }).count(), 0);
  await page().evaluate(() => { state.settings = { ...state.settings, show_preview: { value: true } }; });
  assert.deepStrictEqual(app.errors, []);
});

test("Confirming cancels the job, the row reads Cancelling, then Cancelled once Fabric stops it", { skip }, async () => {
  await showRuns();
  await row("Sales_Load").getByRole("button", { name: "Cancel" }).click();
  await page().getByRole("dialog").getByRole("button", { name: "Cancel run" }).click();
  await page().waitForFunction(() => document.querySelector(".monitor-scroll")?.innerText.includes("Cancelling"));
  assert.deepStrictEqual(cancels, [{ workspace_id: "ws1", item_id: "nb1", job_id: "j1" }]);
  assert.strictEqual(await row("Sales_Load").getByRole("button", { name: "Cancel" }).count(), 0, "no second Cancel while it stops");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "fabric-cancel.png") });
  run.status = "Cancelled";
  await showRuns();
  await page().waitForFunction(() => /Cancelled/.test(document.querySelector(".monitor-scroll")?.innerText || ""));
  assert.match(await row("Sales_Load").locator(".pill-s").innerText(), /^Cancelled$/);
  assert.deepStrictEqual(app.errors, []);
});

test("The tray's running Fabric entry has Cancel run, and a cancelled run isn't a problem", { skip }, async () => {
  run.status = "InProgress";
  delete run.cancel_requested;
  cancels.length = 0;
  await page().evaluate(() => {
    activity.items = [];
    logActivity({ id: "fabric:j1", status: "running", title: "Sales_Load · Notebook run", text: "In Fabric on Sales DE Dev", monitor: true });
    toggleTray(true);
  });
  const item = page().locator(".tray-item", { hasText: "Sales_Load" });
  await item.getByRole("button", { name: "Cancel run" }).click();
  await page().getByRole("dialog").getByRole("button", { name: "Cancel run" }).click();
  await page().waitForFunction(() => /Cancelling in Fabric/.test(document.querySelector(".tray")?.innerText || ""));
  assert.deepStrictEqual(cancels, [{ workspace_id: "ws1", item_id: "nb1", job_id: "j1" }]);
  run.status = "Cancelled";
  await page().evaluate(() => trackFabricJobs());
  await page().waitForFunction(() => activity.items.find((i) => i.id === "fabric:j1")?.status === "cancelled");
  assert.match(await item.locator(".pill-s").innerText(), /^Cancelled$/);
  assert.strictEqual(await item.getByRole("button", { name: "Cancel run" }).count(), 0);
  assert.strictEqual(await page().evaluate(() => activity.items.filter((i) => PROBLEM.has(i.status)).length), 0);
  await page().evaluate(() => toggleTray(false));
  assert.deepStrictEqual(app.errors, []);
});
