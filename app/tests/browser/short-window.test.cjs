// A short window (under 640 px tall, such as 1280 x 940 at 200% zoom) and a narrow one (under 900 px wide), with
// the mock engine (see harness.cjs for how to run it):
// - The left rail shows icons only and every item fits without scrolling. Each keeps its name as its accessible
//   name and its tooltip, and the current page keeps its stripe.
// - Home's hero is one strip: the welcome and its actions, without the kicker, the title or the art.
// - The SQL page's Explorer collapses to a strip with an expand button. The choice is remembered; with none
//   made, a window under 900 px wide starts with it collapsed.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const LH = { workspace_id: "w1", lakehouse_id: "l1", name: "LH_Bronze", workspace_name: "Sales Dev" };
const TALL = { width: 1440, height: 940 };
const SHORT = { width: 720, height: 470 };

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["app_settings"],
    mocks: {
      environment: () => ({ python: "Python 3 (test)", user: "ana@contoso.com", tenant: "t1" }),
      list_workspaces: () => [{ id: "w1", displayName: "Sales Dev", capacityId: "c1" }],
      list_lakehouses: () => [{ id: "l1", displayName: "LH_Bronze" }],
      list_notebooks: () => [],
      list_tables: () => ({ dbo: ["sales"] }),
      table_columns: () => [{ name: "region", type: "string" }],
      cached_schemas: () => ({ tables: {}, columns: {} }),
      profile_get: () => null,
    },
  });
  await page().waitForSelector("#rail-main .rail-item");
  await page().waitForFunction(() => state.settings && "show_preview" in state.settings && !state.starting);
});

after(async () => { await app?.close(); });

const rail = () => page().evaluate(() => {
  const nav = document.getElementById("rail");
  const items = [...nav.querySelectorAll(".rail-item")];
  const box = nav.getBoundingClientRect();
  return {
    scrolls: nav.scrollHeight > nav.clientHeight + 1,
    width: Math.round(box.width),
    count: items.length,
    offscreen: items.filter((b) => { const r = b.getBoundingClientRect(); return r.top < box.top || r.bottom > box.bottom + 0.5; })
      .map((b) => b.textContent.trim()),
    unnamed: items.filter((b) => !b.textContent.trim() || b.title !== b.textContent.trim()).length,
    labelShown: items.some((b) => b.querySelector(".rail-label").getBoundingClientRect().width > 1),
    activeStripe: getComputedStyle(nav.querySelector(".rail-item.active")).boxShadow,
  };
});

test("in a short window the rail shows icons only and every item fits", { skip }, async () => {
  await page().setViewportSize(SHORT);
  await page().evaluate(() => go("home"));
  const r = await rail();
  assert.ok(r.count >= 11, `the rail had ${r.count} items`);
  assert.deepStrictEqual(r.offscreen, [], "rail items below the window");
  assert.strictEqual(r.scrolls, false, "the rail scrolls in a short window");
  assert.strictEqual(r.unnamed, 0, "a rail item lost its name or its tooltip");
  assert.strictEqual(r.labelShown, false, "the names still show under the icons");
  assert.ok(r.width <= 56, `the rail is ${r.width} px wide`);
  assert.match(r.activeStripe, /inset/, "the current page lost its stripe");
  // Names are still what a screen reader and a Playwright role query find.
  await page().getByRole("button", { name: "Settings", exact: true }).first().waitFor();

  await page().setViewportSize(TALL);
  const tall = await rail();
  assert.strictEqual(tall.labelShown, true, "the names are hidden in a tall window");
});

test("in a short window Home's hero is one strip with the welcome and its actions", { skip }, async () => {
  await page().evaluate(() => { state.settings.show_preview.value = true; state.settings.motion_level.value = 2; });
  await page().setViewportSize(SHORT);
  await page().evaluate(() => { homeCache.done = false; go("home"); });
  await page().locator(".home-hero").waitFor();
  const hero = await page().evaluate(() => {
    const shown = (sel) => { const el = document.querySelector(sel); return !!el && el.getBoundingClientRect().height > 0; };
    return { height: document.querySelector(".home-hero").getBoundingClientRect().height, kicker: shown(".home-hero-kicker"),
      title: shown(".home-hero-title"), art: shown(".home-hero-img"), welcome: shown(".home-hero h1") };
  });
  assert.deepStrictEqual({ kicker: hero.kicker, title: hero.title, art: hero.art, welcome: hero.welcome },
    { kicker: false, title: false, art: false, welcome: true });
  assert.ok(hero.height <= 110, `the hero is ${hero.height} px tall`);
  assert.ok(await page().locator(".home-hero-actions").getByRole("button", { name: "New notebook" }).isVisible());

  await page().setViewportSize(TALL);
  assert.ok(await page().locator(".home-hero-title").isVisible(), "the title is gone in a tall window");
});

test("the SQL page's Explorer collapses, expands and remembers the choice", { skip }, async () => {
  await page().setViewportSize(TALL);
  await page().evaluate((lh) => { localStorage.removeItem("fd.sql.explorer"); sqlPage.ref = null; openSqlQuery(lh); }, LH);
  await page().locator(".sql-edit .cm-editor").waitFor();
  const hide = page().getByRole("button", { name: "Collapse the Explorer" });
  assert.strictEqual(await hide.getAttribute("aria-expanded"), "true");
  await hide.click();
  const show = page().getByRole("button", { name: "Expand the Explorer" });
  await show.waitFor();
  assert.strictEqual(await show.getAttribute("aria-expanded"), "false");
  assert.ok(await show.evaluate((b) => document.activeElement === b), "focus was lost when the Explorer collapsed");
  assert.strictEqual(await page().locator("aside.explorer .explorer-content").count(), 0, "the tables still show");
  const strip = await page().locator("aside.sql-explorer-strip").boundingBox();
  assert.ok(strip.width <= 40, `the strip is ${strip.width} px wide`);
  assert.strictEqual(await page().evaluate(() => localStorage.getItem("fd.sql.explorer")), "closed");

  // Drawn again from scratch, as on the next visit: still collapsed.
  await page().evaluate(() => { sqlPage.explorerOpen = undefined; renderQuery(); });
  await show.waitFor();
  await show.click();
  await page().getByRole("button", { name: "Collapse the Explorer" }).waitFor();
  assert.strictEqual(await page().evaluate(() => localStorage.getItem("fd.sql.explorer")), "open");
  await page().locator(".explorer-content").getByText("sales").first().waitFor();
});

test("with no choice kept, a window under 900 px wide starts with the Explorer collapsed", { skip }, async () => {
  await page().setViewportSize(SHORT);
  await page().evaluate(() => { localStorage.removeItem("fd.sql.explorer"); sqlPage.explorerOpen = undefined; renderQuery(); });
  await page().getByRole("button", { name: "Expand the Explorer" }).waitFor();
  // The opened SQL page adds its own item to the rail; that still fits.
  const r = await rail();
  assert.ok(r.count >= 12, `the rail had ${r.count} items`);
  assert.deepStrictEqual(r.offscreen, [], "rail items below the window with a page open");
  await page().setViewportSize(TALL);
  await page().evaluate(() => renderQuery());
  await page().getByRole("button", { name: "Collapse the Explorer" }).waitFor();
});
