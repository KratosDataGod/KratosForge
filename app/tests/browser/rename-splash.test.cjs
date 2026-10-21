// The one-time KratosForge announcement in a real browser (see harness.cjs for how to run it).
const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const SHOTS = process.env.FD_SCREENSHOTS;  // a folder: save what the splash looks like

test("the first start after the update shows the new name, once, and Escape closes it", { skip }, async () => {
  const app = await openApp({ storage: { "fd.rename.kratosforge.seen": "" } });
  try {
    const { page } = app;
    await page.waitForSelector(".overlay.splash");
    assert.strictEqual(await page.getAttribute(".overlay.splash", "role"), "dialog");
    assert.strictEqual(await page.getAttribute(".overlay.splash", "aria-modal"), "true");
    assert.match(await page.textContent("#splash-title"), /Fabric Desktop is now/);
    assert.strictEqual(await page.getAttribute(".splash-letters", "alt"), "KratosForge");
    assert.strictEqual(await page.evaluate(() => document.activeElement.textContent), "Enter the forge");
    // Tab stays inside: the button, the link, and back.
    await page.keyboard.press("Tab");
    assert.strictEqual(await page.evaluate(() => document.activeElement.className), "splash-link");
    await page.keyboard.press("Tab");
    assert.strictEqual(await page.evaluate(() => document.activeElement.textContent), "Enter the forge");
    if (SHOTS) {
      await page.waitForTimeout(2600);
      fs.mkdirSync(SHOTS, { recursive: true });
      await page.screenshot({ path: path.join(SHOTS, "splash.png") });
    }
    await page.keyboard.press("Escape");
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "topbar.png"), clip: { x: 0, y: 0, width: 720, height: 300 } });
    assert.strictEqual(await page.$(".overlay.splash"), null);
    assert.strictEqual(await page.evaluate(() => localStorage.getItem("fd.rename.kratosforge.seen")), "1");
    assert.strictEqual(await page.evaluate(() => showRenameSplash()), false);
    assert.strictEqual(await page.$(".overlay.splash"), null);
    assert.deepStrictEqual(app.errors, []);
  } finally { await app.close(); }
});

test("the button closes it, and the command palette shows it again", { skip }, async () => {
  const app = await openApp({ storage: { "fd.rename.kratosforge.seen": "" } });
  try {
    const { page } = app;
    await page.waitForSelector(".overlay.splash");
    await page.click(".splash-go");
    assert.strictEqual(await page.$(".overlay.splash"), null);
    await page.evaluate(() => showRenameSplash(() => {}, true));
    await page.waitForSelector(".overlay.splash");
    await page.keyboard.press("Enter");
    assert.strictEqual(await page.$(".overlay.splash"), null);
  } finally { await app.close(); }
});

test("with reduced motion nothing animates", { skip }, async () => {
  const app = await openApp({ storage: { "fd.rename.kratosforge.seen": "" } });
  try {
    const { page } = app;
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.waitForSelector(".overlay.splash");
    const anims = await page.evaluate(() => [".splash-emblem", ".splash-letters", ".splash-actions"]
      .map((s) => getComputedStyle(document.querySelector(s)).animationName));
    assert.deepStrictEqual(anims, ["none", "none", "none"]);
    assert.strictEqual(await page.evaluate(() => getComputedStyle(document.querySelector(".splash-embers")).display), "none");
  } finally { await app.close(); }
});
