// Beside a best-practice finding (learn.js, LX-09, from the approved mockup): Explain and Show example
// from the guide that ships with the app, the Lesson link, and Dismiss with its three scopes, against
// the real engine. Focus shows only Dismiss; a rule a policy names can't be dismissed.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp, sqlRunMocks } = require("./harness.cjs");

let axeSource = null;
try { axeSource = fs.readFileSync(require.resolve("axe-core/axe.min.js"), "utf8"); } catch { /* not installed: the axe test skips */ }
const LH = { workspace_id: "ws1", lakehouse_id: "lh1", name: "Sales" };
const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

let app;
const page = () => app.page;
const chip = () => page().locator("button[data-practices]");
const panel = () => page().locator(".sql-compat");
const sq002 = () => panel().locator("[data-rule=SQ002]");

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["app_settings", "save_app_settings", "sql_format", "sql_analyze", "sql_practices", "governance_practice_settings",
      "learn_catalog", "learn_rule_guides", "learn_dismissals", "learn_dismiss", "learn_undismiss"],
    storage: { "fabric-desktop.sql.lh1": JSON.stringify([{ name: "Weekly revenue", sql: "SELECT 1", dialect: "warehouse" }]) },
    mocks: {
      list_tables: () => ({ dbo: ["sales"] }),
      table_columns: () => [{ name: "order_date", type: "date" }],
      ...sqlRunMocks(() => ({ columns: [{ name: "x", type: "int64" }], rows: [[1]] })),
    },
  });
  await page().waitForFunction(() => state.settings && "learn_mode" in state.settings);
  await page().evaluate((lh) => openSqlQuery(lh), LH);
  await page().waitForSelector(".sql-edit .cm-editor");
});

after(async () => { await app?.close(); });

async function setDoc(text) {
  await page().evaluate((text) => {
    const v = sqlPage.editor.view;
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: text }, selection: { anchor: text.length } });
  }, text);
  await page().waitForFunction((text) => sqlPage.tabs[sqlPage.active].compatText === text, text, { timeout: 10000 });
}

// A new check can close the panel (compatibility decides when it opens by itself), so open it again.
async function openPanel() {
  await page().waitForFunction(() => sqlPage.tabs[sqlPage.active].practices);
  if (await panel().isHidden()) await chip().click();
}

async function setMode(mode) {
  await page().evaluate((mode) => saveSettings({ learn_mode: mode }), mode);
  await page().waitForFunction((mode) => state.settings.learn_mode?.value === mode, mode);
}

const VIEW = "CREATE VIEW dbo.v_orders AS\nSELECT *\nFROM sales\nWHERE YEAR(order_date) = 2026;";

test("Focus, the default, shows Dismiss and nothing else beside a finding", { skip }, async () => {
  await setDoc(VIEW);
  await page().waitForFunction(() => /Best practices · 3/.test(document.querySelector("button[data-practices]")?.textContent));
  await chip().click();
  await sq002().locator(".learn-dismiss button").first().waitFor();
  assert.strictEqual(await sq002().locator(".learn-actions button", { hasText: "Explain" }).count(), 0);
  assert.strictEqual(await sq002().locator(".learn-actions button", { hasText: "Show example" }).count(), 0);
});

test("Guided adds Explain and Show example from the shipped guide, and the Lesson link", { skip }, async () => {
  await setMode("guided");
  await setDoc(`${VIEW}\n`);
  await sq002().locator("button", { hasText: "Explain" }).click();
  await sq002().locator(".learn-detail").waitFor();
  assert.strictEqual(await page().evaluate(() => document.activeElement?.textContent), "Explain", "focus stays on Explain after the panel redraws");
  const explain = await sq002().locator(".learn-detail").innerText();
  assert.match(explain, /Why it matters[\s\S]*The engine skips files and row groups/);
  assert.match(explain, /When it's fine[\s\S]*Small tables/);
  assert.match(explain, /Nothing is sent anywhere/);
  assert.strictEqual(await sq002().locator("button", { hasText: "Explain" }).getAttribute("aria-expanded"), "true");
  await sq002().locator("button", { hasText: "Show example" }).click();
  await sq002().locator("pre.after").waitFor();
  assert.strictEqual(await sq002().locator("pre.before").innerText(), "WHERE YEAR(order_date) = 2026");
  assert.match(await sq002().locator("pre.after").innerText(), /^WHERE order_date >= '2026-01-01'/);
  await sq002().locator(".learn-finding a", { hasText: "Lesson: Your first query on this PC" }).waitFor();
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "finding-actions-example.png") });
});

test("the open finding has no serious axe findings", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  await sq002().getByRole("button", { name: /Dismiss SQ002/ }).click();
  await page().addScriptTag({ content: axeSource });
  const found = await page().evaluate(async () => {
    const r = await window.axe.run(document.querySelector(".sql-compat"), { runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] });
    return r.violations.filter((v) => ["serious", "critical"].includes(v.impact)).map((v) => `${v.id} @ ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
  });
  assert.deepStrictEqual(found, []);
  await page().keyboard.press("Escape");
  assert.strictEqual(await sq002().locator(".learn-dismiss-menu").isHidden(), true);
  await sq002().getByRole("button", { name: /Dismiss SQ002/ }).click();
  await sq002().locator("button", { hasText: "Explain" }).focus();  // focus leaving the menu closes it
  assert.strictEqual(await sq002().locator(".learn-dismiss-menu").isHidden(), true);
});

test("Not on this line hides the finding until the line changes, and Show again brings it back", { skip }, async () => {
  await sq002().getByRole("button", { name: /Dismiss SQ002/ }).click();
  await sq002().locator(".learn-dismiss-item", { hasText: "Not on this line" }).click();
  await sq002().waitFor({ state: "detached" });
  assert.ok(await page().evaluate(() => document.activeElement?.dataset.learnFocus?.endsWith("|dismissed")), "focus moves to the dismissed list");
  assert.match(await chip().innerText(), /Best practices · 2/);
  const kept = await page().evaluate(() => learnDismissed);
  assert.strictEqual(kept.length, 1);
  assert.match(kept[0].line, /^[0-9a-f]{64}$/, "the line is kept as its hash, never its text");
  assert.ok(!JSON.stringify(kept).includes("YEAR"), "no code is kept");
  await setDoc(VIEW.replace("2026", "2025"));  // another line now: it shows again
  await sq002().waitFor();
  await setDoc(VIEW);
  await sq002().waitFor({ state: "detached" });
  await panel().locator(".learn-dismissed button", { hasText: "1 dismissed · Show" }).click();
  assert.match(await panel().locator(".learn-dismissed-list").innerText(), /SQ002 · Function on a column in WHERE[\s\S]*Not on this line · line 4/);
  await panel().locator(".learn-dismissed-list button", { hasText: "Show again" }).click();
  await sq002().waitFor();
  assert.ok(await page().evaluate(() => /\|SQ002\|.*\|dismiss$/.test(document.activeElement?.dataset.learnFocus || "")), "focus goes to the finding that came back");
  assert.match(await chip().innerText(), /Best practices · 3/);
});

test("Never show hides a rule everywhere until Show all again", { skip }, async () => {
  await panel().locator("[data-rule=SQ005]").getByRole("button", { name: /Dismiss SQ005/ }).click();
  await panel().locator("[data-rule=SQ005] .learn-dismiss-item", { hasText: "Never show SQ005" }).click();
  await panel().locator("[data-rule=SQ005]").waitFor({ state: "detached" });
  await setDoc("SELECT order_date FROM sales;");
  await openPanel();
  assert.strictEqual(await panel().locator("[data-rule=SQ005]").count(), 0);
  const showAll = panel().locator(".learn-dismissed button", { hasText: "Show all again" });
  if (!(await showAll.count())) await panel().locator(".learn-dismissed button", { hasText: "1 dismissed · Show" }).click();  // the list stays open once opened
  await showAll.click();
  await panel().locator("[data-rule=SQ005]").waitFor();
  assert.deepStrictEqual(await page().evaluate(() => learnDismissed), []);
});

test("a rule a policy names says so, can't be dismissed, and ignores earlier dismissals", { skip }, async () => {
  await panel().locator("[data-rule=SQ005]").getByRole("button", { name: /Dismiss SQ005/ }).click();
  await panel().locator("[data-rule=SQ005] .learn-dismiss-item", { hasText: "Never show SQ005" }).click();
  await panel().locator("[data-rule=SQ005]").waitFor({ state: "detached" });
  await page().evaluate(() => {
    sqlPracticePolicies = Promise.resolve({ settings: { SQ005: { enabled: true, severity: "Warning" } }, policies: ["Names everything"], note: "" });
  });
  await setDoc("SELECT order_date FROM sales;\n");
  await openPanel();
  await panel().locator("[data-rule=SQ005]").waitFor();
  assert.match(await panel().locator("[data-rule=SQ005]").innerText(), /Your organization requires this rule/);
  assert.strictEqual(await panel().locator("[data-rule=SQ005] .learn-dismiss").count(), 0);
  await page().evaluate(() => { sqlPracticePolicies = null; return learnUndismiss(null, { where: "x", redraw: () => {} }); });
});

test("with preview features hidden, nothing is added and nothing is hidden", { skip }, async () => {
  const shown = await page().evaluate(() => {
    const keep = state.settings.show_preview;
    state.settings.show_preview = { ...keep, value: false };
    const out = [learnFindingActions({ rule_id: "SQ002", line: 1 }, { where: "x", required: () => false, redraw: () => {} }),
      learnSplitFindings([{ rule_id: "SQ002" }], "x", () => false).shown.length];
    state.settings.show_preview = keep;
    return out;
  });
  assert.deepStrictEqual(shown, [null, 1]);
});
