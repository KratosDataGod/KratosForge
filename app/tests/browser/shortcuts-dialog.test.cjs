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
  assert.deepStrictEqual(rows, await page().evaluate(() => shortcutGroups().flatMap(([, list]) => list).filter((s) => s.id).map((s) => s.id)));
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

test("a page's own keys show only on that page", { skip }, async () => {
  const labels = () => dialog().locator("h3").allTextContents();
  await page().evaluate(() => { state.view = "source"; shortcutsDialog(); });
  assert.ok((await labels()).includes("Source control"));
  assert.ok(!(await labels()).includes("Entity page"));
  await page().keyboard.press("Escape");
  await page().evaluate(() => { state.view = "home"; shortcutsDialog(); });
  assert.deepStrictEqual(await labels(), ["Anywhere", "Moving around", "Menus and tabs"]);
  await page().keyboard.press("Escape");
});

test("a notebook lists its cell, several-cell and find keys, with Ctrl+S in the same Notebook group", { skip }, async () => {
  const labels = () => dialog().locator("h3").allTextContents();
  await page().evaluate(() => { state.view = "notebook"; shortcutsDialog(); });
  const shown = await labels();
  for (const g of ["Notebook", "Notebook: several cells", "Notebook: find and replace"]) assert.ok(shown.includes(g), g);
  assert.strictEqual(shown.filter((g) => g === "Notebook").length, 1);
  const text = await dialog().textContent();
  assert.match(text, /Save the notebook/);
  assert.match(text, /Run the cell and select the next one/);
  const notebookRows = await dialog().locator("h3", { hasText: /^Notebook$/ }).locator("xpath=following-sibling::*[1]").textContent();
  assert.match(notebookRows, /run only the selected text/);
  assert.doesNotMatch(notebookRows, /Merge the selected cells/);
  assert.match(text, /merge/i);
  await page().keyboard.press("Escape");
  await page().evaluate(() => { state.view = "home"; });
  assert.deepStrictEqual(app.errors, []);
});

test("Alt+Left and Alt+Right (Back and Forward) are listed on every page", { skip }, async () => {
  await page().evaluate(() => { state.view = "home"; shortcutsDialog(); });
  const row = dialog().locator("tr", { hasText: "Go back or forward" });
  assert.deepStrictEqual(await row.locator("kbd").allTextContents(), ["Alt", "Left", "Alt", "Right"]);
  await page().keyboard.press("Escape");
});
