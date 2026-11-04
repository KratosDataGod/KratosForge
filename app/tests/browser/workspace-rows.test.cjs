// A workspace with thousands of items draws its first rows at once and the rest as you scroll near the
// end (app.js itemRows), so opening it doesn't build every row up front. Scrolling down reaches the last
// item, and a redraw of the page keeps the rows already drawn.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const WS = "ws-big";
const ITEMS = 5000;
const mocks = {
  readiness_check: () => [],
  list_workspaces: () => [{ id: WS, displayName: "Large workspace" }],
  workspace_marks: () => ({ favorites: [], pins: [] }),
  list_items: () => Array.from({ length: ITEMS }, (_, i) => ({
    id: `item-${String(i).padStart(5, "0")}`, displayName: `item_${String(i).padStart(5, "0")}`, type: "Notebook", workspaceId: WS })),
};

let app;
const page = () => app.page;
const drawn = () => page().locator(".ws-items tr:not(.ws-more)").count();

before(async () => {
  if (skip) return;
  app = await openApp({ mocks, viewport: { width: 1280, height: 800 } });
  await page().waitForSelector(".rail-item");
  await page().evaluate(() => { state.user = "chris@contoso.com"; state.workspaces = null; go("workspaces"); });
  await page().getByRole("button", { name: "Show items" }).first().click();
  await page().locator(".ws-items tr").first().waitFor();
});
after(async () => { await app?.close(); });

test("a 5,000-item workspace draws its first rows, not all of them", { skip }, async () => {
  const n = await drawn();
  assert.ok(n >= 200 && n < ITEMS, `drew ${n} rows`);
  assert.strictEqual(await page().locator(".ws-items tr").first().innerText().then((t) => t.includes("item_00000")), true);
});

test("scrolling to the end draws the rest, in order, up to the last item", { skip }, async () => {
  for (let i = 0; i < 60 && (await drawn()) < ITEMS; i++) {
    await page().locator(".ws-items tr").last().scrollIntoViewIfNeeded();
    await page().waitForTimeout(50);
  }
  assert.strictEqual(await drawn(), ITEMS);
  assert.strictEqual(await page().locator(".ws-more").count(), 0);
  const last = await page().locator(".ws-items tr").last().innerText();
  assert.ok(last.includes("item_04999"), last);
});

test("a redraw of the page keeps the rows already drawn", { skip }, async () => {
  await page().getByRole("button", { name: "Hide items" }).first().click();
  await page().getByRole("button", { name: "Show items" }).first().click();
  await page().locator(".ws-items tr").first().waitFor();
  assert.strictEqual(await drawn(), ITEMS);
});
