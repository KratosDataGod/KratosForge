// Every row of tabs works with the keyboard (UX-012, #961): Right selects and focuses the next tab on
// rows that were plain buttons before (Monitor, Platform, Data mesh, OneLake).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ viewport: { width: 1440, height: 900 }, mocks: { readiness_check: () => [] } });
});
after(async () => { if (app) await app.close(); });

async function arrowRight(open, list, what) {
  await page().evaluate(open);
  const tabs = page().locator(list).getByRole("tab");
  await tabs.first().waitFor();
  const names = await tabs.allInnerTexts();
  assert.ok(names.length > 1, `${what} has more than one tab`);
  const first = await page().locator(list).locator('[role=tab][aria-selected="true"]').innerText();
  await page().locator(list).locator('[role=tab][aria-selected="true"]').focus();
  await page().keyboard.press("ArrowRight");
  await page().waitForFunction(([l, f]) => {
    const on = document.querySelector(`${l} [role=tab][aria-selected="true"]`);
    return on && on.innerText !== f && document.activeElement === on;
  }, [list, first]);
  assert.strictEqual(await page().locator(list).getAttribute("role"), "tablist", `${what} is a tablist`);
}

test("Right moves to the next tab on rows that weren't tablists before", { skip }, async () => {
  await arrowRight(() => go("monitor"), ".monitor-tabs", "Monitor");
  await arrowRight(() => go("platform"), "#view .subtabs", "Platform");
  await arrowRight(() => go("metadata"), ".meta-groups", "Data mesh");
  await arrowRight(() => go("onelake"), ".onelake-tabs .tab-group", "OneLake");
  assert.deepStrictEqual(app.errors, []);
});
