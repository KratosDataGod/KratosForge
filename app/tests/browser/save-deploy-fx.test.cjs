// Forge animations on an explicit notebook Save and on a platform deploy (docs/forge-animations.md):
// Save and Ctrl+S spark once the save is written, at most once per 5 s, and autosave never does; a
// deploy that stops partway shows the ember beside where it stopped, an engine error the ember beside
// the error, and a deploy that succeeds still strikes. Every engine call is mocked.
const { test, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const apps = [];

async function open(mocks = {}) {
  const app = await openApp({ mocks: { save_notebook: () => ({}), ...mocks } });
  apps.push(app);
  await app.page.emulateMedia({ reducedMotion: "no-preference" });
  await app.page.waitForFunction(() => typeof go === "function" && !!document.querySelector(".rail-item"));
  // Effects drop a level while the machine is busy; the load watch is stopped so only the app decides.
  // Date.now runs on a clock the test can move forward, for the 5 s rule.
  await app.page.evaluate(() => {
    motionLoadWatch?.disconnect(); loadTasks = []; dropUntil = 0; applyMotion();
    const realNow = Date.now.bind(Date);
    window.__skew = 0;
    Date.now = () => realNow() + window.__skew;
    window.__fx = [];
    const once = new WeakSet();
    const seen = (e) => !once.has(e) && once.add(e) && window.__fx.push(`${e.className} @${e.parentElement?.id || e.parentElement?.dataset.save !== undefined && "save" || e.parentElement?.className || ""}`);
    new MutationObserver((list) => list.forEach((m) => m.addedNodes.forEach((n) => {
      if (n.classList?.contains("fx")) seen(n);
      n.querySelectorAll?.(".fx").forEach(seen);
    }))).observe(document.body, { childList: true, subtree: true });
  });
  return app;
}

const made = (page) => page.evaluate(() => window.__fx);
const saves = (app) => app.calls.filter((c) => c.method === "save_notebook").length;

after(async () => { for (const app of apps) await app.close(); });

test("Save sparks once it is written, not again within 5 s; Ctrl+S sparks on its toast; autosave never sparks", { skip }, async () => {
  const app = await open();
  const { page } = app;
  await page.evaluate(() => {
    loadIntoState({ name: "Daily sales", cells: [{ type: "code", cell_id: "a", source: "x = 1", meta: {} }] });
    state.ribbonTab = "home";
    go("notebook");
  });
  await page.waitForSelector("button[data-save]");
  // Autosave: an edit saves on its own after a moment, with no effect.
  await page.evaluate(() => markDirty());
  await page.waitForFunction(() => !state.notebook.dirty, null, { timeout: 10000 });
  assert.strictEqual(saves(app), 1);
  assert.deepStrictEqual(await made(page), [], "autosave played an effect");
  await page.click("button[data-save]");
  await page.waitForFunction(() => window.__fx.some((c) => c.includes("fx-spark") && c.endsWith("@save")));
  // A second Save 1 s later, once the first spark has gone, so only the 5 s rule can hold it back.
  // The Save button's own handler, awaited, so the check below comes after the save has finished.
  await page.waitForSelector("button[data-save] .fx", { state: "detached" });
  await page.evaluate(() => { window.__skew += 1000; });
  assert.strictEqual(await page.evaluate(() => saveExplicitly(() => document.querySelector("button[data-save]"))), true);
  assert.strictEqual(saves(app), 3);
  assert.strictEqual((await made(page)).filter((c) => c.includes("fx-spark")).length, 1, "a second Save within 5 s sparked");
  // Past the 5 s, Ctrl+S sparks beside its "Saved" toast.
  await page.evaluate(() => { window.__skew += 4000; });
  await page.keyboard.press("Control+s");
  await page.waitForFunction(() => window.__fx.some((c) => c.includes("fx-spark") && c.endsWith("@toast")));
  assert.strictEqual(await page.textContent("#toast"), "Saved");
  assert.deepStrictEqual(app.errors, []);
});

const PLAN = { plan_id: "p1", commit: "abcdef1234567", catalog_env: "dev", labels: { policy: "Policy" }, stops: [], removable: [],
  blocked: [], notes: [], steps: [{ kind: "policy", id: "pii", action: "update" }, { kind: "policy", id: "retention", action: "create" }] };

async function deploy(answer) {
  const app = await open({ git_platform_deploy: (p) => (p.confirm ? answer() : PLAN) });
  await app.page.evaluate(() => { gitPlatformDeploy("C:/repo"); });
  await app.page.waitForSelector(".git-deploy");
  await app.page.click(".dialog-actions >> text=Deploy 2 steps");
  return app;
}

test("A deploy that stops partway shows the ember beside where it stopped", { skip }, async () => {
  const { page, errors } = await deploy(() => ({ deployed: false, failed: { kind: "policy", id: "retention", action: "create" },
    message: "Name taken.", done: [{ kind: "policy", id: "pii" }], total: 2, labels: PLAN.labels, ...PLAN, steps: [] }));
  await page.waitForFunction(() => window.__fx.some((c) => c.includes("fx-fail")));
  const fx = await made(page);
  assert.ok(fx.some((c) => c.includes("fx-fail") && c.includes("git-warn")), `the ember isn't beside the note: ${fx}`);
  assert.match(await page.textContent(".git-deploy .callout.git-warn"), /Stopped at Policy retention: create: Name taken\. 1 of 2 steps applied/);
  assert.ok(!fx.some((c) => c.includes("fx-strike")));
  assert.deepStrictEqual(errors, []);
});

test("A deploy the engine refuses shows the ember beside the error; a deploy that succeeds strikes", { skip }, async () => {
  const bad = await deploy(() => { throw new Error("The platform lakehouse can't be reached"); });
  await bad.page.waitForFunction(() => window.__fx.some((c) => c.includes("fx-fail") && c.endsWith("@toast")));
  assert.match(await bad.page.textContent("#toast"), /Deploy failed/);
  const good = await deploy(() => ({ deployed: true, done: PLAN.steps, total: 2 }));
  await good.page.waitForFunction(() => window.__fx.some((c) => c.includes("fx-strike") && c.endsWith("@toast")));
  assert.ok(!(await made(good.page)).some((c) => c.includes("fx-fail")), "a successful deploy made an ember");
  assert.deepStrictEqual([...bad.errors, ...good.errors], []);
});
