// Save a replay bundle on a cell that failed on this PC (#1301), against the real engine
// (replay_bundle_preview, replay_bundle_save in replay.py and prove_service.py). Checks:
// - the dialog lists the code, the input versions, the environment and the error; the data sample
//   starts unticked and the workspace and lakehouse IDs start ticked
// - Save with the defaults writes a zip to Downloads with no sample, and the error's token removed
// - Save with the IDs unticked writes a zip with no workspace or lakehouse ID anywhere in it
// - with a table read the sample can be ticked; when policy (allow_replay_samples) turns samples
//   off the box is disabled and says so
// - Cancel saves nothing
// - axe finds nothing serious in the dialog
//
// Reading a table needs Fabric, so the first cell records a read the way lakehouse.read_table does
// (lakehouse._record_table, from the cell's own thread) of dbo.sales at Delta version 7 in the
// attached lakehouse. Attaching a lakehouse ref needs no sign-in until something is read.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";

const WS = "3f6c2a91-7d4e-4b8a-9c15-2e7f80d4a6b3";
const LH = "a8d51e07-36bc-4f29-8e4a-c19b7f2d05e6";
const LAKEHOUSE = { name: "SalesLH", workspace_id: WS, lakehouse_id: LH, workspace_name: "Contoso Sales" };
// Built in the cell, so the secret is only in the error, never in the code.
const SECRET = "Zq9sEcReTvAlUe42";
const CELLS = [
  { type: "code", cell_id: "c1", meta: {}, source: [
    "from fabric_desktop import lakehouse",
    "from fabric_desktop.onelake import TableRef",
    "lh = lakehouse._current()",
    'lakehouse._record_table(TableRef(lh.workspace_id, lh.lakehouse_id, "dbo", "sales"), 7, "read")',
  ].join("\n") },
  { type: "code", cell_id: "c2", meta: {}, source: [
    'tok = "Zq9" + "sEcReTvAlUe42"',
    `raise RuntimeError(f"Couldn't read abfss://${WS}@onelake.dfs.fabric.microsoft.com/${LH}/Tables/dbo/sales with access_token={tok}")`,
  ].join("\n") },
];

let app, programData;
const page = () => app.page;
const dialog = () => page().getByRole("dialog", { name: "Save a replay bundle" });
const downloads = () => path.join(app.engineHome, "Downloads");
const bundles = () => (fs.existsSync(downloads()) ? fs.readdirSync(downloads()).filter((f) => f.endsWith(".zip")).sort() : []);
const policyFile = () => path.join(programData, "FabricDesktop", "policy.json");

// The zip's member names and the text of every member, read with Python's zipfile.
function unzip(file) {
  const script = "import json, sys, zipfile\nz = zipfile.ZipFile(sys.argv[1])\n"
    + "print(json.dumps({n: z.read(n).decode('utf-8', 'replace') for n in z.namelist()}))";
  return JSON.parse(execFileSync(process.env.FABRIC_DESKTOP_PYTHON || "python3", ["-c", script, file], { encoding: "utf8" }));
}

before(async () => {
  if (skip) return;
  // The engine reads its policy file from %ProgramData%\FabricDesktop, as on Windows.
  programData = fs.mkdtempSync(path.join(os.tmpdir(), "fd-policy-"));
  fs.mkdirSync(path.join(programData, "FabricDesktop"));
  process.env.ProgramData = programData;
  try {
    app = await openApp({
      engine: ["start_cell", "poll_cell", "kernel_variables", "kernel_complete", "notebook_debug_get", "notebook_dependencies",
        "replay_bundle_preview", "replay_bundle_save"],
      mocks: { save_notebook: () => ({ saved: true }) },
    });
  } finally {
    delete process.env.ProgramData;
  }
  await page().evaluate(({ cells, lakehouse }) => {
    loadIntoState({ name: "Daily sales", cells, lakehouse });
    go("notebook");
  }, { cells: CELLS, lakehouse: LAKEHOUSE });
  await page().evaluate(async () => {
    const [read, fail] = state.notebook.cells;
    await runCell(read);
    await runCell(fail);
  });
  await page().waitForFunction(() => state.notebook.cells[1].status === "error", null, { timeout: 60000 });
  assert.strictEqual(await page().evaluate(() => state.notebook.cells[0].status), "ok", "the cell that records the read ran");
});

after(async () => {
  await app?.close();
  if (programData) fs.rmSync(programData, { recursive: true, force: true });
});

// Opens the dialog from the failed cell's offer, as a person would.
async function open() {
  await page().evaluate(() => closeDialog());  // one left open by a test that failed
  await page().locator("#dialog-root .dialog").waitFor({ state: "detached" });
  await page().locator('[data-cell] .replay-offer').getByRole("button", { name: "Save replay bundle" }).click();
  await dialog().waitFor();
  return dialog();
}

async function save(d) {
  const was = bundles();
  await d.getByRole("button", { name: "Save bundle" }).click();
  await d.waitFor({ state: "detached" });
  const added = bundles().filter((f) => !was.includes(f));
  assert.strictEqual(added.length, 1, `one new bundle in Downloads: ${added}`);
  return unzip(path.join(downloads(), added[0]));
}

test("the dialog lists code, inputs, environment and error; sample starts off, IDs on", { skip }, async () => {
  const d = await open();
  const text = await d.innerText();
  assert.match(text, /Code of cells 1 to 2/);
  assert.match(text, /Input versions[\s\S]*dbo\.sales at Delta version 7/);
  assert.match(text, /Environment[\s\S]*Python \d/);
  assert.match(text, /The error, with secrets removed[\s\S]*RuntimeError: .*access_token=\[removed\]/);
  assert.ok(!text.includes(SECRET), "the dialog doesn't show the token");
  const sample = d.getByRole("checkbox", { name: "Include a sample of the input data" });
  const ids = d.getByRole("checkbox", { name: "Include workspace and lakehouse IDs" });
  assert.strictEqual(await sample.isChecked(), false);
  assert.strictEqual(await ids.isChecked(), true);
  // A table was read and policy allows samples: the box can be ticked.
  assert.strictEqual(await sample.isEnabled(), true);
  assert.match(text, /Off by default\. The bundle then holds your data/);
  await sample.check();
  assert.strictEqual(await sample.isChecked(), true);
  await d.getByRole("button", { name: "Cancel" }).click();
  await d.waitFor({ state: "detached" });
  assert.deepStrictEqual(app.errors, []);
});

test("Save with the defaults writes a zip with no sample and the token removed", { skip }, async () => {
  const zip = await save(await open());
  assert.deepStrictEqual(Object.keys(zip), ["bundle.json"], "no samples/ folder");
  const bundle = JSON.parse(zip["bundle.json"]);
  assert.deepStrictEqual(bundle.samples, []);
  assert.strictEqual(bundle.ids_included, true);
  assert.strictEqual(bundle.error.ename, "RuntimeError");
  assert.match(bundle.error.evalue, /access_token=\[removed\]/);
  assert.ok(!zip["bundle.json"].includes(SECRET), "the token is nowhere in the bundle");
  assert.strictEqual(bundle.lakehouse.lakehouse_id, LH, "IDs ticked: the lakehouse ID is in");
  const save1 = app.calls.filter((c) => c.method === "replay_bundle_save").pop();
  assert.strictEqual(save1.params.include_ids, true);
  assert.strictEqual(save1.params.include_sample, false);
  await page().locator(".toast", { hasText: /^Saved replay-Daily_sales-.*\.zip to Downloads\.$/ }).waitFor();
});

test("Save with the IDs unticked writes a zip with no workspace or lakehouse ID in it", { skip }, async () => {
  const d = await open();
  await d.getByRole("checkbox", { name: "Include workspace and lakehouse IDs" }).uncheck();
  const zip = await save(d);
  const text = Object.values(zip).join("\n");
  for (const id of [WS, LH]) {
    assert.ok(!text.toLowerCase().includes(id.toLowerCase()), `${id} is in the bundle`);
    assert.ok(!text.includes(id.replace(/-/g, "")), `${id} without dashes is in the bundle`);
  }
  const bundle = JSON.parse(zip["bundle.json"]);
  assert.strictEqual(bundle.ids_included, false);
  assert.deepStrictEqual(bundle.lakehouse, { name: "SalesLH" });
  assert.strictEqual(bundle.inputs.tables[0].table, "dbo.sales", "the input is still listed, without its IDs");
  assert.match(bundle.error.evalue, /abfss:\/\/<workspace>@onelake\.dfs\.fabric\.microsoft\.com\/<lakehouse>\/Tables/);
  assert.ok(!text.includes(SECRET));
});

test("Cancel saves nothing", { skip }, async () => {
  const was = bundles();
  const calls = app.calls.filter((c) => c.method === "replay_bundle_save").length;
  const d = await open();
  await d.getByRole("button", { name: "Cancel" }).click();
  await d.waitFor({ state: "detached" });
  await new Promise((r) => setTimeout(r, 300));  // grace: nothing should arrive
  assert.deepStrictEqual(bundles(), was);
  assert.strictEqual(app.calls.filter((c) => c.method === "replay_bundle_save").length, calls);
});

test("when policy turns samples off, the box is disabled and says so", { skip }, async () => {
  fs.writeFileSync(policyFile(), JSON.stringify({ allow_replay_samples: false }));
  try {
    const d = await open();
    const sample = d.getByRole("checkbox", { name: "Include a sample of the input data" });
    assert.strictEqual(await sample.isDisabled(), true);
    assert.strictEqual(await sample.isChecked(), false);
    const row = d.locator(".replay-row", { hasText: "A sample of the input data" });
    assert.match(await row.innerText(), /Your organization has turned this off\./);
    await d.getByRole("button", { name: "Cancel" }).click();
    await d.waitFor({ state: "detached" });
  } finally {
    fs.rmSync(policyFile(), { force: true });
  }
});

test("the dialog has no serious axe findings", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  await page().emulateMedia({ reducedMotion: "reduce" });
  const d = await open();
  const found = await axeFindings(page(), "#dialog-root .dialog");
  assert.deepStrictEqual(found, []);
  await d.getByRole("button", { name: "Cancel" }).click();
  await d.waitFor({ state: "detached" });
  assert.deepStrictEqual(app.errors, []);
});
