// #192: coming back to Home doesn't read everything again (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const homeLoaded = () => page().waitForFunction(() => state.view === "home" && homeCache.done, null, { timeout: 20000 });

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { list_notebooks: () => [{ name: "Orders", modified: 1760000000 }], readiness_check: () => [] } });
  await page().evaluate(() => go("home"));
  await homeLoaded();
});
after(async () => { if (app) await app.close(); });

test("going back to Home shows it as it was, with no engine calls", { skip }, async () => {
  const before = app.calls.length;
  await page().evaluate(() => go("create"));
  await page().evaluate(() => go("home"));
  assert.strictEqual(app.calls.length, before, app.calls.slice(before).map((c) => c.method).join(", "));
  assert.ok(await page().$("text=Orders"));
  assert.match(await page().textContent(".home-head"), /Updated just now/);
});

test("Refresh reads everything again", { skip }, async () => {
  const before = app.calls.length;
  await page().click('.home-head button:has-text("Refresh")');
  await homeLoaded();
  assert.ok(app.calls.slice(before).some((c) => c.method === "list_notebooks"));
});

test("a run starting draws Home again", { skip }, async () => {
  await page().evaluate(() => logActivity({ id: "r1", title: "Run all", status: "running" }));
  const before = app.calls.length;
  await page().evaluate(() => { go("create"); go("home"); });
  await homeLoaded();
  assert.ok(app.calls.slice(before).some((c) => c.method === "list_notebooks"));
  assert.ok(await page().$("text=Run all"));
});

test("leaving Home before it finished loading reads it again next time", { skip }, async () => {
  await page().evaluate(() => { homeCache.done = false; go("home"); go("create"); });
  const before = app.calls.length;
  await page().evaluate(() => go("home"));
  await homeLoaded();
  assert.ok(app.calls.slice(before).some((c) => c.method === "list_notebooks"));
});
