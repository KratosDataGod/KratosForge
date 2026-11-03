// #1442: the Replay panel (app/ui/replay.js) against mocks shaped like the engine's answers
// (sidecar/fabric_desktop/prove_service.py replay_* and replay.py; see harness.cjs for how to run it).
// Checks:
// - Save replay bundle on a failed cell shows the preview and sends exactly the boxes ticked
//   (include_ids, include_sample)
// - Create > Open replay bundle sends exactly the bundle picked (its path), then opens its notebook
// - Replay to cell N sends the notebook, the attached lakehouse and what was picked for a table
//   whose version can't be read (use), runs the cells up to the failed one and shows the verdict
// - Delete replay outputs asks in a danger confirm; Cancel sends nothing, Delete sends the notebook
// - Keep as a normal notebook asks first when replay outputs stay, and sends keep: true; End replay
//   sends keep: false
// - engine refusals (the shell's "<ExceptionName>: <text>") show a plain sentence, not the raw text
//   or [object Object]
// - opening a replay notebook sends no write, axe finds nothing serious, and no page errors
// Replay runs the cells in this PC's kernel (bind_proof(..., "pc", ...)), so nothing here needs a kernel:
// start_cell answers each cell at once, as the engine does for a cell that finished.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const READS = ["replay_check", "replay_bundles_list", "replay_bundle_preview"];
const WRITES = ["replay_bundle_save", "replay_bundle_open", "replay_start", "replay_result", "replay_delete_outputs",
  "replay_end", "start_cell", "save_notebook"];
// What opening any notebook reads (notebook.js and the panels beside it), and the app's own polls.
const NOTEBOOK_READS = ["cached_schemas", "list_tables", "git_notebook_info", "notebook_inputs", "notebook_comments_list",
  "notebook_syntax", "kernel_variables", "notebook_dependencies", "notebook_debug_get", "integrations_pending", "editor_pending", "git_info"];

const WS = "3f6c2a91-7d4e-4b8a-9c15-2e7f80d4a6b3";
const LH = "a8d51e07-36bc-4f29-8e4a-c19b7f2d05e6";
const LAKEHOUSE = { name: "SalesLH", workspace_id: WS, lakehouse_id: LH, workspace_name: "Contoso Sales" };
const NAME = "Daily sales replay";
const CELLS = [
  { type: "code", source: "df = lakehouse.read_table('dbo.sales')", meta: {} },
  { type: "code", source: "df['amount'].sum() / df['rows']", meta: {} },
];
const ERROR = { type: "error", ename: "KeyError", evalue: "'rows'", traceback: [] };
// The replay notebook's extra (prove_service.replay_bundle_open), as open_notebook returns it.
const REPLAY = { bundle: "0123456789abcdef", notebook: "Daily sales", saved_at: "2026-10-30T09:15:00Z", failed_cell: 2,
  lakehouse: "SalesLH", ids_included: true };
const BOX = { id: "replay-20261031-101500-a1b2c3", workspace_id: WS, lakehouse_id: LH, lakehouse: "SalesLH",
  started_at: "2026-10-31T10:15:00Z", folder: "Files/_fabric_desktop/proofs/replay-20261031-101500-a1b2c3" };

// prove_service.replay_check: dbo.sales is pinned at v7; dbo.returns' v3 was vacuumed (latest v9, sampled).
function freshCheck(more = {}) {
  return {
    bundle: { notebook: "Daily sales", saved_at: REPLAY.saved_at, failed_cell: 2, error: { ename: "KeyError", evalue: "'rows'" }, limitations: [] },
    tables: [
      { table: "dbo.sales", version: 7, readable: true, latest: 8, why: "", has_sample: false },
      { table: "dbo.returns", version: 3, readable: false, latest: 9, has_sample: true,
        why: "Version 3 can't be read any more (vacuumed or its log was cleaned up)" },
    ],
    environment: [{ what: "pandas", Bundle: "2.2.2", "This PC": "2.2.3" }],
    blockers: [], notes: [], live: false, sandboxes: [], ...more,
  };
}

let check = freshCheck();
const fail = {};  // method -> the engine's error text for its next calls
const failing = (method, fn) => (p) => { if (fail[method]) throw new Error(fail[method]); return fn(p); };

const mocks = {
  save_notebook: () => ({ saved: true }),
  kernel_variables: () => [],
  notebook_dependencies: () => ({ cells: [] }),
  notebook_debug_get: () => ({}),
  // prove_service.replay_bundle_preview: replay.build's bundle, the policy and the tables to sample
  replay_bundle_preview: failing("replay_bundle_preview", ({ name, failed }) => ({
    bundle: { format: "fabric-desktop-replay", version: 1, notebook: name, saved_at: "2026-10-31T09:00:00Z", failed_cell: failed + 1,
      cells: CELLS.map((c) => ({ type: c.type, source: c.source })), error: { ename: "KeyError", evalue: "'rows'", traceback: [] },
      inputs: { tables: [{ table: "dbo.sales", version: 7, lakehouse: "SalesLH" }], files: [] },
      environment: { python: "3.11.9", packages: { pandas: "2.2.3" }, kernel: { process: true } }, ids_included: true,
      lakehouse: { name: "SalesLH", workspace_id: WS, workspace_name: "Contoso Sales", lakehouse_id: LH }, samples: [],
      limitations: ["Inputs are what the cells read through lakehouse (read_table, sql, read_csv and the like) since the kernel started; data read another way isn't recorded."] },
    samples_allowed: true, sample_tables: ["dbo.sales"], sample_rows: 1000 })),
  replay_bundle_save: failing("replay_bundle_save", ({ include_sample }) => ({
    path: "C:\\Users\\chris\\Downloads\\replay-Daily_sales-20261031-090000.zip", bytes: 2048,
    samples: include_sample ? [{ table: "SalesLH/dbo.sales", file: "samples/dbo.sales.parquet", rows: 1000 }] : [], ids_included: true })),
  // prove_service.replay_bundles_list, newest first
  replay_bundles_list: failing("replay_bundles_list", () => [
    { path: "C:\\Users\\chris\\Downloads\\replay-Daily_sales-20261031-090000.zip", file: "replay-Daily_sales-20261031-090000.zip",
      bytes: 2048, notebook: "Daily sales", saved_at: "2026-10-31T09:00:00Z", failed_cell: 2, samples: 1 },
    { path: "C:\\Users\\chris\\Downloads\\replay-Orders-20261029-160000.zip", file: "replay-Orders-20261029-160000.zip",
      bytes: 1024, notebook: "Orders", saved_at: "2026-10-29T16:00:00Z", failed_cell: 4, samples: 0 },
  ]),
  // prove_service.replay_bundle_open -> open_notebook of the new "<notebook> replay"
  replay_bundle_open: failing("replay_bundle_open", () => ({ name: NAME, cells: CELLS, lakehouse: LAKEHOUSE, place: null,
    replay: REPLAY, check: null, onelake_writes: null })),
  replay_check: failing("replay_check", () => JSON.parse(JSON.stringify(check))),
  replay_start: failing("replay_start", ({ use }) => {
    check.live = true;
    check.sandboxes = [...check.sandboxes, BOX];
    return { id: BOX.id, pins: { "dbo.sales": 7, ...(use?.["dbo.returns"] === "latest" ? { "dbo.returns": 9 } : {}) },
      marked: use || {}, sandbox: `${BOX.folder}/pc` };
  }),
  // kernel poll snapshots: the second cell fails as in the bundle
  start_cell: ({ code }) => (code.includes("rows")
    ? { status: "error", outputs: [ERROR], execution_count: 2, seconds: 0.1, version: 2 }
    : { status: "ok", outputs: [], execution_count: 1, seconds: 0.1, version: 1 }),
  replay_result: failing("replay_result", () => ({ verdict: "reproduced", text: "Failed the same way as in the bundle. Reproduced.",
    error: { ename: "KeyError", evalue: "'rows'" }, inputs: ["dbo.returns: read at its latest version (v9), not the bundle's v3."],
    sandbox: `${BOX.folder}/pc` })),
  replay_delete_outputs: failing("replay_delete_outputs", () => {
    const deleted = check.sandboxes.map((b) => b.folder);
    check.sandboxes = [];
    return { deleted, problems: [] };
  }),
  replay_end: failing("replay_end", ({ keep }) => { check.live = false; return { ended: true, kept: !!keep }; }),
};

let baseline = 0;
before(async () => {
  if (skip) return;
  app = await openApp({ mocks });
  await startupReads(app);
  // The approval polls start about 5 s after launch; wait for them so they aren't counted against a test.
  for (const end = Date.now() + 15000; !["integrations_pending", "editor_pending"].every((m) => app.calls.some((c) => c.method === m));
    await new Promise((r) => setTimeout(r, 50))) {
    if (Date.now() > end) throw new Error("the approval polls never came");
  }
});

after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  check = freshCheck();
  for (const k of Object.keys(fail)) delete fail[k];
  await page().evaluate(() => { closeDialog(); const t = document.getElementById("toast"); t.hidden = true; t.textContent = ""; });
  await page().locator("#dialog-root .dialog").waitFor({ state: "detached" });
  baseline = app.calls.length;
});

const writes = (from = baseline) => app.calls.slice(from).filter((c) => WRITES.includes(c.method)).map((c) => ({ method: c.method, params: c.params }));
const sent = (method, from = baseline) => app.calls.slice(from).filter((c) => c.method === method).map((c) => c.params);
const banner = () => page().locator("#replay-banner");
const dialog = (name) => page().getByRole("dialog", { name });
const toast = (re) => page().locator("#toast:not([hidden])", { hasText: re });

// Opens the replay notebook as Open replay bundle leaves it, and waits for its check.
async function openReplay({ actions = true } = {}) {
  const from = app.calls.length;
  await page().evaluate(({ name, cells, lakehouse, replay }) => { loadIntoState({ name, cells, lakehouse, replay }); go("notebook"); },
    { name: NAME, cells: CELLS, lakehouse: LAKEHOUSE, replay: REPLAY });
  await banner().waitFor();
  for (const end = Date.now() + 5000; !sent("replay_check", from).length; await new Promise((r) => setTimeout(r, 25))) {
    if (Date.now() > end) throw new Error("the banner never checked the bundle");
  }
  if (actions) await banner().locator(".replay-actions").waitFor();
}

// A notebook (not a replay one) whose second cell failed on this PC, as runCell leaves it.
async function openFailed() {
  await page().evaluate(({ cells, lakehouse, error }) => {
    loadIntoState({ name: "Daily sales", cells, lakehouse });
    go("notebook");
    const c = state.notebook.cells[1];
    c.status = "error";
    c.outputs = [error];
    c.count = 2;
    renderNotebook();
  }, { cells: CELLS, lakehouse: LAKEHOUSE, error: ERROR });
}

test("opening a replay notebook shows the check, sends no write, has no serious axe findings", { skip }, async () => {
  await openReplay();
  const text = await banner().innerText();
  assert.match(text, /Replay of Daily sales, failed at cell 2/);
  assert.match(text, /Tables are read at the versions in the bundle: dbo\.sales v7\./);
  assert.match(text, /dbo\.returns: Version 3 can't be read any more/);
  assert.match(text, /Environment differs: pandas 2\.2\.3 here, 2\.2\.2 in the bundle/);
  const start = banner().getByRole("button", { name: "Replay to cell 2" });
  assert.strictEqual(await start.isDisabled(), true, "a table still needs a choice");
  assert.deepStrictEqual(sent("replay_check").map((p) => p.lakehouse_ref.lakehouse_id), [LH]);
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#replay-banner"), []);
  await new Promise((r) => setTimeout(r, 300));  // grace: nothing more should arrive
  assert.deepStrictEqual(writes(), []);
  // Each method once, however often a poll repeats it: only reads.
  const made = [...new Set(app.calls.slice(baseline).map((c) => c.method))];
  assert.deepStrictEqual(made.filter((m) => !READS.includes(m) && !NOTEBOOK_READS.includes(m)), [], "only reads");
  assert.deepStrictEqual(app.errors, []);
});

test("Save replay bundle shows the preview and sends exactly the boxes ticked", { skip }, async () => {
  await openFailed();
  await page().locator("[data-cell] .replay-offer").getByRole("button", { name: "Save replay bundle" }).click();
  const d = dialog("Save a replay bundle");
  await d.waitFor();
  const text = await d.innerText();
  assert.match(text, /Code of cells 1 to 2/);
  assert.match(text, /dbo\.sales at Delta version 7/);
  assert.match(text, /Python 3\.11\.9, pandas 2\.2\.3, kernel in its own process/);
  assert.match(text, /KeyError: 'rows'/);
  const preview = sent("replay_bundle_preview");
  assert.strictEqual(preview.length, 1);
  assert.strictEqual(preview[0].failed, 1);
  assert.strictEqual(preview[0].cells.length, 2);
  assert.deepStrictEqual(preview[0].cells[1].outputs, [ERROR], "only the failed cell's output");
  assert.deepStrictEqual(writes(), [], "the preview saves nothing");
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#dialog-root .dialog"), []);
  // IDs off, sample on: the opposite of both defaults.
  await d.getByRole("checkbox", { name: "Include workspace and lakehouse IDs" }).uncheck();
  await d.getByRole("checkbox", { name: "Include a sample of the input data" }).check();
  await d.getByRole("button", { name: "Save bundle" }).click();
  await d.waitFor({ state: "detached" });
  const saves = sent("replay_bundle_save");
  assert.strictEqual(saves.length, 1);
  assert.strictEqual(saves[0].include_ids, false);
  assert.strictEqual(saves[0].include_sample, true);
  assert.strictEqual(saves[0].name, "Daily sales");
  assert.strictEqual(saves[0].failed, 1);
  assert.strictEqual(saves[0].lakehouse_ref.lakehouse_id, LH);
  await toast(/^Saved replay-Daily_sales-20261031-090000\.zip to Downloads, with a sample of 1 table\.$/).waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("Save replay bundle with the defaults sends IDs on, sample off", { skip }, async () => {
  await openFailed();
  await page().locator("[data-cell] .replay-offer").getByRole("button", { name: "Save replay bundle" }).click();
  const d = dialog("Save a replay bundle");
  await d.getByRole("button", { name: "Save bundle" }).click();
  await d.waitFor({ state: "detached" });
  const saves = sent("replay_bundle_save");
  assert.deepStrictEqual(saves.map((p) => [p.include_ids, p.include_sample]), [[true, false]]);
});

test("Open replay bundle sends exactly the bundle picked and opens its notebook", { skip }, async () => {
  await page().evaluate(() => go("create"));
  await page().getByRole("button", { name: /Open replay bundle/ }).click();
  const d = dialog("Open a replay bundle");
  await d.waitFor();
  assert.match(await d.innerText(), /Daily sales[\s\S]*Failed at cell 2[\s\S]*with a data sample/);
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#dialog-root .dialog"), []);
  assert.deepStrictEqual(writes(), [], "listing opens nothing");
  await d.getByRole("button", { name: "Open replay-Orders-20261029-160000.zip" }).click();
  await d.waitFor({ state: "detached" });
  assert.deepStrictEqual(sent("replay_bundle_open"), [{ path: "C:\\Users\\chris\\Downloads\\replay-Orders-20261029-160000.zip" }]);
  await banner().waitFor();
  assert.strictEqual(await page().evaluate(() => state.notebook.name), NAME);
  assert.deepStrictEqual(app.errors, []);
});

test("Replay sends the notebook, lakehouse and what was picked, then shows the verdict", { skip }, async () => {
  await openReplay();
  const pick = banner().getByRole("combobox", { name: "What to read for dbo.returns" });
  await pick.selectOption("latest");
  const start = banner().getByRole("button", { name: "Replay to cell 2" });
  await page().waitForFunction(() => !document.querySelector("#replay-banner .replay-actions button.primary")?.disabled);
  await start.click();
  await banner().locator(".replay-result", { hasText: "Failed the same way as in the bundle. Reproduced." }).waitFor();
  const starts = sent("replay_start");
  assert.strictEqual(starts.length, 1);
  assert.deepStrictEqual(starts[0], { name: NAME, lakehouse_ref: LAKEHOUSE, use: { "dbo.returns": "latest" } });
  assert.deepStrictEqual(sent("start_cell").map((p) => p.code), CELLS.map((c) => c.source), "the cells up to the failed one ran");
  const results = sent("replay_result");
  assert.strictEqual(results.length, 1);
  assert.strictEqual(results[0].name, NAME);
  assert.strictEqual(results[0].stopped_at, 2);
  assert.strictEqual(results[0].status, "error");
  assert.match(await banner().innerText(), /Writes are in Files\/_fabric_desktop\/proofs\/replay-20261031-101500-a1b2c3\/pc\./);
  await banner().getByRole("button", { name: "End replay" }).waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("Delete replay outputs asks in a danger confirm; Cancel sends nothing", { skip }, async () => {
  check = freshCheck({ sandboxes: [BOX] });
  await openReplay();
  await banner().getByRole("button", { name: "Delete replay outputs (1)" }).click();
  const d = dialog("Delete what the replay wrote?");
  await d.waitFor();
  assert.match(await d.innerText(), /Deletes Files\/_fabric_desktop\/proofs\/replay-20261031-101500-a1b2c3 in SalesLH/);
  const del = d.getByRole("button", { name: "Delete" });
  assert.match(await del.getAttribute("class"), /danger/);
  await d.getByRole("button", { name: "Cancel" }).click();
  await d.waitFor({ state: "detached" });
  await new Promise((r) => setTimeout(r, 300));
  assert.deepStrictEqual(writes(), []);
  await banner().getByRole("button", { name: "Delete replay outputs (1)" }).click();
  await dialog("Delete what the replay wrote?").getByRole("button", { name: "Delete" }).click();
  await toast(/^Deleted 1 replay output folders\.$/).waitFor();
  assert.deepStrictEqual(writes(), [{ method: "replay_delete_outputs", params: { name: NAME } }]);
  await banner().getByRole("button", { name: /Delete replay outputs/ }).waitFor({ state: "detached" });
  assert.deepStrictEqual(app.errors, []);
});

test("Keep as a normal notebook asks first when outputs stay, and sends keep: true", { skip }, async () => {
  check = freshCheck({ sandboxes: [BOX] });
  await openReplay();
  await banner().getByRole("button", { name: "Keep as a normal notebook" }).click();
  const d = dialog("Keep the replay outputs?");
  await d.waitFor();
  assert.match(await d.innerText(), /The replay's output folder stays in the lakehouse's Files/);
  await d.getByRole("button", { name: "Cancel" }).click();
  await d.waitFor({ state: "detached" });
  await new Promise((r) => setTimeout(r, 300));
  assert.deepStrictEqual(writes(), [], "Cancel keeps nothing");
  await banner().getByRole("button", { name: "Keep as a normal notebook" }).click();
  await dialog("Keep the replay outputs?").getByRole("button", { name: "Keep as a normal notebook" }).click();
  await toast(/is now a normal notebook/).waitFor();
  assert.deepStrictEqual(writes(), [{ method: "replay_end", params: { name: NAME, keep: true } }]);
  await banner().waitFor({ state: "detached" });
  assert.deepStrictEqual(app.errors, []);
});

test("End replay sends keep: false and leaves the banner", { skip }, async () => {
  check = freshCheck({ live: true, sandboxes: [BOX] });
  await openReplay();
  await banner().getByRole("button", { name: "End replay" }).click();
  await banner().getByRole("button", { name: "Replay to cell 2" }).waitFor();
  assert.deepStrictEqual(writes(), [{ method: "replay_end", params: { name: NAME, keep: false } }]);
  assert.strictEqual(await page().locator("#dialog-root .dialog").count(), 0, "ending doesn't ask");
  assert.deepStrictEqual(app.errors, []);
});

test("engine refusals show a plain sentence, not the raw text", { skip }, async () => {
  const raw = /RuntimeError|LookupError|ValueError|PermissionError|\[object Object\]/;
  // The banner's check
  fail.replay_check = `LookupError: ${NAME} isn't a replay notebook.`;
  await openReplay({ actions: false });  // no actions row without a check
  const problem = banner().locator(".error-text");
  await problem.waitFor();
  assert.strictEqual(await problem.innerText(), `${NAME} isn't a replay notebook.`);
  // Starting
  delete fail.replay_check;
  check = freshCheck({ tables: [freshCheck().tables[0]] });
  await openReplay();
  fail.replay_start = "RuntimeError: A proof is using the kernel. Wait for it, or cancel it.";
  await banner().getByRole("button", { name: "Replay to cell 2" }).click();
  const t1 = toast(/Could not start the replay/);
  await t1.waitFor();
  assert.match(await t1.innerText(), /Could not start the replay: A proof is using the kernel\. Wait for it, or cancel it\./);
  assert.doesNotMatch(await t1.innerText(), raw);
  assert.strictEqual(await banner().getByRole("button", { name: "Replay to cell 2" }).isDisabled(), false, "not left busy");
  // Saving
  await openFailed();
  fail.replay_bundle_save = "PermissionError: Your organization doesn't allow data samples in replay bundles.";
  await page().locator("[data-cell] .replay-offer").getByRole("button", { name: "Save replay bundle" }).click();
  await dialog("Save a replay bundle").getByRole("button", { name: "Save bundle" }).click();
  const t2 = toast(/Could not save the replay bundle/);
  await t2.waitFor();
  assert.doesNotMatch(await t2.innerText(), raw);
  await page().evaluate(() => closeDialog());
  // Opening
  fail.replay_bundle_open = "ValueError: That file is too large for a replay bundle.";
  await page().evaluate(() => go("create"));
  await page().getByRole("button", { name: /Open replay bundle/ }).click();
  await dialog("Open a replay bundle").getByRole("button", { name: "Open replay-Daily_sales-20261031-090000.zip" }).click();
  const t3 = toast(/Could not open the replay bundle/);
  await t3.waitFor();
  assert.match(await t3.innerText(), /That file is too large for a replay bundle\./);
  assert.doesNotMatch(await t3.innerText(), raw);
  assert.deepStrictEqual(app.errors, []);
});
