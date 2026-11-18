// A cell whose MERGE, UPDATE, DELETE, VACUUM or RESTORE would change a Prod-marked workspace is refused by the
// engine (ProdConfirmNeeded, prod_writes_service.py). Run on its own, the cell offers the typed-name confirm, and
// the typed name lets it run again once. Cancel leaves the refusal as it is.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const calls = [];
let allowed = false;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    save_notebook: () => ({ saved: true }), notebook_syntax: () => [], kernel_variables: () => [],
    start_cell: ({ notebook }) => {
      calls.push(["start_cell", notebook]);
      if (allowed) { allowed = false; return { status: "ok", outputs: [{ type: "stream", text: "1 row deleted\n" }], version: 1, execution_count: 1 }; }
      return { status: "error", version: 1, execution_count: 1, outputs: [{ type: "error", ename: "ProdConfirmNeeded",
        evalue: "DELETE on dbo.sales would change Sales Prod, a Prod workspace, so it waits for you to type the workspace's name. Nothing was changed." }] };
    },
    poll_cell: () => ({ status: "ok", outputs: [], version: 2 }),
    notebook_prod_pending: ({ notebook }) => { calls.push(["pending", notebook]); return { workspace_id: "ws-1", workspace_name: "Sales Prod", operation: "DELETE", table: "dbo.sales" }; },
    notebook_prod_confirm: (p) => { calls.push(["confirm", p.notebook, p.workspace_id, p.confirm_name]); allowed = true; return { allowed: true }; },
  } });
  await page().evaluate(() => {
    loadIntoState({ name: "Cleanup", cells: [{ type: "code", cell_id: "c1", source: "spark.sql('DELETE FROM dbo.sales WHERE id = 1')", meta: {} }] });
    go("notebook");
  });
  await page().locator(".cell .cm-content").first().waitFor();
});
after(async () => { await app?.close(); });

test("Cancel leaves the refusal and runs nothing more", { skip }, async () => {
  const p = page();
  calls.length = 0;
  await p.evaluate(() => { runCell(state.notebook.cells[0]); });
  const dialog = p.getByRole("dialog", { name: "Change Sales Prod?" });
  await dialog.waitFor();
  assert.strictEqual(await dialog.getByRole("button", { name: "Run in Prod" }).isDisabled(), true);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await p.getByText("a Prod workspace, so it waits", { exact: false }).waitFor();
  assert.deepStrictEqual(calls.map((c) => c[0]), ["start_cell", "pending"]);
});

test("typing the workspace's name confirms it and runs the cell once more", { skip }, async () => {
  const p = page();
  calls.length = 0;
  await p.evaluate(() => { runCell(state.notebook.cells[0]); });
  const dialog = p.getByRole("dialog", { name: "Change Sales Prod?" });
  await dialog.waitFor();
  await dialog.getByText("DELETE on dbo.sales changes Sales Prod, a Prod workspace.", { exact: false }).waitFor();
  const box = dialog.getByLabel("Type Sales Prod to confirm");
  await box.fill("sales prod");
  assert.strictEqual(await dialog.getByRole("button", { name: "Run in Prod" }).isDisabled(), true);
  await box.fill("Sales Prod");
  if (axeSource) assert.deepStrictEqual(await axeFindings(p, "[role=dialog]"), []);
  await dialog.getByRole("button", { name: "Run in Prod" }).click();
  await p.getByText("1 row deleted").waitFor();
  assert.deepStrictEqual(calls, [["start_cell", "Cleanup"], ["pending", "Cleanup"],
    ["confirm", "Cleanup", "ws-1", "Sales Prod"], ["start_cell", "Cleanup"]]);
});
