// Beside a best-practice finding (learn.js, LX-09, from the approved mockup): Explain and Show example
// from the guide that ships with the app, the Lesson link, and Dismiss with its three scopes, against
// the real engine. Expert shows only Dismiss, Professional adds a line on why, Guided opens Explain;
// a rule a policy names can't be dismissed.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, sqlRunMocks, axeSource, axeFindings } = require("./harness.cjs");

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
      "learn_catalog", "learn_rule_guides", "learn_dismissals", "learn_dismiss", "learn_undismiss", "learn_fixes"],
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

test("Expert shows Dismiss and nothing else beside a finding", { skip }, async () => {
  await setMode("expert");
  await setDoc(VIEW);
  await page().waitForFunction(() => /Best practices · 3/.test(document.querySelector("button[data-practices]")?.textContent));
  await chip().click();
  await sq002().locator(".learn-dismiss button").first().waitFor();
  assert.strictEqual(await sq002().locator(".learn-actions button", { hasText: "Explain" }).count(), 0);
  assert.strictEqual(await sq002().locator(".learn-actions button", { hasText: "Show example" }).count(), 0);
  assert.strictEqual(await sq002().locator(".learn-why").count(), 0);
});

test("SQL guidance overrides Learning guidance for SQL findings only (GD-08)", { skip }, async () => {
  // Learning guidance is Expert from the test above; SQL guidance set to Guided wins on this page.
  await page().evaluate(() => saveSettings({ learn_mode_sql: "guided" }));
  await page().waitForFunction(() => state.settings.learn_mode_sql?.value === "guided");
  await setDoc(VIEW.replace("2026", "2023"));
  await openPanel();
  await sq002().locator(".learn-detail").waitFor();
  assert.match(await sq002().locator(".learn-detail").innerText(), /Why it matters/);
  assert.strictEqual(await page().evaluate(() => [learnMode(), learnMode("sql"), learnMode("notebooks")].join()), "expert,guided,expert");
  await page().evaluate(() => saveSettings({ learn_mode_sql: "same" }));
  await page().waitForFunction(() => state.settings.learn_mode_sql?.value === "same");
  assert.strictEqual(await page().evaluate(() => learnMode("sql")), "expert", "Same follows Learning guidance");
});

test("Professional, the default, adds a line on why, with Explain on request and no Lesson link", { skip }, async () => {
  await setMode("professional");
  assert.strictEqual(await page().evaluate(() => state.settings.learn_mode.source === "user"), true);
  await page().evaluate(() => saveSettings({ learn_mode: null }));  // back to the default
  await page().waitForFunction(() => state.settings.learn_mode?.source === "default" && state.settings.learn_mode.value === "professional");
  await setDoc(`${VIEW}\n`);
  await openPanel();
  await sq002().locator(".learn-why").waitFor();
  assert.strictEqual(await sq002().locator(".learn-why").innerText(),
    "The engine skips files and row groups by comparing a column's values with your filter.");
  assert.strictEqual(await sq002().locator(".learn-detail").count(), 0, "nothing open to start with");
  assert.strictEqual(await sq002().locator(".learn-finding a").count(), 0);
  await sq002().locator("button", { hasText: "Explain" }).click();
  await sq002().locator(".learn-detail").waitFor();
  assert.strictEqual(await page().evaluate(() => document.activeElement?.textContent), "Explain", "focus stays on Explain after the panel redraws");
  assert.strictEqual(await sq002().locator("button", { hasText: "Explain" }).getAttribute("aria-expanded"), "true");
});

test("every shipped guide gives Professional a whole first sentence", { skip }, async () => {
  const short = await page().evaluate(async () => {
    const guides = await rpc("learn_rule_guides");
    return Object.entries(guides).map(([id, g]) => [id, learnFirstSentence(g.why)])
      .filter(([, line]) => line.length < 30 || !/[.!?]$/.test(line));
  });
  assert.deepStrictEqual(short, []);
  assert.strictEqual(await page().evaluate(async () => learnFirstSentence((await rpc("learn_rule_guides")).NB002.why)),
    "An abfss://<workspace id>@... path points at one workspace.", "a period inside code doesn't end the sentence");
  assert.strictEqual(await page().evaluate(() => learnFirstSentence("Use a key, e.g. `dbo.sales.id`... or not. Then more.")),
    "Use a key, e.g. dbo.sales.id... or not.");
});

test("Guided opens Explain to start with, has Show example from the shipped guide, and the Lesson link", { skip }, async () => {
  await setMode("guided");
  await setDoc(VIEW.replace("2026", "2024"));  // a line not opened before
  await openPanel();
  await sq002().locator(".learn-detail").waitFor();
  assert.strictEqual(await sq002().locator(".learn-why").count(), 0, "the full explanation is open instead");
  const explain = await sq002().locator(".learn-detail").innerText();
  assert.match(explain, /^Recommended · Performance\s+Why it matters[\s\S]*The engine skips files and row groups/);
  assert.match(explain, /What to do\s+Compare the column itself/);
  assert.match(explain, /When it's fine[\s\S]*Small tables[\s\S]*How to check\s+Where Try the fix is offered, Compare/);
  assert.doesNotMatch(explain, /Impact|The principle/, "four headings at most: why, what to do, when it's fine, how to check");
  assert.match(explain, /Importance is the guide's view\. The severity and your organization's policies decide what's flagged and required\./);
  assert.match(explain, /Nothing is sent anywhere/);
  await sq002().locator("button", { hasText: "Show example" }).click();
  await sq002().locator("pre.after").waitFor();
  assert.strictEqual(await sq002().locator("pre.before").innerText(), "WHERE YEAR(order_date) = 2026");
  assert.match(await sq002().locator("pre.after").innerText(), /^WHERE order_date >= '2026-01-01'/);
  await sq002().locator(".learn-finding a", { hasText: "Lesson: Your first query on this PC" }).waitFor();
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "finding-actions-example.png") });
  await setDoc(VIEW);
  await openPanel();
  await sq002().waitFor();
});

test("the open finding has no serious axe findings", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  await sq002().getByRole("button", { name: /Dismiss SQ002/ }).click();
  const found = await axeFindings(page(), ".sql-compat");
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

test("on a lakehouse tab Try the fix previews and applies, with no Compare: its T-SQL doesn't run here (GD-02)", { skip }, async () => {
  await setDoc("SELECT order_date FROM sales;\n");
  await openPanel();
  const sq005 = panel().locator("[data-rule=SQ005]");
  await sq005.getByRole("button", { name: "Try the fix" }).click();
  await sq005.locator(".learn-fix").waitFor();
  assert.strictEqual(await sq005.locator("pre.after").innerText(), "dbo.sales");
  assert.strictEqual(await sq005.getByRole("button", { name: "Compare before applying" }).count(), 0);
  assert.match(await sq005.locator(".learn-fix").innerText(), /Compare runs on a warehouse or SQL analytics endpoint's page/);
  await sq005.getByRole("button", { name: "Apply", exact: true }).click();
  assert.strictEqual(await page().evaluate(() => sqlPage.editor.text()), "SELECT order_date FROM dbo.sales;\n");
  await page().keyboard.press("Control+z");
  assert.strictEqual(await page().evaluate(() => sqlPage.editor.text()), "SELECT order_date FROM sales;\n");
});
