// #1456: the notebook's Side by side run (sidebyside.js; see harness.cjs for how to run it). With the
// toggle on, opening the notebook reads the last comparison and the cores and sends no write. Run all
// then runs the risk check (the Fabric side uses the workspace's capacity, side_by_side_service.py
// _sbs_fabric runs it as a Fabric job), and checks which tables both runs would write
// (side_by_side_writes, answered here by the real engine). When it writes any table, or reading the code
// can't tell, it asks first and names them; a notebook that writes nothing asks only the capacity
// confirm. Cancel sends nothing; confirming sends side_by_side_start with exactly the confirm_writes and
// confirm_unknown shown. A running comparison shows its step, and the ribbon's Cancel sends that run's
// id. Engine refusals show a plain message. axe finds nothing serious on the card and the dialogs.
// "Cores on this PC" (side_by_side_cores / side_by_side_cores_set) has its own file, sbs-cores.test.cjs;
// only its refusal is here.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const NAME = "Daily sales";
const LAKEHOUSE = { name: "SalesLH", workspace_id: "ws-1", lakehouse_id: "lh-1", workspace_name: "Contoso Sales" };
// The real engine reads these (side_by_side_service._writes, depgraph.analyze).
const WRITING = [
  { type: "code", cell_id: "a", source: "df = spark.read.table('sales')", meta: {} },
  { type: "code", cell_id: "b", source: "df.write.mode('overwrite').saveAsTable('silver.orders_clean')", meta: {} },
  { type: "code", cell_id: "c", source: "df.groupBy('day').count().write.saveAsTable('gold.daily_sales')", meta: {} },
];
const UNKNOWN = [...WRITING, { type: "code", cell_id: "d", source: "exec(open('more.py').read())", meta: {} }];
const NOTHING = [{ type: "code", cell_id: "a", source: "df = spark.read.table('sales')\ndf.count()", meta: {} }];
const ENGINE = ["app_settings", "save_app_settings", "side_by_side_writes"];
const READS = ["side_by_side_list", "side_by_side_status", "side_by_side_cores", "side_by_side_writes", "risk_check"];
const WRITES = ["side_by_side_start", "side_by_side_cancel", "side_by_side_cores_set", "risk_acknowledge", "benchmark_notebooks"];

// risk_service.risk_check: risk.analyze's result. A pass shows nothing (riskGate).
const RISK = {
  pass: { id: "r-pass", result: "pass", label: "Pass", summary: "Nothing found that should stop this run", can_proceed: true,
    needs_acknowledgement: false, enforced: false, reads: [], note: "Read only: nothing was started, changed or paused to work this out.",
    findings: [{ check: "capacity", outcome: "pass", label: "Pass", title: "fabcap is running (read 12 minutes ago)", detail: "", evidence: [] }] },
  warning: { id: "r-warn", result: "warning", label: "Warning", summary: "1 thing(s) to check before you run", can_proceed: true,
    needs_acknowledgement: false, enforced: false, reads: [], note: "Read only: nothing was started, changed or paused to work this out.",
    findings: [{ check: "recent_failures", outcome: "warning", label: "Warning", title: "3 of the last 5 runs failed", detail: "", evidence: [] }] },
};

// A run as side_by_side_start / side_by_side_status return it (_copy of the record).
const ID = "20261101-101500-a1b2c3";
const at = (s) => new Date(Date.now() + s * 1000).toISOString();
function run(state, more = {}) {
  return { id: ID, name: NAME, state, started_at: at(-30), finished_at: null, message: "",
    lakehouse: { ...LAKEHOUSE }, workspace_id: "ws-1", workspace_name: "Contoso Sales",
    machine: { cores: 24, cores_used: 2, memory_gb: 64 }, code_revision: "f".repeat(64),
    local: { status: "running", seconds: null, cell: 2, cells_total: 3, cells: {} },
    fabric: { status: "pending", engine: "spark", vcores: null }, tables: [], verdict: null, speed: {}, ...more };
}
// side_by_side_cores: {choice, pc_cores, engine, fabric_vcores, cores}.
const CORES = { choice: "all", pc_cores: 24, engine: "spark", fabric_vcores: null, cores: 24 };

let mode = { risk: "pass", startError: null, coresError: null };
let status = null;  // what side_by_side_status answers
let opened = 0;  // app.calls before the notebook opened

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ENGINE, storage: { [`fd.sbs.${NAME}`]: "1" }, mocks: {
    save_notebook: () => ({ saved: true }),
    side_by_side_list: () => [],
    side_by_side_cores: () => CORES,
    side_by_side_cores_set: () => { if (mode.coresError) throw new Error(mode.coresError); return CORES; },
    risk_check: () => RISK[mode.risk],
    risk_acknowledge: (p) => ({ ...p, at: at(0) }),
    side_by_side_start: () => {
      if (mode.startError) throw new Error(mode.startError);
      status = run("local");
      return status;
    },
    side_by_side_status: ({ id }) => {
      if (id !== ID || !status) throw new Error(`LookupError: No side-by-side run ${id}`);
      return status;
    },
    side_by_side_cancel: () => { status = run("cancelled", { message: "Cancelled.", finished_at: at(0) }); return { cancelled: true }; },
  } });
  await startupReads(app);
  // The app's approval polls start 5 s in and repeat while it is open; wait for the first ones.
  for (const end = Date.now() + 15000; !["integrations_pending", "editor_pending"].every((m) => app.calls.some((c) => c.method === m));
    await new Promise((r) => setTimeout(r, 50))) {
    if (Date.now() > end) throw new Error("the app's startup polls never came");
  }
  opened = app.calls.length;
  await page().evaluate(async ({ name, cells, lakehouse }) => {
    await loadSettings();
    loadIntoState({ name, cells, lakehouse });
    state.user = "chris@contoso.com";
    state.ribbonTab = "run";
    go("notebook");
  }, { name: NAME, cells: WRITING, lakehouse: LAKEHOUSE });
});
after(async () => { await app?.close(); });

const card = () => page().locator(".sbs");
const dialog = () => page().getByRole("dialog");
const toastEl = () => page().locator("#toast");
const sent = (method) => app.calls.filter((c) => c.method === method);
const writesSince = (n) => app.calls.slice(n).filter((c) => WRITES.includes(c.method)).map((c) => c.method);
const runAll = () => page().getByRole("button", { name: "Run all", exact: true }).first().click();
const setCells = (cells) => page().evaluate((cells) => {
  state.notebook.cells = cells.map(cellFrom);
  state.notebook.sbs = null;
  renderNotebook();
}, cells);
async function axeClean(scope, what) {
  if (!axeSource) return;
  assert.deepStrictEqual(await axeFindings(page(), scope), [], `axe on ${what}`);
}
async function waitFor(fn, what, ms = 8000) {
  for (const end = Date.now() + ms; !(await fn()); await new Promise((r) => setTimeout(r, 50))) {
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
  }
}
async function toastSays(re) {
  await waitFor(async () => re.test(await toastEl().innerText()), `a toast matching ${re}`);
  const text = await toastEl().innerText();
  assert.doesNotMatch(text, /\[object Object\]|PermissionError|RuntimeError|ValueError|Traceback/, text);
  return text;
}

test("Opening the notebook reads the last comparison and the cores, and sends no write", { skip }, async () => {
  const from = opened;
  await card().getByText("Side by side is on").waitFor();
  await card().getByRole("group", { name: "Cores on this PC" }).waitFor();
  assert.match(await card().innerText(), /runs this notebook on this PC, then publishes it and runs it in Fabric/);
  assert.deepStrictEqual(sent("side_by_side_list").at(-1).params, { name: NAME, limit: 1 });
  assert.strictEqual(sent("side_by_side_cores").at(-1).params.name, NAME);
  assert.deepStrictEqual(writesSince(from), [], "opening it writes nothing");
  // Reads may repeat as the card draws again (dedupe them): only these two, and no risk or tables check.
  const mine = new Set(app.calls.slice(from).map((c) => c.method).filter((m) => m.startsWith("side_by_side") || m.startsWith("risk_")));
  assert.ok([...mine].every((m) => READS.includes(m)), [...mine].join(", "));
  assert.deepStrictEqual([...mine].sort(), ["side_by_side_cores", "side_by_side_list"]);
  await axeClean(".sbs", "the Side by side card");
});

test("Run all checks the risk and the tables first, names them, and Cancel sends nothing", { skip }, async () => {
  const from = app.calls.length;
  await runAll();
  const d = dialog();
  await d.getByText("Overwrite these tables twice?").waitFor();
  const risk = app.calls.slice(from).findIndex((c) => c.method === "risk_check");
  const writes = app.calls.slice(from).findIndex((c) => c.method === "side_by_side_writes");
  assert.ok(risk >= 0 && writes > risk, "the risk check, then the tables check");
  assert.deepStrictEqual(app.calls[from + risk].params, { id: `notebook:${NAME}`, action: "run_in_fabric", env: null, refresh: false });
  assert.deepStrictEqual(app.calls[from + writes].params.cells.map((c) => c.source), WRITING.map((c) => c.source));
  assert.deepStrictEqual(await d.locator("li").allInnerTexts(), ["gold.daily_sales", "silver.orders_clean"]);
  const text = await d.innerText();
  assert.match(text, /both runs write to SalesLH for real/);
  assert.match(text, /uses Fabric capacity for one notebook run/);
  assert.doesNotMatch(text, /reading the code can't see/, "no unknown cells, so no unknown line");
  await axeClean(".dialog", "the overwrite dialog");
  await d.getByRole("button", { name: "Cancel" }).click();
  await d.waitFor({ state: "detached" });
  assert.deepStrictEqual(writesSince(from), [], "Cancel sends nothing");
});

test("A cell reading can't follow is named, and confirming sends exactly what was shown", { skip }, async () => {
  await setCells(UNKNOWN);
  const from = app.calls.length;
  await runAll();
  const d = dialog();
  await d.getByText("Overwrite these tables twice?").waitFor();
  assert.match(await d.innerText(), /Some cells can write tables reading the code can't see: Cell 4: exec\(\) can read or define any name\./);
  const shown = await d.locator("li").allInnerTexts();
  await d.getByRole("button", { name: "Overwrite and run" }).click();
  await card().getByText("Side by side · step 1 of 3").waitFor();
  const start = sent("side_by_side_start");
  assert.strictEqual(start.length, 1);
  assert.deepStrictEqual(start[0].params.confirm_writes, shown);
  assert.strictEqual(start[0].params.confirm_unknown, true);
  assert.strictEqual(start[0].params.name, NAME);
  assert.deepStrictEqual(start[0].params.lakehouse_ref, LAKEHOUSE);
  assert.deepStrictEqual(writesSince(from), ["side_by_side_start"]);
  await waitFor(async () => sent("side_by_side_status").length > 0, "the first status read");
  assert.deepStrictEqual(sent("side_by_side_status").at(-1).params, { id: ID });
  await axeClean(".sbs", "a running comparison");
});

test("Cancel on the ribbon sends side_by_side_cancel with that run's id", { skip }, async () => {
  const from = app.calls.length;
  const cancel = page().locator("button[data-stop]");
  await waitFor(() => cancel.isEnabled(), "the ribbon's Cancel to be enabled");
  await cancel.click();
  await waitFor(async () => sent("side_by_side_cancel").length > 0, "the cancel");
  assert.deepStrictEqual(sent("side_by_side_cancel").map((c) => c.params), [{ id: ID }]);
  assert.deepStrictEqual(writesSince(from), ["side_by_side_cancel"]);
  await card().getByText("Cancelled.").first().waitFor({ timeout: 10000 });
  await toastSays(new RegExp(`${NAME}: Cancelled\\.`));
});

test("Writing the same tables runs only after the confirm, with exactly those tables", { skip }, async () => {
  await setCells(WRITING);
  status = null;
  await runAll();
  await dialog().getByRole("button", { name: "Overwrite and run" }).click();
  await card().getByText("Side by side · step 1 of 3").waitFor();
  const start = sent("side_by_side_start").at(-1).params;
  assert.deepStrictEqual(start.confirm_writes, ["gold.daily_sales", "silver.orders_clean"]);
  assert.strictEqual(start.confirm_unknown, false);
  await page().locator("button[data-stop]").click();
  await card().getByText("Cancelled.").first().waitFor({ timeout: 10000 });
});

test("A notebook that writes nothing asks only the capacity confirm", { skip }, async () => {
  await setCells(NOTHING);
  status = null;
  let from = app.calls.length;
  await runAll();
  const d = dialog();
  await d.getByText(`Run ${NAME} side by side?`).waitFor();
  assert.strictEqual(await page().getByText("Overwrite these tables twice?").count(), 0);
  assert.match(await d.innerText(), /uses Fabric capacity for one notebook run\. It writes no tables\./);
  await axeClean(".dialog", "the capacity confirm");
  await d.getByRole("button", { name: "Cancel" }).click();
  await d.waitFor({ state: "detached" });
  assert.deepStrictEqual(writesSince(from), [], "Cancel sends nothing");

  from = app.calls.length;
  await runAll();
  await d.getByRole("button", { name: "Run side by side" }).click();
  await card().getByText("Side by side · step 1 of 3").waitFor();
  const start = sent("side_by_side_start").at(-1).params;
  assert.deepStrictEqual([start.confirm_writes, start.confirm_unknown], [[], false]);
  assert.deepStrictEqual(writesSince(from), ["side_by_side_start"]);
  await page().locator("button[data-stop]").click();
  await card().getByText("Cancelled.").first().waitFor({ timeout: 10000 });
});

test("A risk warning asks first; Cancel sends nothing and doesn't check the tables", { skip }, async () => {
  mode.risk = "warning";
  status = null;
  const from = app.calls.length;
  await runAll();
  const d = dialog();
  await d.getByText(`Run ${NAME} side by side?`).waitFor();
  await d.getByText("3 of the last 5 runs failed").waitFor();
  await axeClean(".dialog", "the risk dialog");
  await d.getByRole("button", { name: "Cancel" }).click();
  await d.waitFor({ state: "detached" });
  assert.deepStrictEqual(writesSince(from), []);
  assert.strictEqual(app.calls.slice(from).filter((c) => c.method === "side_by_side_writes").length, 0);
  mode.risk = "pass";
});

test("Engine refusals show a plain message", { skip }, async () => {
  await setCells(NOTHING);
  status = null;
  // side_by_side_start: RuntimeError when one is going, PermissionError when the confirm doesn't match.
  for (const [error, plain] of [
    ["RuntimeError: A side-by-side run is already going. Wait for it, or cancel it.", /^Could not start the side-by-side run: A side-by-side run is already going\. Wait for it, or cancel it\./],
    ["PermissionError: Side by side writes this notebook's tables for real, on this PC and then in Fabric. Confirm the tables it writes first, or use Prove, which keeps both runs' writes in a sandbox.",
      /^Could not start the side-by-side run: Side by side writes this notebook's tables for real/],
  ]) {
    mode.startError = error;
    await runAll();
    await dialog().getByRole("button", { name: "Run side by side" }).click();
    await toastSays(plain);
    assert.strictEqual(await page().getByText("Side by side · step 1 of 3").count(), 0, "nothing shows as running");
  }
  mode.startError = null;
  // side_by_side_cores_set: ValueError for a count this PC doesn't have.
  mode.coresError = "ValueError: Cores must be all, match, or a number from 1 to 24";
  await card().getByRole("button", { name: "Match Fabric" }).click();
  await toastSays(/^Not saved: Cores must be all, match, or a number from 1 to 24/);
  mode.coresError = null;
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
