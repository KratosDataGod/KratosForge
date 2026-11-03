// GD-09: a governance policy sets a team's default guidance level (learn/team.py). The Best practices
// panel uses it only while nobody chose a level, says where it came from, and a level the person picks
// wins. Real engine for the SQL rules; the policy read is mocked so the team level is known.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, sqlRunMocks } = require("./harness.cjs");

const LH = { workspace_id: "ws1", lakehouse_id: "lh1", name: "Sales" };
const skip = !playwright && "Playwright is not installed";
const VIEW = "CREATE VIEW dbo.v_orders AS\nSELECT *\nFROM sales\nWHERE YEAR(order_date) = 2026;";

let app;
const page = () => app.page;
const panel = () => page().locator(".sql-compat");
const sq002 = () => panel().locator("[data-rule=SQ002]");

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["app_settings", "save_app_settings", "sql_format", "sql_analyze", "sql_practices",
      "learn_catalog", "learn_rule_guides", "learn_dismissals", "learn_fixes"],
    storage: { "fabric-desktop.sql.lh1": JSON.stringify([{ name: "Weekly revenue", sql: "SELECT 1", dialect: "warehouse" }]) },
    mocks: {
      governance_practice_settings: ({ kind }) => ({ settings: {}, policies: [], note: "",
        guidance: kind === "sql" ? { level: "guided", policy: "New starters" } : null }),
      list_tables: () => ({ dbo: ["sales"] }),
      table_columns: () => [{ name: "order_date", type: "date" }],
      ...sqlRunMocks(() => ({ columns: [{ name: "x", type: "int64" }], rows: [[1]] })),
    },
  });
  await page().waitForFunction(() => state.settings && "learn_mode" in state.settings);
  await page().evaluate(() => saveSettings({ learn_mode: null, learn_mode_sql: null }));
  await page().waitForFunction(() => state.settings.learn_mode?.source === "default");
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
  await page().waitForFunction(() => sqlPage.tabs[sqlPage.active].practices);
  if (await panel().isHidden()) await page().locator("button[data-practices]").click();
}

test("with no level chosen, the team's level applies and the panel says where it came from", { skip }, async () => {
  await setDoc(VIEW);
  await sq002().locator(".learn-detail").waitFor();  // Guided opens Explain to start with
  assert.strictEqual(await page().evaluate(() => [learnMode("sql"), learnMode("notebooks"), learnMode()].join()),
    "guided,professional,professional", "only the subject the policy set, and the default elsewhere");
  assert.strictEqual(await panel().locator(".learn-team-note").innerText(),
    "Guidance: Guided, your team's default from New starters. Choose your own in Settings › Appearance › Layout.");
});

test("a level the person picks wins over the team's, and the note goes", { skip }, async () => {
  await page().evaluate(() => saveSettings({ learn_mode: "expert" }));
  await page().waitForFunction(() => state.settings.learn_mode?.source === "user");
  await setDoc(VIEW.replace("2026", "2025"));
  await sq002().waitFor();
  assert.strictEqual(await page().evaluate(() => learnMode("sql")), "expert");
  assert.strictEqual(await panel().locator(".learn-team-note").count(), 0);
  assert.strictEqual(await sq002().locator(".learn-detail").count(), 0);
  await page().evaluate(() => saveSettings({ learn_mode: null, learn_mode_sql: "professional" }));
  await page().waitForFunction(() => state.settings.learn_mode_sql?.value === "professional");
  assert.strictEqual(await page().evaluate(() => learnMode("sql")), "professional", "a SQL level wins too");
  await page().evaluate(() => saveSettings({ learn_mode_sql: null }));
  await page().waitForFunction(() => state.settings.learn_mode_sql?.source === "default");
});

test("with preview features hidden, the team's level adds nothing", { skip }, async () => {
  await page().evaluate(() => saveSettings({ show_preview: false }));
  await page().waitForFunction(() => !previewShown());
  assert.strictEqual(await page().evaluate(() => [learnMode("sql"), learnTeamNote("sql")].join()), "expert,");
  await page().evaluate(() => saveSettings({ show_preview: null }));
});
