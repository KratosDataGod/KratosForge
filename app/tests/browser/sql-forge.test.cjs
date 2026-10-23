// KF-MO-005: Forge animations on the SQL query page (docs/forge-animations.md). A spark on the Results
// pill when a query that took over 2 s succeeds, the ember when one fails, nothing for a quick query
// or a stop. The engine is mocked and says how long each run took, so no test waits on a clock.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const LH = { workspace_id: "ws1", lakehouse_id: "lh1", name: "Sales" };
const COLS = [{ name: "n", type: "int64" }];
// What the next run does: its final state and the seconds the engine says it took.
let plan = {};
let next = 1;

let app;
const skip = !playwright && "Playwright is not installed";

before(async () => {
  if (skip) return;
  app = await openApp({
    mocks: {
      list_tables: () => ({ dbo: ["sales"] }),
      table_columns: () => [],
      sql_analyze: () => { throw new Error("not checked in this test"); },
      sql_start: () => {
        const id = `run${next++}`;
        if (plan.state === "cancelled") return { id, state: "running", rows: 0, seconds: 0.5 };
        return { id, state: plan.state, rows: 1, columns: COLS, seconds: plan.seconds,
          ...(plan.state === "failed" ? { error: "Binder Error: column \"x\" not found", error_kind: "sql" } : {}) };
      },
      sql_status: ({ execution }) => ({ id: execution, state: "cancelled", rows: 0, seconds: 3 }),
      sql_page: ({ execution }) => ({ id: execution, offset: 0, limit: 1000, rows: [[1]], columns: COLS, total: 1,
        has_previous: false, has_next: false }),
    },
  });
  await app.page.evaluate((lh) => openSqlQuery(lh), LH);
  await app.page.waitForSelector(".sql-edit .cm-editor");
  // Every effect made, with the element it played on; the long-task watch is stopped so the runner's
  // load can't drop a level (see forge-animations.test.cjs).
  await app.page.evaluate(() => {
    motionLoadWatch?.disconnect();
    loadTasks = [];
    dropUntil = 0;
    applyMotion();
    window.__fx = [];
    new MutationObserver((list) => list.forEach((m) => m.addedNodes.forEach((n) => {
      if (n.classList?.contains("fx")) window.__fx.push(`${n.className} @${n.parentElement?.className}`);
    }))).observe(document.body, { childList: true, subtree: true });
  });
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

const starts = () => app.calls.filter((c) => c.method === "sql_start").length;

// Runs one query as planned and returns the effects it made, once the run has finished and redrawn.
async function runAs(p) {
  plan = p;
  const before = starts();
  await app.page.evaluate(() => {
    window.__fx = [];
    const v = sqlPage.editor.view;
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: "SELECT 1 AS n" } });
    v.focus();
  });
  await app.page.keyboard.press("Control+Enter");
  await app.page.waitForFunction(() => !sqlPage.tabs[sqlPage.active].running, before);
  assert.strictEqual(starts(), before + 1, "the query did not run");
  return app.page.evaluate(() => ({ fx: window.__fx, pill: document.querySelector(".sql-pill").textContent,
    events: motionDiagnostics().events }));
}

test("a quick success plays nothing; one over 2 s sparks on the Results pill", { skip }, async () => {
  const quick = await runAs({ state: "completed", seconds: 1.9 });
  assert.strictEqual(quick.pill, "Succeeded");
  assert.deepStrictEqual(quick.fx, [], "a quick query sparked");
  const slow = await runAs({ state: "completed", seconds: 2.4 });
  assert.strictEqual(slow.pill, "Succeeded");
  assert.strictEqual(slow.fx.length, 1);
  assert.match(slow.fx[0], /\bfx-spark\b.* @.*\bsql-pill\b/, "the spark is not on the Results pill");
  assert.strictEqual(slow.events["sql.succeeded"], 1, "the quick success was sent as an event");
  assert.deepStrictEqual(app.errors, []);
});

test("a failed query shows the ember beside Failed, however quick it was", { skip }, async () => {
  const out = await runAs({ state: "failed", seconds: 0.1 });
  assert.strictEqual(out.pill, "Failed");
  assert.strictEqual(out.fx.length, 1);
  assert.match(out.fx[0], /\bfx-fail\b.* @.*\bsql-pill\b/);
  assert.match(await app.page.locator("div.output.error").innerText(), /Binder Error/);
  assert.deepStrictEqual(app.errors, []);
});

test("a stopped query plays nothing", { skip }, async () => {
  const out = await runAs({ state: "cancelled" });
  assert.strictEqual(out.pill, "Stopped");
  assert.deepStrictEqual(out.fx, []);
  assert.strictEqual(out.events["sql.failed"], 1, "the stop was sent as a failure");
  assert.deepStrictEqual(app.errors, []);
});
