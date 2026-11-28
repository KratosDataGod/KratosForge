// Scroll output (N45), as JupyterLab's "Enable scrolling for outputs": a checkbox item in a code cell's
// ... menu keeps the cell's output to a fixed height and scrolls it. The scroll area is a named region the
// keyboard can reach, the setting is the cell's meta.scrolled (saved as metadata.scrolled, notebook.py),
// and turning it off shows the whole output again.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const LONG = Array.from({ length: 300 }, (_, i) => `line ${i + 1}`).join("\n");

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { save_notebook: () => ({ saved: true }) } });
});
after(async () => { await app?.close(); });

const menuItem = async () => {
  const cell = page().locator(".cell").first();
  await cell.hover();
  await cell.getByRole("button", { name: "More cell actions" }).click();
  return page().getByRole("menuitemcheckbox", { name: "Scroll output" });
};

test("Scroll output in a cell's ... menu caps its output, scrolls from the keyboard and turns off again", { skip }, async () => {
  await page().evaluate((text) => {
    loadIntoState({ name: "Scroll", cells: [{ type: "code", cell_id: "c1", source: "print(long)", meta: {} }] });
    state.notebook.cells[0].outputs = [{ type: "stream", text }];
    go("notebook");
  }, LONG);
  await page().locator(".cell .output pre").first().waitFor();
  const tall = await page().locator(".cell .output").first().evaluate((e) => e.getBoundingClientRect().height);
  assert.ok(tall > 1000, "without it the whole output shows");
  assert.strictEqual(await page().locator(".out-scroll").count(), 0);

  let item = await menuItem();
  assert.strictEqual(await item.getAttribute("aria-checked"), "false");
  await item.click();
  await page().waitForFunction(() => state.notebook.cells[0].meta.scrolled === true);
  const region = page().getByRole("region", { name: "Cell output, scrolls" });
  await region.waitFor();
  const box = await region.evaluate((e) => ({ h: e.clientHeight, sh: e.scrollHeight, tab: e.tabIndex }));
  assert.ok(box.h <= 400 && box.sh > box.h, `capped and scrollable: ${JSON.stringify(box)}`);
  assert.strictEqual(box.tab, 0);
  await region.focus();
  await page().keyboard.press("PageDown");
  await page().waitForFunction(() => document.querySelector(".out-scroll").scrollTop > 0);
  assert.strictEqual(await page().evaluate(() => state.notebook.dirty), true, "the notebook has a change to save");

  item = await menuItem();
  assert.strictEqual(await item.getAttribute("aria-checked"), "true");
  await item.click();
  await page().waitForFunction(() => !state.notebook.cells[0].meta.scrolled && !document.querySelector(".out-scroll"));
  assert.deepStrictEqual(app.errors || [], []);
});

test("a cell saved with metadata.scrolled opens scrolled; Markdown cells don't offer it", { skip }, async () => {
  await page().evaluate((text) => {
    loadIntoState({ name: "Scroll", cells: [
      { type: "code", cell_id: "c1", source: "print(long)", meta: { scrolled: true } },
      { type: "markdown", cell_id: "m1", source: "Notes", meta: {} },
    ] });
    state.notebook.cells[0].outputs = [{ type: "stream", text }];
    go("notebook");
  }, LONG);
  await page().getByRole("region", { name: "Cell output, scrolls" }).waitFor();
  const md = page().locator(".cell").nth(1);
  await md.hover();
  await md.getByRole("button", { name: "More cell actions" }).click();
  await page().locator("#menu").waitFor();
  assert.strictEqual(await page().getByRole("menuitemcheckbox", { name: "Scroll output" }).count(), 0);
  await page().keyboard.press("Escape");
});
