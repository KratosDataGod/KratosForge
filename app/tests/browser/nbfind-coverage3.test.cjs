// Notebook find and replace (nbfind.js) beyond nbfind-coverage and notebook-find: the searchable text of each
// kind of output, an empty query, Markdown left out, empty matches skipped, the 5,000 cap reached in outputs,
// a regular expression's groups in the replacement, Ctrl+H reopening a bar closed without its replace row, the selected text becoming the query, Previous from no
// match, Ctrl+Enter in the replace box, Replace all where matches are frozen and in outputs, Undo replace all
// after cells changed, typing with no query, Ctrl+F outside the editor, and an output match when the page
// can't mark text. No engine calls.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const bar = () => page().locator(".nb-find");
const counted = (text) => page().waitForFunction((t) => document.querySelector(".nb-find-count")?.textContent === t, text);
const toastSays = (text) => page().waitForFunction((t) => document.getElementById("toast").textContent === t, text);
const sources = () => page().evaluate(() => state.notebook.cells.map((c) => c.source));

const CELLS = [
  { type: "code", cell_id: "c1", source: "total = 1\ntotal_two = 2", meta: {} },
  { type: "code", cell_id: "c2", source: "print(total)", meta: { frozen: true } },
  { type: "code", cell_id: "c3", source: "print(total)", meta: { frozen: true } },
  { type: "code", cell_id: "c4", source: "show()", meta: {} },
];

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { readiness_check: () => [], save_notebook: () => ({ saved: true }) } });
});
after(async () => { if (app) await app.close(); });
beforeEach(async () => {
  if (skip) return;
  await page().evaluate((cells) => {
    if (nbFind.open) closeFind();
    Object.assign(nbFind, { q: "", with: "", regex: false, word: false, matchCase: false, markdown: true, outputs: false, replace: false });
    closeDialog(); toastClose();
    loadIntoState({ name: `Find ${Math.random().toString(36).slice(2, 7)}`, cells: structuredClone(cells) });
    state.notebook.cells[3].outputs = [{ type: "stream", text: "total rows: 3" }, { type: "stream", text: "total again" }];
    go("notebook");
  }, CELLS);
  await page().locator(".cells").waitFor();
  app.errors.length = 0;
});

test("the text find searches: each kind of output, an empty query, Markdown left out, empty matches and the cap in outputs", { skip }, async () => {
  const out = await page().evaluate(() => {
    const md = { type: "markdown", source: "total", outputs: [] };
    const code = { type: "code", source: "a", outputs: [{ type: "stream", text: "x".repeat(6000) }] };
    return {
      texts: [findOutputText(null), findOutputText({ type: "error", ename: "E", evalue: "bad", traceback: "tb" }),
        findOutputText({ type: "table", columns: ["a"], rows: [["1"], "not a row"] }), findOutputText({ type: "image" }),
        findOutputText({ type: "text" })],
      empty: findMatches([md], "").matches.length,
      noMd: findMatches([md], "total", { markdown: false }).matches.length,
      zero: findMatches([code], "z*", { regex: true }).matches.length,
      capped: (() => { const r = findMatches([code, code], "x", { outputs: true }); return [r.matches.length, !!r.capped]; })(),
      bad: findMatches([md], "(", { regex: true }).error,
      groups: findReplacement({ cell: { source: "id_12" }, from: 0, to: 5 }, "id_(\\d+)", "n$1", { regex: true }),
    };
  });
  assert.deepStrictEqual(out.texts, ["", "E: bad\ntb", "a\n1\n", "", ""]);
  assert.deepStrictEqual([out.empty, out.noMd, out.zero], [0, 0, 0]);
  assert.deepStrictEqual(out.capped, [5000, true]);
  assert.strictEqual(out.bad, "That isn't a valid regular expression.");
  assert.strictEqual(out.groups, "n12");
  assert.deepStrictEqual(app.errors, []);
});

test("the selected text becomes the query; Previous from no match goes to the last; typing with no query just counts", { skip }, async () => {
  await page().evaluate(() => {
    const c = state.notebook.cells[0];
    selectCell(c);
    const v = editors.get(c.id);
    v.focus();
    v.dispatch({ selection: { anchor: 0, head: 5 } });
    openFind(false);
  });
  assert.strictEqual(await bar().getByLabel("Find in notebook").inputValue(), "total");
  await counted("1 of 4");
  await page().evaluate(() => { nbFind.current = -1; findStep(-1); });
  await counted("4 of 4");

  await bar().getByLabel("Find in notebook").fill("");
  await page().evaluate(() => { const v = editors.get(state.notebook.cells[0].id); v.dispatch({ changes: { from: 0, insert: "# " } }); });
  await page().waitForFunction(() => document.querySelector(".nb-find-count")?.textContent === "");
  assert.deepStrictEqual(app.errors, []);
});

test("Ctrl+H after the bar was closed without its replace row shows the row", { skip }, async () => {
  await page().locator(".cells").click({ position: { x: 5, y: 5 } });
  await page().keyboard.press("Control+f");
  await bar().waitFor();
  await page().keyboard.press("Escape");
  await bar().waitFor({ state: "detached" });
  await page().locator(".cells").click({ position: { x: 5, y: 5 } });
  await page().keyboard.press("Control+h");
  await bar().getByLabel("Replace with").waitFor();
  assert.strictEqual(await bar().getByRole("button", { name: "Show replace" }).getAttribute("aria-expanded"), "true");
  assert.deepStrictEqual(app.errors, []);
});

test("Ctrl+Enter in the replace box replaces all, and says which matches were frozen or in outputs", { skip }, async () => {
  await page().evaluate(() => { nbFind.outputs = true; openFind(true); });
  await bar().getByLabel("Find in notebook").fill("total");
  await counted("1 of 6");
  await bar().getByLabel("Replace with").fill("sum");
  await bar().getByLabel("Replace with").press("Control+Enter");
  await toastSays("Replaced 2 matches in 1 cell. 2 in frozen cells were left as they are. 2 in outputs were left as they are.");
  assert.deepStrictEqual((await sources()).slice(0, 2), ["sum = 1\nsum_two = 2", "print(total)"]);

  // Now every match is frozen or in an output.
  await page().evaluate(() => toastClose());
  await bar().getByLabel("Replace with").press("Control+Enter");
  await toastSays("All 4 matches are in frozen cells (2) or outputs (2), which Replace leaves as they are, so nothing was changed.");
  await page().evaluate(() => { toastClose(); nbFind.outputs = false; findRecompute(false); });
  await counted("1 of 2");
  await bar().getByRole("button", { name: "Replace all", exact: true }).click();
  await toastSays("All 2 matches are in frozen cells, which Replace leaves as they are, so nothing was changed.");
  assert.deepStrictEqual(app.errors, []);
});

test("Undo replace all keeps the cells changed since, and says how many", { skip }, async () => {
  await page().evaluate(() => {
    state.notebook.cells[1].meta.frozen = false; state.notebook.cells[2].meta.frozen = false;
    openFind(true);
  });
  await bar().getByLabel("Find in notebook").fill("print");
  await counted("1 of 2");
  await bar().getByLabel("Replace with").fill("echo");
  await bar().getByRole("button", { name: "Replace all", exact: true }).click();
  await toastSays("Replaced 2 matches in 2 cells.");
  // Both replaced cells change again outside the bar's watch, so Undo can't put them back.
  await page().evaluate(() => { state.notebook.cells[1].source = "x"; state.notebook.cells[2].source = "y"; toastClose(); undoReplaceAll(); });
  await toastSays("Replace all was undone, except in 2 cells changed since, which were left as they are.");
  assert.deepStrictEqual((await sources()).slice(1, 3), ["x", "y"]);
  assert.deepStrictEqual(app.errors, []);
});

test("Ctrl+F outside a cell's editor opens the bar; an output match scrolls to the outputs when text can't be marked", { skip }, async () => {
  await page().locator(".cells").click({ position: { x: 5, y: 5 } });
  await page().keyboard.press("Control+f");
  await bar().waitFor();
  await page().evaluate(() => { window.__hl = CSS.highlights; Object.defineProperty(CSS, "highlights", { configurable: true, value: undefined }); });
  try {
    await page().evaluate(() => { nbFind.outputs = true; nbFind.markdown = true; });
    await bar().getByLabel("Find in notebook").fill("again");
    await counted("1 of 1");
    assert.strictEqual(await page().evaluate(() => state.selected === state.notebook.cells[3].id), true);
  } finally {
    await page().evaluate(() => Object.defineProperty(CSS, "highlights", { configurable: true, value: window.__hl }));
  }
  await bar().getByLabel("Find in notebook").press("Escape");
  await bar().waitFor({ state: "detached" });
  assert.deepStrictEqual(app.errors, []);
});
