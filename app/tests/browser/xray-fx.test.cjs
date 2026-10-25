// Estate X-ray scan progress (docs/forge-animations.md): before the scan can count, the navy wolf
// trots in place; once it counts, the bar shows how many it read; paused, the grey bar stands still.
// A scan the card watched glows once on its numbers when it ends, or shows the ember beside why it
// stopped; a scan that ended before the card was drawn plays nothing. Fabric is mocked and the scan
// moves only when the test says.
const { test, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const RESULT = { scanned: "2026-10-25T10:00:00Z", workspaces: 2, lakehouses: 3, tables: 40, bytes: 1e9, findings: [], skipped: [] };
const running = (progress, paused = false) => ({ running: true, paused, progress, result: null });

const apps = [];
async function open(scan, { level = 2 } = {}) {
  const app = await openApp({ mocks: {
    app_settings: () => ({ motion_level: { value: level } }),
    xray: () => scan.now,
  } });
  apps.push(app);
  await app.page.emulateMedia({ reducedMotion: "no-preference" });
  await app.page.waitForFunction(() => typeof loadXray === "function" && !!document.querySelector(".rail-item"));
  await app.page.evaluate(async () => {
    await loadSettings();
    // Home has drawn its own card signed out; the card under test is the only one that scans.
    await new Promise((r) => setTimeout(r, 300));
    state.user = "chris";
    const box = document.createElement("section");
    box.id = "xray-under-test";
    box.className = "card pad xray";
    document.body.prepend(box);
    window.__box = box;
    motionLoadWatch?.disconnect(); loadTasks = []; dropUntil = 0; applyMotion();
    window.__fx = [];
    const once = new WeakSet();
    const seen = (e) => !once.has(e) && once.add(e) && window.__fx.push(`${e.className} @${e.parentElement?.classList[0] || ""}`);
    new MutationObserver((list) => list.forEach((m) => m.addedNodes.forEach((n) => {
      if (n.classList?.contains("fx") && n.closest("#xray-under-test")) seen(n);
      n.querySelectorAll?.("#xray-under-test .fx, .fx").forEach((e) => e.closest("#xray-under-test") && seen(e));
    }))).observe(document.body, { childList: true, subtree: true });
  });
  return app;
}

const load = (page) => page.evaluate(() => loadXray(window.__box, false));
const wait = (page, fn) => page.waitForFunction(fn, null, { timeout: 15000 });
const card = "#xray-under-test";

after(async () => { for (const app of apps) await app.close(); });

test("Before it can count the scan shows the navy wolf, then the count, and glows once when it ends", { skip }, async () => {
  const scan = { now: running({ phase: "Listing workspaces" }) };
  const { page, errors } = await open(scan);
  await load(page);
  await page.waitForSelector(`${card} .remote-progress .wolf-idle`);
  assert.strictEqual(await page.textContent(`${card} .remote-where`), "Reading Fabric");
  assert.strictEqual(await page.getAttribute(`${card} [role=progressbar]`, "aria-valuenow"), null);
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "xray-wolf.png") });
  scan.now = running({ phase: "Reading table logs", done: 10, total: 40 });
  await wait(page, () => /10 of 40/.test(document.querySelector("#xray-under-test .xray-progress")?.textContent || ""));
  assert.strictEqual(await page.locator(`${card} .wolf-idle`).count(), 0, "a counted scan kept the trotting wolf");
  scan.now = { running: false, result: RESULT };
  await wait(page, () => window.__fx.some((c) => c.includes("fx-arrive")));
  const made = await page.evaluate(() => window.__fx);
  assert.deepStrictEqual(made.map((c) => c.split(" @")[1]), ["xray-stats"], `${made}`);
  assert.deepStrictEqual(errors, []);
});

test("A scan that stops shows the ember; a paused scan stands still; one that ended before plays nothing", { skip }, async () => {
  const scan = { now: running({ phase: "Listing workspaces" }, true) };
  const { page, errors } = await open(scan);
  await load(page);
  await page.waitForSelector(`${card} .bar.paused`);
  assert.strictEqual(await page.locator(`${card} .wolf-idle`).count(), 0, "a paused scan trotted");
  scan.now = { running: false, result: null, error: "Fabric didn't answer" };
  await load(page);
  await wait(page, () => window.__fx.some((c) => c.includes("fx-fail")));
  assert.deepStrictEqual((await page.evaluate(() => window.__fx)).map((c) => c.split(" @")[1]), ["reason"]);
  // A fresh card on a scan that already ended: nothing plays.
  await page.evaluate(() => { window.__fx = []; const b = window.__box.cloneNode(); window.__box.replaceWith(b); window.__box = b; });
  scan.now = { running: false, result: RESULT };
  await load(page);
  await page.waitForSelector(`${card} .xray-stats`);
  await page.waitForTimeout(300);
  assert.deepStrictEqual(await page.evaluate(() => window.__fx), []);
  assert.deepStrictEqual(errors, []);
});

test("With motion Off the scan says it is reading, with the plain bar and no wolf or glow", { skip }, async () => {
  const scan = { now: running({ phase: "Listing workspaces" }) };
  const { page, errors } = await open(scan, { level: 0 });
  await load(page);
  await page.waitForSelector(`${card} .remote-where`);
  assert.strictEqual(await page.locator(`${card} .wolf-idle`).count(), 0);
  assert.ok(await page.locator(`${card} .bar.indeterminate`).isVisible());
  scan.now = { running: false, result: RESULT };
  await load(page);
  await page.waitForSelector(`${card} .xray-stats`);
  await page.waitForTimeout(300);
  assert.deepStrictEqual(await page.evaluate(() => window.__fx), []);
  assert.deepStrictEqual(errors, []);
});
