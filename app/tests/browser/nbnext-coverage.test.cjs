// The notebook's actions and next step (nbnext.js), the paths action-first and the notebook tests leave
// out: Run the selected cell, Restart the kernel and run all, Validate, Change publish workspace...
// under More, and the next-step line naming Validate's errors and warnings. Each action is run the way
// the command bar runs it (runAction); the functions it calls are recorded instead of run.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const CELLS = [{ type: "code", cell_id: "c1", source: "a = 1", meta: {} }, { type: "code", cell_id: "c2", source: "b = 2", meta: {} }];
const SPIED = ["runCell", "restartKernel", "runAll", "openValidate", "ensureSignedIn", "chooseNotebookWorkspace"];

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { save_notebook: () => ({ saved: true }) } });
  await page().evaluate(() => { state.settings = { ...(state.settings || {}), show_preview: { value: true } }; });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  await page().evaluate(([cells, spied]) => {
    loadIntoState({ name: "Next", cells: structuredClone(cells) });
    state.kernel = "idle";
    go("notebook");
    window.__done = [];
    window.__real = window.__real || {};
    for (const n of spied) {
      window.__real[n] ||= window[n];
      window[n] = (...a) => { window.__done.push([n, ...a.map((x) => x?.cell_id || x?.name || x)]); return n === "ensureSignedIn" ? Promise.resolve(true) : undefined; };
    }
  }, [CELLS, SPIED]);
  app.errors.length = 0;
});

const done = () => page().evaluate(() => window.__done);

test("Run the selected cell runs it; Restart and run all restarts first; Validate opens Validate", { skip }, async () => {
  await page().evaluate(() => { state.selected = state.notebook.cells[1].id; });
  await page().evaluate(() => runAction("notebook.run_cell"));
  await page().evaluate(() => runAction("notebook.restart_run_all"));
  await page().evaluate(() => runAction("notebook.validate"));
  assert.deepStrictEqual(await done(), [["runCell", "c2"], ["restartKernel"], ["runAll"], ["openValidate", "Next"]]);
  assert.deepStrictEqual(app.errors, []);
});

test("Change publish workspace... under More signs in, then asks for the workspace", { skip }, async () => {
  const items = await page().evaluate(() => actionRegistry.actions.get("notebook.publish").moreAfter(actionContext()).map((i) => i.label));
  assert.deepStrictEqual(items, ["Change publish workspace..."]);
  await page().evaluate(() => actionRegistry.actions.get("notebook.publish").moreAfter(actionContext())[0].action());
  await page().waitForFunction(() => window.__done.length === 2);
  assert.deepStrictEqual(await done(), [["ensureSignedIn"], ["chooseNotebookWorkspace", "Next"]]);
  assert.deepStrictEqual(app.errors, []);
});

test("after a run, the next step names Validate's errors and warnings and offers Review errors", { skip }, async () => {
  await page().evaluate(() => {
    const nb = state.notebook;
    nb.validate = { ...(nb.validate || {}), record: { counts: { error: 2, warning: 1 } } };
    nb.lastRun = { whole: true, ran: 2, of: 2, seconds: 1.25 };
    refreshNbNextLine(nb);
  });
  const line = page().locator(".nb-next");
  await line.waitFor();
  assert.strictEqual(await line.locator('[data-recommendation="notebook.findings"]').innerText(), "Validate found 2 errors and 1 warning.");
  assert.match(await line.locator("b").innerText(), /^Ran 2 cells on this PC in 1\.3 s\.$/);
  await line.getByRole("button", { name: "Review errors" }).click();
  assert.deepStrictEqual(await done(), [["openValidate", "Next"]]);
  assert.deepStrictEqual(app.errors, []);
});
