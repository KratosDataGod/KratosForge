// AF-2 (docs/ux/action-first.md): Home leads with Continue and Start something, then Recent notebooks,
// and asks to sign in in one place only (see harness.cjs for how to run it).
const { test, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
after(async () => { await app?.close(); });

const notebooks = [{ name: "Orders", modified: 1760000000 }, { name: "Customers", modified: 1760005000 }, { name: "Old", modified: 1750000000 }];
const readiness = [{ key: "engine", title: "Local engine", status: "ok", text: "Starts on this PC." },
  { key: "signin", title: "Signed in", status: "fail", text: "Not signed in, so Fabric can't be reached yet.", fix: "sign_in" }];

async function home(storage = {}) {
  await app?.close();
  app = await openApp({ storage, mocks: { list_notebooks: () => notebooks, readiness_check: () => readiness,
    open_notebook: ({ name }) => ({ name, cells: [], metadata: {} }) } });
  await app.page.waitForFunction(() => state.view === "home" && homeCache.done, null, { timeout: 20000 });
  return app.page;
}

// Top of each part of Home, in page order.
const order = (p) => p.evaluate(() => [...document.querySelector(".page.home").children].map((e) =>
  e.matches(".home-start") ? "start" : e.matches(".ready") ? "ready" : e.querySelector?.(":scope h2")?.textContent === "Recent notebooks" ? "recent"
    : e.matches(".callout") ? "callout" : e.matches(".get-started") ? "get-started" : null).filter(Boolean));

test("Start something and Recent notebooks come first, with Continue opening the notebook changed last", { skip }, async () => {
  const p = await home();
  assert.deepStrictEqual((await order(p)).slice(0, 3), ["start", "recent", "ready"]);
  const cont = p.locator(".home-start button.primary");
  assert.strictEqual(await cont.count(), 1);
  assert.strictEqual((await cont.textContent()).trim(), "Continue: Customers");
  // Every way to start stays.
  for (const label of ["New notebook", "Import from Fabric or Databricks", "New lakehouse", "OneLake catalog"]) {
    assert.ok(await p.$(`.home-start button:has-text("${label}")`), label);
  }
  await cont.click();
  await p.waitForFunction(() => state.view === "notebook");
  assert.ok(app.calls.some((c) => c.method === "open_notebook" && c.params.name === "Customers"));
  assert.deepStrictEqual(app.errors, []);
});

test("signed out, Home asks to sign in once and says Fabric wasn't checked", { skip }, async () => {
  const p = await home();
  assert.ok(!(await order(p)).includes("callout"));  // Ready to work already asks
  assert.strictEqual(await p.locator('.page.home button:text-is("Sign in")').count(), 1);
  assert.doesNotMatch(await p.textContent(".get-started"), /Sign in with your work account/);
  assert.match(await p.textContent(".page.home > .sub"), /^Sign in so KratosForge can check Fabric\. Notebooks run on this PC either way\.$/);
});

test("with Ready to work hidden, the sign-in callout and the Get started step come back", { skip }, async () => {
  const p = await home({ "fd.ready.hidden": "1" });
  assert.deepStrictEqual((await order(p)).slice(0, 3), ["start", "recent", "callout"]);
  assert.match(await p.textContent(".get-started"), /Sign in with your work account/);
});

test("with no notebooks there is no Continue, and New notebook is still there", { skip }, async () => {
  await app?.close();
  app = await openApp({ mocks: { list_notebooks: () => [], readiness_check: () => readiness } });
  const p = app.page;
  await p.waitForFunction(() => state.view === "home" && homeCache.done, null, { timeout: 20000 });
  assert.strictEqual(await p.locator(".home-start button.primary").count(), 0);
  assert.ok(await p.$('.home-start button:has-text("New notebook")'));
});
