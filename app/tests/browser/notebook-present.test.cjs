// Notebook > View > Present (nbpresent.js, parity N49, Preview): the notebook's Markdown and the outputs
// saved from its last run as 16:9 slides, full screen, without the code. A slide starts at each # or ##
// heading (### stays on the slide), or only at the cells marked "Starts a slide" once any is. The outputs
// are the real engine's export page (notebook_print_page), as the report's are; nothing runs.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const RUNS = /^(start_cell|poll_cell|debug_cell|engine_start_cell|engine_poll_cell|run_notebook_fabric_step|notebook_vorder_cell|sql_start|restart_kernel)$/;

const NB = { name: "Quarterly review", cells: [
  { type: "markdown", cell_id: "m1", source: "# Quarterly review\n\nRevenue by region since 1 October.", meta: {} },
  { type: "code", cell_id: "c1", source: "secret_code = spark.read.table('gold.fact_sales')", meta: {} },
  { type: "markdown", cell_id: "m2", source: "## West\n\nThe West grew most.\n\n### Detail\n\nStill the West slide.", meta: {} },
  { type: "code", cell_id: "c2", source: "show(df)", meta: {} },
  { type: "code", cell_id: "c3", source: "empty_cell = 1", meta: {} },
  { type: "markdown", cell_id: "m3", source: "Intro to the next part.\n\n## North\n\nNorth held level.", meta: {} },
] };

async function load(nb = NB, { ranAt = Date.UTC(2026, 10, 14, 10, 42) } = {}) {
  await page().evaluate(([nb, ranAt]) => {
    closeNotebookPresent();
    loadIntoState(structuredClone(nb));
    const [, c1, , c2] = state.notebook.cells;
    c1.outputs = [{ type: "stream", text: "loaded 3 rows\n" }];
    c2.outputs = [{ type: "table", columns: ["region", "revenue"], rows: [["West", 1204330.5], ["North", 998120]] }];
    if (ranAt) c2.finishedAtMs = ranAt;
    state.ribbonTab = "view";
    go("notebook");
  }, [nb, ranAt]);
  await page().locator(".cell .cm-content").first().waitFor();
}

const present = () => page().getByRole("dialog", { name: /presenting$/ });
const slideText = () => page().locator(".nbp-slide").innerText();

async function cellMenu(i) {
  const cell = page().locator(".cell").nth(i);
  await cell.locator(".gutter").click();
  await cell.hover();
  await cell.locator("button[aria-label='More cell actions']").click();
  await page().locator("#menu").waitFor();
}

async function openWithShortcut(cellIndex = 3) {
  await page().locator(".cell").nth(cellIndex).locator(".gutter").click();
  await page().waitForFunction((i) => document.activeElement?.matches?.(".cell") && document.activeElement === document.querySelectorAll(".cell")[i], cellIndex);
  await page().keyboard.press("Control+Shift+F5");
  await present().waitFor();
}

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["notebook_print_page"], mocks: { save_notebook: () => ({ saved: true }), notebook_syntax: () => [],
    notebook_dependencies: () => ({ cells: [] }) } });
});
after(async () => { await app?.close(); });

test("View > Present opens the slides full screen, from the engine's export page, and nothing runs", { skip }, async () => {
  await load();
  const before = app.calls.length;
  await page().getByRole("button", { name: /^Present/ }).click();
  await present().waitFor();
  // The full-screen element is the present container when the browser allows it; the overlay covers the window either way.
  const shown = await page().evaluate(() => ({ fs: document.fullscreenElement?.className || null,
    fixed: getComputedStyle(document.querySelector(".nb-present")).position }));
  assert.ok(shown.fs === null || shown.fs === "nb-present", shown.fs);
  assert.strictEqual(shown.fixed, "fixed");
  const top = await page().locator(".nbp-top").innerText();
  assert.match(top, /Read-only\. Nothing runs\./);
  assert.match(top, /Outputs from the last run, .*2026/);
  assert.match(await slideText(), /Quarterly review[\s\S]*Revenue by region[\s\S]*loaded 3 rows/);
  assert.doesNotMatch(await present().innerText(), /secret_code|show\(df\)|empty_cell/, "code is hidden");
  const made = app.calls.slice(before);
  const pages = made.filter((c) => c.method === "notebook_print_page");
  assert.strictEqual(pages.length, 1, "one export page for the outputs");
  assert.deepStrictEqual(pages[0].params.cells.map((c) => c.cell_id), ["c1", "c2"], "only the cells with outputs go to the export page");
  assert.ok(!app.calls.some((c) => RUNS.test(c.method)), `nothing runs: ${app.calls.map((c) => c.method).filter((m) => RUNS.test(m))}`);
  await page().keyboard.press("Escape");
  await present().waitFor({ state: "detached" });
});

test("a slide starts at # and ##, ### stays on it, and a cell with no output is left out", { skip }, async () => {
  await load();
  await openWithShortcut();
  const titles = await page().evaluate(() => nbPresent.slides.map((s) => s.title));
  assert.deepStrictEqual(titles, ["Quarterly review", "West", "North"]);
  assert.strictEqual(await page().locator(".nbp-count").innerText(), "1 / 3");
  await page().keyboard.press("ArrowRight");
  const west = await slideText();
  assert.match(west, /West[\s\S]*The West grew most\.[\s\S]*Detail[\s\S]*Still the West slide\.[\s\S]*West\s+1204330\.5[\s\S]*Intro to the next part\./);
  assert.strictEqual(await page().locator(".nbp-slide table").count(), 1, "the table output is the export page's table");
  await page().keyboard.press("ArrowRight");
  assert.match(await slideText(), /^North[\s\S]*North held level\.$/);
  assert.doesNotMatch(await present().innerText(), /empty_cell/);
  await page().keyboard.press("Escape");
});

test("arrows and Page keys move, the count is announced politely, and Esc goes back to the cell you started from", { skip }, async () => {
  await load();
  await openWithShortcut(3);
  const from = await page().evaluate(() => nbPresent.from);
  const live = page().locator("[data-nbp-live]");
  assert.strictEqual(await live.getAttribute("aria-live"), "polite");
  await page().keyboard.press("PageDown");
  assert.strictEqual(await page().locator(".nbp-count").innerText(), "2 / 3");
  await page().waitForFunction(() => document.querySelector("[data-nbp-live]").textContent === "Slide 2 of 3");
  await page().keyboard.press("ArrowRight");
  assert.strictEqual(await page().locator(".nbp-count").innerText(), "3 / 3");
  assert.ok(await page().getByRole("button", { name: "Next ›" }).isDisabled());
  await page().keyboard.press("PageUp");
  await page().keyboard.press("ArrowLeft");
  assert.strictEqual(await page().locator(".nbp-count").innerText(), "1 / 3");
  assert.strictEqual(await page().evaluate(() => state.view), "notebook", "Left doesn't go Back while presenting");
  await page().getByRole("button", { name: "Next ›" }).click();
  assert.strictEqual(await page().locator(".nbp-count").innerText(), "2 / 3");
  await page().getByRole("button", { name: "‹ Previous" }).click();
  assert.strictEqual(await page().locator(".nbp-count").innerText(), "1 / 3");
  assert.strictEqual(await page().locator(".nbp-dots").getAttribute("aria-hidden"), "true");
  assert.match(await page().locator(".nbp-bottom").innerText(), /← → to move · S for slides · Esc to leave/);

  // Tab stays inside while presenting.
  for (let i = 0; i < 12; i++) {
    await page().keyboard.press(i % 3 ? "Tab" : "Shift+Tab");
    assert.ok(await page().evaluate(() => document.querySelector(".nb-present").contains(document.activeElement)), `Tab ${i} left the slides`);
  }
  await page().keyboard.press("Escape");
  await present().waitFor({ state: "detached" });
  assert.strictEqual(await page().evaluate(() => document.activeElement?.dataset?.cell), String(from));
});

test("S shows the numbered slides with the current one marked; a marked cell says it starts a slide", { skip }, async () => {
  const nb = structuredClone(NB);
  nb.cells[2].meta = { slide_start: true };
  await load(nb);
  await openWithShortcut();
  const strip = page().getByRole("navigation", { name: "Slides" });
  assert.ok(!(await strip.isVisible()));
  await page().keyboard.press("s");
  await strip.waitFor();
  const thumbs = strip.getByRole("button");
  assert.deepStrictEqual(await thumbs.allInnerTexts(), ["1. Quarterly review", "2. West\nStarts a slide"]);
  assert.strictEqual(await thumbs.nth(0).getAttribute("aria-current"), "true");
  await thumbs.nth(1).click();
  assert.strictEqual(await page().locator(".nbp-count").innerText(), "2 / 2");
  assert.strictEqual(await thumbs.nth(1).getAttribute("aria-current"), "true");
  assert.strictEqual(await thumbs.nth(0).getAttribute("aria-current"), null);
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), ".nb-present"), []);
  await page().keyboard.press("S");
  assert.ok(!(await strip.isVisible()));
  await page().keyboard.press("Escape");
});

test("once a cell is marked, only the marks start slides; the mark is set from the cell menu and saved in the cell's metadata", { skip }, async () => {
  await load();
  // Mark the second code cell (c2) from its cell menu.
  await cellMenu(3);
  const item = page().getByRole("menuitemcheckbox", { name: /Starts a slide/ });
  assert.strictEqual(await item.getAttribute("aria-checked"), "false");
  await item.focus();  // the menu may open past the window's bottom edge: pick it with the keyboard
  await page().keyboard.press("Enter");
  assert.deepStrictEqual(await page().evaluate(() => state.notebook.cells.map((c) => !!c.meta?.slide_start)), [false, false, false, true, false, false]);
  assert.strictEqual(await page().locator(".cell").nth(3).locator(".nbp-mark").innerText(), "Starts a slide");
  await page().keyboard.press("Control+Shift+F5");
  await present().waitFor();
  // Slide 1: everything before the mark, ## West included; slide 2: the table, then ## North stays on it.
  assert.strictEqual(await page().locator(".nbp-count").innerText(), "1 / 2");
  assert.match(await slideText(), /Quarterly review[\s\S]*West[\s\S]*Still the West slide\.$/);
  await page().keyboard.press("ArrowRight");
  assert.match(await slideText(), /^region[\s\S]*North held level\.$/);
  await page().keyboard.press("Escape");

  // The cell's meta goes to the engine as it is, which saves the mark under fabric_desktop in the .ipynb
  // (test_ipynb_round_trip.py); nothing else in the cell's meta changed.
  assert.deepStrictEqual(await page().evaluate(() => state.notebook.cells[3].meta), { slide_start: true });
  await cellMenu(3);
  await page().getByRole("menuitemcheckbox", { name: /Starts a slide/ }).focus();
  await page().keyboard.press("Enter");
  assert.strictEqual(await page().evaluate(() => "slide_start" in state.notebook.cells[3].meta), false);
});

test("full screen refused: the fixed overlay; no known run time leaves the line out; nothing to show doesn't open", { skip }, async () => {
  await load(NB, { ranAt: null });
  await page().evaluate(() => {
    window.__fs = Element.prototype.requestFullscreen;
    Element.prototype.requestFullscreen = () => Promise.reject(new TypeError("Permissions check failed"));
  });
  await openWithShortcut();
  await page().waitForTimeout(100);
  assert.deepStrictEqual(await page().evaluate(() => ({ fs: document.fullscreenElement, ...(({ position, top, left }) => ({ position, top, left }))(getComputedStyle(document.querySelector(".nb-present"))) })),
    { fs: null, position: "fixed", top: "0px", left: "0px" });
  assert.strictEqual(await present().count(), 1, "still presenting");
  await page().evaluate(() => { Element.prototype.requestFullscreen = window.__fs; });
  const top = await page().locator(".nbp-top").innerText();
  assert.match(top, /Read-only\. Nothing runs\./);
  assert.doesNotMatch(top, /Outputs from the last run/);
  await page().keyboard.press("Escape");
  await page().evaluate(() => {
    loadIntoState({ name: "Bare", cells: [{ type: "code", cell_id: "x1", source: "x = 1", meta: {} }] });
    go("notebook");
  });
  await page().locator(".cell .cm-content").first().waitFor();
  await page().evaluate(() => openNotebookPresent());
  await page().locator("#toast").filter({ hasText: "There's nothing to present" }).waitFor();
  assert.strictEqual(await present().count(), 0);
});

test("F5 still reaches the debugger's Continue, and Ctrl+Shift+F5 doesn't", { skip }, async () => {
  await load();
  await page().evaluate(() => {
    window.__dbg = [];
    window.__realPaused = dbgPausedCell;
    window.__realCommand = dbgCommand;
    window.dbgPausedCell = () => state.notebook.cells[1];
    window.dbgCommand = (a) => { window.__dbg.push(a); };
  });
  try {
    await page().locator(".cell").nth(3).locator(".gutter").click();
    await page().keyboard.press("F5");
    assert.deepStrictEqual(await page().evaluate(() => window.__dbg), ["continue"]);
    assert.strictEqual(await present().count(), 0);
    await page().keyboard.press("Control+Shift+F5");
    await present().waitFor();
    assert.deepStrictEqual(await page().evaluate(() => window.__dbg), ["continue"], "Ctrl+Shift+F5 isn't Continue");
    await page().keyboard.press("Escape");
    assert.strictEqual(await page().evaluate(() => dbgKeyAction(new KeyboardEvent("keydown", { key: "F5", ctrlKey: true, shiftKey: true }))), null);
    assert.strictEqual(await page().evaluate(() => dbgKeyAction(new KeyboardEvent("keydown", { key: "F5" }))), "continue");
  } finally {
    await page().evaluate(() => { window.dbgPausedCell = window.__realPaused; window.dbgCommand = window.__realCommand; });
  }
});

test("with Preview features off, the button, the menu item and the shortcut are absent", { skip }, async () => {
  await page().evaluate(() => { state.settings = { ...(state.settings || {}), show_preview: { value: false } }; });
  try {
    await load();
    assert.strictEqual(await page().getByRole("button", { name: /^Present/ }).count(), 0);
    await cellMenu(3);
    await page().locator("#menu").waitFor();
    assert.strictEqual(await page().locator("#menu").getByText("Starts a slide").count(), 0);
    await page().keyboard.press("Escape");
    await page().locator(".cell").nth(3).locator(".gutter").click();
    await page().keyboard.press("Control+Shift+F5");
    await page().waitForTimeout(200);
    assert.strictEqual(await present().count(), 0);
    assert.ok(!app.calls.some((c) => RUNS.test(c.method)), "nothing ran in any test");
  } finally {
    await page().evaluate(() => { state.settings.show_preview.value = true; });
  }
});
