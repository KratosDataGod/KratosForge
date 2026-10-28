// The entity file editor on a Data mesh entity page (meta.js renderEntity): Tab types four spaces,
// and Esc, then Tab, moves focus on, so Tab never traps focus in the box (#928).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";

const ENTITY = {
  name: "silver.customers", layer: "silver", mode: "merge", watermark: "updated_at", owner: "ana@contoso.com",
  path: "metadata/entities/silver.customers.toml", text: "[entity]\nname = \"silver.customers\"\n",
  errors: [], columns: { id: "bigint", name: "string" }, keys: ["id"], surrogate_keys: [], derived: [], hook_adds: [],
  runs: [], custom: [], checks: 2, reads: ["bronze.customers"], impact: [], source: null,
};

let app;
const page = () => app.page;
const active = () => page().evaluate(() => {
  const el = document.activeElement;
  return el?.id || el?.getAttribute("aria-label") || el?.textContent?.trim().slice(0, 30) || el?.tagName;
});

before(async () => {
  if (skip) return;
  app = await openApp({
    mocks: {
      meta_project: () => ({ path: "C:/mesh", name: "Mesh", environments: {} }),
      meta_entity: () => structuredClone(ENTITY),
      meta_runs_all: () => ({ runs: [] }),
      meta_entity_shortcuts: () => [],
    },
  });
  await page().evaluate(() => openEntity("silver.customers"));
  await page().waitForSelector("#entity-toml");
});
after(async () => { await app?.close(); });

test("Tab in the entity file types four spaces and keeps focus there", { skip }, async () => {
  const box = page().locator("#entity-toml");
  await box.focus();
  await box.evaluate((t) => t.setSelectionRange(0, 0));
  await page().keyboard.press("Tab");
  assert.strictEqual(await active(), "entity-toml");
  assert.match(await box.inputValue(), /^ {4}\[entity\]/);
  assert.strictEqual(await box.getAttribute("aria-label"), "Entity file (TOML)");
});

test("Esc, then Tab, moves on; Shift+Tab goes back; typing after Esc indents again", { skip }, async () => {
  const box = page().locator("#entity-toml");
  await box.focus();
  await page().keyboard.press("Escape");
  await page().keyboard.press("Tab");
  assert.notStrictEqual(await active(), "entity-toml", "Esc, then Tab, leaves the box");
  await box.focus();
  await page().keyboard.press("Shift+Tab");
  assert.notStrictEqual(await active(), "entity-toml", "Shift+Tab leaves the box");
  await box.focus();
  await page().keyboard.press("Escape");
  await page().keyboard.press("ArrowRight");
  const before = (await box.inputValue()).length;
  await page().keyboard.press("Tab");
  assert.strictEqual(await active(), "entity-toml", "another key after Esc means Tab indents again");
  assert.strictEqual((await box.inputValue()).length, before + 4);
});

test("a click after Esc means Tab types spaces again", { skip }, async () => {
  const box = page().locator("#entity-toml");
  await box.focus();
  await page().keyboard.press("Escape");
  await box.click();
  const before = (await box.inputValue()).length;
  await page().keyboard.press("Tab");
  assert.strictEqual(await active(), "entity-toml");
  assert.strictEqual((await box.inputValue()).length, before + 4);
});

test("nothing on the page threw", { skip }, async () => {
  assert.deepStrictEqual(app.errors, []);
});
