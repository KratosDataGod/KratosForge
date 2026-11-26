// The new notebook wizard (wizard.js) beyond notebook-wizard.test.cjs: a changed notebook that isn't saved keeps
// the wizard closed, templates that can't be listed, a field written over several lines, a template that writes
// no table, a lakehouse already attached, the proof contract's wording, and a notebook created without a name.
// The engine's wizard calls are mocked in wizard.py's shapes; nothing reaches Fabric.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const LH = { workspace_id: "w1", workspace_name: "Sales Dev", lakehouse_id: "lh1", name: "LH_Gold" };
// wizard.py templates(): the gallery, its groups, the choice lists, which fields take several lines, and each field's label.
const TEMPLATES = {
  templates: [{ id: "run_many", title: "Run notebooks in order", text: "Runs other notebooks one after another.", group: "orchestrate", fields: ["notebooks"] }],
  groups: { orchestrate: "Orchestrate" }, choices: {}, multiline: ["notebooks"], fields: {},
};

let listFails;
before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    save_notebook: () => { throw new Error("The disk is full"); },
    wizard_templates: () => { if (listFails) throw new Error("The templates couldn't be read"); return TEMPLATES; },
    wizard_preview: () => ({ cells: [{ type: "markdown", source: "# Run in order" }, { type: "code", source: "run('Load orders')" }], proof: { tables: {} } }),
    wizard_create: ({ name }) => ({ name: name || "Notebook 7", cells: [{ type: "code", source: "run('Load orders')", meta: {} }] }),
  } });
});
after(async () => { await app?.close(); });
beforeEach(async () => {
  if (skip) return;
  listFails = false;
  await page().evaluate(() => { closeDialog(); toastClose(); state.notebook = null; go("home"); });
  app.errors.length = 0;
});
const dialog = () => page().getByRole("dialog", { name: /New notebook from a template/ });

test("the proof contract says what it matches, what it ignores, and when there is none", { skip }, async () => {
  const out = await page().evaluate(() => [
    wizardContract({}),
    wizardContract({ "silver.orders": { keys: [], ignore: ["loaded_at"] }, "silver.items": { keys: ["id"], ignore: [] } }),
  ]);
  assert.deepStrictEqual(out, ["This notebook writes no tables, so there is no proof contract.",
    "Proof contract saved in the notebook: rows of silver.orders are compared whole, ignoring loaded_at; rows of silver.items are matched by id."]);
  assert.deepStrictEqual(app.errors, []);
});

test("a changed notebook that can't be saved keeps the wizard closed; templates that can't be listed say so", { skip }, async () => {
  await page().evaluate(() => { loadIntoState({ name: "Dirty", cells: [{ type: "code", source: "x", meta: {} }] }); go("notebook"); state.notebook.dirty = true; });
  await page().evaluate(() => openWizard());
  assert.strictEqual(await dialog().count(), 0);
  assert.strictEqual(app.calls.filter((c) => c.method === "wizard_templates").length, 0);
  await page().evaluate(() => { state.notebook.dirty = false; toastClose(); });
  listFails = true;
  await page().evaluate(() => openWizard());
  await page().waitForFunction(() => /^Could not open the wizard/.test(document.getElementById("toast").textContent));
  assert.strictEqual(await dialog().count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("a several-line field, no table written, an attached lakehouse, and a notebook created without a name", { skip }, async () => {
  await page().evaluate((lh) => { loadIntoState({ name: "Has data", lakehouse: lh, cells: [{ type: "code", source: "x", meta: {} }] }); go("notebook"); }, LH);
  await page().evaluate(() => openWizard());
  await dialog().getByRole("button", { name: /Run notebooks in order/ }).click();
  const area = dialog().locator("textarea");
  await area.fill("Load orders\nClean orders");
  assert.strictEqual(await area.getAttribute("placeholder"), "Load orders\nClean orders");
  assert.match(await dialog().innerText(), /Attached to LH_Gold\. Table names are checked when the notebook runs\./);
  assert.strictEqual(await dialog().getByText("Columns to ignore when a proof compares runs").count(), 0, "it writes no table");
  await dialog().getByRole("button", { name: "Next: review" }).click();
  await dialog().getByText("This notebook writes no tables, so there is no proof contract.").waitFor();
  await dialog().getByRole("button", { name: "Create notebook" }).click();
  await page().waitForFunction(() => state.view === "notebook" && state.notebook?.name === "Notebook 7");
  const made = app.calls.filter((c) => c.method === "wizard_create").at(-1).params;
  assert.deepStrictEqual(made, { template: "run_many", values: { notebooks: "Load orders\nClean orders" }, lakehouse_ref: LH, name: null });
  assert.deepStrictEqual(app.errors, []);
});
