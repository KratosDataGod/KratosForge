// Notebook > View > Present (nbpresent.js) beyond notebook-present.test.cjs: headings inside fenced code don't
// start a slide, a slide without a heading is called by its number, the Slides button and S open and close the
// strip, Home and End, Ctrl+Shift+F5 while presenting, focus kept on the slide, leaving when the cell it opened
// from is gone, leaving full screen another way, outputs that couldn't be read, leaving the notebook while
// they're read, and the calls that do nothing when Present isn't open. The export page is mocked in
// notebook_print_page's shape; nothing runs.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
let printPage = null;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { save_notebook: () => ({ saved: true }), notebook_syntax: () => [], notebook_dependencies: () => ({ cells: [] }),
    notebook_print_page: (p) => printPage(p) } });
});
after(async () => { await app?.close(); });
beforeEach(async () => {
  if (skip) return;
  printPage = () => ({ html: "<div class=\"nbfp-cell\"><pre>3 rows</pre></div>" });
  await page().evaluate(() => { closeNotebookPresent(); closeDialog(); toastClose(); });
  app.errors.length = 0;
});

const MD = { name: "Talk", cells: [
  { type: "markdown", cell_id: "m1", source: "Just an opening line.", meta: {} },
  { type: "markdown", cell_id: "m2", source: "# One\n\n```\n# not a slide\n```\n\nStill one.", meta: {} },
  { type: "markdown", cell_id: "m3", source: "## Two\n\n~~~~\n## also not\n~~~ trailing\n~~~~\n\nStill two.", meta: {} },
  { type: "markdown", cell_id: "m4", source: "## Three", meta: {} },
] };
async function load(nb = MD) {
  await page().evaluate((nb) => {
    loadIntoState(structuredClone(nb));
    state.ribbonTab = "view";
    go("notebook");
  }, nb);
  await page().locator(".cell").first().waitFor();
}
const present = () => page().getByRole("dialog", { name: /presenting$/ });

test("a heading inside fenced code stays on its slide, and a slide with no heading is called by its number", { skip }, async () => {
  const out = await page().evaluate((cells) => {
    const slides = nbPresentSplit(cells);
    return { n: slides.length, titles: slides.map((s, i) => nbPresentTitle(s, i + 1)),
      pieces: nbPresentPieces("# A\n```\n# x\n```\n## B").length, open: nbPresentPieces("# A\n```\n## B").length };
  }, MD.cells);
  assert.deepStrictEqual(out, { n: 4, titles: ["Slide 1", "One", "Two", "Three"], pieces: 2, open: 1 });
  assert.deepStrictEqual(app.errors, []);
});

test("the Slides button, S, Home, End and Ctrl+Shift+F5 while presenting; focus stays on the slide", { skip }, async () => {
  await load();
  await page().evaluate(() => openNotebookPresent());
  await present().waitFor();
  const count = () => page().locator(".nbp-count").innerText();
  await page().keyboard.press("End");
  assert.strictEqual(await count(), "4 / 4");
  await page().keyboard.press("Home");
  assert.strictEqual(await count(), "1 / 4");
  await page().keyboard.press("Control+Shift+F5");
  assert.strictEqual(await page().locator(".nb-present").count(), 1, "still one Present");

  await present().getByRole("button", { name: "Slides" }).click();
  await page().locator(".nbp-strip:not([hidden]) .nbp-thumb.current").waitFor();
  assert.strictEqual(await page().evaluate(() => document.activeElement.matches(".nbp-thumb.current")), true);
  await page().keyboard.press("s");
  await page().locator(".nbp-strip[hidden]").waitFor({ state: "attached" });
  assert.strictEqual(await page().evaluate(() => document.activeElement.matches(".nbp-slide")), true);

  // Focus that lands outside Present comes back to the slide.
  const back = await page().evaluate(() => {
    const b = document.createElement("button"); document.body.append(b); b.focus();
    const on = document.activeElement.matches(".nbp-slide"); b.remove(); return on;
  });
  assert.strictEqual(back, true);

  // The cell it opened from is gone: leaving focuses the page.
  await page().evaluate(() => { nbPresent.from = "gone"; });
  await page().keyboard.press("Escape");
  await present().waitFor({ state: "detached" });
  assert.strictEqual(await page().evaluate(() => document.activeElement?.id), "view");
  assert.deepStrictEqual(app.errors, []);
});

test("leaving full screen another way leaves Present", { skip }, async () => {
  await load();
  // The browser may or may not grant full screen; here it refuses, so the event below is the only way out.
  await page().evaluate(() => {
    window.__rfs = Element.prototype.requestFullscreen;
    Element.prototype.requestFullscreen = () => Promise.reject(new Error("refused"));
  });
  await page().evaluate(() => openNotebookPresent());
  await present().waitFor();
  await page().evaluate(() => { nbPresent.fullscreen = true; document.dispatchEvent(new Event("fullscreenchange")); });
  await present().waitFor({ state: "detached" });
  await page().evaluate(() => { Element.prototype.requestFullscreen = window.__rfs; });
  assert.deepStrictEqual(app.errors, []);
});

test("outputs that couldn't be read say so; leaving the notebook while they're read opens nothing", { skip }, async () => {
  const withOutput = { name: "With output", cells: [{ type: "code", cell_id: "c1", source: "x", meta: {} }] };
  const giveOutputs = () => page().evaluate(() => { state.notebook.cells[0].outputs = [{ type: "stream", text: "3 rows\n" }]; });
  await load(withOutput);
  await giveOutputs();
  printPage = () => { throw new Error("The export page couldn't be made"); };
  await page().evaluate(() => openNotebookPresent());
  await page().waitForFunction(() => /^Could not show the notebook's outputs/.test(document.getElementById("toast").textContent));
  assert.strictEqual(await present().count(), 0);
  await page().evaluate(() => toastClose());

  let release;
  const held = new Promise((r) => { release = r; });
  printPage = async () => { await held; return { html: "<div class=\"nbfp-cell\"><pre>3 rows</pre></div>" }; };
  await load(withOutput);
  await giveOutputs();
  const opening = page().evaluate(() => openNotebookPresent());
  await page().waitForFunction(() => nbPresent.opening);
  assert.strictEqual(await page().evaluate(() => { openNotebookPresent(); return nbPresent.opening; }), true, "a second open while opening waits");
  await page().evaluate(() => go("home"));
  release();
  await opening;
  assert.strictEqual(await page().locator(".nb-present").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("with Present closed, moving, the strip and its keys do nothing; Ctrl+Shift+F5 over a dialog doesn't open it", { skip }, async () => {
  await load();
  await page().evaluate(() => { nbPresentGo(2); nbPresentStrip(true); nbPresentKey(new KeyboardEvent("keydown", { key: "End" })); closeNotebookPresent(); });
  await page().evaluate(() => showDialog("Something else", document.createElement("div"), []));
  await page().keyboard.press("Control+Shift+F5");
  assert.strictEqual(await page().locator(".nb-present").count(), 0);
  await page().evaluate(() => closeDialog());
  assert.deepStrictEqual(app.errors, []);
});
