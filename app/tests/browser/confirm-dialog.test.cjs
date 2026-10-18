// UX-018: confirmDialog in a real browser (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;

before(async () => { if (!skip) app = await openApp(); });
after(async () => { if (app) await app.close(); });

// Opens a confirmation and returns a handle whose answer resolves when the dialog does.
const ask = (args) => page().evaluate((a) => {
  window.__answer = undefined;
  confirmDialog(...a).then((v) => { window.__answer = v; });
}, args);
const answer = () => page().waitForFunction(() => window.__answer !== undefined).then(() => page().evaluate(() => window.__answer));

test("a plain confirmation keeps the navy OK", { skip }, async () => {
  await ask(["Run the query again?", "It starts over.", "Run"]);
  assert.ok(await page().$(".dialog-actions .tool.primary"));
  assert.strictEqual(await page().$(".dialog-actions .danger-solid"), null);
  await page().click(".dialog-actions .tool.primary");
  assert.strictEqual(await answer(), true);
});

test("a destructive confirmation is red and starts on Cancel", { skip }, async () => {
  await ask(["Delete Orders?", "Its files are removed from OneLake.", "Delete", { danger: true }]);
  assert.ok(await page().$(".dialog-actions .danger-solid"));
  assert.strictEqual(await page().evaluate(() => document.activeElement.textContent), "Cancel");
  await page().keyboard.press("Enter");
  assert.strictEqual(await answer(), false);
});

test("Escape, the close button and a click outside answer Cancel", { skip }, async () => {
  await ask(["Delete Orders?", "x", "Delete", { danger: true }]);
  await page().keyboard.press("Escape");
  assert.strictEqual(await answer(), false);
  await ask(["Delete Orders?", "x", "Delete", { danger: true }]);
  await page().click('.dialog-head [aria-label="Close"]');
  assert.strictEqual(await answer(), false);
  await ask(["Delete Orders?", "x", "Delete", { danger: true }]);
  await page().mouse.click(5, 5);
  assert.strictEqual(await answer(), false);
});

test("typeToConfirm keeps the action off until the name is typed", { skip }, async () => {
  await ask(["Overwrite sales in Prod?", "The table is replaced.", "Overwrite", { danger: true, typeToConfirm: "sales" }]);
  const ok = ".dialog-actions .danger-solid";
  assert.ok(await page().$eval(ok, (b) => b.disabled));
  await page().fill(".dialog input", "sale");
  await page().keyboard.press("Enter");
  assert.ok(await page().$eval(ok, (b) => b.disabled));
  await page().fill(".dialog input", "sales");
  assert.ok(!(await page().$eval(ok, (b) => b.disabled)));
  await page().keyboard.press("Enter");
  assert.strictEqual(await answer(), true);
  assert.strictEqual(await page().$(".dialog"), null);
});
