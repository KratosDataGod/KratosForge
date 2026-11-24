// IntelliSense in Python cells (nbintel.js) beyond notebook-intellisense.test.cjs: a hover while the
// kernel can't answer shows no card, the highlighted completion's docs (and none when there are no
// docs), and a syntax error the engine places between two characters still gets an underline.
// kernel_inspect and notebook_syntax are mocked.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
let inspect;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    save_notebook: () => ({ saved: true }),
    kernel_inspect: (p) => inspect(p),
    notebook_syntax: ({ source }) => (source.includes("print(")
      ? [{ line: 2, col: 6, end_line: 2, end_col: 6, message: "'(' was never closed" }] : []),
  } });
  await page().evaluate(() => {
    loadIntoState({ name: "Intel", cells: [{ type: "code", cell_id: "c1", source: "rows = load()\nprint(rows", meta: {} }] });
    go("notebook");
  });
  await page().locator(".cell .cm-content").first().waitFor();
});
after(async () => { await app?.close(); });

const inspects = () => app.calls.filter((c) => c.method === "kernel_inspect");

test("hovering while the kernel can't answer shows no card", { skip }, async () => {
  inspect = () => { throw new Error("The kernel is busy."); };
  const before = inspects().length;
  const at = await page().evaluate(() => {
    const view = editors.get(state.notebook.cells[0].id);
    const c = view.coordsAtPos(view.state.doc.toString().indexOf("load") + 1);
    return { x: c.left + 2, y: (c.top + c.bottom) / 2 };
  });
  await page().mouse.move(at.x, at.y);
  // The hover waits 450 ms before it asks the kernel.
  for (const end = Date.now() + 5000; inspects().length === before;) {
    if (Date.now() > end) throw new Error("timed out waiting for the hover to ask the kernel");
    await page().waitForTimeout(25);
  }
  const asked = inspects().at(-1).params;
  assert.deepStrictEqual([asked.code, asked.notebook], ["rows = load()", "Intel"]);
  assert.ok(asked.cursor >= 7 && asked.cursor <= 11, `the cursor is on load (${asked.cursor})`);
  assert.strictEqual(await page().locator(".nb-intel.hover:not([hidden])").count(), 0);
  await page().mouse.move(5, 5);
});

test("the highlighted completion's docs come from the kernel, and a name with no docs shows none", { skip }, async () => {
  inspect = ({ code }) => (code === "rows.append"
    ? { name: "append", kind: "function", signature: "(object, /)", doc: "Append object to the end of the list.", from: 0 }
    : { name: "x", kind: "variable", type: "int", signature: "", doc: "", value: "" });
  const card = await page().evaluate(async () => {
    const node = await intelCompletionInfo("rows.", "append")();
    const none = await intelCompletionInfo("", "x")();
    return { text: node.textContent, cls: node.className, none };
  });
  assert.deepStrictEqual(card, { text: "append(object, /)functionAppend object to the end of the list.", cls: "nb-intel-info", none: null });
  assert.deepStrictEqual(inspects().at(-2).params, { code: "rows.append", cursor: 11, notebook: "Intel" });
});

test("a syntax error placed between two characters is underlined one character wide", { skip }, async () => {
  const mark = page().locator(".cell .cm-lintRange-error");
  await mark.waitFor();
  assert.strictEqual(await mark.textContent(), "r");
});
