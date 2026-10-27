// Promote (docs/forge-animations.md): an Anvil Strike on the message once the target environment is
// on the new metadata version, the failure ember beside the error when the promote fails or is
// blocked, and nothing for a cancelled sign-in. The engine is mocked.
const { test, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";

const PROJECT = { environments: { dev: {}, test: { workspace_id: "w1", workspace_name: "Sales Test", target: "onelake" }, prod: {} },
  layers: [{ lakehouse: "LH_Gold" }] };

const apps = [];
async function open(promote, { level = 2 } = {}) {
  const app = await openApp({ mocks: {
    app_settings: () => ({ motion_level: { value: level } }),
    meta_promote_check: () => ({ errors: [], notes: [], version: "1.4.0" }),
    meta_promote_impact: () => ({}),
    meta_last_release: () => null,
    meta_promote: () => promote(),
  } });
  apps.push(app);
  await app.page.emulateMedia({ reducedMotion: "no-preference" });
  await app.page.waitForFunction(() => typeof renderEnvTab === "function" && !!document.querySelector(".rail-item"));
  await app.page.evaluate(async (p) => {
    await loadSettings();
    state.user = "chris";
    const body = document.createElement("div");
    body.id = "env-under-test";
    document.body.prepend(body);
    await renderEnvTab(body, p);
    motionLoadWatch?.disconnect(); loadTasks = []; dropUntil = 0; applyMotion();
    window.__fx = [];
    // The page redrawn after a promote may toast its own news; what the promote said is kept here.
    window.__toasts = [];
    const shown = toast;
    toast = (text, ...rest) => { window.__toasts.push(text); return shown(text, ...rest); };
    const once = new WeakSet();
    const seen = (e) => !once.has(e) && once.add(e) && window.__fx.push(`${e.className} @${e.parentElement?.id || e.parentElement?.classList[0] || ""}`);
    new MutationObserver((list) => list.forEach((m) => m.addedNodes.forEach((n) => {
      if (n.classList?.contains("fx")) seen(n);
      n.querySelectorAll?.(".fx").forEach(seen);
    }))).observe(document.body, { childList: true, subtree: true });
  }, PROJECT);
  return app;
}

const promote = (page) => page.click("#env-under-test button:has-text('Promote to Test')");
const wait = (page, fn) => page.waitForFunction(fn, null, { timeout: 15000 });
const where = (page) => page.evaluate(() => window.__fx.map((c) => c.split(" @")[1]));

after(async () => { for (const app of apps) await app.close(); });

test("A promote that lands strikes the anvil once on the message", { skip }, async () => {
  const { page, errors } = await open(() => ({ version: "1.4.0" }));
  await promote(page);
  await wait(page, () => window.__fx.some((c) => c.includes("fx-strike")));
  assert.ok((await page.evaluate(() => window.__toasts)).includes("Test is now on metadata 1.4.0"));
  assert.deepStrictEqual(await where(page), ["toast"]);
  assert.deepStrictEqual(errors, []);
});

test("A promote that fails shows the ember; a cancelled sign-in plays nothing", { skip }, async () => {
  const failed = await open(() => { throw new Error("OneLake didn't answer"); });
  await promote(failed.page);
  await wait(failed.page, () => window.__fx.some((c) => c.includes("fx-fail")));
  assert.deepStrictEqual(await where(failed.page), ["toast"]);

  const cancelled = await open(() => { throw new Error("SignInCancelled: the sign-in window was closed"); });
  await promote(cancelled.page);
  await cancelled.page.waitForTimeout(500);
  assert.deepStrictEqual(await cancelled.page.evaluate(() => window.__fx), []);
  assert.deepStrictEqual(cancelled.errors, []);
});

test("With motion Off a promote plays nothing", { skip }, async () => {
  const { page, errors } = await open(() => ({ version: "1.4.0" }), { level: 0 });
  await promote(page);
  await wait(page, () => window.__toasts.includes("Test is now on metadata 1.4.0"));
  await page.waitForTimeout(300);
  assert.deepStrictEqual(await page.evaluate(() => window.__fx), []);
  assert.deepStrictEqual(errors, []);
});
