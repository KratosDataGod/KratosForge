// A long notebook's editors (notebook.js EAGER_EDITORS, WR-134): a code cell far from the screen shows
// its code as plain text until its editor comes back. The plain text is the editor's height whatever
// the editor settings, follows text changed while the editor is out, and bringing an editor back never
// builds a second one; a cell drawn again on its own still follows the screen.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const CELLS = 200;

function notebook() {
  const cells = [];
  for (let i = 0; i < CELLS; i += 1) {
    const lines = [`total_${i} = 0`, `for n in range(${i}):`, `    total_${i} += n  # a comment long enough to wrap when the editor wraps its lines at the width of the cell, as the setting asks`];
    cells.push({ type: "code", cell_id: `c${i}`, source: lines.join("\n"), meta: i === 150 ? { breakpoints: [2] } : {} });
  }
  return { name: "Long", cells };
}

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { open_notebook: () => notebook() } });
  await page().evaluate(async () => { await openNotebook("Long"); await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))); });
});

after(async () => { if (app) await app.close(); });

// The cell's editor slot height as plain text, then with its editor back.
const heights = (i) => page().evaluate((i) => {
  const cell = state.notebook.cells[i];
  const slot = () => document.querySelector(`[data-cell="${cell.id}"] .editor`);
  const plain = !!slot().querySelector(".nb-lazy-code");
  const before = slot().getBoundingClientRect().height;
  editors.get(cell.id);
  return { plain, before, after: slot().getBoundingClientRect().height, editor: !!slot().querySelector(".cm-editor") };
}, i);

test("a far cell's plain text is as tall as its editor with a larger font and wrapping on", { skip }, async () => {
  const plain = await heights(120);
  assert.ok(plain.plain && plain.editor);
  assert.ok(Math.abs(plain.before - plain.after) <= 1, `default: ${plain.before} vs ${plain.after}`);
  for (const settings of [{ editor_font_size: { value: 16 } }, { editor_font_size: { value: 16 }, editor_word_wrap: { value: "notebooks" } }]) {
    await page().evaluate((s) => { state.settings = { ...(state.settings || {}), ...s }; applyEditorPrefs(); renderNotebook(); }, settings);
    const h = await heights(settings.editor_word_wrap ? 140 : 130);
    assert.ok(h.plain && h.editor);
    assert.ok(Math.abs(h.before - h.after) <= 1, `${JSON.stringify(settings)}: plain ${h.before}px, editor ${h.after}px`);
  }
  await page().evaluate(() => { state.settings = { ...state.settings, editor_font_size: { value: 0 }, editor_word_wrap: { value: "off" } }; applyEditorPrefs(); renderNotebook(); });
});

test("drawing a far cell with a breakpoint again builds one editor, not two", { skip }, async () => {
  const out = await page().evaluate(() => {
    const cell = state.notebook.cells[150];
    const plain = !!document.querySelector(`[data-cell="${cell.id}"] .nb-lazy-code`);
    const open = () => [...editorOpen].filter((r) => r.kind === "notebook").length;
    const before = open();
    updateCell(cell);
    return { plain, dbg: dbgOn(), added: open() - before, inMap: editors.peek(cell.id)?.dom.isConnected };
  });
  assert.ok(out.plain && out.dbg);
  assert.strictEqual(out.added, 1, "one editor built");
  assert.ok(out.inMap, "the editor in the page is the one kept");
});

test("Replace all changes the plain text of cells whose editor is out of the page", { skip }, async () => {
  const out = await page().evaluate(() => {
    const never = state.notebook.cells[170];  // never built
    const parked = state.notebook.cells[60];  // built, then taken out of the page
    const text = (c) => document.querySelector(`[data-cell="${c.id}"] .editor`).textContent;
    const view = editors.get(parked.id);
    const slot = document.querySelector(`[data-cell="${parked.id}"] .editor`);
    slot.park();
    const shown = { never: !!document.querySelector(`[data-cell="${never.id}"] .nb-lazy-code`), parked: !!slot.querySelector(".nb-lazy-code") };
    for (const c of [never, parked]) {
      const from = c.source.indexOf("total_");
      findWrite(c, [{ from, to: from + 5, insert: "grand" }]);
    }
    return { shown, never: text(never), parked: text(parked), view: view.state.doc.toString() };
  });
  assert.deepStrictEqual(out.shown, { never: true, parked: true });
  assert.match(out.never, /^grand_170 = 0/);
  assert.match(out.parked, /^grand_60 = 0/);
  assert.match(out.view, /^grand_60 = 0/);
});

test("a long notebook's cell drawn again on its own still gives its editor back when far away", { skip }, async () => {
  const out = await page().evaluate(async () => {
    const frames = (n) => new Promise((r) => { const f = () => (--n ? requestAnimationFrame(f) : r()); requestAnimationFrame(f); });
    const scroller = document.querySelector(".cells");
    scroller.scrollTop = 0;
    await frames(3);
    const cell = state.notebook.cells[2];
    updateCell(cell);
    await frames(2);
    scroller.scrollTop = scroller.scrollHeight;
    await frames(4);
    return { plain: !!document.querySelector(`[data-cell="${cell.id}"] .nb-lazy-code`) };
  });
  assert.ok(out.plain, "the redrawn cell's editor was taken out of the page when it went far away");
});
