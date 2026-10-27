// Monitor's controls fit on one row (UX cleanup): Operations picks its view from a dropdown on the view row,
// the sentence about the tab is the line under the title, and Workloads groups from a dropdown beside its views.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

let app;
const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const groups = [];

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1440, height: 900 },
    mocks: {
      readiness_check: () => [],
      workloads_list: (p) => {
        groups.push(p.group);
        return { views: [{ id: "attention", label: "Needs attention", count: 2 }, { id: "all", label: "All", count: 9 }],
          groups: [], rows: [], sources: [], total: 0, offset: 0, limit: 50 };
      },
    },
  });
});
after(async () => { if (app) await app.close(); });

test("Operations picks its view from a dropdown on the view row, and the row is one line", { skip }, async () => {
  await page().evaluate(() => { monitorPage.panel = "queue"; go("home"); go("monitor"); });
  const row = page().locator(".monitor-view-row");
  const pick = row.getByRole("combobox", { name: "Operations view" });
  assert.strictEqual(await pick.inputValue(), "queue");
  assert.strictEqual(await page().locator(".monitor-local > .monitor-filters").count(), 0, "no row of view chips in the card");
  // What the tab shows is the line under the title, with its Learn more link.
  assert.match(await page().locator(".page-head .monitor-context").innerText(), /What is running and what runs next[\s\S]*Learn more/);
  assert.ok(await page().locator('.monitor-tabs .tab.active:has-text("KratosForge")').count());
  assert.ok(await page().locator('.monitor-view-row .monitor-pages button.on:has-text("Operations")').count());
  const box = await row.boundingBox();
  assert.ok(box.height < 60, `view row is ${box.height}px tall`);
  await pick.selectOption("incidents");
  await page().waitForFunction(() => monitorPage.panel === "incidents");
  assert.strictEqual(await page().locator(".monitor-view-row").getByRole("combobox", { name: "Operations view" }).inputValue(), "incidents");
  assert.deepStrictEqual(app.errors, []);
});

test("Runs has no Operations dropdown and keeps its filters", { skip }, async () => {
  await page().evaluate(() => { monitorPage.panel = "runs"; go("home"); go("monitor"); });
  await page().getByRole("group", { name: "Where it ran" }).waitFor();
  assert.strictEqual(await page().getByRole("combobox", { name: "Operations view" }).count(), 0);
  assert.match(await page().locator(".monitor-context").innerText(), /Everything this app ran or started/);
});

test("Workloads groups from a dropdown on the same row as its views", { skip }, async () => {
  await page().evaluate(() => { monitorPage.panel = "workloads"; go("home"); go("monitor"); });
  const group = page().getByRole("combobox", { name: "Group by" });
  await group.waitFor();
  const row = page().locator(".wl-panel > .monitor-filters");
  assert.match(await row.innerText(), /Needs attention · 2[\s\S]*Group by/);
  const views = await row.getByRole("button", { name: /Needs attention/ }).boundingBox();
  const sel = await group.boundingBox();
  assert.ok(Math.abs(views.y + views.height / 2 - (sel.y + sel.height / 2)) < 8, "views and Group by share a row");
  await group.selectOption("owner");
  await page().waitForFunction(() => wlPage.group === "owner");
  assert.strictEqual(groups.at(-1), "owner");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "monitor-one-row.png") });
  assert.deepStrictEqual(app.errors, []);
});

test("Monitor's tabs are by source and stay with Preview features hidden; each tab's pages are on the view row", { skip }, async () => {
  await page().evaluate(() => { monitorPage.panel = "runs"; go("home"); go("monitor"); });
  await page().locator(".monitor-pages").waitFor();
  const tabs = () => page().locator(".monitor-tabs .tab").allTextContents();
  const pages = () => page().locator(".monitor-view-row .monitor-pages button").allTextContents();
  assert.deepStrictEqual(await tabs(), ["KratosForge", "Fabric"]);
  assert.deepStrictEqual((await pages()).map((t) => t.replace("Preview", "")), ["Runs", "Schedules", "Operations"]);
  await page().locator(".monitor-tabs .tab", { hasText: "Fabric" }).click();
  await page().waitForFunction(() => monitorPage.panel === "fabric");
  assert.deepStrictEqual(await pages(), ["Monitoring hub", "Schedules"]);
  await page().locator(".monitor-pages button", { hasText: "Schedules" }).click();
  await page().waitForFunction(() => monitorPage.panel === "fabric_schedules");
  assert.ok(await page().locator('.monitor-tabs .tab.active:has-text("Fabric")').count());
  // With Preview features hidden, the tabs and the hub are still there; the Preview pages aren't.
  await page().evaluate(() => { state.settings = { ...state.settings, show_preview: { value: false } }; monitorPage.panel = "runs"; render(); });
  await page().locator(".monitor-tabs .tab").first().waitFor();
  assert.deepStrictEqual(await tabs(), ["KratosForge", "Fabric"]);
  assert.strictEqual(await page().locator(".monitor-pages").count(), 0, "Runs is KratosForge's only page then");
  await page().locator(".monitor-tabs .tab", { hasText: "Fabric" }).click();
  await page().waitForSelector(".monitor-fabric .fabric-slot");
  await page().evaluate(() => { state.settings = { ...state.settings, show_preview: { value: true } }; monitorPage.panel = "runs"; render(); });
  assert.deepStrictEqual(app.errors, []);
});
