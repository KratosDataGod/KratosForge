// Forge animations in the notebook (docs/forge-animations.md), against the real engine's kernel:
// a spark when a cell succeeds, the wolf track during a run of several cells, an ember when a cell
// fails, and none of the decoration with the setting off or with reduced motion. No lakehouse is
// attached, so nothing reaches OneLake.
const { test, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const CELLS = [
  { type: "code", cell_id: "a", source: "import time\ntime.sleep(0.5)\nx = 1", meta: {} },
  { type: "code", cell_id: "b", source: "time.sleep(0.5)\ny = x + 1", meta: {} },
  { type: "code", cell_id: "c", source: "time.sleep(0.5)\nz = y + 1", meta: {} },
];
const ENGINE = ["start_cell", "poll_cell", "kernel_variables", "app_settings", "save_app_settings"];

const apps = [];
async function open({ cells = CELLS, reducedMotion = "no-preference", settings } = {}) {
  const app = await openApp({ engine: ENGINE, mocks: settings ? { app_settings: settings } : {} });
  apps.push(app);
  await app.page.emulateMedia({ reducedMotion });
  await app.page.evaluate(async (cells) => {
    await loadSettings();
    loadIntoState({ name: "Daily sales", cells });
    state.ribbonTab = "run";
    go("notebook");
  }, cells);
  // Every effect element made, whether or not it is still there when we look.
  await app.page.evaluate(() => {
    window.__fx = [];
    new MutationObserver((list) => list.forEach((m) => m.addedNodes.forEach((n) => {
      if (n.classList?.contains("fx") || n.classList?.contains("wolf")) window.__fx.push(n.className);
      n.querySelectorAll?.(".fx, .wolf").forEach((e) => window.__fx.push(e.className));
    }))).observe(document.body, { childList: true, subtree: true });
  });
  return app;
}

after(async () => { for (const app of apps) await app.close(); });

test("Run all shows the wolf at the cells done, glows at the end, and says so once", { skip }, async () => {
  const app = await open();
  const { page } = app;
  await page.getByRole("button", { name: "Run all" }).first().click();
  const bar = page.getByRole("progressbar", { name: "Notebook run" });
  await bar.waitFor();
  assert.match(await page.locator(".wolf-text").innerText(), /^Cell [12] of 3$/);
  assert.ok(await page.locator(".wolf-bar .wolf").count(), "no wolf on the track");
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "forge-run-all.png") });
  await page.locator(".fx-arrive").waitFor({ state: "attached", timeout: 30000 });
  await bar.waitFor({ state: "detached" });
  await page.waitForFunction(() => document.getElementById("announce").textContent === "Run finished: 3 cells succeeded");
  const made = await page.evaluate(() => window.__fx);
  assert.ok(!made.some((c) => /fx-spark/.test(c)), "cells sparked one by one during Run all");
  assert.deepStrictEqual(await page.evaluate(() => state.notebook.cells.map((c) => c.status)), ["ok", "ok", "ok"]);
  assert.deepStrictEqual(app.errors, []);
});

test("A cell that succeeds sparks; a cell that fails shows the ember and the error at once", { skip }, async () => {
  const cells = [CELLS[0], { type: "code", cell_id: "f", source: "raise KeyError('daily_total')", meta: {} }];
  const app = await open({ cells });
  const { page } = app;
  const [first, second] = await page.evaluate(() => state.notebook.cells.map((c) => `[data-cell="${c.id}"]`));
  const spark = page.waitForSelector(`${first} .fx-spark`, { state: "attached", timeout: 30000 });
  await page.evaluate(() => runCell(state.notebook.cells[0]));
  await spark;
  await page.locator(`${first} .fx-spark`).waitFor({ state: "detached" });
  await page.waitForFunction(() => document.getElementById("announce").textContent === "Cell 1 succeeded");
  await page.evaluate(() => runCell(state.notebook.cells[1]));
  assert.strictEqual(await page.locator(`${second} .cell-status > span`).first().innerText(), "Failed");
  assert.match(await page.locator(`${second} .output.error`).innerText(), /KeyError/);
  assert.ok((await page.evaluate(() => window.__fx)).some((c) => /fx-fail/.test(c)), "no ember for the failed cell");
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "forge-fail.png") });
  assert.deepStrictEqual(app.errors, []);
});

test("With Forge animations off nothing decorative is made, and the run still shows its progress", { skip }, async () => {
  const app = await open();
  const { page } = app;
  await page.evaluate(() => saveSettings({ forge_animations: false }));
  assert.strictEqual(await page.evaluate(() => document.documentElement.dataset.motion), "off");
  await page.getByRole("button", { name: "Run all" }).first().click();
  await page.getByRole("progressbar", { name: "Notebook run" }).waitFor();
  await page.waitForFunction(() => document.getElementById("announce").textContent === "Run finished: 3 cells succeeded", null, { timeout: 30000 });
  assert.deepStrictEqual(await page.evaluate(() => window.__fx), [], "an effect was made with the setting off");
  // The setting is kept for the next start, and Settings shows it.
  assert.strictEqual(await page.evaluate(async () => (await rpc("app_settings")).forge_animations.value), false);
  await page.evaluate(() => go("settings"));
  const toggle = page.getByRole("checkbox", { name: "Forge animations" });
  await toggle.waitFor();
  assert.strictEqual(await toggle.isChecked(), false);
  await toggle.check();
  await page.waitForFunction(() => document.documentElement.dataset.motion === "on");
  assert.deepStrictEqual(app.errors, []);
});

test("Windows reduced motion turns the effects off whatever the setting says", { skip }, async () => {
  const app = await open({ reducedMotion: "reduce" });
  const { page } = app;
  assert.strictEqual(await page.evaluate(() => motionOn()), false);
  await page.getByRole("button", { name: "Run all" }).first().click();
  await page.waitForFunction(() => document.getElementById("announce").textContent === "Run finished: 3 cells succeeded", null, { timeout: 30000 });
  assert.deepStrictEqual(await page.evaluate(() => window.__fx), []);
  await page.evaluate(() => go("settings"));
  await page.getByText("Off while Windows reduces motion").waitFor();
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "forge-settings-reduced.png") });
  assert.deepStrictEqual(app.errors, []);
});
