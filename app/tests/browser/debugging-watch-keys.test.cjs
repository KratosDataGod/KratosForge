// Watches in the variables pane (debugging.js) from the keyboard: Enter adds the watch, saves exactly
// that for the notebook, and leaves focus in the box so the next watch can be typed straight away (the
// pane is drawn afresh, which used to drop focus to the page). An empty box adds nothing. The engine's
// watch store is mocked.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
let watches = [];

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    save_notebook: () => ({ saved: true }),
    kernel_variables: () => [],
    notebook_dependencies: () => ({}),
    notebook_debug_get: () => ({ watches, ignore_hidden: [] }),
    notebook_debug_set: (p) => { if (p.watches) watches = p.watches; return { watches, ignore_hidden: [] }; },
  } });
  await page().evaluate(() => {
    loadIntoState({ name: "Watch keys", cells: [{ type: "code", cell_id: "c1", source: "rows = [1, 2]", meta: {} }] });
    state.showVariables = true;
    go("notebook");
  });
  await page().locator(".varpane .dbg-pane").waitFor();
});
after(async () => { await app?.close(); });

test("Enter adds a watch and keeps focus in the box for the next one; an empty box adds nothing", { skip }, async () => {
  const box = () => page().locator(".varpane").getByRole("textbox", { name: "Watch expression" });
  const sets = () => app.calls.filter((c) => c.method === "notebook_debug_set").map((c) => c.params.watches);
  await box().click();
  await page().keyboard.press("Enter");
  assert.deepStrictEqual(sets(), [], "an empty box saves nothing");

  await page().keyboard.type("len(rows)");
  await page().keyboard.press("Enter");
  await page().locator(".varpane .dbg-watch", { hasText: "len(rows)" }).waitFor();
  await page().waitForFunction(() => document.activeElement?.matches(".varpane .dbg-add"));
  assert.strictEqual(await box().inputValue(), "");

  // Straight on to the next watch, without reaching for the mouse.
  await page().keyboard.type("rows[0]");
  await page().keyboard.press("Enter");
  await page().locator(".varpane .dbg-watch", { hasText: "rows[0]" }).waitFor();
  assert.deepStrictEqual(sets(), [["len(rows)"], ["len(rows)", "rows[0]"]]);
  assert.ok(await page().evaluate(() => document.activeElement?.matches(".varpane .dbg-add")));
  assert.deepStrictEqual(app.errors, []);
});
