// N22: a picture pasted (or dropped) into a Markdown cell is kept in the notebook as a Jupyter
// attachment, as JupyterLab and VS Code do. The real engine saves and reads the file back; the cell
// draws the picture only through mdImageData()'s data: URL.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ csp: true,
    engine: ["list_notebooks", "open_notebook", "save_notebook", "notebook_places", "compute_check", "kernel_variables", "engine_catalog"],
    mocks: { notebook_runs: () => ({ runs: [] }) } });
});
after(async () => { if (app) await app.close(); });

async function open(name, cells) {
  await page().evaluate(async ({ name, cells }) => {
    await rpc("save_notebook", { name, cells });
    await openNotebook(name);
  }, { name, cells });
  await page().waitForFunction((name) => state.notebook?.name === name, name);
}

// Pastes (or drops) files into the first Markdown cell's editor: [{type, b64}] or [{type, size}] (zeros).
const send = (kind, files, text = null) => page().evaluate(({ kind, files, text }) => {
  const cell = state.notebook.cells.find((c) => c.type === "markdown");
  enterEditMode(cell);
  const view = editorFor(cell);
  view.dispatch({ selection: { anchor: view.state.doc.length } });
  const dt = new DataTransfer();
  for (const f of files) {
    const bytes = f.b64 ? Uint8Array.from(atob(f.b64), (ch) => ch.charCodeAt(0)) : new Uint8Array(f.size);
    dt.items.add(new File([bytes], f.name || "clip", { type: f.type }));
  }
  if (text) dt.setData("text/plain", text);
  const e = kind === "paste" ? new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true })
    : new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true, clientX: 0, clientY: 0 });
  view.contentDOM.dispatchEvent(e);
}, { kind, files, text });
const cellNow = () => page().evaluate(() => {
  const c = state.notebook.cells.find((x) => x.type === "markdown");
  return { source: c.source, attachments: c.attachments };
});

test("a pasted PNG becomes an attachment, shows in the cell, saves and goes once its text is gone", { skip }, async () => {
  await open("Pasted", [{ type: "markdown", source: "# Notes\n\n" }]);
  await send("paste", [{ type: "image/png", b64: PNG }]);
  await page().waitForFunction(() => state.notebook.cells[0].source.includes("attachment:image.png"));
  let c = await cellNow();
  assert.strictEqual(c.source, "# Notes\n\n![image.png](attachment:image.png)");
  assert.deepStrictEqual(c.attachments, { "image.png": { "image/png": PNG } });

  // A second one gets the next free name; a drop works the same way.
  await send("drop", [{ type: "image/png", b64: PNG }]);
  await page().waitForFunction(() => state.notebook.cells[0].source.includes("attachment:image-2.png"));
  c = await cellNow();
  assert.deepStrictEqual(Object.keys(c.attachments), ["image.png", "image-2.png"]);

  // Rendered: the picture is a data: URL from the cell's own attachments.
  await page().evaluate(() => { const cell = state.notebook.cells[0]; mdFinish(cell); cell.editing = false; updateCell(cell); });
  const srcs = await page().$$eval(`.cell .md-view img.md-img`, (imgs) => imgs.map((i) => i.getAttribute("src")));
  assert.ok(srcs.length >= 1 && srcs.every((s) => s === `data:image/png;base64,${PNG}`), String(srcs));

  // Saved into the .ipynb and read back.
  await page().evaluate(() => saveNotebook());
  await page().evaluate(() => openNotebook("Pasted"));
  c = await cellNow();
  assert.deepStrictEqual(c.attachments, { "image.png": { "image/png": PNG }, "image-2.png": { "image/png": PNG } });

  // Removing the text (as an undo would) and saving drops the attachment from the file.
  await page().evaluate(async () => {
    const cell = state.notebook.cells[0];
    cell.source = cell.source.replace("![image-2.png](attachment:image-2.png)", "");
    markDirty();
    await saveNotebook();
    await openNotebook("Pasted");
  });
  c = await cellNow();
  assert.deepStrictEqual(c.attachments, { "image.png": { "image/png": PNG } });
  assert.deepStrictEqual(app.violations, []);
});

test("a picture that is too big or not an allowed type adds nothing; text still pastes", { skip }, async () => {
  await open("Too big", [{ type: "markdown", source: "Words" }]);
  await send("paste", [{ type: "image/png", size: 8 * 1024 * 1024 }]);
  await page().waitForFunction(() => /too big/.test(document.getElementById("toast").textContent));
  assert.strictEqual(await page().locator("#toast").innerText(),
    "This image is too big to add to the notebook. Images can be up to 7.5 MB.");
  let c = await cellNow();
  assert.strictEqual(c.source, "Words");
  assert.strictEqual(c.attachments, null);

  await send("paste", [{ type: "image/svg+xml", b64: "PHN2Zz48L3N2Zz4=" }]);
  await page().waitForFunction(() => /Only PNG/.test(document.getElementById("toast").textContent));
  c = await cellNow();
  assert.strictEqual(c.source, "Words");
  assert.strictEqual(c.attachments, null);

  await send("paste", [], " and more");
  await page().waitForFunction(() => state.notebook.cells[0].source === "Words and more");
  assert.strictEqual((await cellNow()).attachments, null);
});
