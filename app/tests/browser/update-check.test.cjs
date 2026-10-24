// Check for updates from the account menu, in a real browser (see harness.cjs for how to run it).
const { test } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const RUNNING = "5d69f21aa1b2c3d4e5f60718293a4b5c6d7e8f90";
const NEWER = "a41c0e9aa1b2c3d4e5f60718293a4b5c6d7e8f90";
const PAGE = "https://github.com/KratosDataGod/KratosForge/releases/tag/latest";

// The shell's check_for_update answers with `latest`; update_app and open_link are recorded.
async function withShell(page, latest, autoUpdate = true) {
  await page.evaluate(({ RUNNING, latest, autoUpdate }) => {
    const invoke = window.__TAURI__.core.invoke;
    window.__TAURI__.core.invoke = async (cmd, args) => {
      if (cmd === "check_for_update") {
        window.__fdShellCalls.push({ cmd, args });
        return { commit: RUNNING, auto_update: autoUpdate, latest };
      }
      return invoke(cmd, args);
    };
  }, { RUNNING, latest, autoUpdate });
}

const shellCalls = (page, cmd) => page.evaluate((cmd) => window.__fdShellCalls.filter((c) => c.cmd === cmd).map((c) => c.args), cmd);

test("a newer build offers Install and restart, which hands that build to the updater", { skip }, async () => {
  const app = await openApp();
  try {
    const { page } = app;
    await withShell(page, { commit: NEWER, channel: "preview", tag: "preview", page: PAGE });
    await page.click("#account");
    await page.click("#menu >> text=Check for updates");
    await page.waitForSelector(".dialog >> text=Build a41c0e9 is out");
    assert.match(await page.textContent(".dialog .about"), /This build5d69f21Latest on Preview.*a41c0e9/);
    await page.click(".dialog >> text=Download manually");
    assert.deepStrictEqual(await shellCalls(page, "open_link"), [{ url: PAGE }]);
    await page.click(".dialog >> text=Install and restart");
    await page.waitForFunction(() => window.__fdShellCalls.some((c) => c.cmd === "update_app"));
    assert.deepStrictEqual(await shellCalls(page, "update_app"), [{ commit: NEWER }]);
    assert.deepStrictEqual(app.errors, []);
  } finally {
    await app.close();
  }
});

test("nothing newer says so and offers only the release page; a managed PC never installs", { skip }, async () => {
  const app = await openApp();
  try {
    const { page } = app;
    await withShell(page, { commit: RUNNING, channel: "preview", tag: "preview", page: PAGE });
    await page.evaluate(() => checkForUpdates());
    await page.waitForSelector(".dialog >> text=You're up to date");
    assert.strictEqual(await page.$(".dialog >> text=Install and restart"), null);
    assert.ok(await page.$(".dialog >> text=Download manually"));

    await withShell(page, { commit: NEWER, channel: "stable", tag: "stable", page: PAGE }, false);
    await page.evaluate(() => checkForUpdates());
    await page.waitForSelector(".dialog >> text=Your organization rolls out new builds");
    assert.strictEqual(await page.$(".dialog >> text=Install and restart"), null);
    assert.deepStrictEqual(await shellCalls(page, "update_app"), []);
    assert.deepStrictEqual(app.errors, []);
  } finally {
    await app.close();
  }
});
