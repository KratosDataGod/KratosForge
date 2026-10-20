// The forge splash after an update, in a real browser (see harness.cjs for how to run it).
const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const SHOTS = process.env.FD_SCREENSHOTS;  // a folder: save what the splash looks like

const history = [
  { date: "2026-10-20", title: "Pipelines run notebook steps", pr: 319, commit: "c4" },
  { date: "2026-10-20", title: "SCD Type 2 templates", pr: 316, commit: "c3" },
  { date: "2026-10-19", title: "One shared keys database for every environment", pr: 293, commit: "c2" },
  { date: "2026-10-19", title: "Remove asks before it deletes a schedule", pr: 317, commit: "c1" },
];

// The harness starts on a local build; this is the first start on build c4, after c2.
async function updated(app, seen = "c2") {
  await app.page.evaluate(({ history, seen }) => {
    localStorage.setItem("fd.whatsnew.seen", seen);
    Object.assign(state, { commit: "c4", build: "c4c4c4c", release: { commit: "c4", notes: "Pipelines run notebook steps", history } });
    return showRenameSplash(() => showWhatsNew()) || showUpdateSplash();
  }, { history, seen });
}

test("the first start on a new build shows the forge with what's new since the last build, once", { skip }, async () => {
  const app = await openApp();
  try {
    const { page } = app;
    await updated(app);
    await page.waitForSelector(".overlay.splash");
    assert.match(await page.textContent("#splash-title"), /Updated to build c4c4c4c/);
    assert.deepStrictEqual(await page.$$eval(".splash-news li", (l) => l.map((e) => e.textContent)),
      ["Pipelines run notebook steps", "SCD Type 2 templates"]);
    assert.strictEqual(await page.evaluate(() => document.activeElement.textContent), "Enter the forge");
    if (SHOTS) {
      await page.waitForTimeout(2600);
      fs.mkdirSync(SHOTS, { recursive: true });
      await page.screenshot({ path: path.join(SHOTS, "update-splash.png") });
    }
    await page.keyboard.press("Escape");
    assert.strictEqual(await page.$(".overlay.splash"), null);
    assert.strictEqual(await page.evaluate(() => localStorage.getItem("fd.whatsnew.seen")), "c4");
    assert.strictEqual(await page.evaluate(() => showUpdateSplash()), false);
    assert.strictEqual(await page.$(".overlay"), null, "neither the splash nor What's new shows twice");
    assert.deepStrictEqual(app.errors, []);
  } finally { await app.close(); }
});

test("See everything new opens What's new", { skip }, async () => {
  const app = await openApp();
  try {
    const { page } = app;
    await updated(app);
    await page.click(".splash-link");
    await page.waitForSelector(".whatsnew-history");
    assert.strictEqual(await page.$(".overlay.splash"), null);
  } finally { await app.close(); }
});

test("turned off on the splash, the next update shows the small What's new window", { skip }, async () => {
  const app = await openApp();
  try {
    const { page } = app;
    await updated(app);
    await page.click(".splash-optout input");
    await page.click(".splash-go");
    assert.strictEqual(await page.evaluate(() => localStorage.getItem("fd.updatesplash.off")), "1");
    await updated(app, "c3");
    assert.strictEqual(await page.$(".overlay.splash"), null);
    await page.waitForSelector(".whatsnew-history");
    // Asked from the command palette, it shows anyway.
    await page.evaluate(() => { closeDialog(); showUpdateSplash(true); });
    await page.waitForSelector(".overlay.splash");
  } finally { await app.close(); }
});


test("a first start on a new install shows no update splash, and the next build does", { skip }, async () => {
  const app = await openApp();
  try {
    const { page } = app;
    await updated(app, "");
    assert.strictEqual(await page.$(".overlay"), null);
    assert.strictEqual(await page.evaluate(() => localStorage.getItem("fd.whatsnew.seen")), "c4");
    await page.evaluate(({ history }) => {
      Object.assign(state, { commit: "c5", release: { commit: "c5", notes: "", history: [{ title: "Next", commit: "c5" }, ...history] } });
      return showUpdateSplash();
    }, { history });
    await page.waitForSelector(".overlay.splash");
    assert.deepStrictEqual(await page.$$eval(".splash-news li", (l) => l.map((e) => e.textContent)), ["Next"]);
  } finally { await app.close(); }
});
