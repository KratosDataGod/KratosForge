// App-wide helpers in features.js: the guard paths other tests don't reach. A "Learn more" link and the
// shortcuts list's notebook-guide link open the user guide, and say so as an error when the guide can't
// open. The command bar finds workspaces and entities by name and opens the one picked. Mocks only.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { readiness_check: () => [] } });
  await page().waitForFunction(() => typeof learnMore === "function" && typeof openPalette === "function");
  await page().evaluate(() => {
    // open_doc fails while window.__docFails is set; every other shell call goes on as before.
    const invoke = window.__TAURI__.core.invoke;
    window.__TAURI__.core.invoke = async (cmd, args) => {
      if (cmd === "open_doc" && window.__docFails) { window.__fdShellCalls.push({ cmd, args }); throw new Error("No program is set to open .md files"); }
      return invoke(cmd, args);
    };
  });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

const docCalls = () => page().evaluate(() => window.__fdShellCalls.filter((c) => c.cmd === "open_doc").map((c) => c.args.page));
const clearCalls = () => page().evaluate(() => { window.__fdShellCalls.length = 0; });

test("a Learn more link opens that section of the user guide", { skip }, async () => {
  await clearCalls();
  await page().evaluate(() => { window.__docFails = false; document.getElementById("view").append(learnMore("data-health", "About data health")); });
  await page().getByRole("link", { name: "About data health" }).click();
  await page().waitForFunction(() => window.__fdShellCalls.some((c) => c.cmd === "open_doc"));
  assert.deepStrictEqual(await docCalls(), ["user-guide.md#data-health"]);
});

test("a Learn more link whose guide can't open says so as an error", { skip }, async () => {
  await page().evaluate(() => { window.__docFails = true; document.getElementById("view").append(learnMore("", "Whole guide")); });
  await page().getByRole("link", { name: "Whole guide" }).click();
  await page().waitForFunction(() => document.getElementById("toast")?.textContent.includes("Could not open the guide"));
  assert.ok((await page().textContent("#toast")).includes("No program is set to open .md files"));
  assert.ok((await docCalls()).includes("user-guide.md"));
  await page().evaluate(() => { window.__docFails = false; });
});

test("the shortcuts list links to the notebook guide's editing keys", { skip }, async () => {
  await clearCalls();
  await page().evaluate(() => shortcutsDialog());
  await page().locator(".dialog").getByRole("link", { name: "see the notebook guide" }).click();
  await page().waitForFunction(() => window.__fdShellCalls.some((c) => c.cmd === "open_doc"));
  assert.deepStrictEqual(await docCalls(), ["notebooks.md#editing"]);
  await page().keyboard.press("Escape");
  await page().waitForFunction(() => !document.querySelector(".dialog"));
});

test("the command bar finds a workspace by name and opens it", { skip }, async () => {
  await page().evaluate(() => {
    state.workspaces = [{ id: "w1", displayName: "Sales Dev" }, { id: "w2", displayName: "Finance Prod" }];
    window.__opened = [];
    window.openWorkspace = (ws) => window.__opened.push(["workspace", ws.id]);
    openPalette("sales dev");
  });
  const rows = await page().locator("#palette .palette-item").allTextContents();
  assert.ok(rows.some((r) => r.includes("Sales Dev")) && !rows.some((r) => r.includes("Finance Prod")), rows.join("|"));
  assert.ok((await page().textContent("#palette")).includes("Workspaces"));
  await page().getByRole("option", { name: "Sales Dev", exact: true }).click();
  assert.deepStrictEqual(await page().evaluate(() => window.__opened), [["workspace", "w1"]]);
  assert.strictEqual(await page().locator("#palette").count(), 0, "picking a result closes the bar");
});

test("the command bar finds a Data mesh entity by name and opens it", { skip }, async () => {
  await page().evaluate(() => {
    state.meta = { ...(state.meta || {}), project: { ...(state.meta?.project || {}), entities: [{ name: "gold.fact_sales" }, { name: "silver.orders" }] } };
    window.__opened = [];
    window.openEntity = (name) => window.__opened.push(["entity", name]);
    openPalette("fact_sales");
  });
  assert.ok((await page().textContent("#palette")).includes("Entities"));
  await page().getByRole("option", { name: "gold.fact_sales", exact: true }).click();
  assert.deepStrictEqual(await page().evaluate(() => window.__opened), [["entity", "gold.fact_sales"]]);
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
