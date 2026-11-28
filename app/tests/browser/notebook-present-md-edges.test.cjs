// WR-135: Present mode (nbpresent.js, N49) and the Markdown toolbar (nbmarkdown.js, N23) on a 2,000-cell
// notebook and on odd Markdown.
//   - Where slides start, on Markdown that trips a line-by-line reader: Windows line ends, longer and
//     mixed fences, a fence in a list, indented code, quotes, HTML headings, #tags, right-to-left text,
//     and underlined (=== and ---) headings anywhere in a cell, read as the Outline reads them.
//   - Present on 2,000 cells (400 slides): one export page, a budget for opening, Home and End, the count
//     without a dot per slide, and Esc back to the cell it started from, far down the notebook.
//   - The toolbar on odd selections (spaces at the ends, backticks in the text, a fence inside a block)
//     and on a Markdown cell near the end of the 2,000 cells, whose neighbours' editors are not built.
// The budgets are generous proxies, as in notebook-large-perf: they catch a gross regression, not noise.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const CELLS = 2000;
const BUDGET = { presentOpenMs: 6000, endMs: 1000 };

function bigNotebook() {
  const cells = [];
  for (let i = 0; i < CELLS; i += 1) {
    if (i % 5 === 0) cells.push({ type: "markdown", cell_id: `m${i}`, source: `## Section ${i}\n\nAbout step ${i}.`, meta: {} });
    else cells.push({ type: "code", cell_id: `c${i}`, source: `x_${i} = ${i}`, meta: {} });
  }
  return { name: "Big", cells, lakehouse: null, compute: "default", disk_stamp: "stamp-1", check: null };
}

let app;
const page = () => app.page;
const present = () => page().getByRole("dialog", { name: /presenting$/ });

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["notebook_print_page"], mocks: {
    open_notebook: () => bigNotebook(),
    save_notebook: () => ({ saved: true }), notebook_syntax: () => [], notebook_dependencies: () => ({ cells: [] }),
  } });
  // The engine's first page is its cold start, which isn't Present's to time.
  await page().evaluate(() => rpc("notebook_print_page", { name: "warm", cells: [] }));
});
after(async () => { await app?.close(); });

// The slides' first lines, for Markdown cells with these sources.
const split = (sources) => page().evaluate((sources) => nbPresentSplit(sources.map((source, i) => ({ type: "markdown", id: i, source, meta: {} })))
  .map((s) => s.parts.map((p) => p.md).join("|")), sources);

test("slides start at # and ## headings and underlined headings, and not inside code, quotes or HTML", { skip }, async () => {
  await page().evaluate(() => { loadIntoState({ name: "Edges", cells: [{ type: "markdown", cell_id: "m", source: "x", meta: {} }] }); go("notebook"); });
  const cases = [
    ["# A\r\ntext\r\n## B\r\nmore", ["# A\r\ntext\r", "## B\r\nmore"]],
    ["Intro\n\nTitle\n=====\n\nbody\n\nSub\n---\nx", ["Intro\n", "Title\n=====\n\nbody\n", "Sub\n---\nx"]],
    ["Intro\n\nTwo\nlines\n===\nbody", ["Intro\n", "Two\nlines\n===\nbody"]],
    ["Intro\nSub\n---", ["Intro\nSub\n---"]],  // one paragraph: the heading is both lines, and it starts the cell
    ["Intro\n\n- a\n---\nafter", ["Intro\n\n- a\n---\nafter"]],  // after a list item --- is a rule
    ["Intro\n\n---\nrule", ["Intro\n\n---\nrule"]],
    ["Intro\n\n> q\n---", ["Intro\n\n> q\n---"]],
    ["Intro\n\n    code\n---", ["Intro\n\n    code\n---"]],
    ["````\n```\n# not\n```\n````\n## Real", ["````\n```\n# not\n```\n````", "## Real"]],
    ["```\n~~~\n# not\n```\n## yes", ["```\n~~~\n# not\n```", "## yes"]],
    ["- item\n  ```\n  # in fence\n  ```\n## yes", ["- item\n  ```\n  # in fence\n  ```", "## yes"]],
    ["```\nTitle\n===\n```\nafter", ["```\nTitle\n===\n```\nafter"]],
    ["<h1>x</h1>\n## y", ["<h1>x</h1>", "## y"]],
    ["text\n\n    # comment\n## yes", ["text\n\n    # comment", "## yes"]],
    ["#tag\n##also", ["#tag\n##also"]],
    ["> # q\n## yes", ["> # q", "## yes"]],
    ["# مرحبا\nنص\n## שלום\nטקסט", ["# مرحبا\nنص", "## שלום\nטקסט"]],
    ["Intro\n### Three stays\n## Two", ["Intro\n### Three stays", "## Two"]],
  ];
  for (const [source, want] of cases) assert.deepStrictEqual(await split([source]), want, JSON.stringify(source));
  // An underlined heading first in a cell starts a slide, as one in the middle does.
  assert.deepStrictEqual(await split(["Intro", "Next\n===\nbody"]), ["Intro", "Next\n===\nbody"]);
  // The slide's title is the heading's text, underlined or not, right to left as written.
  const titles = await page().evaluate(() => nbPresentSplit([{ type: "markdown", id: 1, source: "Intro\n\nTitle *here*\n===\nx\n## שלום", meta: {} }])
    .map((s, i) => nbPresentTitle(s, i + 1)));
  assert.deepStrictEqual(titles, ["Slide 1", "Title here", "שלום"]);
});

test("Present on 2,000 cells: 400 slides from one export page, Home and End, the count alone, Esc back far down", { skip }, async () => {
  const opened = await page().evaluate(async () => {
    const frame = () => new Promise((r) => requestAnimationFrame(() => r()));
    await openNotebook("Big");
    // Outputs on every tenth code cell, as a run would leave them.
    state.notebook.cells.forEach((c, i) => { if (c.type === "code" && i % 10 === 1) c.outputs = [{ type: "stream", text: `row ${i}\n` }]; });
    state.ribbonTab = "view";
    go("notebook");
    await frame();
    const cell = document.querySelectorAll(".cell")[1861];
    cell.scrollIntoView();
    cell.querySelector(".gutter").click();
    return state.selected;
  });
  assert.ok(opened, "a cell far down is selected");
  const before = app.calls.length;
  const t0 = Date.now();
  await page().keyboard.press("Control+Shift+F5");
  await present().waitFor();
  const openMs = Date.now() - t0;
  assert.ok(openMs < BUDGET.presentOpenMs, `Present took ${openMs} ms to open`);
  const pages = app.calls.slice(before).filter((c) => c.method === "notebook_print_page");
  assert.strictEqual(pages.length, 1, "one export page for all the outputs");
  assert.strictEqual(pages[0].params.cells.length, 200);
  assert.strictEqual(await page().locator(".nbp-count").innerText(), "1 / 400");
  assert.strictEqual(await page().locator(".nbp-dot").count(), 0, "no dot per slide on a long deck");
  const bar = await page().locator(".nbp-bottom").evaluate((el) => ({ scroll: el.scrollWidth, client: el.clientWidth }));
  assert.ok(bar.scroll <= bar.client + 1, `the bottom bar fits: ${JSON.stringify(bar)}`);
  const t1 = Date.now();
  await page().keyboard.press("End");
  await page().locator(".nbp-count", { hasText: "400 / 400" }).waitFor();
  assert.ok(Date.now() - t1 < BUDGET.endMs, "End is quick");
  assert.match(await page().locator(".nbp-slide").innerText(), /^Section 1995\s+About step 1995\.$/);
  await page().keyboard.press("ArrowLeft");
  await page().locator(".nbp-count", { hasText: "399 / 400" }).waitFor();
  assert.match(await page().locator(".nbp-slide").innerText(), /Section 1990[\s\S]*row 1991/);
  await page().keyboard.press("Home");
  await page().locator(".nbp-count", { hasText: "1 / 400" }).waitFor();
  await page().keyboard.press("s");
  assert.strictEqual(await page().locator(".nbp-thumb").count(), 400);
  await page().keyboard.press("s");
  await page().keyboard.press("Escape");
  await present().waitFor({ state: "detached" });
  const back = await page().evaluate((id) => document.activeElement?.dataset?.cell === String(id), opened);
  assert.ok(back, "focus is back on the cell Present started from");
  assert.strictEqual(app.calls.slice(before).filter((c) => /^(start_cell|poll_cell|engine_start_cell)$/.test(c.method)).length, 0, "nothing ran");
});

test("a short deck keeps a dot per slide", { skip }, async () => {
  await page().evaluate(() => {
    loadIntoState({ name: "Short", cells: [{ type: "markdown", cell_id: "a", source: "# One", meta: {} }, { type: "markdown", cell_id: "b", source: "# Two", meta: {} }] });
    state.ribbonTab = "view";
    go("notebook");
  });
  await page().getByRole("button", { name: /^Present/ }).click();
  await present().waitFor();
  assert.strictEqual(await page().locator(".nbp-dot").count(), 2);
  await page().keyboard.press("Escape");
  await present().waitFor({ state: "detached" });
});

// A Markdown cell being edited, `text` in its editor with [from, to) selected; i is the cell's index.
async function editing(i, text, from, to) {
  await page().evaluate(({ i, text, from, to }) => {
    const cell = state.notebook.cells[i];
    cell.source = text;
    cell.editing = true;
    renderNotebook();
    document.querySelector(`[data-cell="${CSS.escape(String(cell.id))}"]`).scrollIntoView();
    const view = editors.get(cell.id);
    if (view.state.doc.toString() !== text) view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } });
    view.focus();
    view.dispatch({ selection: { anchor: from, head: to } });
  }, { i, text, from, to });
}
const tool = (i, name) => page().locator(".cell").nth(i).getByRole("toolbar", { name: "Markdown formatting" }).getByRole("button", { name, exact: true });
const src = (i) => page().evaluate((i) => state.notebook.cells[i].source, i);

test("the toolbar on odd selections: spaces at the ends, backticks in the text, a fence inside a block", { skip }, async () => {
  await page().evaluate(() => {
    loadIntoState({ name: "Odd", cells: [{ type: "markdown", cell_id: "m", source: "", meta: {} }] });
    go("notebook");
  });
  const cases = [
    ["Bold", "say  hi  now", 3, 9, "say  **hi**  now"],
    ["Italic", "a b c", 1, 4, "a *b* c"],
    ["Code", "say  hi  now", 3, 9, "say  `hi`  now"],
    ["Bold", "   ", 0, 3, "**bold text**"],
    ["Code", "use a`b here", 4, 7, "use ``a`b`` here"],
    ["Code", "x `y` z", 2, 5, "x `` `y` `` z"],
    ["Code", "Run:\n```py\nx = 1\n```", 0, 20, "````\nRun:\n```py\nx = 1\n```\n````"],
    ["Bold", "مرحبا بالعالم", 0, 5, "**مرحبا** بالعالم"],
  ];
  for (const [name, text, from, to, want] of cases) {
    await editing(0, text, from, to);
    await tool(0, name).click();
    assert.strictEqual(await src(0), want, `${name} on ${JSON.stringify(text.slice(from, to))}`);
  }
  // What the toolbar wrote renders as it says: the code keeps its backticks, the inner fence stays text.
  await editing(0, "Run:\n```py\nx = 1\n```", 0, 20);
  await tool(0, "Code").click();
  await page().evaluate(() => { state.notebook.cells[0].editing = false; renderNotebook(); });
  const shown = await page().locator(".cell.markdown .md-view").evaluate((el) => ({ pres: el.querySelectorAll("pre").length, text: el.innerText }));
  assert.strictEqual(shown.pres, 1);
  assert.match(shown.text, /Run:\s*```py\s*x = 1\s*```/);
});

test("the toolbar works on a Markdown cell near the end of 2,000 cells, with editors only near it", { skip }, async () => {
  await page().evaluate(() => openNotebook("Big"));
  await page().locator(".cell").nth(1995).waitFor({ state: "attached" });
  await editing(1995, "Section 1995 notes", 0, 7);
  await tool(1995, "Bold").click();
  assert.strictEqual(await src(1995), "**Section** 1995 notes");
  await page().keyboard.press("Control+z");
  assert.strictEqual(await src(1995), "Section 1995 notes");
  const editors = await page().evaluate(() => document.querySelectorAll(".cells .cm-editor").length);
  assert.ok(editors <= 80, `${editors} editors in the page`);
  await page().keyboard.press("Escape");
  await page().locator(".cell").nth(1995).locator(".md-view").waitFor();
  assert.strictEqual(await page().locator(".md-toolbar").count(), 0);
});
