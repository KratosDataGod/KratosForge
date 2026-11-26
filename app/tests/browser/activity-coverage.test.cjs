// The Activity tray (activity.js), the paths forge-activity and incident-bell leave out: the same
// problem logged again counting up with where else it happened, a run the app was closed during, a
// running bar with its progress, an entry's details and its See results, Open in Monitor, Open in
// Fabric, Check for updates and Copy details links, and Clear finished keeping what still runs.
// Entries are logged the way the pages log them; what the person sees, and where each link goes, is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const RESULTS = [{ name: "daily_load", status: "failed", error: "Table not found" }];

let app;
const page = () => app.page;
const tray = () => page().locator("#tray-root .tray");
const item = (title) => tray().locator(".tray-item", { has: page().locator("b", { hasText: title }) });
const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test(document.getElementById("toast")?.textContent || ""), re.source);

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {} });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  await page().evaluate((results) => {
    toggleTray(false);
    activity.items = [];
    activity.filter = "all";
    window.__copied = null;
    Object.defineProperty(navigator, "clipboard", { value: { writeText: async (t) => { window.__copied = t; } }, configurable: true });
    logActivity({ id: "old", status: "running", title: "Publish to Test" });
    activity.items[0].stale = true;
    logActivity({ id: "run1", status: "running", title: "Copy the sales tables", progress: 0.4 });
    logActivity({ id: "done1", status: "failed", title: "Run all", text: "1 notebook failed", results, monitor: true, url: "https://app.fabric.microsoft.com/groups/w1" });
    logActivity({ id: "upd", status: "update", title: "KratosForge 2.0 is out", update: true });
    logActivity({ status: "error", title: "Home", text: "The engine didn't start.", details: "Traceback: boom" });
    logActivity({ status: "error", title: "Settings", text: "The engine didn't start.", details: "Traceback: boom" });
    logActivity({ status: "error", title: "Monitor", text: "The engine didn't start.", details: "Traceback: boom" });
    toggleTray(true);
  }, RESULTS);
  await tray().waitFor();
  app.errors.length = 0;
});

// Replaces a page function for the test, recording its calls.
const spy = (name) => page().evaluate((n) => { window.__spied = []; window[`__${n}`] = window[n]; window[n] = (...a) => { window.__spied.push(a); }; }, name);
const unspy = (name) => page().evaluate((n) => { window[n] = window[`__${n}`]; const s = window.__spied; window.__spied = []; return s; }, name);

test("the same problem again counts up and names where else it happened; its details copy", { skip }, async () => {
  assert.strictEqual(await tray().locator(".tray-item", { hasText: "The engine didn't start." }).count(), 1);
  const it = item("Monitor");
  assert.match(await it.innerText(), /Happened 3 times, also in: Settings, Home/);
  await it.locator("summary", { hasText: "Details" }).click();
  assert.strictEqual(await it.locator("pre").innerText(), "Traceback: boom");
  await it.getByRole("button", { name: "Copy details" }).click();
  await toastSays(/^Details copied$/);
  assert.strictEqual(await page().evaluate(() => window.__copied), "Traceback: boom");
  assert.deepStrictEqual(app.errors, []);
});

test("a run the app was closed during says Monitor has it; a running one shows how far it is", { skip }, async () => {
  const stale = item("Publish to Test");
  assert.match(await stale.innerText(), /^Unknown/);
  assert.match(await stale.innerText(), /The app was closed while this ran\. Monitor has its final state\./);
  assert.strictEqual(await item("Copy the sales tables").locator(".progress .bar").evaluate((b) => b.style.width), "40%");
  assert.deepStrictEqual(app.errors, []);
});

test("See results, Open in Monitor, Open in Fabric and Check for updates close the tray and go there", { skip }, async () => {
  const run = item("Run all");
  await spy("showRunResults");
  await run.getByRole("button", { name: "See results" }).click();
  assert.deepStrictEqual(await unspy("showRunResults"), [[RESULTS, undefined]]);
  await tray().waitFor({ state: "detached" });
  for (const [button, fn, args] of [["Open in Monitor", "go", [["monitor"]]],
    ["Open in Fabric", "openInFabric", [["https://app.fabric.microsoft.com/groups/w1"]]],
    ["Check for updates", "checkForUpdates", [[]]]]) {
    await page().evaluate(() => toggleTray(true));
    await spy(fn);
    await tray().getByRole("button", { name: button }).click();
    assert.deepStrictEqual(await unspy(fn), args, button);
    await tray().waitFor({ state: "detached" });
  }
  assert.deepStrictEqual(app.errors, []);
});

test("Clear finished keeps only what is still running", { skip }, async () => {
  await tray().getByRole("button", { name: "Clear finished" }).click();
  assert.deepStrictEqual(await tray().locator(".tray-item b").allInnerTexts(), ["Copy the sales tables"]);
  assert.deepStrictEqual(await page().evaluate(() => JSON.parse(localStorage.getItem("fd.activity")).items.map((i) => i.id)), ["run1"]);
  assert.deepStrictEqual(app.errors, []);
});
