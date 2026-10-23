// Motion & effects (docs/forge-animations.md) in the real UI: the four levels and their switches in
// Settings, kept by the real engine; the Preview effects window, whose Energy Flow follows a real
// three-step pipeline run on this PC (two waits and a variable; nothing is read, written or sent);
// reduced motion; and effects never taking focus or catching the pointer.
const { test, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const ENGINE = ["app_settings", "save_app_settings", "pipeline_run_start", "pipeline_run_status"];

const apps = [];
async function open({ reducedMotion = "no-preference" } = {}) {
  const app = await openApp({ engine: ENGINE });
  apps.push(app);
  await app.page.emulateMedia({ reducedMotion });
  await app.page.evaluate(async () => { await loadSettings(); go("settings"); });
  await app.page.getByRole("radiogroup", { name: "Visual intensity" }).waitFor();
  await app.page.evaluate(() => {
    window.__fx = [];
    new MutationObserver((list) => list.forEach((m) => m.addedNodes.forEach((n) => {
      if (n.classList?.contains("fx")) window.__fx.push(n.className);
    }))).observe(document.body, { childList: true, subtree: true });
  });
  return app;
}

after(async () => { for (const app of apps) await app.close(); });

test("Settings > Appearance > Motion & effects: Refined by default, each level kept and applied at once", { skip }, async () => {
  const app = await open();
  const { page } = app;
  assert.strictEqual(await page.getByRole("radio", { name: /^2 Refined/ }).isChecked(), true);
  assert.strictEqual(await page.evaluate(() => document.documentElement.dataset.motionLevel), "2");
  await page.evaluate(() => { document.getElementById("toast").hidden = true; });
  if (SHOTS) await page.locator(".motion-settings").screenshot({ path: path.join(SHOTS, "motion-settings.png") });
  await page.getByRole("radio", { name: /^3 Expressive/ }).check();
  await page.waitForFunction(() => document.documentElement.dataset.motionLevel === "3");
  assert.strictEqual(await page.evaluate(async () => (await rpc("app_settings")).motion_level.value), 3, "the level wasn't kept");
  await page.getByRole("radio", { name: /^1 Minimal/ }).check();
  await page.waitForFunction(() => document.documentElement.dataset.motionLevel === "1");
  // Below Refined the family switches have nothing to switch, so they're greyed out.
  // The level applies before Settings draws again, so wait for the redraw rather than read it at once.
  await page.waitForFunction(() => document.querySelector('input[aria-label="Forge sparks"]')?.disabled === true);
  await page.getByRole("radio", { name: /^2 Refined/ }).check();
  await page.waitForFunction(() => document.documentElement.dataset.motionLevel === "2");
  await page.getByRole("checkbox", { name: "Forge sparks" }).uncheck();
  await page.waitForFunction(() => document.documentElement.dataset.fxSparks === "off");
  assert.strictEqual(await page.evaluate(async () => (await rpc("app_settings")).motion_sparks.value), false);
  assert.deepStrictEqual(app.errors, []);
});

test("Preview effects plays each effect, follows a real local pipeline run, and takes neither focus nor the pointer", { skip }, async () => {
  const app = await open();
  const { page } = app;
  await page.evaluate(() => saveSettings({ motion_level: 3 }));
  await page.getByRole("button", { name: "Preview effects" }).click();
  const dialog = page.getByRole("dialog", { name: "Motion & effects" });
  await dialog.waitFor();
  const plays = dialog.getByRole("button", { name: "Play" });
  for (let i = 0; i < await plays.count(); i += 1) {
    const button = plays.nth(i);
    await button.click();
    // An effect never moves focus away from what was clicked, and never catches the pointer.
    assert.strictEqual(await button.evaluate((b) => b === document.activeElement), true, `Play ${i + 1} lost focus`);
    const fxs = await page.evaluate(() => [...document.querySelectorAll(".fx")].map((e) => [e.getAttribute("aria-hidden"), getComputedStyle(e).pointerEvents]));
    for (const [hidden, pointer] of fxs) { assert.strictEqual(hidden, "true"); assert.strictEqual(pointer, "none"); }
    if (SHOTS && i === 5) await dialog.screenshot({ path: path.join(SHOTS, "motion-gallery-strike.png") });
    await page.waitForTimeout(1100);  // past every effect's rate limit, so the next one plays
  }
  const made = await page.evaluate(() => window.__fx.join(" "));
  for (const kind of ["spark", "fail", "pulse", "arrive", "wolfrun", "strike"]) assert.match(made, new RegExp(`fx-${kind}\\b`), `no ${kind}`);
  // Energy Flow on a real pipeline run on this PC.
  await dialog.getByRole("button", { name: "Run sample on this PC" }).click();
  await dialog.locator(".flow-node").first().waitFor();
  assert.match(await dialog.locator(".flow-where").innerText(), /^Local run on this PC \(not run in Fabric\)$/);
  await page.waitForFunction(() => document.querySelector('.flow-node[data-state="running"]'), null, { timeout: 20000 });
  if (SHOTS) await dialog.screenshot({ path: path.join(SHOTS, "motion-flow-running.png") });
  await page.waitForFunction(() => document.querySelectorAll('.flow-node[data-state="succeeded"]').length === 3, null, { timeout: 20000 });
  assert.deepStrictEqual(await dialog.locator(".flow-state").allInnerTexts(), ["Succeeded", "Succeeded", "Succeeded"]);
  await page.waitForFunction(() => /fx-settle/.test(window.__fx.join(" ")), null, { timeout: 5000 });
  await page.waitForFunction(() => document.getElementById("announce").textContent === "Sample pipeline succeeded");
  if (SHOTS) await dialog.screenshot({ path: path.join(SHOTS, "motion-flow-done.png") });
  // Every effect cleans up after itself.
  await page.waitForFunction(() => !document.querySelector(".fx") && motionDiagnostics().live === 0, null, { timeout: 3000 });
  assert.deepStrictEqual(app.errors, []);
});

test("Windows reduced motion: nothing plays, unless the person opts out of following it", { skip }, async () => {
  const app = await open({ reducedMotion: "reduce" });
  const { page } = app;
  await page.getByText("Off now: Windows asks apps to reduce motion.").waitFor();
  assert.strictEqual(await page.evaluate(() => motionLevel()), 0);
  await page.getByRole("button", { name: "Preview effects" }).click();
  await page.getByRole("dialog", { name: "Motion & effects" }).getByRole("button", { name: "Play" }).first().click();
  assert.deepStrictEqual(await page.evaluate(() => window.__fx), [], "an effect played under reduced motion");
  await page.evaluate(() => closeDialog());
  await page.getByRole("checkbox", { name: "Respect Windows' reduced-motion setting" }).uncheck();
  await page.waitForFunction(() => document.documentElement.dataset.motionOverride === "on");
  assert.strictEqual(await page.evaluate(() => motionLevel()), 2);
  assert.deepStrictEqual(app.errors, []);
});

test("Changing page slides the new page in only at Minimal and above, and never on a redraw", { skip }, async () => {
  const app = await open();
  const { page } = app;
  const animated = () => page.evaluate(() => document.getElementById("view").getAnimations().length);
  await page.evaluate(() => go("home"));
  assert.strictEqual(await animated(), 1, "no transition on a page change");
  // Let the transition end before the redraw, however slow the machine: finish it rather than wait a fixed time.
  await page.evaluate(() => document.getElementById("view").getAnimations().forEach((a) => a.finish()));
  await page.waitForFunction(() => document.getElementById("view").getAnimations().length === 0);
  await page.evaluate(() => render());
  assert.strictEqual(await animated(), 0, "a redraw of the same page animated");
  await page.evaluate(() => saveSettings({ motion_level: 0 }));
  await page.evaluate(() => go("settings"));
  assert.strictEqual(await animated(), 0, "a page change animated with motion Off");
  assert.deepStrictEqual(app.errors, []);
});
