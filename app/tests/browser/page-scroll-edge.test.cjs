// Pages with a width cap (Home, Settings, the narrow pages) scroll in the full-width page, so the
// scroll bar sits on the window's right edge in a wide window while the content keeps its width.
const { test, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
after(async () => { await app?.close(); });

const notebooks = Array.from({ length: 40 }, (_, i) => ({ name: `Notebook ${i}`, modified: 1760000000 + i }));

// The page's right edge, its parent's right edge, and the widest child of the page.
const measure = (p, sel) => p.$eval(sel, (el) => ({
  right: el.getBoundingClientRect().right,
  parentRight: el.parentElement.getBoundingClientRect().right,
  widest: Math.max(...[...el.children].map((c) => c.getBoundingClientRect().width)),
}));

test("in a wide window Home and Settings scroll at the window's right edge, content still capped", { skip }, async () => {
  app = await openApp({ viewport: { width: 2200, height: 900 }, mocks: { list_notebooks: () => notebooks, readiness_check: () => [] } });
  const p = app.page;
  await p.waitForFunction(() => state.view === "home" && homeCache.done, null, { timeout: 20000 });
  const home = await measure(p, ".page.home");
  assert.ok(Math.abs(home.right - home.parentRight) < 1, `Home fills its area (${home.right} vs ${home.parentRight})`);
  assert.ok(home.widest <= 1136.5, `Home content keeps its width (${home.widest}px)`);
  await p.evaluate(() => go("settings"));
  await p.waitForSelector(".page.settings");
  const settings = await measure(p, ".page.settings");
  assert.ok(Math.abs(settings.right - settings.parentRight) < 1, `Settings fills its area (${settings.right} vs ${settings.parentRight})`);
  assert.ok(settings.widest <= 796.5, `Settings content keeps its width (${settings.widest}px)`);
  assert.deepStrictEqual(app.errors, []);
});
