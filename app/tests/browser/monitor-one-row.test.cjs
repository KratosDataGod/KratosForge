// Monitor's controls fit on one row (UX cleanup): Operations picks its view from a dropdown on the band,
// the sentence about the panel is Learn more's tooltip, and Workloads groups from a dropdown beside its views.
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

test("Operations picks its view from a dropdown on the band, and the band is one row", { skip }, async () => {
  await page().evaluate(() => { monitorPage.panel = "queue"; go("home"); go("monitor"); });
  const band = page().locator(".monitor-local .monitor-band");
  const pick = band.getByRole("combobox", { name: "Operations view" });
  assert.strictEqual(await pick.inputValue(), "queue");
  assert.strictEqual(await page().locator(".monitor-local > .monitor-filters").count(), 0, "no row of view chips under the band");
  const about = band.locator(".monitor-about a");
  assert.match(await about.getAttribute("title"), /What is running and what runs next/);
  assert.strictEqual(await about.innerText(), "Learn more");
  // Keyboard and screen reader users get the sentence too: the link is described by it.
  assert.match(await page().locator(`#${await about.getAttribute("aria-describedby")}`).textContent(), /What is running and what runs next/);
  const box = await band.boundingBox();
  assert.ok(box.height < 60, `band is ${box.height}px tall`);
  await pick.selectOption("incidents");
  await page().waitForFunction(() => monitorPage.panel === "incidents");
  assert.strictEqual(await page().locator(".monitor-band").getByRole("combobox", { name: "Operations view" }).inputValue(), "incidents");
  assert.deepStrictEqual(app.errors, []);
});

test("Runs has no Operations dropdown and keeps its filters", { skip }, async () => {
  await page().evaluate(() => { monitorPage.panel = "runs"; go("home"); go("monitor"); });
  await page().getByRole("group", { name: "Where it ran" }).waitFor();
  assert.strictEqual(await page().getByRole("combobox", { name: "Operations view" }).count(), 0);
  assert.match(await page().locator(".monitor-about a").getAttribute("title"), /Everything this app ran or started/);
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
