// A page you leave is let go: nothing global (the item preview's panel, the element focused last for
// dialogs) keeps its elements alive. A 2,000-item workspace left for Home must leave no table rows
// behind after a garbage collection; on main before this fix all of them stayed.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const WS = "ws-big";
const mocks = {
  readiness_check: () => [],
  list_workspaces: () => [{ id: WS, displayName: "Large workspace" }],
  workspace_marks: () => ({ favorites: [], pins: [] }),
  list_items: () => Array.from({ length: 2000 }, (_, i) => ({ id: `item-${i}`, displayName: `item_${i}`, type: "Notebook", workspaceId: WS })),
};

let app;
before(async () => { if (!skip) app = await openApp({ mocks }); });
after(async () => { await app?.close(); });

async function liveRows(page) {
  const cdp = await page.context().newCDPSession(page);
  for (let i = 0; i < 3; i++) await cdp.send("HeapProfiler.collectGarbage");
  const proto = await cdp.send("Runtime.evaluate", { expression: "HTMLTableRowElement.prototype" });
  const found = await cdp.send("Runtime.queryObjects", { prototypeObjectId: proto.result.objectId });
  const n = await cdp.send("Runtime.callFunctionOn", { objectId: found.objects.objectId,
    functionDeclaration: "function () { return this.length; }", returnByValue: true });
  await cdp.detach();
  return n.result.value;
}

test("a workspace page left for Home keeps none of its rows in memory", { skip }, async () => {
  const page = app.page;
  await page.waitForSelector(".rail-item");
  await page.evaluate(() => { state.user = "chris@contoso.com"; state.workspaces = null; go("workspaces"); });
  // Focused, as a click focuses it: the element focused last is what dialogs give focus back to.
  await page.evaluate(() => {
    const show = [...document.querySelectorAll("button")].find((b) => b.getAttribute("aria-label") === "Show items");
    show.focus();
    show.click();
  });
  // At least the first screen: a big list draws more rows as you scroll.
  await page.waitForFunction(() => document.querySelectorAll(".ws-items tr").length >= 200);
  await page.evaluate(() => go("home"));
  await page.waitForFunction(() => !document.querySelector(".ws-items"));
  const inPage = await page.evaluate(() => document.querySelectorAll("tr").length);
  assert.strictEqual(await liveRows(page), inPage);
});
