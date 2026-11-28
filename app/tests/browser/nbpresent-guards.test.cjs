// Notebook > View > Present (nbpresent.js) edge paths beyond notebook-present.test.cjs: a # line inside
// a fenced code block doesn't start a slide, the slide before the first heading is named by its number, closing
// Present with no cell selected puts the focus back on the page, and hiding the slide strip from
// inside it moves the focus to the slide.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

const FENCED = "# Setup\n\nRun this first:\n\n```bash\n# not a heading, it's a shell comment\npip install x\n```\n\nStill the Setup slide.";
const NB = { name: "Edge deck", cells: [
  { type: "markdown", cell_id: "m1", source: `Before any heading.\n\n${FENCED}`, meta: {} },
  { type: "code", cell_id: "c1", source: "show(df)", meta: {} },
  { type: "markdown", cell_id: "m2", source: "No heading here, just a note.", meta: {} },
  { type: "markdown", cell_id: "m3", source: "# Results", meta: {} },
] };

async function load() {
  await page().evaluate((nb) => {
    closeNotebookPresent();
    loadIntoState(structuredClone(nb));
    state.notebook.cells[1].outputs = [{ type: "table", columns: ["region"], rows: [["West"]] }];
    state.ribbonTab = "view";
    go("notebook");
  }, NB);
  await page().locator(".cell .cm-content").first().waitFor();
}

const present = () => page().getByRole("dialog", { name: /presenting$/ });

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["notebook_print_page"], mocks: { save_notebook: () => ({ saved: true }), notebook_syntax: () => [],
    notebook_dependencies: () => ({ cells: [] }) } });
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  app.errors.length = 0;
  await load();
});

test("a # line inside a fenced block stays on its slide, and the slide before the first heading is named by its number", { skip }, async () => {
  await page().getByRole("button", { name: /^Present/ }).click();
  await present().waitFor();
  await page().keyboard.press("s");
  const thumbs = page().getByRole("navigation", { name: "Slides" }).getByRole("button");
  await thumbs.first().waitFor();
  const titles = (await thumbs.allInnerTexts()).map((t) => t.split("\n")[0]);
  assert.deepStrictEqual(titles, ["1. Slide 1", "2. Setup", "3. Results"]);
  assert.match(await page().locator(".nbp-slide").innerText(), /^Before any heading\.\s*$/);
  await thumbs.nth(1).click();
  assert.match(await page().locator(".nbp-slide").innerText(), /not a heading, it's a shell comment[\s\S]*Still the Setup slide/);
  await page().keyboard.press("Escape");
  await present().waitFor({ state: "detached" });
  assert.deepStrictEqual(app.errors, []);
});

test("closing Present with no cell selected puts the focus back on the page", { skip }, async () => {
  await page().evaluate(() => { state.selected = null; });
  await page().getByRole("button", { name: /^Present/ }).click();
  await present().waitFor();
  await page().keyboard.press("Escape");
  await present().waitFor({ state: "detached" });
  await page().waitForFunction(() => document.activeElement?.id === "view");
  assert.deepStrictEqual(app.errors, []);
});

test("hiding the slide strip from a thumbnail moves the focus to the slide", { skip }, async () => {
  await page().getByRole("button", { name: /^Present/ }).click();
  await present().waitFor();
  await page().keyboard.press("s");
  await page().waitForFunction(() => document.activeElement?.matches?.(".nbp-thumb.current"));
  await page().keyboard.press("s");
  await page().waitForFunction(() => document.activeElement?.matches?.(".nbp-slide"));
  assert.ok(!(await page().getByRole("navigation", { name: "Slides" }).isVisible()));
  await page().keyboard.press("Escape");
  await present().waitFor({ state: "detached" });
  assert.deepStrictEqual(app.errors, []);
});
