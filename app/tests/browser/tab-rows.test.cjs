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
  app = await openApp({ engine: ["app_settings"], viewport: { width: 1440, height: 900 }, mocks: { readiness_check: () => [] } });
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

// Settings' row (General, Integrations) is drawn only while Preview features show (#1061).
test("Settings' tab row moves with Right, Home and End", { skip }, async () => {
  await page().evaluate(() => { state.settingsTab = "general"; go("settings"); });
  const list = "#view .subtabs";
  await page().locator(`${list} [role=tab]`).first().waitFor();
  assert.strictEqual(await page().locator(list).getAttribute("role"), "tablist", "Settings' row is a tablist");
  const on = (name) => page().waitForFunction((n) => {
    const t = document.querySelector('#view .subtabs [role=tab][aria-selected="true"]');
    return t && t.innerText.startsWith(n) && document.activeElement === t;
  }, name);
  await page().locator(`${list} [role=tab][aria-selected="true"]`).focus();
  await page().keyboard.press("ArrowRight");
  await on("Integrations");
  await page().keyboard.press("Home");
  await on("General");
  await page().keyboard.press("End");
  await on("Integrations");
  assert.deepStrictEqual(app.errors, []);
});

test("focus follows a tab row that redraws a moment after the click", { skip }, async () => {
  await page().evaluate(() => {
    const host = document.createElement("div");
    host.id = "late-tabs";
    let on = 0;
    const draw = () => host.replaceChildren(h("div", { role: "tablist", "aria-label": "Late" }, ["One", "Two", "Three"].map((t, i) =>
      h("button", { role: "tab", "aria-selected": String(i === on), onclick: () => setTimeout(() => { on = i; draw(); }, 300) }, t))));
    draw();
    document.body.append(host);
  });
  await page().locator('#late-tabs [aria-selected="true"]').focus();
  await page().keyboard.press("ArrowRight");
  await page().waitForFunction(() => document.activeElement?.textContent === "Two" && document.activeElement.getAttribute("aria-selected") === "true");
  await page().evaluate(() => document.getElementById("late-tabs").remove());
});
