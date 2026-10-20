// UX-019: the Keyboard shortcuts dialog, opened with "?", from the command bar and from Settings (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const dialog = () => page().locator("#dialog-root .dialog[aria-label='Keyboard shortcuts']");

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["app_settings"] });  // Settings needs real settings to draw its cards
  await page().waitForSelector("#rail-main .rail-item");
});

after(async () => { await app?.close(); });

test("? opens the list drawn from the shortcut registry, and Esc closes it", { skip }, async () => {
  await page().locator("#rail-main .rail-item").first().focus();
  await page().keyboard.press("?");
  await dialog().waitFor();
  const rows = await dialog().locator("tr[data-shortcut]").evaluateAll((trs) => trs.map((t) => t.dataset.shortcut));
  assert.deepStrictEqual(rows, await page().evaluate(() => shortcutRegistry.map((s) => s.id)));
  const ctrlK = await dialog().locator("tr[data-shortcut=palette] kbd").allTextContents();
  assert.deepStrictEqual(ctrlK, ["Ctrl", "K"]);
  assert.match(await dialog().textContent(), /notebook guide/);
  await page().keyboard.press("Escape");
  assert.strictEqual(await dialog().count(), 0);
});

test("? typed in a box stays a character", { skip }, async () => {
  await page().evaluate(() => document.getElementById("view").append(h("input", { id: "typing" })));
  await page().focus("#typing");
  await page().keyboard.press("?");
  assert.strictEqual(await page().inputValue("#typing"), "?");
  assert.strictEqual(await dialog().count(), 0);
  await page().evaluate(() => document.getElementById("typing").remove());
});

test("the command bar and Settings open it too", { skip }, async () => {
  await page().keyboard.press("Control+k");
  await page().locator("#palette input").fill("keyboard");
  await page().locator(".palette-item", { hasText: "Keyboard shortcuts" }).click();
  await dialog().waitFor();
  await page().locator("#dialog-root button", { hasText: "Close" }).last().click();
  assert.strictEqual(await dialog().count(), 0);

  await page().evaluate(() => go("settings"));
  await page().locator("button", { hasText: "Keyboard shortcuts" }).click();
  await dialog().waitFor();
  await page().keyboard.press("Escape");
  assert.strictEqual(await dialog().count(), 0);
  assert.deepStrictEqual(app.errors, []);
});
