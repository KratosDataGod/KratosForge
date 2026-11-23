// IntelliSense in Python cells (nbintel.js) against the real engine: hovering a function shows its
// signature and docs, a secret variable's value stays hidden, typing a call shows its parameters
// with the current one marked, and a syntax error gets a red underline. The hover card follows WCAG
// 1.4.13 (content on hover): the pointer can move onto it, Esc closes it without moving the pointer
// or focus, and a long doc can be scrolled to its end.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const SETUP = "def total(rows, *, tax=0.2):\n    'Add up the rows, then add tax.'\n    return sum(rows) * (1 + tax)\napi_token = 'abc123'";
const REPORT = "def report():\n    '''Builds the daily report.\n\n" + Array.from({ length: 40 }, (_, i) => `    Step ${i + 1}: check part ${i + 1}.`).join("\n") +
  "\n    The last line of the docs.'''\n    return 1";
const CELLS = [
  { type: "code", cell_id: "c1", source: SETUP, meta: {} },
  { type: "code", cell_id: "c2", source: "total([1, 2])\napi_token", meta: {} },
  { type: "code", cell_id: "c3", source: "", meta: {} },
  { type: "code", cell_id: "c4", source: REPORT, meta: {} },
];

let app;
const page = () => app.page;
const cell = (i) => page().locator(".cell").nth(i);

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["start_cell", "poll_cell", "kernel_variables", "kernel_complete", "kernel_inspect", "notebook_syntax"],
    mocks: { save_notebook: () => ({ saved: true }) },
  });
  await page().evaluate((cells) => {
    loadIntoState({ name: "Intel", cells });
    go("notebook");
  }, CELLS);
  await page().evaluate(() => runCell(state.notebook.cells[0]));
  await page().evaluate(() => runCell(state.notebook.cells[3]));
});

after(async () => { if (app) await app.close(); });

async function hover(i, text) {
  const at = await page().evaluate(([i, text]) => {
    const view = editors.get(state.notebook.cells[i].id);
    const c = view.coordsAtPos(view.state.doc.toString().indexOf(text) + 1);
    return { x: c.left + 2, y: (c.top + c.bottom) / 2 };
  }, [i, text]);
  await page().mouse.move(at.x, at.y);
}

test("hovering a function shows its signature and docs", { skip }, async () => {
  await hover(1, "total");
  const tip = page().locator(".nb-intel.hover");
  await tip.waitFor();
  const text = await tip.innerText();
  assert.match(text, /total\(rows, \*, tax=0\.2\)/);
  assert.match(text, /Add up the rows, then add tax\./);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-hover.png") });
});

test("hovering a secret variable shows its type but not its value", { skip }, async () => {
  await page().mouse.move(5, 5);
  await page().locator(".nb-intel.hover").waitFor({ state: "hidden" });
  await hover(1, "api_token");
  const tip = page().locator(".nb-intel.hover");
  await tip.getByText("hidden").waitFor();
  assert.doesNotMatch(await tip.innerText(), /abc123/);
  await page().mouse.move(5, 5);
});

// WCAG 1.4.13: the card stays while the pointer moves from the name onto it, and goes when the pointer leaves both.
test("the hover card stays while the pointer moves onto it", { skip }, async () => {
  await hover(1, "total");
  const tip = page().locator(".nb-intel.hover");
  await tip.getByText("Add up the rows").waitFor();
  const box = await tip.boundingBox();
  await page().mouse.move(box.x + box.width / 2, box.y + box.height - 4, { steps: 8 });
  await page().waitForTimeout(700);  // past the hover delay and any hide delay
  assert.ok(await tip.isVisible(), "the card is still shown with the pointer on it");
  assert.match(await tip.innerText(), /total\(rows/, "and it is still the card for total");
  await page().mouse.move(5, 5);
  await tip.waitFor({ state: "hidden" });
});

test("Esc closes the hover card without moving the pointer or focus", { skip }, async () => {
  await cell(2).locator(".cm-content").click();
  await hover(1, "total");
  const tip = page().locator(".nb-intel.hover");
  await tip.waitFor();
  const focused = () => page().evaluate(() => document.activeElement?.closest(".cell")?.dataset.cellId ?? document.activeElement?.className);
  const before = await focused();
  await page().keyboard.press("Escape");
  await tip.waitFor({ state: "hidden" });
  assert.strictEqual(await focused(), before, "focus stays where it was");
  assert.ok(await page().evaluate(() => !!document.activeElement?.closest(".cm-content")), "and the cell is still being edited: Esc closed only the card");
  await page().waitForTimeout(700);
  assert.ok(!(await tip.isVisible()), "the card doesn't come back while the pointer rests there");
  await page().mouse.move(5, 5);
  // Esc goes to the card alone: with the Activity panel open, it closes the card and leaves the panel open.
  await page().evaluate(() => toggleTray(true));
  await hover(1, "total");
  await tip.getByText("Add up the rows").waitFor();
  await page().keyboard.press("Escape");
  await tip.waitFor({ state: "hidden" });
  assert.ok(await page().evaluate(() => activity.open), "the Activity panel is still open");
  await page().evaluate(() => toggleTray(false));
  await page().mouse.move(5, 5);
});

test("a long doc in the hover card can be scrolled to its end", { skip }, async () => {
  await cell(3).scrollIntoViewIfNeeded();
  await hover(3, "report");
  const tip = page().locator(".nb-intel.hover");
  await tip.getByText("Builds the daily report.").waitFor();
  const doc = tip.locator(".nb-intel-doc");
  const box = await doc.boundingBox();
  await page().mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 8 });
  await page().mouse.wheel(0, 4000);
  await page().waitForFunction(() => {
    const d = document.querySelector(".nb-intel.hover .nb-intel-doc");
    return d && d.scrollTop > 0 && d.scrollTop + d.clientHeight >= d.scrollHeight - 1;
  }, null, { timeout: 3000 });
  const last = await doc.evaluate((d) => {
    const r = document.createRange();
    const node = [...d.childNodes].find((n) => n.textContent.includes("The last line"));
    r.selectNodeContents(node);
    const rects = [...r.getClientRects()], box = d.getBoundingClientRect();
    return rects.at(-1).bottom <= box.bottom + 1;
  });
  assert.ok(last, "the doc's last line is in view");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-hover-long-doc.png") });
  await page().mouse.move(5, 5);
  await tip.waitFor({ state: "hidden" });
});

test("typing a call shows its parameters with the current one marked", { skip }, async () => {
  await cell(2).locator(".cm-content").click();
  await page().keyboard.type("total([3], ta");
  const tip = page().locator(".nb-intel.call");
  await tip.waitFor();
  assert.match(await tip.innerText(), /total\(rows, tax=0\.2\)/);
  await page().keyboard.type("x=");
  await page().waitForFunction(() => document.querySelector(".nb-intel.call b")?.textContent === "tax=0.2");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-signature.png") });
  await page().keyboard.press("Escape");
  await tip.waitFor({ state: "hidden" });
});

test("a syntax error is underlined while typing, and fixing it clears it", { skip }, async () => {
  await cell(2).locator(".cm-content").click();
  await page().keyboard.press("Control+a");
  await page().keyboard.type("x = (1,\ny = 2");
  await cell(2).locator(".cm-lintRange-error").first().waitFor({ timeout: 5000 });
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-syntax.png") });
  await page().keyboard.press("Control+a");
  await page().keyboard.type("x = (1,)\ny = 2");
  await page().waitForFunction(() => !document.querySelectorAll(".cell")[2].querySelector(".cm-lintRange-error"), null, { timeout: 5000 });
  // A SQL cell isn't checked as Python.
  await page().keyboard.press("Control+a");
  await page().keyboard.type("%%sql\nSELECT (");
  await page().waitForTimeout(1200);
  assert.strictEqual(await cell(2).locator(".cm-lintRange-error").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});
