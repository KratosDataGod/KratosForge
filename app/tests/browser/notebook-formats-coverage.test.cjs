// Notebook formats (notebook-formats.js), the paths notebook-formats leaves out: an export the engine
// says didn't carry everything over lists what it left out, the PDF page keeps only inline images, and a
// file over 64 MB is refused before it is read.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    save_notebook: () => ({ saved: true }), notebook_syntax: () => [], kernel_variables: () => [],
    notebook_export: () => ({ path: "C:/Users/me/Downloads/Sales.py",
      notes: [{ cell: 2, kind: "dropped", what: "The slider is left out." }, { cell: null, kind: "fabric", what: "The environment isn't kept." }] }),
  } });
  await page().evaluate(() => {
    loadIntoState({ name: "Sales", cells: [{ type: "code", cell_id: "c1", source: "x = 1", meta: {} }] });
    go("notebook");
  });
  app.errors.length = 0;
});
after(async () => { await app?.close(); });
beforeEach(() => { if (!skip) app.errors.length = 0; });

test("an export that left things out says so, cell by cell", { skip }, async () => {
  const p = page();
  await p.evaluate(() => { nbfExport("databricks"); });
  const dialog = p.getByRole("dialog", { name: "What Databricks source doesn't hold" });
  await dialog.waitFor();
  assert.match(await dialog.innerText(), /The file is saved\. 2 things in this notebook didn't carry over as it is:/);
  const rows = await dialog.locator("tbody tr").evaluateAll((trs) => trs.map((tr) => [...tr.cells].map((td) => td.textContent)));
  assert.deepStrictEqual(rows, [["2", "Not carried over", "The slider is left out."], ["Whole notebook", "Before running in Fabric", "The environment isn't kept."]]);
  await dialog.getByRole("button", { name: "OK" }).click();
  await dialog.waitFor({ state: "detached" });
  assert.deepStrictEqual(app.errors, []);
});

test("the PDF page keeps inline images and classes, and drops other sources and attributes", { skip }, async () => {
  const out = await page().evaluate(() => {
    const box = document.createElement("div");
    box.append(...nbfPrintNodes('<div class="cell out" id="x" onclick="alert(1)"><img src="data:image/png;base64,AAAA" alt="chart" width="3">'
      + '<img src="https://example.com/a.png" alt="far"><a href="https://example.com">link</a></div>'));
    return box.innerHTML;
  });
  assert.strictEqual(out, '<div class="nbfp-cell nbfp-out"><img src="data:image/png;base64,AAAA" alt="chart"><img alt="far"><a>link</a></div>');
  assert.deepStrictEqual(app.errors, []);
});

test("a file over 64 MB is refused before it is read", { skip }, async () => {
  const p = page();
  await p.evaluate(() => { nbfImportDialog(); });
  const input = p.getByLabel("Notebook file");
  await input.waitFor({ state: "attached" });
  await input.evaluate((el) => {
    const dt = new DataTransfer();
    dt.items.add(new File([new Uint8Array(64 * 1024 * 1024 + 1)], "huge.ipynb"));
    el.files = dt.files;
    el.dispatchEvent(new Event("change"));
  });
  await p.getByText("The file is larger than 64 MB, so it can't be imported.").waitFor();
  assert.ok(!app.calls.some((c) => c.method === "notebook_formats_import_check"), "the file isn't sent to be read");
  assert.ok(await p.getByRole("button", { name: "Import" }).isDisabled());
  await p.evaluate(() => closeDialog());
  assert.deepStrictEqual(app.errors, []);
});
