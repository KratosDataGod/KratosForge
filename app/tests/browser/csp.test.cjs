// HD-020: the UI under the shell's Content Security Policy (tauri.conf.json), served from an http
// origin as the packaged app is, so a page that needs an inline script, eval, a style attribute in
// markup or a network request fails here instead of only in a release build (see harness.cjs).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, sqlRunMocks } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const LH = { workspace_id: "ws1", lakehouse_id: "lh1", name: "Sales" };
const CELLS = [
  { type: "markdown", cell_id: "m1", source: "# Load\nSome **bold** text.", meta: {} },
  { type: "code", cell_id: "c1", source: "a = 1", meta: {} },
];

let app;
const page = () => app.page;
const settle = () => page().waitForTimeout(300);

before(async () => {
  if (skip) return;
  app = await openApp({
    csp: true,
    mocks: { list_tables: () => ({ dbo: ["sales"] }), table_columns: () => [], ...sqlRunMocks(() => ({ columns: [], rows: [] })) },
  });
});

after(async () => { if (app) await app.close(); });

test("the policy is in force: an inline script is blocked and reported", { skip }, async () => {
  const ran = await page().evaluate(() => {
    window.__inline = false;
    const s = document.createElement("script");
    s.textContent = "window.__inline = true";
    document.body.append(s);
    return window.__inline;
  });
  assert.strictEqual(ran, false);
  await page().waitForFunction(() => true);
  await settle();
  assert.ok(app.violations.some((v) => v.startsWith("script-src")), app.violations.join("\n"));
  app.violations.length = 0;
});

for (const name of ["home", "create", "monitor", "settings", "notebooks", "workspaces", "optimize", "profile"]) {
  test(`${name} opens with nothing blocked`, { skip }, async () => {
    await page().evaluate((n) => go(n), name);
    await settle();
    assert.deepStrictEqual(app.violations, []);
    assert.deepStrictEqual(app.errors, []);
  });
}

test("the notebook and its editors work under the policy (CodeMirror styles carry the nonce)", { skip }, async () => {
  await page().evaluate((cells) => { loadIntoState({ name: "CSP", cells }); go("notebook"); }, CELLS);
  const code = page().locator(".cell").nth(1).locator(".cm-content");
  await code.waitFor();
  await code.click();
  await page().keyboard.type("\nb = 2");
  await settle();
  // CodeMirror styles the editor through its own <style> tag, which needs the nonce; without it the
  // editor loses its layout (display: flex) and the policy reports style-src.
  assert.strictEqual(await page().evaluate(() => getComputedStyle(document.querySelector(".cm-editor")).display), "flex");
  assert.deepStrictEqual(app.violations, []);
  assert.deepStrictEqual(app.errors, []);
});

test("the SQL editor opens with nothing blocked", { skip }, async () => {
  await page().evaluate((lh) => openSqlQuery(lh), LH);
  await page().waitForSelector(".cm-content");
  await settle();
  assert.deepStrictEqual(app.violations, []);
  assert.deepStrictEqual(app.errors, []);
});
