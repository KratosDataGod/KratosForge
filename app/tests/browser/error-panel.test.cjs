// UX-011: a load that fails shows one error panel (a sentence, Retry, and the raw text under
// Details), and Retry loads it again. Drives the SQL page's first page of results.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, sqlRunMocks } = require("./harness.cjs");

const LH = { workspace_id: "ws1", lakehouse_id: "lh1", name: "Sales" };
const SHOTS = process.env.SCREENSHOT_DIR;

let app;
let pageFails = 1;
const skip = !playwright && "Playwright is not installed";

before(async () => {
  if (skip) return;
  const runs = sqlRunMocks(() => ({ columns: [{ name: "n", type: "int64" }], rows: [[1], [2]] }));
  app = await openApp({
    engine: ["sql_format", "sql_analyze"],
    mocks: {
      list_tables: () => ({ dbo: ["sales"] }), table_columns: () => [],
      ...runs,
      sql_page: (params) => {
        if (pageFails-- > 0) throw new Error("FileNotFoundError: [Errno 2] No such file or directory: 'C:\\\\runs\\\\run1\\\\part-0.arrow'");
        return runs.sql_page(params);
      },
    },
  });
  await app.page.evaluate((lh) => openSqlQuery(lh), LH);
  await app.page.waitForSelector(".sql-edit .cm-editor");
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

test("a failed load says what happened in words, and Retry loads it", { skip }, async () => {
  const page = app.page;
  await page.evaluate(() => {
    const v = sqlPage.editor.view;
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: "SELECT 1 AS n" } });
    v.focus();
  });
  await page.keyboard.press("Control+Enter");
  const panel = page.locator(".sql-grid .error-panel");
  await panel.waitFor();
  assert.strictEqual(await panel.getAttribute("role"), "alert");
  const first = await panel.locator(".error-panel-text").textContent();
  assert.match(first, /^There is no file or folder at C:\\runs\\run1\\part-0\.arrow on this PC\./);
  assert.doesNotMatch(first, /FileNotFoundError|Fabric/, "no exception name, and not called a Fabric item");
  assert.strictEqual(await panel.locator("details pre").isVisible(), false, "the raw text waits under Details");
  await panel.locator("summary").click();
  assert.match(await panel.locator("details pre").textContent(), /^FileNotFoundError: \[Errno 2\]/);
  assert.ok(await panel.getByRole("button", { name: "Copy the details" }).isVisible());
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "error-panel-retry.png") });

  await panel.getByRole("button", { name: "Retry" }).click();
  await page.waitForFunction(() => document.querySelectorAll(".sql-grid tbody tr").length === 2);
  assert.strictEqual(await page.locator(".sql-grid .error-panel").count(), 0);
  assert.strictEqual(app.calls.filter((c) => c.method === "sql_start").length, 1, "Retry reads the page again; it doesn't rerun the query");
  assert.deepStrictEqual(app.errors, []);
});

test("an error toast is announced, offers Retry, and stays while hovered", { skip }, async () => {
  const page = app.page;
  await page.evaluate(() => {
    window.__tries = 0;
    attempt(async () => { window.__tries++; throw "RuntimeError: 429 TooManyRequests"; }, "Couldn't read runs", { retry: true });
  });
  const t = page.locator("#toast");
  await t.waitFor();
  assert.strictEqual(await t.getAttribute("role"), "alert");
  assert.match(await t.textContent(), /^Couldn't read runs: Fabric is busy/);
  await t.hover();
  await page.waitForTimeout(8500);
  assert.ok(await t.isVisible(), "still there after 8 seconds while the pointer is on it");
  await t.getByRole("button", { name: "Retry" }).click();
  await page.waitForFunction(() => window.__tries === 2);
});

test("Could not list workspaces offers Retry, and Retry fills the list", { skip }, async () => {
  let fails = 1;
  const ws = await openApp({
    mocks: {
      environment: () => ({ python: "Python 3 (test)", user: { name: "Test", username: "t@example.com" } }),
      list_workspaces: () => { if (fails-- > 0) throw new Error("ConnectionError: Max retries exceeded"); return [{ id: "w1", displayName: "Sales Dev" }]; },
    },
  });
  try {
    const page = ws.page;
    await page.evaluate(() => { state.user = state.user || { name: "Test" }; state.workspaces = null; loadWorkspaces(); });
    const t = page.locator("#toast");
    await page.waitForFunction(() => /Could not list workspaces/.test(document.getElementById("toast")?.textContent || ""));
    assert.strictEqual(await t.getAttribute("role"), "alert");
    await t.getByRole("button", { name: "Retry" }).click();
    await page.waitForFunction(() => state.workspaces?.[0]?.displayName === "Sales Dev");
    assert.strictEqual(ws.calls.filter((c) => c.method === "list_workspaces").length, 2);
  } finally { await ws.close(); }
});

// WCAG 2.2.1 (timing adjustable): a toast with a button (Retry, Show in folder, a next step) doesn't time out, so
// someone who reaches it by keyboard, after Tab through the page, still finds it. It goes when closed (its ×, or
// Esc inside it, which puts focus back where it was) or when another toast takes its place. A toast without a
// button still goes by itself: its words are said as it shows, and errors are kept in Activity.
test("a toast with a button stays until it is closed, and Esc in it puts focus back", { skip }, async () => {
  const page = app.page;
  await page.mouse.move(5, 5);
  await page.evaluate(() => {
    window.__tries = 0;
    attempt(async () => { window.__tries++; throw "RuntimeError: 429 TooManyRequests"; }, "Couldn't read runs", { retry: true });
  });
  const t = page.locator("#toast");
  await t.waitFor();
  await page.waitForTimeout(9000);  // past the 8 seconds an error toast used to last
  assert.ok(await t.isVisible(), "still there after 9 seconds, untouched");
  // Someone works in the search box, then moves to the toast's Retry.
  await page.locator("#search").focus();
  await t.getByRole("button", { name: "Retry" }).focus();
  await page.keyboard.press("Escape");
  await t.waitFor({ state: "hidden" });
  assert.strictEqual(await page.evaluate(() => document.activeElement?.id), "search", "focus goes back to where it was");
  assert.strictEqual(await page.evaluate(() => window.__tries), 1, "Esc runs nothing");

  // The × closes it too, and has a name.
  await page.evaluate(() => toast("Support bundle saved", false, { action: { label: "Show in folder", run: () => {} } }));
  await t.waitFor();
  await page.waitForTimeout(4500);
  assert.ok(await t.isVisible(), "a toast with a button isn't timed");
  await t.getByRole("button", { name: "Close" }).click();
  await t.waitFor({ state: "hidden" });

  // A toast without a button still goes by itself.
  await page.evaluate(() => toast("Saved"));
  await t.waitFor();
  assert.strictEqual(await t.getByRole("button", { name: "Close" }).count(), 0, "no × on a toast that goes by itself");
  await t.waitFor({ state: "hidden", timeout: 6000 });
  assert.deepStrictEqual(app.errors, []);
});
