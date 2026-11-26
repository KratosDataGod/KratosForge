// The notebook's Best practices panel (nbpractices.js), the paths notebook-practices leaves out:
// policies that can't be read (every rule on, said why), a check that fails, a policy that tunes the
// rules with nothing found, the palette's Show best practices, and leaving the notebook stopping the
// checks. The engine's replies are mocked; what the person sees is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const CELLS = [{ type: "code", cell_id: "c1", source: "a = 1", meta: {} }];

// Set before the app opens.
const db = { settingsFail: null, checkFail: null };
let app;
const page = () => app.page;
const pane = () => page().locator(".practicespane");

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    governance_practice_settings: () => { if (db.settingsFail) throw new Error(db.settingsFail); return { settings: {}, policies: ["Contoso standard"], note: "" }; },
    notebook_practices: () => { if (db.checkFail) throw new Error(db.checkFail); return { findings: [] }; },
    learn_dismissals: () => [],
    learn_rule_guides: () => ({}),
    learn_team_notes: () => ({ notes: {} }),
  } });
  await page().evaluate(() => { state.settings = { ...(state.settings || {}), show_preview: { value: true } }; });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  Object.assign(db, { settingsFail: null, checkFail: null });
  await page().evaluate((cells) => {
    loadIntoState({ name: "Checks", cells: structuredClone(cells) });
    state.ribbonTab = "view";
    go("notebook");
  }, CELLS);
  await page().locator("[data-cell]").first().waitFor();
  app.calls.length = 0;
  app.errors.length = 0;
});

test("policies that can't be read leave every rule on and say why; a check that fails says why", { skip }, async () => {
  db.settingsFail = "The policy file isn't valid YAML.";
  db.checkFail = "The engine stopped.";
  await page().locator("button[data-practices]").click();
  await pane().getByText("The policies couldn't be read, so every rule is on as built: The policy file isn't valid YAML.").waitFor();
  await pane().getByText("Couldn't check this notebook: The engine stopped.").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("a policy that tunes the rules is named, and nothing found says every rule passes", { skip }, async () => {
  await page().evaluate(() => { features.commands.find((c) => c.label === "Show best practices for this notebook").run(); });
  await pane().getByText("Nothing to look at. Every rule that's on passes.").waitFor();
  await pane().getByText("Tuned by Contoso standard: a rule a policy turns off isn't shown.").waitFor();
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "notebook_practices").map((c) => c.params.sources), [["a = 1"]]);
  assert.deepStrictEqual(app.errors, []);
});

test("leaving the notebook stops the checks as you type", { skip }, async () => {
  await page().locator("button[data-practices]").click();
  await pane().getByText("Nothing to look at.", { exact: false }).waitFor();
  await page().evaluate(() => { window.__nb = state.notebook; go("home"); });
  // The next tick of the 1.5 s check finds the notebook gone and closes the panel.
  await page().waitForFunction(() => window.__nb.practices.open === false, null, { timeout: 10000 });
  const checks = app.calls.filter((c) => c.method === "notebook_practices").length;
  assert.strictEqual(checks, 1);
  assert.deepStrictEqual(app.errors, []);
});
