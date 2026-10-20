// UX-020: Home draws at once with placeholders while the component and update check runs, calls
// nothing until it returns, then fills in (see harness.cjs for how to run it).
const { test, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
after(async () => { await app?.close(); });

test("Home draws before a slow release check returns, then fills in", { skip }, async () => {
  app = await openApp({ holdCheck: true,
    mocks: { list_notebooks: () => [{ name: "Orders", modified: 1760000000 }], readiness_check: () => [] } });
  const page = app.page;
  await page.waitForSelector(".page.home[data-starting]");
  assert.match(await page.textContent(".home-head"), /Welcome to KratosForge/);
  assert.match(await page.textContent(".page.home"), /Starting the local engine\.\.\./);
  // One live region speaks for the page, so a screen reader says it once; every placeholder spins.
  assert.strictEqual(await page.locator('.page.home [role="status"]').count(), 1);
  assert.match(await page.textContent('.page.home [role="status"]'), /Starting the local engine/);
  assert.ok(await page.locator('.page.home .loading .spinner[aria-hidden="true"]').count() >= 4);
  assert.ok(await page.locator('.home-start button:has-text("New notebook")').isDisabled());
  // Nothing may start the sidecar while the check runs.
  assert.deepStrictEqual(app.calls.map((c) => c.method), []);
  assert.strictEqual(await page.evaluate(() => homeCache.page), null);

  await page.evaluate(() => __fdReleaseCheck());
  await page.waitForFunction(() => state.view === "home" && homeCache.done, null, { timeout: 20000 });
  assert.strictEqual(await page.locator(".page.home[data-starting]").count(), 0);
  assert.ok(await page.$("text=Orders"));
  assert.ok(app.calls.some((c) => c.method === "list_notebooks"));
  assert.deepStrictEqual(app.errors, []);
});
