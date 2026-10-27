// Pop-out windows (Preview): Monitor's two sections open in windows of their own and dock back
// (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const FABRIC_RUN = "https://app.fabric.microsoft.com/groups/ws1/synapsenotebooks/nb1?experience=fabric-developer";
const now = Date.now();
const at = (min) => new Date(now - min * 60000).toISOString();
const mocks = {
  history: () => Array.from({ length: 40 }, (_, i) => ({ finished_at: at(60 + i), job: `notebook ${i}`, seconds: 2, rows: 10,
    table_uri: `abfss://ws@onelake/lh/Tables/t${i}`, mode: "append", delta_version: i })),
  meta_runs_all: () => ({ runs: [{ run_id: "r1", env: "dev", entity: "bronze.orders", status: "failed", started_at: at(5),
    seconds: 1, rows_written: 0, error: "no files match landing/orders/*.csv" }], unavailable: {} }),
  fabric_runs: () => [{ started_at: at(2), status: "InProgress", name: "Sales load", kind: "Notebook run", url: FABRIC_RUN }],
  pc_schedule_runs: () => [],
};
const shellCalls = (page, cmd) => page.evaluate((c) => window.__fdShellCalls.filter((x) => x.cmd === c).map((x) => x.args), cmd);

let main, runs, fabric;
before(async () => {
  if (skip) return;
  [main, runs, fabric] = await Promise.all([
    openApp({ mocks }),
    openApp({ mocks, search: "?popout=runs", viewport: { width: 720, height: 640 } }),
    openApp({ mocks, search: "?popout=fabric", viewport: { width: 1100, height: 640 } }),
  ]);
  await main.page.evaluate(() => { monitorPage.showFabric = true; go("monitor"); });
  await main.page.waitForSelector(".monitor-scroll");
});
after(async () => { await Promise.all([main, runs, fabric].map((a) => a?.close())); });

test("each Monitor section has Pop out, marked Preview", { skip }, async () => {
  // The runs list's Pop out is the page's button, top right; the hub's is on its band.
  const places = await main.page.$$eval(".page.monitor .page-head .head-actions, .page.monitor .monitor-band", (els) => els.map((b) => b.textContent));
  assert.strictEqual(places.length, 2);
  for (const place of places) assert.match(place, /Pop outPreview/);
});

test("popping out the runs list leaves a band and gives Fabric's hub the page", { skip }, async () => {
  const p = main.page;
  await p.click(".page.monitor .page-head button:has-text('Pop out')");
  await p.waitForSelector(".monitor-local.popped-out");
  assert.deepStrictEqual(await shellCalls(p, "pop_out_open"), [{ section: "runs", onTop: false }]);
  assert.match(await p.textContent(".monitor-local.popped-out"), /From KratosForge.*In a window of its own.*Show window.*Dock back/);
  assert.strictEqual(await p.$(".monitor-scroll"), null, "the list is only in its window");
  const [local, hub] = await p.$$eval(".page.monitor > .card", (els) => els.map((e) => e.getBoundingClientRect().height));
  assert.ok(hub > local * 5, `Fabric's hub gets the room (${local} vs ${hub})`);
  if (SHOTS) await p.screenshot({ path: path.join(SHOTS, "pop-out-main-runs-out.png") });
  await p.click(".monitor-local.popped-out button:has-text('Dock back')");
  assert.deepStrictEqual(await shellCalls(p, "pop_out_close"), [{ section: "runs" }]);
  // The shell says the window closed; the list comes back.
  await p.evaluate(() => window.__fdEmit("pop-out", { section: "runs", open: false }));
  await p.waitForSelector(".monitor-scroll");
});

test("popping out Fabric's hub hides its view here and gives the runs list the page", { skip }, async () => {
  const p = main.page;
  await p.evaluate(() => { window.__fdShellCalls.length = 0; });
  await p.click(".monitor-fabric .monitor-band button:has-text('Pop out')");
  await p.waitForSelector(".monitor-fabric.popped-out");
  assert.deepStrictEqual(await shellCalls(p, "pop_out_open"), [{ section: "fabric", onTop: false }]);
  assert.ok((await shellCalls(p, "fabric_view_hide")).length > 0, "the main window's Fabric view is hidden");
  const [local, hub] = await p.$$eval(".page.monitor > .card", (els) => els.map((e) => e.getBoundingClientRect().height));
  assert.ok(local > hub * 5, `the runs list gets the room (${local} vs ${hub})`);
  if (SHOTS) await p.screenshot({ path: path.join(SHOTS, "pop-out-main-fabric-out.png") });
  await p.evaluate(() => window.__fdEmit("pop-out", { section: "fabric", open: false }));
  await p.waitForSelector(".monitor-fabric .fabric-slot");
});

test("a Fabric link from a pop-out window opens in the main window's Fabric view", { skip }, async () => {
  await main.page.evaluate((url) => window.__fdEmit("pop-out-open-in-fabric", url), FABRIC_RUN);
  await main.page.waitForFunction(() => state.view === "fabric" && fabricPage.page.includes("synapsenotebooks"));
});

test("the runs window shows only the list, starts nothing and never moves the main window's page", { skip }, async () => {
  const p = runs.page;
  await p.waitForSelector(".monitor-scroll");
  assert.deepStrictEqual(await shellCalls(p, "check_components"), [], "no component check or update");
  assert.strictEqual(await p.$(".monitor-fabric"), null);
  assert.strictEqual(await p.isVisible("#rail"), false);
  assert.strictEqual(await p.isVisible(".search-wrap"), false);
  assert.strictEqual(await p.$(".page.monitor button:has-text('Pop out')"), null);
  assert.ok(await p.isVisible(".pop-out-controls"));
  assert.strictEqual(await p.evaluate(() => localStorage.getItem(NAV_KEY)), null, "the main window reopens where it was");
  const rpcs = runs.calls.map((c) => c.method);
  for (const m of ["sync_pc_schedules", "meta_project"]) assert.ok(!rpcs.includes(m), `no ${m}`);
  if (SHOTS) await p.screenshot({ path: path.join(SHOTS, "pop-out-runs-window.png") });
});

test("the runs window refreshes in place, keeping your place in the list", { skip }, async () => {
  const p = runs.page;
  await p.$eval(".monitor-scroll", (el) => { el.scrollTop = 200; });
  const before = runs.calls.filter((c) => c.method === "history").length;
  await p.evaluate(() => popOutRefresh());
  assert.strictEqual(await p.$(".page.monitor .loading"), null, "the list stays while the new one is read");
  await p.waitForFunction(() => document.querySelector(".monitor-scroll")?.scrollTop === 200);
  assert.ok(runs.calls.filter((c) => c.method === "history").length > before, "it read the runs again");
});

test("a Fabric run's Details goes to the main window; Keep on top and Dock back act on this window", { skip }, async () => {
  const p = runs.page;
  await p.click("tr.click:has-text('Sales load')");
  assert.deepStrictEqual(await shellCalls(p, "pop_out_open_in_fabric"), [{ page: FABRIC_RUN }]);
  assert.strictEqual(await p.evaluate(() => state.view), "monitor");
  await p.check(".pop-out-controls input[type=checkbox]");
  assert.deepStrictEqual(await shellCalls(p, "pop_out_on_top"), [{ on: true }]);
  assert.strictEqual(await p.evaluate(() => localStorage.getItem("fd.popout.runs.top")), "1");
  await p.click(".pop-out-controls button:has-text('Dock back')");
  assert.deepStrictEqual(await shellCalls(p, "pop_out_close"), [{ section: "runs" }]);
});

test("the Fabric window docks its own Fabric view and has the hub's buttons", { skip }, async () => {
  const p = fabric.page;
  await p.waitForSelector(".monitor-fabric .fabric-slot");
  await p.waitForFunction(() => window.__fdShellCalls.some((c) => c.cmd === "fabric_view_show"));
  const shows = await shellCalls(p, "fabric_view_show");
  assert.strictEqual(shows[0].page, "monitor");
  assert.ok(shows[0].width > 900 && shows[0].height > 400, "the view fills the window");
  assert.strictEqual(await p.$(".monitor-local"), null);
  const band = await p.textContent(".monitor-band");
  for (const b of ["Back", "Home", "Reload", "Open in browser"]) assert.ok(band.includes(b), b);
  assert.ok(!band.includes("Hide") && !band.includes("Pop out"));
  assert.strictEqual(await p.isVisible(".nav-buttons"), false);
  if (SHOTS) await p.screenshot({ path: path.join(SHOTS, "pop-out-fabric-window.png") });
});

test("no page errors", { skip }, () => {
  for (const a of [main, runs, fabric]) assert.deepStrictEqual(a.errors, []);
});
