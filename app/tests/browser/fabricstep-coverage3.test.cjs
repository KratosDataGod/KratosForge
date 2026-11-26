// The Fabric step (fabricstep.js) beyond fabricstep-coverage and the other Fabric step tests: a check the engine
// can't answer (before Run all and when the cell runs), Cancel in Insert V-Order cell, the confirm when the check
// doesn't name the workspace and Stop here, and the step's output line: a status the app doesn't name, the run's
// link, each table's V-Order chip with how many files were checked, and Rewrite all files when writing the cell
// fails. Every engine call is mocked; nothing reaches Fabric.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const LH = { workspace_id: "w1", workspace_name: "Sales Dev", lakehouse_id: "lh1", name: "LH_Gold" };
const STEP = { type: "code", cell_id: "s", source: "spark.sql('OPTIMIZE dbo.sales VORDER')", meta: { fabric_step: true } };

let checkFails, writeFails;
before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    save_notebook: () => ({ saved: true }),
    cached_schemas: () => null,
    list_tables: () => ({ dbo: ["sales"] }),
    notebook_vorder_cell: (p) => {
      if (p.picked && writeFails) throw new Error("The cell couldn't be written");
      return p.picked ? { source: `spark.sql("OPTIMIZE ${p.picked.join(", ")} VORDER")` } : { candidates: ["dbo.sales"] };
    },
    notebook_fabric_step_check: () => {
      if (checkFails) throw new Error("The engine couldn't read the notebook");
      return { ok: true, item_name: "Gold (Fabric step)", workspace_name: "Sales Dev", workspace_id: "w1", lakehouse: "LH_Gold", tables: ["dbo.sales"] };
    },
  } });
});
after(async () => { await app?.close(); });
beforeEach(async () => {
  if (skip) return;
  checkFails = false;
  writeFails = false;
  await page().evaluate((cells) => {
    document.getElementById("dialog-root").replaceChildren();
    toastClose();
    state.nbTables = {}; state.nbSchemas = {};
    loadIntoState({ name: `Gold ${Math.random().toString(36).slice(2, 7)}`, cells: structuredClone(cells), lakehouse: { workspace_id: "w1", workspace_name: "Sales Dev", lakehouse_id: "lh1", name: "LH_Gold" } });
    go("notebook");
  }, [STEP]);
  await page().locator(".cell").first().waitFor();
  app.errors.length = 0;
});
const toastSays = (text) => page().waitForFunction((t) => document.getElementById("toast").textContent === t, text);
const dialog = () => page().locator("#dialog-root .dialog");

test("a check the engine can't answer stops Run all before any cell runs, and fails the cell when it runs", { skip }, async () => {
  checkFails = true;
  const out = await page().evaluate(async () => {
    const nb = state.notebook;
    const none = await fabricStepPrecheck(nb, [{ meta: {} }]);
    const pre = await fabricStepPrecheck(nb, nb.cells);
    const toast = document.getElementById("toast").textContent;
    const ran = await runFabricStep(nb.cells[0], nb);
    return { none, pre, toast, ran, cell: nb.cells[0].outputs[0].evalue };
  });
  assert.deepStrictEqual(out, { none: true, pre: false, toast: "The engine couldn't read the notebook", ran: false,
    cell: "The engine couldn't read the notebook" });
  assert.deepStrictEqual(app.errors, []);
});

test("Cancel in Insert V-Order cell leaves the cell as it was", { skip }, async () => {
  const done = page().evaluate(() => insertVOrderCell(state.notebook.cells[0]));
  await dialog().getByRole("checkbox", { name: "dbo.sales" }).waitFor();
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await done;
  assert.strictEqual(await page().evaluate(() => state.notebook.cells[0].source), STEP.source);
  assert.deepStrictEqual(app.errors, []);
});

test("the confirm names the workspace by its id when the check has no name, and Stop here answers no", { skip }, async () => {
  const answer = page().evaluate(() => stepConfirm(state.notebook, { item_name: "Gold (Fabric step)", workspace_id: "w-123", lakehouse: "LH_Gold", tables: [] }));
  await dialog().waitFor();
  const text = await dialog().innerText();
  assert.match(text, /on the lakehouse's workspace's Fabric capacity/);
  assert.match(text, /Workspace\s+w-123/);
  await dialog().getByRole("button", { name: "Stop here" }).click();
  assert.strictEqual(await answer, false);
  assert.deepStrictEqual(app.errors, []);
});

test("the output line names an unknown status, links the run, counts checked files, and says when Rewrite all fails", { skip }, async () => {
  await page().evaluate(() => {
    window.__opened = [];
    window.__openInFabric = openInFabric;
    openInFabric = (url) => { window.__opened.push(url); };
    const cell = state.notebook.cells[0];
    cell.status = "ok";
    cell.outputs = [{ type: "fabric_step", status: "Paused", url: "https://app.fabric.microsoft.com/run/9", seconds: 12.4, tables: [
      { table: "dbo.sales", v_ordered: false, checked: 5, files: 9, v_ordered_files: 3 },
      { table: "dbo.items", v_ordered: null },
      { table: "dbo.days", v_ordered: true, checked: 4, files: 4, v_ordered_files: 4 }] }];
    updateResults(cell);
  });
  const out = page().locator(".cell").first().locator(".fabric-step-output");
  await out.waitFor();
  assert.match(await out.innerText(), /^Paused · 12 s/);
  const chips = out.locator(".vorder-chip");
  assert.deepStrictEqual(await chips.allInnerTexts(), ["dbo.sales not V-Ordered", "dbo.items not checked", "dbo.days V-Ordered"]);
  assert.deepStrictEqual(await chips.evaluateAll((els) => els.map((e) => e.title)), ["3 of 5 newest (of 9) files V-Ordered", "", "4 of 4 files V-Ordered"]);
  await out.getByRole("link", { name: "Open the run in Fabric" }).click();
  assert.deepStrictEqual(await page().evaluate(() => window.__opened), ["https://app.fabric.microsoft.com/run/9"]);
  writeFails = true;
  await out.getByRole("button", { name: "Rewrite all files" }).click();
  await page().waitForFunction(() => /^Could not write the V-Order cell/.test(document.getElementById("toast").textContent));
  assert.strictEqual(await page().evaluate(() => state.notebook.cells[0].source), STEP.source);
  await page().evaluate(() => { openInFabric = window.__openInFabric; });
  assert.deepStrictEqual(app.errors, []);
});
