// Remote runs (KF-MO-009, docs/forge-animations.md): a job the app started in Fabric shows the navy
// wolf trotting in an indeterminate bar in the bell and on Home, with words saying where it runs and
// no fill, percentage or estimate; with motion Off or reduced motion the plain indeterminate bar shows.
// The run is logged straight into the activity list, so nothing reaches Fabric.
const { test, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const ENGINE = ["app_settings", "save_app_settings"];

const apps = [];
async function open({ reducedMotion = "no-preference" } = {}) {
  const app = await openApp({ engine: ENGINE, mocks: { fabric_runs: () => [] } });
  apps.push(app);
  await app.page.emulateMedia({ reducedMotion });
  await app.page.evaluate(async () => {
    await loadSettings();
    logActivity({ id: "validate:v1", status: "running", title: "Daily sales · Validate", text: "Running every cell from the top on this PC" });
    logActivity({ id: "fabric:j1", status: "running", title: "Daily sales · Notebook run", text: "In Fabric on Sales" });
    toggleTray(true);
  });
  return app;
}

// What the run in Fabric's bar shows, as a person sees it.
const remote = (page) => page.evaluate(() => {
  const bar = document.querySelector('.tray [role="progressbar"][aria-label="Daily sales · Notebook run"]');
  const wrap = bar?.closest(".remote-progress");
  const wolf = bar?.querySelector(".wolf-idle");
  const fill = bar?.querySelector(".bar");
  return {
    found: !!bar, valuenow: bar?.getAttribute("aria-valuenow"), valuetext: bar?.getAttribute("aria-valuetext"),
    text: wrap?.textContent, wolf: !!wolf && getComputedStyle(wolf).display !== "none",
    wolfColor: wolf && getComputedStyle(wolf).color, fill: !!fill && getComputedStyle(fill).display !== "none",
  };
});

after(async () => { for (const app of apps) await app.close(); });

test("A run in Fabric shows the navy wolf trotting in place, says where it runs, and shows no fill or percentage", { skip }, async () => {
  const app = await open();
  const { page } = app;
  const r = await remote(page);
  assert.ok(r.found, "the run in Fabric has no progress bar");
  assert.strictEqual(r.valuenow, null, "an indeterminate bar claims a value");
  assert.strictEqual(r.valuetext, "Running in Fabric");
  assert.strictEqual(r.text, "Running in Fabric");
  assert.ok(r.wolf, "no wolf at the default level");
  assert.strictEqual(r.wolfColor, "rgb(27, 54, 100)", "the wolf is not Kratos navy");
  assert.ok(!r.fill, "the sliding fill shows under the wolf");
  assert.ok(!/\d+\s*%|remaining|left/.test(await page.textContent(".tray")), "a percentage or estimate is shown");
  // A run on this PC keeps its own bar and no wolf.
  const local = page.locator('.tray [role="progressbar"][aria-label="Daily sales · Validate"]');
  assert.strictEqual(await local.locator(".wolf").count(), 0);
  assert.strictEqual(await local.locator(".bar.indeterminate").count(), 1);
  if (SHOTS) await page.locator(".tray").screenshot({ path: path.join(SHOTS, "remote-progress-bell.png") });
  // Home's running list shows the same bar.
  await page.evaluate(() => { toggleTray(false); go("home"); });
  await page.locator('.home-item [role="progressbar"][aria-label="Daily sales · Notebook run"] .wolf-idle').waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("With motion Off the wolf goes and the plain indeterminate bar and the words stay", { skip }, async () => {
  const app = await open();
  const { page } = app;
  await page.evaluate(async () => { await saveSettings({ motion_level: 0 }); renderTray(); });
  await page.waitForFunction(() => document.documentElement.dataset.motion === "off");
  const r = await remote(page);
  assert.ok(!r.wolf, "a wolf trotted with motion Off");
  assert.ok(r.fill, "the indeterminate bar went with the wolf");
  assert.strictEqual(r.text, "Running in Fabric");
  assert.deepStrictEqual(app.errors, []);
});

test("Windows reduced motion: no wolf, the indeterminate bar and the words stay", { skip }, async () => {
  const app = await open({ reducedMotion: "reduce" });
  const r = await remote(app.page);
  assert.ok(!r.wolf, "a wolf trotted under reduced motion");
  assert.ok(r.fill, "the indeterminate bar went with the wolf");
  assert.strictEqual(r.valuenow, null);
  assert.strictEqual(r.text, "Running in Fabric");
  assert.deepStrictEqual(app.errors, []);
});
