// Validate notebook (validate.js) beyond validate.test.cjs: a clean run still going when the panel opens
// (closing says it keeps going, Cancel asks the engine to stop it, and its result arrives), the count
// chips, a code check that fails and Check again, a clean run that can't be contained or that needs a
// yes first, a result gone out of date, and Clean up when the plan can't be read, has nothing, or has a
// tidy-up to untick and a suggestion to go to. The engine is mocked, so each answer is set per test.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

const CELLS = [
  { type: "code", cell_id: "a", source: "rows = read()", meta: {} },
  { type: "code", cell_id: "b", source: "import os\nwrite(rows)", meta: {} },
];
const FINDINGS = [
  { severity: "error", title: "Cell 2 stopped", text: "NameError: read", cell: 2, found_by: ["run"] },
  { severity: "warning", title: "Unused import os", cell: 2, found_by: ["code"] },
  { severity: "warning", title: "A print in a loop", found_by: ["code"] },
];
const CHECKED = { verdict: "not_ready", counts: { error: 1, warning: 2 }, findings: FINDINGS, run: { ok: true, uncontained: [], blockers: [] } };

let list, record, check, plan;
before(async () => {
  if (skip) return;
  app = await openApp({ viewport: { width: 1440, height: 900 }, mocks: {
    save_notebook: () => ({ saved: true }),
    notebook_validate_list: () => list,
    notebook_validate_status: () => record,
    notebook_validate_check: () => { if (check instanceof Error) throw check; return check; },
    notebook_cleanup_plan: () => { if (plan instanceof Error) throw plan; return plan; },
    notebook_validate_start: () => ({ id: "v2" }),
    notebook_validate_cancel: () => ({ cancelled: true }),
  } });
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  list = [];
  record = null;
  check = JSON.parse(JSON.stringify(CHECKED));
  plan = { tidy: [], suggestions: [] };
  await page().evaluate((cells) => {
    document.getElementById("dialog-root").replaceChildren();
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    loadIntoState({ name: `Daily ${Math.random().toString(36).slice(2, 7)}`, cells: JSON.parse(JSON.stringify(cells)) });
    state.ribbonTab = "run";
    go("notebook");
  }, CELLS);
});

const pane = () => page().locator(".validatepane");
const openPanel = () => page().getByRole("button", { name: /^Validate/ }).click();
const calls = (m) => app.calls.filter((c) => c.method === m).map((c) => c.params);
const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test(document.getElementById("toast").textContent), re.source);
const titles = () => pane().locator(".validate-find b").allInnerTexts();

test("a clean run still going is followed: closing says it keeps going, Cancel stops it, and its result arrives", { skip }, async () => {
  list = [{ id: "v1" }];
  record = { id: "v1", state: "running", cell: 2, cells: [{ cell: 1, status: "ok", seconds: 0.4 }] };
  await openPanel();
  await pane().getByText("running cell 2, 1 done").waitFor();
  await pane().getByRole("button", { name: "Close Validate" }).click();
  await toastSays(/^The clean run keeps going\. Open Validate again to follow it\.$/);
  await pane().waitFor({ state: "detached" });

  await openPanel();
  await pane().getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(calls("notebook_validate_cancel").at(-1), { id: "v1" });
  record = { id: "v1", state: "cancelled", verdict: "not_ready", summary: "Cancelled at cell 2", counts: { error: 1, warning: 2 }, findings: FINDINGS,
    cells: [{ cell: 1, status: "ok", seconds: 0.4 }, { cell: 2, status: "cancelled" }] };
  await pane().getByText("Not ready: 1 error, 2 warnings").waitFor({ timeout: 10000 });
  await toastSays(/: Cancelled at cell 2$/);
  assert.match(await pane().innerText(), /Cell 1\s+Succeeded · 0\.4 s\s+Cell 2\s+Cancelled/);
  await pane().getByRole("button", { name: "Validate again" }).waitFor();
});

test("the count chips show one severity at a time", { skip }, async () => {
  await openPanel();
  await pane().getByText("Not ready: 1 error, 2 warnings").waitFor();
  assert.deepStrictEqual(await titles(), ["Cell 2 stopped", "Unused import os", "A print in a loop"]);
  await pane().getByRole("button", { name: "Warnings 2" }).click();
  await pane().getByRole("button", { name: "Warnings 2", pressed: true }).waitFor();
  assert.deepStrictEqual(await titles(), ["Unused import os", "A print in a loop"]);
  await pane().getByRole("button", { name: "Errors 1" }).click();
  assert.deepStrictEqual(await titles(), ["Cell 2 stopped"]);
  await pane().getByRole("button", { name: "Info 0" }).click();
  assert.deepStrictEqual(await titles(), []);
});

test("a code check that fails says why, and Check again reads it", { skip }, async () => {
  check = new Error("The engine stopped while reading cell 2.");
  await openPanel();
  await pane().locator(".problems", { hasText: "The engine stopped while reading cell 2." }).waitFor();
  check = JSON.parse(JSON.stringify(CHECKED));
  await pane().getByRole("button", { name: "Check again" }).click();
  await pane().getByText("Not ready: 1 error, 2 warnings").waitFor();
});

test("a clean run that can't be contained is offered only with a yes; one that can't run at all says which cell", { skip }, async () => {
  check.run = { ok: false, blockers: [{ cell: 2, text: "starts a Spark session on a cluster" }], uncontained: [] };
  await openPanel();
  await pane().getByText("A clean run can't contain cell 2: it starts a Spark session on a cluster.").waitFor();
  assert.ok(await pane().getByRole("button", { name: "Run clean check" }).isDisabled());

  check.run = { ok: true, blockers: [], uncontained: [{ cell: 2, line: 2, text: "calls an HTTP API" }, { cell: 1, text: "writes C:\\temp\\out.csv" }] };
  await pane().getByRole("button", { name: "Check again" }).click();
  await pane().getByRole("button", { name: "Run clean check" }).and(page().locator(":enabled")).waitFor();
  await pane().getByRole("button", { name: "Run clean check" }).click();
  const confirm = page().locator("#dialog-root .dialog");
  await confirm.getByText("Cell 2, line 2: calls an HTTP API", { exact: false }).waitFor();
  assert.match(await confirm.innerText(), /Cell 1: writes C:\\temp\\out\.csv/);
  await confirm.getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(calls("notebook_validate_start"), [], "no means no run");

  record = { id: "v2", state: "running", cells: [] };
  await pane().getByRole("button", { name: "Run clean check" }).click();
  await confirm.getByRole("button", { name: "Run clean check" }).click();
  await pane().getByText("Clean run: starting a fresh kernel").waitFor();
  assert.strictEqual(calls("notebook_validate_start").at(-1).allow_uncontained, true);
  record = { id: "v2", state: "done", verdict: "ready_with_warnings", summary: "Every cell ran", counts: { warning: 1 }, findings: [FINDINGS[1]],
    cells: [{ cell: 1, status: "ok", seconds: 1 }, { cell: 2, status: "ok", seconds: 2 }] };
  await pane().getByText("Ready with warnings: 1 warning").waitFor({ timeout: 10000 });
});

test("a result for code that changed since says which cell, and Check again reads it afresh", { skip }, async () => {
  await openPanel();
  await pane().getByText("Not ready: 1 error, 2 warnings").waitFor();
  await page().evaluate(() => { setSource(state.notebook.cells[0], "rows = read_all()"); drawValidate(state.notebook); });
  await pane().getByText("Cell 1 changed after this check", { exact: false }).waitFor();
  const n = calls("notebook_validate_check").length;
  await pane().locator(".prove-note").getByRole("button", { name: "Check again" }).click();
  await pane().locator(".prove-note").waitFor({ state: "detached" });
  assert.strictEqual(calls("notebook_validate_check").length, n + 1);
  assert.strictEqual(calls("notebook_validate_check").at(-1).cells[0].source, "rows = read_all()");
});

test("Clean up says when it can't read the code or has nothing; a tidy-up unticked isn't applied, and a suggestion goes to its cell", { skip }, async () => {
  plan = new Error("The cleanup reader failed.");
  await openPanel();
  await pane().getByRole("tab", { name: /Clean up/ }).click();
  await pane().locator(".problems", { hasText: "The cleanup reader failed." }).waitFor();
  await pane().getByText("Nothing to clean up.").waitFor();

  plan = { tidy: [{ id: "imports", title: "Remove 1 unused import", text: "import os", on: true, diff: [{ cell: 2, removed: ["import os"] }] }],
    suggestions: [{ kind: "optimize", title: "Read only the columns you use", text: "Cell 2 reads every column.", fix: "Pass columns=[...]", cell: 2 }] };
  await page().evaluate(() => refreshCleanup(state.notebook));
  const box = pane().getByRole("checkbox", { name: /Remove 1 unused import/ });
  await box.waitFor();
  assert.ok(await box.isChecked());
  assert.strictEqual(await pane().getByLabel("Lines removed from cell 2").innerText(), "- import os");
  await box.uncheck();
  assert.ok(await pane().getByRole("button", { name: "Apply 0 tidy-ups" }).isDisabled());
  await box.check();
  await pane().getByRole("button", { name: "Apply 1 tidy-up" }).waitFor();
  assert.match(await pane().locator(".validate-find").innerText(), /^Optimize\s+Read only the columns you use/);
  await pane().getByRole("button", { name: "Go to cell 2" }).click();
  assert.strictEqual(await page().evaluate(() => state.notebook.cells.indexOf(selectedCell()) + 1), 2);
  assert.deepStrictEqual(app.errors, []);
});
