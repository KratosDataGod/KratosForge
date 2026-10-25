// Building a Direct Lake semantic model (docs/forge-animations.md): a Forge Spark on the "Created" or
// "Updated" message once Fabric gives back the model, the failure ember beside the error when the
// build fails, and nothing while the app asks whether to replace a model or when the sign-in is
// cancelled. Fabric is mocked.
const { test, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";

const PLAN = { layer: "gold", layers: ["gold"], name: "Sales model", workspace_name: "Sales Test", workspace_id: "ws",
  lakehouse: "LH_Gold", existing: null, tables: [], relationships: [] };
const BUILT = { created: true, name: "Sales model", tables: ["dim_customer", "fact_sales"], relationships: 1, skipped: [], endpoint_refreshed: true };

const apps = [];
async function open(build, { level = 2 } = {}) {
  const app = await openApp({ mocks: {
    app_settings: () => ({ motion_level: { value: level } }),
    meta_model_plan: () => PLAN,
    meta_build_model: (p) => build(p),
  } });
  apps.push(app);
  await app.page.emulateMedia({ reducedMotion: "no-preference" });
  await app.page.waitForFunction(() => typeof renderModelTab === "function" && !!document.querySelector(".rail-item"));
  await app.page.evaluate(async () => {
    await loadSettings();
    state.user = { name: "Chris" };
    state.meta.env = "test";
    state.meta.tab = "model";
    state.view = "metadata";
    const body = document.createElement("div");
    body.id = "model-under-test";
    document.querySelector("main, #main, body").prepend(body);
    await renderModelTab(body);
    motionLoadWatch?.disconnect(); loadTasks = []; dropUntil = 0; applyMotion();
    window.__fx = [];
    const once = new WeakSet();
    const seen = (e) => !once.has(e) && once.add(e) && window.__fx.push(`${e.className} @${e.parentElement?.id || e.parentElement?.classList[0] || ""}`);
    new MutationObserver((list) => list.forEach((m) => m.addedNodes.forEach((n) => {
      if (n.classList?.contains("fx")) seen(n);
      n.querySelectorAll?.(".fx").forEach(seen);
    }))).observe(document.body, { childList: true, subtree: true });
  });
  return app;
}

const create = (page) => page.click("#model-under-test button:has-text('Create model')");
const wait = (page, fn) => page.waitForFunction(fn, null, { timeout: 15000 });

after(async () => { for (const app of apps) await app.close(); });

test("A model Fabric gives back sparks once on the Created message", { skip }, async () => {
  const { page, errors } = await open(() => BUILT);
  await create(page);
  await wait(page, () => window.__fx.some((c) => c.includes("fx-spark")));
  assert.match(await page.textContent("#model-under-test .pass-box"), /Created Sales model: 2 tables, 1 relationship\./);
  const made = await page.evaluate(() => window.__fx);
  assert.deepStrictEqual(made.map((c) => c.split(" @")[1]), ["pass-box"], `${made}`);
  assert.deepStrictEqual(errors, []);
});

test("A build that fails shows the ember beside the error; asking to replace plays nothing", { skip }, async () => {
  const { page } = await open(() => { throw new Error("The SQL endpoint isn't ready"); });
  await create(page);
  await wait(page, () => window.__fx.some((c) => c.includes("fx-fail")));
  const made = await page.evaluate(() => window.__fx);
  assert.deepStrictEqual(made.map((c) => c.split(" @")[1]), ["toast"], `${made}`);

  const asks = await open(() => ({ confirm: { name: "Sales model", workspace_name: "Sales Test" } }));
  await create(asks.page);
  await asks.page.waitForSelector(".dialog");
  await asks.page.waitForTimeout(300);
  assert.deepStrictEqual(await asks.page.evaluate(() => window.__fx), []);
  assert.deepStrictEqual(asks.errors, []);
});

test("A build stopped by closing the sign-in window plays nothing", { skip }, async () => {
  const { page, errors } = await open(() => { throw new Error("SignInCancelled: the sign-in window was closed"); });
  await create(page);
  await page.waitForTimeout(500);
  assert.deepStrictEqual(await page.evaluate(() => window.__fx), []);
  assert.strictEqual(await page.locator("#model-under-test .pass-box").count(), 0);
  assert.deepStrictEqual(errors, []);
});

test("With motion Off the model is built with no effect", { skip }, async () => {
  const { page, errors } = await open(() => BUILT, { level: 0 });
  await create(page);
  await page.waitForSelector("#model-under-test .pass-box");
  await page.waitForTimeout(300);
  assert.deepStrictEqual(await page.evaluate(() => window.__fx), []);
  assert.deepStrictEqual(errors, []);
});
