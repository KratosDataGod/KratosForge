// Recent runs (runs.js) beyond notebook-runs.test.cjs: opening from the command bar closes the Validate
// panel, runs that can't be read say why and Refresh reads them again, Open Monitor goes there, and
// the panel's close button and the ribbon's toggle close it. notebook_runs is mocked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const ago = (minutes) => new Date(Date.now() - minutes * 60000).toISOString();
const RUN = { source: "pc", started: ago(30), ended: ago(29), seconds: 12, status: "ok", trigger: "home", message: "2 cells ran",
  cells: 2, schedule_id: "s9" };
let app;
let runs;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { notebook_runs: () => runs() } });
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  runs = () => [RUN];
  await page().evaluate(() => {
    document.getElementById("palette")?.remove();
    loadIntoState({ name: "Daily sales", cells: [{ type: "code", cell_id: "c1", source: "x = 1", meta: {} }] });
    go("notebook");
  });
});

const pane = () => page().locator(".runspane");
const openFromBar = async () => {
  await page().evaluate(() => openPalette());
  await page().getByRole("combobox", { name: "Command bar" }).fill("Show this notebook's runs");
  await page().getByRole("option", { name: "Show this notebook's runs", exact: true }).click();
  await pane().waitFor();
};

test("the command bar opens Recent runs in place of Validate; runs that can't be read say why until Refresh", { skip }, async () => {
  runs = () => { throw new Error("The run log is locked by another program."); };
  await page().evaluate(() => { state.notebook.validate = { open: true }; });
  await openFromBar();
  assert.strictEqual(await page().evaluate(() => state.notebook.validate.open), false, "one panel beside the cells at a time");
  await pane().locator(".problems").waitFor();
  assert.match(await pane().locator(".problems").textContent(), /^Couldn't read this notebook's runs\. /);
  assert.strictEqual(await pane().locator(".muted.small.grow").textContent(), "Reading runs...");

  runs = () => [RUN];
  await pane().getByRole("button", { name: "Refresh", exact: true }).click();
  await pane().getByText("Run from Home").waitFor();
  assert.strictEqual(await pane().locator(".problems").count(), 0);
  assert.strictEqual(await pane().locator(".muted.small.grow").textContent(), "1 run");
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "notebook_runs").at(-1).params, { name: "Daily sales", limit: 50, refresh: false });
});

test("Open Monitor goes to Monitor", { skip }, async () => {
  await openFromBar();
  await pane().getByRole("button", { name: "Open Monitor" }).click();
  await page().waitForFunction(() => state.view === "monitor");
});

test("the panel's close button and the ribbon's toggle both close it", { skip }, async () => {
  await openFromBar();
  await pane().getByRole("button", { name: "Close Recent runs" }).click();
  await page().waitForFunction(() => !document.querySelector(".runspane"));
  assert.strictEqual(await page().evaluate(() => state.notebook.runs.open), false);

  await openFromBar();
  await page().getByRole("button", { name: "Schedule" }).click();
  await page().getByRole("menuitemcheckbox", { name: /^Recent runs/ }).click();
  await page().waitForFunction(() => !document.querySelector(".runspane"));
});
