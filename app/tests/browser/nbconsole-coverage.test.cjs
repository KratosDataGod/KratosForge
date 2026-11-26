// The scratch console (nbconsole.js), the paths notebook-console leaves out: a widget answer, a run with
// no output, a run the kernel couldn't start, Esc leaving the box, and Copy with nothing to copy.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    save_notebook: () => ({ saved: true }), notebook_syntax: () => [], kernel_variables: () => [],
    start_cell: ({ code }) => {
      if (code === "crash()") throw new Error("The kernel went away.");
      if (code === "slider()") return { status: "ok", outputs: [{ type: "widget", data: {} }], version: 1 };
      return { status: "ok", outputs: [], version: 1 };
    },
  } });
  await page().evaluate(() => {
    loadIntoState({ name: "Scratch cov", cells: [{ type: "code", cell_id: "c1", source: "x = 1", meta: {} }] });
    go("notebook");
    consoleOf(state.notebook).open = true;
    renderNotebook();
  });
  await page().getByLabel("Scratch input").waitFor();
  app.errors.length = 0;
});
after(async () => { await app?.close(); });
beforeEach(() => { if (!skip) app.errors.length = 0; });

const toastSays = (text) => page().waitForFunction((t) => {
  const el = document.getElementById("toast");
  return !el.hidden && el.textContent.startsWith(t);
}, text, { timeout: 10000 });

test("Copy with nothing typed or run says what to do and adds no cell", { skip }, async () => {
  const p = page();
  const pane = p.getByRole("complementary", { name: "Scratch" });
  await pane.getByRole("button", { name: "Copy to a new cell" }).click();
  await toastSays("Type something, or run a line first, to copy it to a cell.");
  assert.strictEqual(await p.evaluate(() => state.notebook.cells.length), 1);
  assert.deepStrictEqual(app.errors, []);
});

test("a widget says it shows in cells, a quiet run says Done, and a kernel that fails shows the error", { skip }, async () => {
  const p = page();
  const pane = p.getByRole("complementary", { name: "Scratch" });
  const box = pane.getByLabel("Scratch input");
  const log = pane.getByRole("log", { name: "Scratch results" });
  await box.fill("slider()");
  await box.press("Shift+Enter");
  await log.getByText("Widgets show in cells, not in the scratch console.").waitFor();
  await box.fill("y = 2");
  await box.press("Shift+Enter");
  await log.getByText("Done, no output.").waitFor();
  await box.fill("crash()");
  await box.press("Shift+Enter");
  await log.locator(".console-entry.failed").filter({ hasText: "crash()" }).waitFor();
  const failed = await log.locator(".console-entry.failed").innerText();
  assert.match(failed, /Kernel error/);
  assert.match(failed, /The kernel went away\./);
  assert.strictEqual(await p.evaluate(() => kernelBusy(state.notebook)), false, "the kernel is idle again");
  assert.deepStrictEqual(app.errors, []);
});

test("Esc in the box leaves it without closing Scratch", { skip }, async () => {
  const p = page();
  const box = p.getByLabel("Scratch input");
  await box.focus();
  await box.press("Escape");
  assert.strictEqual(await p.evaluate(() => document.activeElement?.classList.contains("console-input")), false);
  assert.ok(await p.getByRole("complementary", { name: "Scratch" }).isVisible());
  assert.deepStrictEqual(app.errors, []);
});
