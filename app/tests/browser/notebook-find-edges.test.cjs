// WR-138: notebook find and replace (nbfind.js) at its edges. A regular expression's ^ and $ match at
// each line; whole word knows letters of any script, with emoji around them; Replace moves on to the
// next match, past the text it put in; the count follows cells deleted while the bar is open; the bar
// opened again shows its query and its replace row; and Replace all in a 2,000-cell notebook, whose far
// cells have no editor (WR-134), changes every match, is saved, and is undone without building editors.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";

let app;
const saved = [];
const page = () => app.page;
const code = (source, meta = {}) => ({ type: "code", source, meta });
const load = (cells) => page().evaluate((cells) => {
  if (nbFind.open) closeFind();
  Object.assign(nbFind, { q: "", with: "", matchCase: false, word: false, regex: false, markdown: true, outputs: false, replace: false });
  loadIntoState({ name: "Edges", cells });
  go("notebook");
}, cells);
const sources = () => page().evaluate(() => state.notebook.cells.map((c) => c.source));
const bar = () => page().locator(".nb-find");
const count = () => bar().locator(".nb-find-count");

// Opens the bar with Ctrl+H from command mode and sets the query, options and replacement.
async function find(q, { regex = false, word = false, with: withText = "" } = {}) {
  await page().locator(".cells").click({ position: { x: 5, y: 5 } });
  await page().keyboard.press("Control+h");
  await bar().waitFor();
  if (regex) await bar().getByLabel("Use regular expression").click();
  if (word) await bar().getByLabel("Match whole word").click();
  await bar().getByLabel("Find in notebook").fill(q);
  await bar().getByLabel("Replace with").fill(withText);
}

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { save_notebook: (p) => { saved.push(p); return { saved: true }; } } });
});

after(async () => { if (app) await app.close(); });

test("a regular expression's ^ and $ match at every line, and $& and $1 replace there", { skip }, async () => {
  await load([code("import os\nimport sys\nx = 1\ny = 22"), { type: "markdown", source: "# Notes\nimport pandas", meta: {} }]);
  await find("^import (\\w+)$", { regex: true, with: "# $&, was $1" });
  await count().getByText("1 of 3").waitFor();
  await bar().getByRole("button", { name: "Replace all" }).click();
  const [src, md] = await sources();
  assert.strictEqual(src, "# import os, was os\n# import sys, was sys\nx = 1\ny = 22");
  assert.strictEqual(md, "# Notes\n# import pandas, was pandas");  // a Markdown cell shown rendered
  assert.match(await page().locator(".cell").nth(1).innerText(), /^import pandas, was pandas$/m);  // the preview, redrawn
  await bar().getByLabel("Find in notebook").fill("\\d+$");
  await count().getByText("1 of 2").waitFor();  // 1 and 22, each at its line's end
  assert.deepStrictEqual(app.errors, []);
});

test("whole word finds a word with accents, beside emoji, and replaces it at the right place", { skip }, async () => {
  await load([code("drink = '🙂café🙂'\nprint(café, xcafé, café_au_lait)\n# naïve café")]);
  await find("café", { word: true, with: "tea" });
  await count().getByText("1 of 3").waitFor();
  await bar().getByRole("button", { name: "Replace all" }).click();
  assert.strictEqual((await sources())[0], "drink = '🙂tea🙂'\nprint(tea, xcafé, café_au_lait)\n# naïve tea");
  await bar().getByLabel("Find in notebook").fill("naïve");
  await count().getByText("1 of 1").waitFor();
  await bar().getByLabel("Find in notebook").fill("aïv");  // inside a word
  await count().getByText("No results").waitFor();
});

test("Replace goes on to the next match, in the next cell and past the text it put in", { skip }, async () => {
  await load([code("foo = 1"), code("foo = 2"), code("foo = 3")]);
  await find("foo", { with: "foofoo" });
  await count().getByText("1 of 3").waitFor();
  await bar().getByLabel("Find in notebook").press("Enter");
  await count().getByText("2 of 3").waitFor();
  await bar().getByRole("button", { name: "Replace", exact: true }).click();
  assert.deepStrictEqual(await sources(), ["foo = 1", "foofoo = 2", "foo = 3"]);
  // The current match is the third cell's, not the first cell's or the "foo"s just put in.
  const cur = await page().evaluate(() => { const m = nbFind.matches[nbFind.current]; return [state.notebook.cells.indexOf(m.cell), m.from]; });
  assert.deepStrictEqual(cur, [2, 0]);
  await count().getByText("4 of 4").waitFor();
  assert.strictEqual(await page().evaluate(() => state.selected === state.notebook.cells[2].id), true);
});

test("deleting a cell while the bar is open updates the count, and closing doesn't select the deleted cell", { skip }, async () => {
  await load([code("foo = 1"), code("foo = 2"), code("foo = 3"), code("bar = 4")]);
  await find("foo");
  await bar().getByLabel("Find in notebook").press("Enter");
  await bar().getByLabel("Find in notebook").press("Enter");
  await count().getByText("3 of 3").waitFor();
  await page().evaluate(() => deleteCell(state.notebook.cells[2]));  // the current match's cell
  await count().getByText("2 of 2").waitFor();
  assert.strictEqual(await page().evaluate(() => nbFind.matches.every((m) => state.notebook.cells.includes(m.cell))), true);
  await page().evaluate(() => deleteCell(state.notebook.cells[0]));
  await count().getByText("1 of 1").waitFor();
  await bar().getByLabel("Find in notebook").press("Escape");
  assert.strictEqual(await page().evaluate(() => state.notebook.cells.some((c) => c.id === state.selected)), true);
});

test("opened again, the bar shows the selected text as its query, and Ctrl+H shows the replace row", { skip }, async () => {
  await load([code("alpha = beta")]);
  await page().locator(".cells").click({ position: { x: 5, y: 5 } });
  await page().keyboard.press("Control+f");
  await bar().getByLabel("Find in notebook").fill("alpha");
  await count().getByText("1 of 1").waitFor();
  await bar().getByLabel("Find in notebook").press("Escape");
  await page().evaluate(() => {
    const view = editors.get(state.notebook.cells[0].id);
    view.focus();
    view.dispatch({ selection: { anchor: 8, head: 12 } });  // "beta"
  });
  await page().keyboard.press("Control+h");
  await bar().waitFor();
  assert.strictEqual(await bar().getByLabel("Find in notebook").inputValue(), "beta");
  assert.strictEqual(await bar().getByLabel("Replace with").isVisible(), true);
  assert.strictEqual(await bar().getByLabel("Show replace").getAttribute("aria-expanded"), "true");
});

test("Replace all in a 2,000-cell notebook changes every match, far cells too, saves them, and undoes", { skip }, async () => {
  const cells = [];
  for (let i = 0; i < 2000; i += 1) cells.push(code(`x_${i} = foo + foo + foo  # cell ${i}`));
  await load(cells);
  await find("foo", { with: "bar" });
  await count().getByText("1 of 5000+").waitFor();  // the count stops at 5,000
  await bar().getByRole("button", { name: "Replace all" }).click();
  await page().locator("#toast").getByText("Replaced 6000 matches in 2000 cells.").waitFor();
  const out = await page().evaluate(() => {
    const far = state.notebook.cells[1900];
    return { left: state.notebook.cells.filter((c) => c.source.includes("foo")).length,
      far: far.source, plain: document.querySelector(`[data-cell="${far.id}"] .nb-lazy-code`)?.textContent, built: editors.size };
  });
  assert.strictEqual(out.left, 0);
  assert.strictEqual(out.far, "x_1900 = bar + bar + bar  # cell 1900");
  assert.strictEqual(out.plain, out.far);
  await count().getByText("No results").waitFor();
  await page().evaluate(() => saveNotebook());
  const last = saved.at(-1);
  assert.strictEqual(last.cells[1999].source, "x_1999 = bar + bar + bar  # cell 1999");
  // Ctrl+Z in a cell with its editor undoes that cell's replacements, as typing would.
  const undone = await page().evaluate(() => {
    const view = editors.peek(state.notebook.cells[0].id);
    CM.undo(view);
    return state.notebook.cells[0].source;
  });
  assert.strictEqual(undone, "x_0 = foo + foo + foo  # cell 0");
  await page().evaluate(() => setSource(state.notebook.cells[0], "x_0 = bar + bar + bar  # cell 0"));
  await page().waitForTimeout(200);
  const t0 = Date.now();
  await page().evaluate(() => undoReplaceAll());
  const ms = Date.now() - t0;
  const back = await page().evaluate(() => ({ foo: state.notebook.cells.filter((c) => c.source === `x_${state.notebook.cells.indexOf(c)} = foo + foo + foo  # cell ${state.notebook.cells.indexOf(c)}`).length,
    built: editors.size, plain: document.querySelector(`[data-cell="${state.notebook.cells[1900].id}"] .nb-lazy-code`)?.textContent }));
  assert.strictEqual(back.foo, 2000);
  assert.strictEqual(back.plain, "x_1900 = foo + foo + foo  # cell 1900");
  assert.ok(back.built <= out.built + 5, `Undo replace all built editors: ${out.built} before, ${back.built} after`);
  assert.ok(ms < 5000, `Undo replace all took ${ms} ms`);
  assert.deepStrictEqual(app.errors, []);
});
