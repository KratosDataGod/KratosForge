// Monitor > Runs, Forge Activity (forge-activity.js): the "N more running" link under the lanes. It
// scrolls to the runs table, where every run is listed, and doesn't follow its "#" link.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const ago = (min) => new Date(Date.now() - min * 60000).toISOString();
const running = (n) => Array.from({ length: n }, (_, i) => (
  { run_id: `r${i}`, env: "dev", entity: `bronze.t${i}`, status: "running", started_at: ago(i + 1), seconds: null, rows_written: null }));
let meta = [];

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1440, height: 900 },
    mocks: {
      readiness_check: () => [],
      app_settings: () => ({ motion_level: { value: 2 }, show_preview: { value: true } }),
      history: () => [],
      meta_runs_all: () => ({ runs: meta, unavailable: {} }),
      fabric_runs: () => [],
      pc_schedule_runs: () => [],
      pipeline_runs: () => ({ runs: [] }),
      fabric_pipeline_runs: () => ({ runs: [], pending: false }),
    },
  });
  await page().waitForFunction(() => typeof go === "function" && typeof forgeActivityFill === "function" && !!document.querySelector(".rail-item"));
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  meta = [];
  app.errors.length = 0;
  // Records which element was scrolled to, instead of moving the page.
  await page().evaluate(() => {
    window.__scrolled = [];
    if (!window.__realScroll) window.__realScroll = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (opts) { window.__scrolled.push({ cls: this.className, opts }); };
  });
});

async function open() {
  await page().evaluate(() => { monitorPage.panel = "runs"; go("home"); go("monitor"); });
  await page().locator(".forge-strip").waitFor({ timeout: 10000 });
}

test("the more-running link scrolls to the runs table and stays on the page", { skip }, async () => {
  meta = running(5);
  await open();
  const more = page().locator(".forge-band .forge-more");
  assert.match(await more.innerText(), /^2 more running, see the table below$/);
  const hash = await page().evaluate(() => location.hash);
  await more.click();
  const scrolled = await page().evaluate(() => window.__scrolled);
  assert.deepStrictEqual(scrolled, [{ cls: "monitor-scroll", opts: { block: "start" } }]);
  assert.strictEqual(await page().evaluate(() => location.hash), hash, "the # link was followed");
  assert.strictEqual(await page().locator(".monitor-scroll tbody tr").count(), 5);
  assert.deepStrictEqual(app.errors, []);
});

test("with three or fewer running there's no more-running link", { skip }, async () => {
  meta = running(3);
  await open();
  assert.strictEqual(await page().locator(".forge-band .forge-lane").count(), 3);
  assert.strictEqual(await page().locator(".forge-band .forge-more").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});
