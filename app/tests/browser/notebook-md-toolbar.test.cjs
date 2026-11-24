// Rich-text editing of Markdown cells (nbmarkdown.js, Preview): the formatting toolbar while a Markdown
// cell is being edited, each button with and without a selection, Ctrl+B, Ctrl+I and Ctrl+K, undo, the
// live Split preview, the View switch remembered per notebook, tabs on a narrow cell, the hint, plain
// Markdown in what is saved, Esc and a click outside finishing editing, and nothing new with Preview
// features hidden.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const saved = [];
const page = () => app.page;
const md = () => page().locator(".cell.markdown").first();
const bar = () => md().getByRole("toolbar", { name: "Markdown formatting" });
const tool = (name) => bar().getByRole("button", { name, exact: true });
const source = () => page().evaluate(() => state.notebook.cells[0].source);

// A notebook with one Markdown cell being edited, its editor holding `text` with [from, to) selected.
async function open(text, from = text.length, to = from, { name = "Notes", preview = true, keep = false } = {}) {
  await page().evaluate(({ text, from, to, name, preview, keep }) => {
    if (!keep) localStorage.removeItem(`fd.mdview.${name}`);  // each test starts on Split
    state.settings = { ...(state.settings || {}), show_preview: { ...(state.settings?.show_preview || {}), value: preview } };
    loadIntoState({ name, cells: [{ type: "markdown", cell_id: "m1", source: text, meta: {} },
      { type: "code", cell_id: "c1", source: "x = 1", meta: {} }] });
    go("notebook");
    const cell = state.notebook.cells[0];
    cell.editing = true;
    renderNotebook();
    const view = editors.get(cell.id);
    view.focus();
    view.dispatch({ selection: { anchor: from, head: to } });
  }, { text, from, to, name, preview, keep });
  await md().locator(".cm-content").waitFor();
}

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { save_notebook: (p) => { saved.push(p); return { saved: true }; }, notebook_syntax: () => [] } });
});
after(async () => { await app?.close(); });

test("the toolbar shows only while a Markdown cell is being edited", { skip }, async () => {
  await open("# Title");
  const titles = await bar().locator("button").evaluateAll((bs) => bs.map((b) => b.title || b.textContent));
  assert.deepStrictEqual(titles, ["Bold (Ctrl+B)", "Italic (Ctrl+I)", "Heading", "Bulleted list", "Numbered list",
    "Link (Ctrl+K)", "Code", "Table", "Edit", "Split", "Preview"]);
  assert.strictEqual(await bar().getByRole("separator").count(), 2);
  assert.strictEqual(await bar().getByRole("group", { name: "View" }).count(), 1);
  await md().getByText("Saved as Markdown. Press Esc or click outside the cell to finish editing.").waitFor();
  // At rest: the rendered cell as today, with no toolbar.
  await page().evaluate(() => { state.notebook.cells[0].editing = false; renderNotebook(); });
  await md().locator(".md-view h1", { hasText: "Title" }).waitFor();
  assert.strictEqual(await page().getByRole("toolbar", { name: "Markdown formatting" }).count(), 0);
  assert.strictEqual(await page().locator(".md-hint").count(), 0);
});

test("each button wraps the selection", { skip }, async () => {
  const cases = [
    ["Bold", "say hi", 4, 6, "say **hi**"],
    ["Italic", "say hi", 4, 6, "say *hi*"],
    ["Heading", "one\ntwo", 0, 3, "## one\ntwo"],
    ["Bulleted list", "a\nb", 0, 3, "- a\n- b"],
    ["Numbered list", "a\nb", 0, 3, "1. a\n2. b"],
    ["Link", "see docs", 4, 8, "see [docs](url)"],
    ["Code", "run x now", 4, 5, "run `x` now"],
    ["Code", "a = 1\nb = 2", 0, 11, "```\na = 1\nb = 2\n```"],
    ["Table", "Intro", 0, 0, "| Column 1 | Column 2 |\n| --- | --- |\n| Cell | Cell |\n| Cell | Cell |\nIntro"],
  ];
  for (const [name, text, from, to, want] of cases) {
    await open(text, from, to);
    await tool(name).click();
    assert.strictEqual(await source(), want, name);
  }
});

test("with nothing selected, each button inserts a placeholder, left selected to type over", { skip }, async () => {
  const cases = [
    ["Bold", "**bold text**", "bold text"],
    ["Italic", "*italic text*", "italic text"],
    ["Heading", "## Heading", "Heading"],
    ["Bulleted list", "- List item", "List item"],
    ["Numbered list", "1. List item", "List item"],
    ["Link", "[link text](url)", "link text"],
    ["Code", "`code`", "code"],
    ["Table", "| Column 1 | Column 2 |\n| --- | --- |\n| Cell | Cell |\n| Cell | Cell |\n", "Column 1"],
  ];
  for (const [name, want, picked] of cases) {
    await open("");
    await tool(name).click();
    assert.strictEqual(await source(), want, name);
    const sel = await page().evaluate(() => { const v = editors.get(state.notebook.cells[0].id); const r = v.state.selection.main; return v.state.sliceDoc(r.from, r.to); });
    assert.strictEqual(sel, picked, `${name} selects its placeholder`);
  }
  // On a line with text, a line prefix starts a new line; the table leaves a blank line before it.
  await open("Intro");
  await tool("Heading").click();
  assert.strictEqual(await source(), "Intro\n## Heading");
  await open("Intro");
  await tool("Table").click();
  assert.match(await source(), /^Intro\n\n\| Column 1 \| Column 2 \|\n\| --- \| --- \|\n\| Cell \| Cell \|\n\| Cell \| Cell \|\n$/);
});

test("Ctrl+B, Ctrl+I and Ctrl+K work in the editor, and Ctrl+Z undoes each edit", { skip }, async () => {
  await open("a b c", 0, 1);
  await page().keyboard.press("Control+b");
  assert.strictEqual(await source(), "**a** b c");
  await page().evaluate(() => editors.get(state.notebook.cells[0].id).dispatch({ selection: { anchor: 6, head: 7 } }));
  await page().keyboard.press("Control+i");
  assert.strictEqual(await source(), "**a** *b* c");
  await page().evaluate(() => editors.get(state.notebook.cells[0].id).dispatch({ selection: { anchor: 10, head: 11 } }));
  await page().keyboard.press("Control+k");
  assert.strictEqual(await source(), "**a** *b* [c](url)");
  assert.strictEqual(await page().locator("#palette").count(), 0, "Ctrl+K in the cell doesn't open the command bar");
  // A toolbar button's edit is undone the same way.
  await tool("Heading").click();
  assert.strictEqual(await source(), "## **a** *b* [c](url)");
  for (const want of ["**a** *b* [c](url)", "**a** *b* c", "**a** b c", "a b c"]) {
    await page().keyboard.press("Control+z");
    assert.strictEqual(await source(), want);
  }
  // The keys work in Edit view too, where the preview is hidden.
  await bar().getByRole("button", { name: "Edit", exact: true }).click();
  await md().locator(".cm-content").click();
  await page().keyboard.press("Control+a");
  await page().keyboard.press("Control+b");
  assert.strictEqual(await source(), "**a b c**");
});

test("Split previews the cell as you type, with the cell's own renderer", { skip }, async () => {
  await open("Total: $x^2$");
  const prev = md().locator(".md-preview");
  assert.strictEqual(await prev.isVisible(), true);
  assert.strictEqual(await md().locator(".md-edit").isVisible(), true);
  const box = async (sel) => md().locator(sel).boundingBox();
  assert.ok((await box(".md-edit")).x < (await box(".md-preview")).x, "editor on the left, preview on the right");
  await prev.locator(".md-math, .katex").first().waitFor();  // KaTeX, as at rest
  await page().keyboard.type("\n\n## Region totals");
  await prev.locator("h2", { hasText: "Region totals" }).waitFor();
  // The preview is what the cell shows at rest.
  const live = await prev.innerHTML();
  await page().evaluate(() => { state.notebook.cells[0].editing = false; renderNotebook(); });
  assert.strictEqual(await md().locator(".md-view").innerHTML(), live);
});

test("Edit, Split and Preview, remembered per notebook; Split is the default", { skip }, async () => {
  await page().evaluate(() => { localStorage.removeItem("fd.mdview.Plan"); localStorage.removeItem("fd.mdview.Other"); });
  await open("Hello", 5, 5, { name: "Plan", keep: true });
  const pressed = () => bar().locator("[aria-pressed=true]").innerText();
  assert.strictEqual(await pressed(), "Split");
  await bar().getByRole("button", { name: "Preview", exact: true }).click();
  assert.strictEqual(await md().locator(".md-edit").isVisible(), false);
  assert.strictEqual(await md().locator(".md-preview").isVisible(), true);
  assert.strictEqual(await tool("Bold").isDisabled(), true, "nothing to format in Preview");
  await bar().getByRole("button", { name: "Edit", exact: true }).click();
  assert.strictEqual(await md().locator(".md-edit").isVisible(), true);
  assert.strictEqual(await md().locator(".md-preview").isVisible(), false);
  assert.strictEqual(await page().evaluate(() => localStorage.getItem("fd.mdview.Plan")), "edit");
  await open("Hello", 5, 5, { name: "Other", keep: true });
  assert.strictEqual(await pressed(), "Split", "another notebook keeps its own");
  await open("Hello", 5, 5, { name: "Plan", keep: true });
  assert.strictEqual(await pressed(), "Edit", "the notebook's choice comes back");
  await bar().getByRole("button", { name: "Split", exact: true }).click();
});

test("on a narrow cell, Split becomes Edit and Preview tabs", { skip }, async () => {
  await open("Hello **there**");
  await page().setViewportSize({ width: 640, height: 900 });
  try {
    await page().evaluate(() => { state.showExplorer = false; renderNotebook(); });
    const width = await md().locator(".md-rich").evaluate((el) => el.getBoundingClientRect().width);
    assert.ok(width < 620, `cell is ${width}px`);
    const tabs = md().getByRole("tablist", { name: "Markdown cell" });
    await tabs.waitFor();
    assert.strictEqual(await md().locator(".md-preview").isVisible(), false);
    await tabs.getByRole("tab", { name: "Preview" }).click();
    assert.strictEqual(await tabs.getByRole("tab", { name: "Preview" }).getAttribute("aria-selected"), "true");
    assert.strictEqual(await md().locator(".md-preview strong", { hasText: "there" }).isVisible(), true);
    assert.strictEqual(await md().locator(".md-edit").isVisible(), false);
    await page().keyboard.press("ArrowLeft");
    assert.strictEqual(await page().evaluate(() => document.activeElement.textContent), "Edit");
    await page().keyboard.press("Enter");
    assert.strictEqual(await md().locator(".md-edit").isVisible(), true);
  } finally {
    await page().setViewportSize({ width: 1440, height: 940 });
  }
  assert.strictEqual(await md().getByRole("tablist").isVisible(), false, "wide again: side by side");
  assert.strictEqual(await md().locator(".md-preview").isVisible(), true);
});

test("the cell is saved as its Markdown source and nothing else", { skip }, async () => {
  await open("Plain", 0, 5);
  await tool("Bold").click();
  saved.length = 0;
  await page().evaluate(() => saveNotebook());
  const cell = saved.at(-1).cells[0];
  assert.deepStrictEqual(cell, { type: "markdown", source: "**Plain**", meta: {}, cell_id: "m1" });
});

test("Esc, or a click outside the cell, finishes editing; the toolbar is keyboard reachable", { skip }, async () => {
  await open("Done");
  await page().keyboard.press("Escape");
  await md().locator(".md-view", { hasText: "Done" }).waitFor();
  assert.strictEqual(await page().getByRole("toolbar").count(), 0);
  // Back in: Tab from the cell's own buttons reaches the toolbar's one Tab stop, the next Tab the
  // editor, and arrows move along the toolbar.
  await page().keyboard.press("Enter");
  await bar().waitFor();
  await md().getByRole("button", { name: "Delete cell" }).focus();
  await page().keyboard.press("Tab");
  assert.strictEqual(await page().evaluate(() => document.activeElement.title), "Bold (Ctrl+B)");
  await page().keyboard.press("Tab");
  assert.strictEqual(await page().evaluate(() => !!document.activeElement.closest(".cm-editor")), true, "one Tab stop for the toolbar");
  await page().keyboard.press("Shift+Tab");  // in the editor, Shift+Tab outdents, as today
  await md().getByRole("button", { name: "Delete cell" }).focus();
  await page().keyboard.press("Tab");
  await page().keyboard.press("ArrowRight");
  assert.strictEqual(await page().evaluate(() => document.activeElement.title), "Italic (Ctrl+I)");
  await page().keyboard.press("End");
  assert.strictEqual(await page().evaluate(() => document.activeElement.textContent), "Preview");
  assert.strictEqual(await bar().locator("button[tabindex='0']").count(), 1);
  await page().keyboard.press("Escape");
  await md().locator(".md-view", { hasText: "Done" }).waitFor();
  await page().keyboard.press("Enter");
  await bar().waitFor();
  await page().locator(".cell").nth(1).locator(".cm-content").click();
  await md().locator(".md-view", { hasText: "Done" }).waitFor();
  assert.strictEqual(await page().getByRole("toolbar", { name: "Markdown formatting" }).count(), 0);
});

test("with Preview features hidden, Markdown editing is as before", { skip }, async () => {
  await open("Before", 0, 6, { preview: false });
  assert.strictEqual(await page().getByRole("toolbar", { name: "Markdown formatting" }).count(), 0);
  assert.strictEqual(await page().locator(".md-rich, .md-hint, .md-preview").count(), 0);
  await page().keyboard.press("Control+b");
  assert.strictEqual(await source(), "Before", "Ctrl+B adds nothing");
  await page().keyboard.press("Escape");
  assert.strictEqual(await page().evaluate(() => state.notebook.cells[0].editing), true, "Esc leaves the cell open, as before");
  await page().locator(".cell").nth(1).locator(".cm-content").click();
  assert.strictEqual(await page().evaluate(() => state.notebook.cells[0].editing), true, "and so does a click outside");
  await page().evaluate(() => { state.settings.show_preview = { ...state.settings.show_preview, value: true }; });
});

test("the editing cell passes axe", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  await open("# Title\n\nSome *text* and a [link](https://example.com).");
  assert.deepStrictEqual(await axeFindings(page(), ".cell.markdown"), []);
  await page().setViewportSize({ width: 640, height: 900 });
  try {
    await page().evaluate(() => { state.showExplorer = false; renderNotebook(); });
    await md().getByRole("tablist").waitFor();
    assert.deepStrictEqual(await axeFindings(page(), ".cell.markdown"), []);
  } finally {
    await page().setViewportSize({ width: 1440, height: 940 });
  }
});
