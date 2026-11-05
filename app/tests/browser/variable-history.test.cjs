// #1397: variable history, watches and hidden state in the notebook's Variables pane (debugging.js),
// against the real engine and kernel (see harness.cjs for how to run it). After a cell runs on this
// PC, History lists what it added, changed and removed, with types and sizes and never values. A
// watch added or removed in the pane is saved for the notebook (notebook_debug_set gets exactly the
// new list) and its new value shows after the next run (notebook.Kernel._watch). A cell that uses a
// name no cell defines but the kernel still holds, left over from a deleted or changed cell, gets the
// hidden state warning, which goes once a cell defines the name or the kernel restarts. History is for
// this kernel only: a restart empties it. axe finds nothing serious on the pane and the warning.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, axeSource, axeFindings, settleForAxe } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const noAxe = skip || (!axeSource && "axe-core is not installed");

// Every call here goes to the real engine (server.py); nothing is mocked.
const READS = ["kernel_variables", "notebook_debug_get", "notebook_dependencies", "kernel_complete", "poll_cell"];
const WRITES = ["notebook_debug_set", "save_notebook", "restart_kernel"];
const ENGINE = [...READS, ...WRITES, "start_cell", "interrupt_cell"];

const NAME = "Variable history";
const SECRET = "SECRET-VALUE-123";
const CELLS = [
  { type: "code", cell_id: "v1", source: `label = "${SECRET}"\nrows = [1, 2, 3]\ntemp = 0`, meta: {} },
  { type: "code", cell_id: "v2", source: "rows = rows + [4]\ndel temp\ncount = 7", meta: {} },
];

let app;
const page = () => app.page;
const pane = () => page().locator(".varpane");
const callsSince = (from, method) => app.calls.slice(from).filter((c) => c.method === method);
const writesSince = (from) => app.calls.slice(from).filter((c) => WRITES.includes(c.method)).map((c) => c.method);

// Runs one cell (by cell_id) and waits until the pane has taken in its run: dbgAfterRun has read
// the variables and the dependency graph and drawn the pane and the cell's warning.
async function run(cellId) {
  const done = await page().evaluate(() => window.__dbgDone);
  await page().evaluate((id) => runCell(state.notebook.cells.find((c) => c.cell_id === id)), cellId);
  await page().waitForFunction((done) => window.__dbgDone > done && state.kernel !== "busy", done, { timeout: 60000 });
}

async function showTab(name) {
  await pane().getByRole("tab", { name }).click();
  await pane().locator(".dbg-tab.on", { hasText: name }).waitFor();
}

const historyLines = () => pane().locator(".dbg-run").evaluateAll((runs) => runs.map((r) => [...r.children].map((c) => c.textContent)));

// The engine's own record of the notebook's watches and ignored names (prove_service.notebook_debug_get).
const savedDebug = () => page().evaluate((name) => rpc("notebook_debug_get", { name }), NAME);

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ENGINE });
  await page().evaluate(async ({ name, cells }) => {
    // Counts finished dbgAfterRun calls (runCell calls it by its global name), so a test knows the pane is drawn.
    window.__dbgDone = 0;
    const real = dbgAfterRun;
    window.dbgAfterRun = async (...args) => { try { return await real(...args); } finally { window.__dbgDone += 1; } };
    await rpc("save_notebook", { name, cells });  // notebook_debug_set saves into the notebook's file
    loadIntoState({ name, cells });
    state.showVariables = true;
    go("notebook");
  }, { name: NAME, cells: CELLS });
  await pane().locator(".dbg-pane").waitFor();
});

after(async () => { if (app) await app.close(); });

test("History lists what each cell added, changed and removed, with types and sizes and no values", { skip }, async () => {
  const from = app.calls.length;
  await run("v1");
  await run("v2");
  // Now shows values (so the distinctive value really is in the kernel and readable)...
  await pane().locator("td.name", { hasText: "label" }).waitFor();
  assert.match(await pane().innerText(), new RegExp(SECRET));

  // ...History never does.
  await showTab("History");
  const lines = await historyLines();
  assert.strictEqual(lines.length, 2, "one entry per run, newest first");
  assert.match(lines[0][0], /^Cell 2 · /);
  assert.deepStrictEqual(lines[0].slice(1), ["+ count  int  ", "~ rows  list  4  (was 3)", "− temp      removed"]);
  assert.match(lines[1][0], /^Cell 1 · /);
  assert.deepStrictEqual(lines[1].slice(1).map((l) => l.split("  ")[0]).sort(), ["+ label", "+ rows", "+ temp"]);
  assert.match(lines[1].find((l) => l.startsWith("+ label")), /^\+ label  str  16$/);
  assert.match(lines[1].find((l) => l.startsWith("+ rows")), /^\+ rows  list  3$/);
  const text = await pane().innerText();
  assert.ok(!text.includes(SECRET), "History shows no value");
  assert.ok(!text.includes("[1, 2, 3"), "History shows no list contents");
  assert.match(text, /Names, types and sizes only; values aren't kept\./);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "variable-history.png") });

  // Running cells and reading History saves nothing.
  assert.deepStrictEqual(writesSince(from), []);
  assert.deepStrictEqual(app.errors, []);
});

test("adding and removing a watch saves exactly that for the notebook, and its value shows after the next run", { skip }, async () => {
  let from = app.calls.length;
  await pane().getByRole("textbox", { name: "Watch expression" }).fill("len(rows)");
  await pane().getByRole("button", { name: "Add", exact: true }).click();
  const watch = pane().locator(".dbg-watch", { hasText: "len(rows)" });
  await watch.getByText("Shown after the next cell runs.").waitFor();
  assert.deepStrictEqual(callsSince(from, "notebook_debug_set").map((c) => c.params), [{ name: NAME, watches: ["len(rows)"] }]);
  assert.deepStrictEqual(writesSince(from), ["notebook_debug_set"]);
  assert.deepStrictEqual(await savedDebug(), { watches: ["len(rows)"], ignore_hidden: [] }, "saved with the notebook");

  // The next run sends the watch with the cell and shows its value: rows has 5 items now.
  from = app.calls.length;
  await run("v2");
  assert.deepStrictEqual(callsSince(from, "start_cell").map((c) => c.params.watches), [["len(rows)"]]);
  await watch.locator(".dbg-value", { hasText: /^5$/ }).waitFor();
  await run("v2");
  await watch.locator(".dbg-value", { hasText: /^6$/ }).waitFor();
  assert.match(await watch.locator(".dbg-trail").innerText(), /after cell 2: 5\s+after cell 2: 6 \(changed\)/);

  from = app.calls.length;
  await pane().getByRole("button", { name: "Remove watch len(rows)" }).click();
  await watch.waitFor({ state: "detached" });
  assert.deepStrictEqual(callsSince(from, "notebook_debug_set").map((c) => c.params), [{ name: NAME, watches: [] }]);
  assert.deepStrictEqual(await savedDebug(), { watches: [], ignore_hidden: [] });
  from = app.calls.length;
  await run("v2");
  assert.deepStrictEqual(callsSince(from, "start_cell").map((c) => c.params.watches), [undefined], "a removed watch isn't sent");
  assert.deepStrictEqual(app.errors, []);
});

// The hidden state warning under the cell with this cell_id (the DOM marks cells by their screen id).
const cellSel = (cellId) => page().evaluate((cid) => `.cell[data-cell="${state.notebook.cells.find((c) => c.cell_id === cid).id}"]`, cellId);
async function warning(cellId) { return page().locator(`${await cellSel(cellId)} .dbg-hidden`); }

test("a name left over from a deleted cell gets the hidden state warning, which goes once a cell defines it", { skip }, async () => {
  await page().evaluate(() => {
    state.selected = state.notebook.cells[1].id;
    insertCell("leftover = 41");
    insertCell("answer = leftover + 1");
    renderNotebook();
  });
  const [setter, user] = await page().evaluate(() => state.notebook.cells.slice(2).map((c) => c.cell_id));
  await run(setter);
  await run(user);
  assert.strictEqual(await (await warning(user)).count(), 0, "no warning while a cell defines it");

  await page().evaluate((id) => { deleteCell(state.notebook.cells.find((c) => c.cell_id === id)); renderNotebook(); }, setter);
  await run(user);
  await (await warning(user)).waitFor();
  const text = await (await warning(user)).innerText();
  assert.match(text, /leftover isn't defined by any cell in this notebook/);
  assert.match(text, /it will fail in Fabric, in a proof and after a restart/);
  assert.match(text, /It was last set by a cell that read: leftover = 41/);

  // Insert that cell back above: once it has run, the name has a cell again and the warning goes.
  await (await warning(user)).getByRole("button", { name: "Insert that cell back above" }).click();
  const back = await page().evaluate(() => state.notebook.cells[2]);
  assert.strictEqual(back.source, "leftover = 41");
  await run(back.cell_id);
  await run(user);
  assert.strictEqual(await (await warning(user)).count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("a name left over from a changed cell gets the warning; axe finds nothing serious on it or the pane", { skip }, async () => {
  const [setter, user] = await page().evaluate(() => state.notebook.cells.slice(2).map((c) => c.cell_id));
  await page().evaluate((id) => { setSource(state.notebook.cells.find((c) => c.cell_id === id), "renamed = 41"); renderNotebook(); }, setter);
  await run(user);
  await (await warning(user)).waitFor();
  assert.match(await (await warning(user)).innerText(), /leftover isn't defined by any cell in this notebook[\s\S]*leftover = 41/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "variable-history-hidden.png") });

  if (noAxe) return;
  await pane().getByRole("textbox", { name: "Watch expression" }).fill("count");
  await pane().getByRole("button", { name: "Add", exact: true }).click();
  await pane().locator(".dbg-watch", { hasText: "count" }).getByText("Shown after the next cell runs.").waitFor();
  await run(user);
  await pane().locator(".dbg-watch .dbg-value", { hasText: /^7$/ }).waitFor();
  await showTab("History");
  await settleForAxe(page());
  assert.deepStrictEqual(await axeFindings(page(), ".varpane"), [], "the pane, History tab");
  await showTab("Now");
  assert.deepStrictEqual(await axeFindings(page(), ".varpane"), [], "the pane, Now tab");
  assert.deepStrictEqual(await axeFindings(page(), `${await cellSel(user)} .dbg-hidden`), [], "the warning");
  assert.deepStrictEqual(app.errors, []);
});

test("a kernel restart empties History and clears the warning; History then has this kernel's runs only", { skip }, async () => {
  const user = await page().evaluate(() => state.notebook.cells[3].cell_id);
  await showTab("History");
  assert.ok((await historyLines()).length > 0);
  await (await warning(user)).waitFor();

  const from = app.calls.length;
  await page().evaluate(() => restartKernel());
  await page().locator("#toast", { hasText: "Kernel restarted" }).waitFor();
  assert.strictEqual(callsSince(from, "restart_kernel").length, 1);
  await pane().getByText("Run a cell to see what it adds, changes and removes.").waitFor();
  assert.strictEqual(await pane().locator(".dbg-run").count(), 0);
  assert.strictEqual(await (await warning(user)).count(), 0, "the warning goes with the kernel");

  // The first run after the restart: the kernel is empty, so cell 1 adds everything again, and
  // nothing from before the restart comes back.
  await run("v1");
  const lines = await historyLines();
  assert.strictEqual(lines.length, 1);
  assert.match(lines[0][0], /^Cell 1 · /);
  assert.deepStrictEqual(lines[0].slice(1).map((l) => l.split("  ")[0]).sort(), ["+ label", "+ rows", "+ temp"]);
  assert.ok(!(await pane().innerText()).includes(SECRET));
  assert.deepStrictEqual(app.errors, []);
});
