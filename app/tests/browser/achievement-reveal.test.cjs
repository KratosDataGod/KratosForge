// KF-MO-008 Achievement Reveal (docs/forge-animations.md): when Profile awards an achievement, its
// message gets the reveal, only with Achievement celebrations on (off by default), once per award,
// never again for the same award, even after a restart. Settings come from the real engine.
const { test, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const FIRST = { id: "first-flame", name: "First Flame", xp: 50 };
const STEADY = { id: "steady-hand", name: "Steady Hand", xp: 100 };

const apps = [];
// Served over http (csp), so localStorage survives the reload below.
async function open() {
  const app = await openApp({ engine: ["app_settings", "save_app_settings"], csp: true });
  apps.push(app);
  await watch(app.page);
  return app;
}

// Every effect element made, as forge-animations.test.cjs records them, with the load watch stopped
// so the machine's load can't decide whether an effect plays.
async function watch(page) {
  await page.evaluate(async () => {
    await loadSettings();
    motionLoadWatch?.disconnect();
    loadTasks = [];
    dropUntil = 0;
    applyMotion();
    window.__fx = [];
    const seen = (e) => window.__fx.push(`${e.className} @${e.parentElement?.id ?? ""}`);
    new MutationObserver((list) => list.forEach((m) => m.addedNodes.forEach((n) => {
      if (n.classList?.contains("fx")) seen(n);
      n.querySelectorAll?.(".fx").forEach(seen);
    }))).observe(document.body, { childList: true, subtree: true });
  });
}

const toastSays = (page, text) => page.waitForFunction((t) => {
  const el = document.getElementById("toast");
  return !el.hidden && el.textContent.startsWith(t);
}, text, { timeout: 10000 });

after(async () => { for (const app of apps) await app.close(); });

test("By default an award shows its message and plays no reveal", { skip }, async () => {
  const app = await open();
  const { page } = app;
  assert.strictEqual(await page.evaluate(() => state.settings.motion_achievements.value), false, "celebrations are on by default");
  assert.strictEqual(await page.evaluate(() => document.documentElement.dataset.fxAchievements), "off");
  await page.evaluate((a) => profileEarned([a]), FIRST);
  await toastSays(page, "Achievement earned: First Flame (+50 XP)");
  assert.deepStrictEqual(await page.evaluate(() => window.__fx), []);
  assert.strictEqual(await page.evaluate(() => motionDiagnostics().skipped.family), 1);
  // Settings offers the switch, off, now that Profile can award something.
  await page.evaluate(() => go("settings"));
  const sw = page.getByRole("checkbox", { name: "Achievement celebrations" });
  await sw.waitFor();
  assert.strictEqual(await sw.isChecked(), false);
  assert.deepStrictEqual(app.errors, []);
});

test("With celebrations on each new award reveals once, and never again, even after a restart", { skip }, async () => {
  const app = await open();
  const { page } = app;
  await page.evaluate(() => go("settings"));
  await page.getByRole("checkbox", { name: "Achievement celebrations" }).check();
  await page.waitForFunction(() => document.documentElement.dataset.fxAchievements === "on");
  await page.evaluate((a) => profileEarned([a]), FIRST);
  await toastSays(page, "Achievement earned: First Flame");
  await page.waitForFunction(() => window.__fx.length > 0);
  assert.deepStrictEqual(await page.evaluate(() => window.__fx), ["fx fx-achieve @toast"]);
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "achievement-reveal.png") });
  // The same award reported again (Validate's result followed again) shows nothing new.
  await page.evaluate(() => { document.getElementById("toast").hidden = true; });
  await page.evaluate((a) => profileEarned([a]), FIRST);
  await page.waitForTimeout(3000);  // longer than the message's 2.5 s delay: proves nothing came
  assert.strictEqual(await page.evaluate(() => document.getElementById("toast").hidden), true, "the award was announced twice");
  assert.strictEqual(await page.evaluate(() => window.__fx.length), 1, "the award was revealed twice");
  await page.reload();
  await page.waitForFunction(() => typeof profileEarned === "function");
  await watch(page);
  assert.strictEqual(await page.evaluate(() => state.settings.motion_achievements.value), true, "the switch wasn't kept");
  await page.evaluate((a) => profileEarned(a), [FIRST, STEADY]);
  await toastSays(page, "Achievement earned: Steady Hand (+100 XP)");
  await page.waitForFunction(() => window.__fx.length > 0);
  assert.deepStrictEqual(await page.evaluate(() => window.__fx), ["fx fx-achieve @toast"]);
  assert.doesNotMatch(await page.evaluate(() => document.getElementById("toast").textContent), /First Flame/);
  assert.deepStrictEqual(app.errors, []);
});
