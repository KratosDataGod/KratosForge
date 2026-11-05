// PB-014 criterion 2: a cell the compatibility check can't read (Unknown) is named in the notebook's
// compute note, apart from the cells that stop it. The real engine's compute_check decides; nothing
// about the check is mocked.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ viewport: { width: 1280, height: 800 },
    engine: ["list_notebooks", "open_notebook", "save_notebook", "notebook_places", "compute_check", "kernel_variables", "engine_catalog"],
    mocks: { notebook_runs: () => ({ runs: [] }) } });
});
after(async () => { if (app) await app.close(); });

const READ = { type: "code", source: "df = spark.read.table(\"sales\")\ndf.count()" };
const DYNAMIC_SQL = { type: "code", source: "table = input_table\nspark.sql(f\"DELETE FROM {table} WHERE year < 2020\")" };
const EXEC = { type: "code", source: "code = open(\"steps.py\").read()\nexec(code)" };

async function open(name, cells, compute) {
  await page().evaluate(async ({ name, cells, compute }) => {
    await rpc("save_notebook", { name, cells });
    await openNotebook(name);
    if (compute) await setNotebookCompute(compute);
  }, { name, cells, compute });
  await page().waitForFunction((name) => state.notebook?.name === name && state.notebook.check, name);
}
const slot = () => page().locator(".compute-note-slot");
const items = (scope) => scope.locator("li").allInnerTexts();
async function noSerious(scope) {
  if (!axeSource) return;
  assert.deepStrictEqual(await axeFindings(page(), scope), []);
}

test("a notebook the check reads fully shows no Unknown note", { skip }, async () => {
  await open("Clear", [READ]);
  assert.strictEqual(await page().locator(".compute-unknown").count(), 0);
  assert.strictEqual(await slot().innerText(), "");
});

test("Unknown cells on this PC are named with their reason and line, apart from blockers", { skip }, async () => {
  await open("Unknown here", [READ, DYNAMIC_SQL, EXEC]);
  const local = await page().evaluate(() => state.notebook.check.local);
  assert.strictEqual(local.reasons.length, 0, "nothing stops it on this PC");
  assert.deepStrictEqual(local.unknown.map((u) => u.cell), [2, 3]);
  const note = page().locator(".compute-unknown");
  await note.waitFor();
  assert.strictEqual(await page().locator(".compute-note-slot .problems").count(), 0, "Unknown isn't drawn as a blocker");
  assert.strictEqual(await note.locator("b").innerText(), "The check can't tell whether 2 cells run on this PC");
  const listed = await items(note);
  assert.strictEqual(listed.length, 2);
  assert.match(listed[0], /^Cell 2 changes table rows with SQL whose table is known only at run time.*: spark\.sql\(f"DELETE FROM \{table\} WHERE year < 2020"\)$/);
  assert.strictEqual(listed[1], "Cell 3 runs code built at run time (exec), which the check can't read: exec(code)");
  assert.match(await note.innerText(), /Prove and the risk check count these cells as not checked\./);
  await noSerious(".compute-note-slot");
});

test("one Unknown cell reads in the singular", { skip }, async () => {
  await open("One unknown", [READ, EXEC]);
  assert.strictEqual(await page().locator(".compute-unknown b").innerText(), "The check can't tell whether 1 cell runs on this PC");
});

test("with blockers, the Unknown cells follow them in the same note", { skip }, async () => {
  await open("Blocked and unknown", [READ, EXEC], "fabric");
  const fabric = await page().evaluate(() => state.notebook.check.fabric);
  assert.ok(fabric.reasons.length > 0, "Fabric is blocked: no lakehouse attached");
  assert.deepStrictEqual(fabric.unknown.map((u) => u.cell), [2]);
  const problems = page().locator(".compute-note-slot .problems");
  await problems.waitFor();
  assert.strictEqual(await problems.locator("b").innerText(), "This notebook can't run in Fabric yet");
  assert.strictEqual(await problems.locator(".compute-unknown-head").innerText(), "The check also can't tell for 1 cell:");
  const listed = await items(problems);
  assert.strictEqual(listed.at(-1), "Cell 2 runs code built at run time (exec), which the check can't read: exec(code)");
  assert.strictEqual(listed.length, fabric.reasons.length + 1);
  assert.strictEqual(await page().locator(".compute-unknown").count(), 0, "one note, not two");
  await noSerious(".compute-note-slot");
  await page().evaluate(() => setNotebookCompute("local"));
});

test("a cell's code is shown as text, never as markup", { skip }, async () => {
  await page().evaluate(() => { window.__ran = undefined; });
  await open("Odd code", [{ type: "code", source: "spark.sql(f\"DELETE FROM {t}<img src=x onerror=__ran=1>\")" }]);
  const note = page().locator(".compute-unknown");
  await note.waitFor();
  assert.match((await items(note))[0], /: spark\.sql\(f"DELETE FROM \{t\}<img src=x onerror=__ran=1>"\)$/);
  assert.strictEqual(await note.locator("img").count(), 0);
  assert.strictEqual(await page().evaluate(() => window.__ran), undefined);
});

// In Fabric the check needs an attached lakehouse, so an Unknown-only Fabric answer is given here as
// compute_check's shape; the note still says "in Fabric" and keeps the Fabric run callout under it.
test("Unknown cells in Fabric say so, and the Fabric callout stays", { skip }, async () => {
  await open("Unknown in Fabric", [READ, EXEC], "fabric");
  const got = await page().evaluate(() => {
    const nb = state.notebook;
    const unknown = [{ cell: 2, text: "runs code built at run time (exec), which the check can't read", code: "exec(code)" }];
    const el = computeNote({ ...nb, check: { ...nb.check, fabric: { ok: false, status: "unknown", reasons: [], unknown } } });
    return { head: el.querySelector(".compute-unknown b")?.textContent, problems: el.querySelectorAll(".problems").length,
      callout: !!el.querySelector(".compute-callout") };
  });
  await page().evaluate(() => setNotebookCompute("local"));
  assert.deepStrictEqual(got, { head: "The check can't tell whether 1 cell runs in Fabric", problems: 0, callout: true });
});
