// Create > Notebook from a template and Open replay bundle (issue: "nothing happens"), in a real
// browser (see harness.cjs for how to run it).
const { test } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const toast = (page) => page.waitForSelector("#toast:not([hidden])").then(() => page.$eval("#toast", (t) => t.textContent));

test("both tiles open their dialogs with the current engine", { skip }, async () => {
  const app = await openApp({ engine: ["wizard_templates", "replay_bundles_list"] });
  try {
    await app.page.evaluate(() => go("create"));
    await app.page.click("text=Notebook from a template");
    await app.page.waitForSelector(".wizard-template");
    await app.page.evaluate(() => closeDialog());
    await app.page.click("text=Open replay bundle");
    await app.page.waitForSelector(".replay-dialog");
    assert.deepStrictEqual(app.errors, []);
  } finally { await app.close(); }
});

test("an engine from an older build says so instead of doing nothing", { skip }, async () => {
  const app = await openApp({ mocks: { wizard_templates: () => ({ templates: [], fields: {}, modes: ["overwrite", "append"] }) } });
  try {
    await app.page.evaluate(() => go("create"));
    await app.page.click("text=Notebook from a template");
    assert.match(await toast(app.page), /engine is older than this app/);
    assert.strictEqual(await app.page.$(".wizard"), null);
  } finally { await app.close(); }
});

test("a click whose handler fails shows the error", { skip }, async () => {
  const app = await openApp();
  try {
    await app.page.evaluate(() => { setTimeout(() => { throw new Error("boom"); }); Promise.reject(new Error("bang")); });
    assert.match(await toast(app.page), /Something didn't work: (boom|bang)/);
  } finally { await app.close(); }
});
