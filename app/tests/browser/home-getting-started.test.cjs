// Getting started with KratosForge on Home: the main starting points in a card that folds to one
// line and remembers it, ticking what was tried (see harness.cjs for how to run it).
const { test, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
after(async () => { await app?.close(); });

async function home(storage = {}, notebooks = []) {
  await app?.close();
  app = await openApp({ storage, mocks: { list_notebooks: () => notebooks, meta_project: () => ({ path: null }) } });
  await app.page.waitForFunction(() => state.view === "home" && homeCache.done, null, { timeout: 20000 });
  return app.page;
}

const titles = (p) => p.$$eval(".get-started .gs-tile b", (els) => els.map((e) => e.textContent));

test("shows the six starting points, open the first time", { skip }, async () => {
  const p = await home();
  assert.match(await p.textContent(".get-started .fold-toggle"), /Getting started with KratosForge/);
  assert.deepStrictEqual(await titles(p), ["Run a notebook on this PC", "Bring your notebooks", "Explore OneLake",
    "Build medallion tables", "Spend less Fabric capacity", "Set up the platform"]);
  assert.ok(await p.isVisible(".get-started .gs-grid"));
  assert.strictEqual(await p.locator(".get-started .gs-tile.done").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("folding keeps it to its header line, and stays folded on the next visit", { skip }, async () => {
  const p = await home();
  const open = (await p.locator(".get-started").boundingBox()).height;
  await p.click(".get-started .fold-toggle");
  assert.ok(!(await p.isVisible(".get-started .gs-grid")));
  const folded = (await p.locator(".get-started").boundingBox()).height;
  assert.ok(folded < 64 && folded < open / 2, `folded ${folded}px, open ${open}px`);
  assert.strictEqual(await p.evaluate(() => localStorage.getItem("fd.fold.home.getstarted")), "1");
  const again = await home({ "fd.fold.home.getstarted": "1" });
  assert.ok(!(await again.isVisible(".get-started .gs-grid")));
  assert.strictEqual(await again.getAttribute(".get-started .fold-toggle", "aria-expanded"), "false");
});

test("someone who hid the old Get started card sees it folded, not gone", { skip }, async () => {
  const p = await home({ "fd.getstarted.hidden": "1" });
  assert.ok(await p.isVisible(".get-started .fold-toggle"));
  assert.ok(!(await p.isVisible(".get-started .gs-grid")));
});

test("ticks a notebook and an opened lakehouse, and each tile opens its place", { skip }, async () => {
  const p = await home({ "fd.done.lakehouse": "1" }, [{ name: "Orders", modified: 1760000000 }]);
  await p.waitForSelector(".get-started .gs-tile.done >> nth=1");
  const done = await p.$$eval(".get-started .gs-tile.done b", (els) => els.map((e) => e.textContent));
  assert.deepStrictEqual(done, ["Run a notebook on this PC", "Explore OneLake"]);
  await p.click('.get-started .gs-tile:has-text("Explore OneLake") button');
  await p.waitForFunction(() => state.view === "onelake");
});
