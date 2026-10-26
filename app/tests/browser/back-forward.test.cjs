// UX-008: Back and Forward in the top bar, Alt+Left and Alt+Right, and the app reopening the last
// page (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const view = () => page().evaluate(() => state.view);
const rail = (label) => page().locator("#rail-main .rail-item, #rail-bottom .rail-item", { hasText: label }).first();

before(async () => {
  if (skip) return;
  // Served over http, as the shell serves the app, not from file://. Chromium's localStorage for
  // file:// pages sometimes doesn't survive a reload under load (the reloaded page reads none of it),
  // which made "a restart reopens the last page" fail now and then with no app change involved.
  app = await openApp({ csp: true });
  await page().waitForSelector("#rail-main .rail-item");
  await page().waitForFunction(() => nav.restored);
});

after(async () => { await app?.close(); });

test("Back and Forward buttons walk the pages you opened", { skip }, async () => {
  assert.strictEqual(await view(), "home");
  assert.ok(await page().isDisabled("#nav-back"), "Back is enabled with nowhere to go");
  assert.strictEqual(await page().getAttribute("#nav-back", "title"), "Back (Alt+Left)");
  assert.strictEqual(await page().getAttribute("#nav-forward", "title"), "Forward (Alt+Right)");
  await rail("Create").click();
  await rail("Settings").click();
  assert.strictEqual(await view(), "settings");
  await page().click("#nav-back");
  assert.strictEqual(await view(), "create");
  assert.strictEqual(await page().locator("#rail-main .rail-item[aria-current=page]").textContent(), "Create");
  await page().click("#nav-forward");
  assert.strictEqual(await view(), "settings");
  assert.ok(await page().isDisabled("#nav-forward"));
});

test("Alt+Left and Alt+Right go back and forward, but not while typing", { skip }, async () => {
  await page().evaluate(() => document.activeElement?.blur());
  await page().keyboard.press("Alt+ArrowLeft");
  assert.strictEqual(await view(), "create");
  await page().keyboard.press("Alt+ArrowRight");
  assert.strictEqual(await view(), "settings");
  await page().evaluate(() => {
    const input = h("input", { id: "typing" });
    document.getElementById("view").append(input);
    input.focus();
  });
  await page().keyboard.press("Alt+ArrowLeft");
  assert.strictEqual(await view(), "settings", "Alt+Left navigated while typing in a box");
});

test("Back after Open in Fabric returns to the page before", { skip }, async () => {
  await page().evaluate(() => openInFabric("monitor"));
  assert.strictEqual(await view(), "fabric");
  await page().click("#nav-back");
  assert.strictEqual(await view(), "settings");
});

test("a restart reopens the last page", { skip }, async () => {
  await rail("Create").click();
  await page().reload();
  await page().waitForFunction(() => typeof nav !== "undefined" && nav.restored);
  assert.strictEqual(await view(), "create");
  assert.strictEqual(await page().locator("#rail-main .rail-item[aria-current=page]").textContent(), "Create");
  assert.ok(await page().isDisabled("#nav-back"), "a fresh start has no history to go back to");
  assert.deepStrictEqual(app.errors, []);
  assert.deepStrictEqual(app.violations, [], "the shell's Content Security Policy blocked something");
});
