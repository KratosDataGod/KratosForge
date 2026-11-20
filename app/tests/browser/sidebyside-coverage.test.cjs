// The notebook's Side by side (sidebyside.js) beyond side-by-side.test.cjs, with every engine call
// mocked: the ribbon toggle and its command, Run all without a lakehouse, a run followed to its end
// with this PC's cell results copied back, a run the engine lost track of, the finished comparison's
// card (times, uploads, capacity, the tables, Show all, Close), the cores text for each choice, the
// benchmark notebooks link and the guide link (see harness.cjs for how to run it).
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";

const LAKEHOUSE = { name: "SalesLH", workspace_id: "ws-1", lakehouse_id: "lh-1", workspace_name: "Contoso Sales" };
const CELLS = [
  { type: "code", cell_id: "a", source: "df = spark.read.table('sales')", meta: {} },
  { type: "code", cell_id: "b", source: "df.count()", meta: {} },
];
const at = (s) => new Date(Date.now() + s * 1000).toISOString();
const CORES = { choice: "all", pc_cores: 24, engine: "spark", fabric_vcores: null, cores: 24 };
const table = (name, result, extra = {}) => ({ table: name, result, local: { rows: 1200 }, fabric: { rows: 1200 }, columns: "4 of 4", ...extra });
const TABLES = [
  table("gold.daily_sales", "match"),
  table("gold.weekly", "differs", { fabric: { rows: 1199 }, note: "1 row differs" }),
  table("silver.local_only", "only_local", { fabric: null, columns: null }),
  table("silver.fabric_only", "only_fabric", { local: null }),
  table("silver.broken", "error", { note: "Couldn't read the Delta log" }),
  table("bronze.a", "match"), table("bronze.b", "match"), table("bronze.c", "match"),
  table("bronze.d", "match"), table("bronze.e", "skipped"),
];
// A finished comparison as side_by_side_list returns it.
const DONE = {
  id: "r-done", name: "Finished", state: "done", started_at: "2026-11-18T09:30:00Z", message: "",
  workspace_name: "Contoso Sales", machine: { cores: 24, memory_gb: 64 },
  local: { status: "failed", failed_cell: 3, seconds: 779.6, processing_seconds: 700, upload_wait_seconds: 79.6,
    upload: { tables: 3, bytes: 1.5e9, seconds: 65, mb_per_second: 23.08, skipped: 2 }, message: "Cell 3 raised ZeroDivisionError" },
  fabric: { status: "ok", engine: "python", vcores: 4, total_seconds: 3900, start_seconds: 40, run_seconds: 3860,
    cu_seconds: 15600.4, cost: 0.5, currency: "USD", url: "https://app.fabric.microsoft.com/groups/ws-1/run/1", message: "The session restarted once" },
  tables: TABLES, verdict: { same: false, text: "1 table differs." },
  speed: { cores: "This PC used 24 cores; Fabric 4 vCores.", end_to_end: "This PC was 5.0x faster end to end.",
    run: "Run alone: 5.5x faster.", processing: "Processing alone: 5.6x faster." },
};

let lists, statuses, coresFor, benchmark;
let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    mocks: {
      save_notebook: () => ({ saved: true }),
      notebook_dependencies: () => ({ cells: [] }),
      side_by_side_list: ({ name }) => lists[name] || [],
      side_by_side_cores: ({ name }) => coresFor[name] || CORES,
      side_by_side_cores_set: ({ name, choice }) => {
        const was = coresFor[name] || CORES;
        const cores = typeof choice === "number" ? choice : choice === "match" && was.fabric_vcores ? was.fabric_vcores : was.pc_cores;
        return { ...was, choice, cores };
      },
      side_by_side_status: ({ id }) => {
        const next = statuses[id]?.shift();
        if (!next) throw new Error(`LookupError: No side-by-side run ${id}`);
        return next;
      },
      side_by_side_writes: () => ({ tables: [], unknown: false, reasons: [] }),
      side_by_side_start: (p) => ({ id: "r-new", name: p.name, state: "local", started_at: at(0), workspace_name: "Contoso Sales",
        local: { status: "running", cell: 1, cells_total: 2, cells: {} }, fabric: { status: "pending" }, tables: [], speed: {} }),
      risk_check: () => ({ id: "r-pass", result: "pass", label: "Pass", summary: "Nothing found", can_proceed: true,
        needs_acknowledgement: false, enforced: false, reads: [], note: "", findings: [] }),
      benchmark_notebooks: (p) => benchmark(p),
      open_notebook: ({ name }) => ({ name, cells: [{ type: "code", cell_id: "x", source: "print(1)", meta: {} }] }),
    },
  });
  await page().evaluate(() => { state.user = "chris@contoso.com"; });
});

after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  lists = {};
  statuses = {};
  coresFor = {};
  benchmark = () => ({ notebooks: ["TPC-H data", "TPC-H queries"], added: [] });
  await page().evaluate(() => { closeDialog(); document.querySelector("#toast")?.replaceChildren(); });
  app.calls.length = 0;
  app.errors.length = 0;
});

// Opens a notebook called `name` on the ribbon's Run tab, with Side by side on or off for it.
async function open(name, { on = true, lakehouse = LAKEHOUSE } = {}) {
  await page().evaluate(({ name, on, cells, lakehouse }) => {
    localStorage.setItem(`fd.sbs.${name}`, on ? "1" : "0");
    loadIntoState({ name, cells, lakehouse });
    state.ribbonTab = "run";
    go("notebook");
  }, { name, on, cells: CELLS, lakehouse });
}
const card = () => page().locator(".sbs");
const toggle = () => page().getByRole("button", { name: /^Side by side/ });
const toastText = () => page().locator("#toast").innerText();
const sent = (method) => app.calls.filter((c) => c.method === method).map((c) => c.params);
async function toastSays(re) {
  await page().waitForFunction((src) => new RegExp(src).test(document.querySelector("#toast")?.innerText || ""), re.source);
  return toastText();
}

test("The ribbon toggle turns Side by side on for this notebook, shows the card, and off again", { skip }, async () => {
  await open("Toggle me", { on: false });
  assert.strictEqual(await toggle().getAttribute("aria-pressed"), "false");
  assert.strictEqual(await card().count(), 0);
  assert.strictEqual(sent("side_by_side_list").length, 0, "nothing is read while it is off");
  await toggle().click();
  await toastSays(/^Side by side is on: Run all runs this notebook on this PC, then in Fabric, and compares them\./);
  await card().getByText("Side by side is on").waitFor();
  assert.match(await toggle().innerText(), /Side by side: On/);
  assert.strictEqual(await toggle().getAttribute("aria-pressed"), "true");
  assert.strictEqual(await page().evaluate(() => localStorage.getItem("fd.sbs.Toggle me")), "1");
  assert.deepStrictEqual(sent("side_by_side_list"), [{ name: "Toggle me", limit: 1 }]);
  await toggle().click();
  await card().waitFor({ state: "detached" });
  assert.strictEqual(await page().evaluate(() => localStorage.getItem("fd.sbs.Toggle me")), "0");
  // The command bar's command flips it too.
  await page().keyboard.press("Control+k");
  await page().keyboard.type("Side by side: compare");
  await page().locator("#palette .palette-item", { hasText: "Side by side: compare this notebook" }).click();
  await card().getByText("Side by side is on").waitFor();
  assert.strictEqual(await page().evaluate(() => localStorage.getItem("fd.sbs.Toggle me")), "1");
  assert.deepStrictEqual(app.errors, []);
});

test("Run all with Side by side on and no lakehouse says to attach one and sends nothing", { skip }, async () => {
  await open("No lakehouse", { lakehouse: null });
  await card().getByText("Side by side is on").waitFor();
  await page().getByRole("button", { name: "Run all", exact: true }).first().click();
  await toastSays(/^Attach a lakehouse first\. Both runs read and write it, so their results can be compared\./);
  assert.deepStrictEqual([sent("risk_check").length, sent("side_by_side_writes").length, sent("side_by_side_start").length], [0, 0, 0]);
  assert.deepStrictEqual(app.errors, []);
});

test("A run is followed step by step, copies this PC's cell results back, and ends with the verdict", { skip }, async () => {
  await open("Followed");
  await card().getByText("Side by side is on").waitFor();
  const step = (state, more) => ({ id: "r-new", name: "Followed", state, started_at: at(-20), workspace_name: "Contoso Sales",
    tables: [], speed: {}, ...more });
  statuses["r-new"] = [
    step("local", { local: { status: "running", cell: 2, cells_total: 2, cells: { 0: { status: "ok", seconds: 1.2, execution_count: 7,
      outputs: [{ type: "text", text: "loaded" }] } } }, fabric: { status: "pending" } }),
    step("fabric", { local: { status: "ok", seconds: 12.34, cells: { 1: { status: "ok", seconds: 0.4, outputs: [] } } },
      fabric: { status: "running", job_status: "InProgress", elapsed: 65, url: "https://app.fabric.microsoft.com/run/9" } }),
    step("done", { local: { status: "ok", seconds: 12.34 }, fabric: { status: "ok", total_seconds: 61.6, job_seconds: 50 },
      verdict: { same: true, text: "All 2 tables match." }, speed: { end_to_end: "This PC was 5.0x faster." } }),
  ];
  await page().getByRole("button", { name: "Run all", exact: true }).first().click();
  await page().getByRole("dialog").getByRole("button", { name: "Run side by side" }).click();
  await card().getByText("Side by side · step 1 of 3").waitFor();
  assert.match(await card().innerText(), /Publishes to Contoso Sales and runs there/);
  assert.strictEqual(await toggle().isDisabled(), true, "the toggle waits while a run is going");
  await card().getByText("Side by side · step 2 of 3").waitFor();
  const progress = await card().innerText();
  assert.match(progress, /✓ 1\. Run on this PC\s+12\.3 s/);
  assert.match(progress, /● 2\. Run in Fabric\s+Running · 1 min 5 s/);
  assert.match(progress, /○ 3\. Check the outputs/);
  assert.ok(await card().getByRole("link", { name: "Open in Fabric" }).isVisible());
  const cells = await page().evaluate(() => state.notebook.cells.map((c) => [c.status, c.count, c.outputs.length]));
  assert.deepStrictEqual(cells, [["ok", 7, 1], ["ok", null, 0]]);
  const text = await toastSays(/^Followed: All 2 tables match\. This PC was 5\.0x faster\./);
  assert.ok(text);
  await card().getByText("✓ All 2 tables match.").waitFor();
  const result = await card().innerText();
  assert.match(result, /Job 50\.0 s in Fabric, the rest publishing and queueing/);
  assert.match(result, /No wait to start/);
  assert.strictEqual(await toggle().isDisabled(), false);
  assert.deepStrictEqual(sent("side_by_side_status").map((p) => p.id), ["r-new", "r-new", "r-new"]);
  assert.deepStrictEqual(app.errors, []);
});

test("A comparison still going when the notebook opens is followed; one the engine lost says so", { skip }, async () => {
  lists["Lost one"] = [{ id: "r-lost", name: "Lost one", state: "local", started_at: at(-5), local: { status: "running", cell: 1, cells_total: 4 },
    fabric: {}, tables: [] }];
  await open("Lost one");
  await card().getByText("Side by side · step 1 of 3").waitFor();
  assert.match(await card().innerText(), /Cell 1 of 4/);
  assert.match(await card().innerText(), /Publishes to its workspace and runs there/);
  await toastSays(/^Lost one: Didn't finish: Lost track of the run: LookupError: No side-by-side run r-lost/);
  await card().getByText(/✗ Didn't finish: Lost track of the run/).waitFor();
  assert.deepStrictEqual(sent("side_by_side_status"), [{ id: "r-lost" }]);
  assert.deepStrictEqual(app.errors, []);
});

test("A finished comparison shows both sides' times, uploads, capacity and every table", { skip }, async () => {
  lists.Finished = [DONE];
  coresFor.Finished = { choice: "match", pc_cores: 24, engine: "spark", fabric_vcores: null, cores: 24 };
  await open("Finished");
  await card().getByText("✗ 1 table differs.").waitFor();
  const text = await card().innerText();
  assert.match(text, /This PC · 24 cores · 64 GB/);
  assert.match(text, /13 min 0 s/, "779.6 s rounds once");
  assert.match(text, /Failed at cell 3/);
  assert.match(text, /Processing on this PC 11 min 40 s/);
  assert.match(text, /Waiting for OneLake uploads 1 min 20 s/);
  assert.match(text, /Uploaded 1\.5 GB to OneLake in 1 min 5 s · about 23\.1 MB\/s · 2 unchanged, skipped/);
  assert.match(text, /Fabric · Contoso Sales · Python notebook · 4 vCores/);
  assert.match(text, /1 h 5 min/);
  assert.match(text, /Session start 40\.0 s \+ run 1 h 4 min/);
  assert.match(text, /About 15,600 CU-seconds · about \$0\.500 \(estimate\)/);
  for (const line of Object.values(DONE.speed)) assert.ok(text.includes(line), line);
  assert.match(text, /This PC: Cell 3 raised ZeroDivisionError/);
  assert.match(text, /Fabric: The session restarted once/);
  // This PC's bar is split into processing and waiting.
  assert.strictEqual(await card().locator(".sbs-bar.split .upload").count(), 1);
  // The cores text for Match Fabric when the notebook's Fabric cores aren't known.
  assert.match(text, /A Spark notebook's cores depend on its pool, so this uses all 24 cores\. Pick a number to match the pool\./);
  assert.ok(await card().getByText("Cores on this PC for the next run").isVisible());
  // Eight tables, then Show all.
  const rows = () => card().locator(".sbs-tr:not(.head)").allInnerTexts();
  const first = await rows();
  assert.strictEqual(first.length, 8);
  assert.deepStrictEqual(first.slice(0, 5).map((r) => r.split("\n").map((s) => s.trim()).filter(Boolean)), [
    ["gold.daily_sales", "1,200", "1,200", "4 of 4", "✓ Match"],
    ["gold.weekly", "1,200", "1,199", "4 of 4", "✗ Differs 1 row differs"],
    ["silver.local_only", "1,200", "–", "–", "This PC only"],
    ["silver.fabric_only", "–", "1,200", "4 of 4", "Fabric only"],
    ["silver.broken", "1,200", "1,200", "4 of 4", "Couldn't read Couldn't read the Delta log"],
  ]);
  await card().getByRole("link", { name: "Show all 10 tables" }).click();
  assert.strictEqual((await rows()).length, 10);
  assert.match((await rows())[9], /bronze\.e[\s\S]*skipped/, "a result the app doesn't name shows as the engine wrote it");
  await card().getByRole("link", { name: "Show fewer" }).click();
  assert.strictEqual((await rows()).length, 8);
  // Open the run in Fabric goes to the Fabric page.
  await card().getByRole("link", { name: "Open the run in Fabric" }).click();
  await page().waitForFunction(() => state.view === "fabric");
  await page().evaluate(() => go("notebook"));
  // Close puts the card back to its "on" state.
  await card().getByRole("button", { name: "Close" }).click();
  await card().getByText("Side by side is on").waitFor();
  assert.strictEqual(await card().getByText("1 table differs.").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("Other results: a cancelled run, row counts uploaded, capacity unknown, a cost in another currency", { skip }, async () => {
  const base = { id: "r-x", started_at: "2026-11-18T09:30:00Z", workspace_name: "Contoso Sales", tables: [], speed: {} };
  lists.Cancelled = [{ ...base, name: "Cancelled", state: "cancelled", machine: { cores: 8, cores_used: 4 },
    local: { status: "cancelled", seconds: 3, upload: { tables: 1, rows: 45000, seconds: 2 } }, fabric: { status: "pending" } }];
  coresFor.Cancelled = { choice: "match", pc_cores: 4, engine: "python", fabric_vcores: 8, cores: 4 };
  await open("Cancelled");
  await card().getByText("✗ Cancelled.").waitFor();
  let text = await card().innerText();
  assert.match(text, /This PC · 4 of 8 cores/);
  assert.match(text, /No wait to start/);
  assert.match(text, /Uploaded 45,000 rows to OneLake in 2\.0 s$/m);
  assert.match(text, /Click to done/);
  assert.match(text, /Capacity use not known/);
  assert.match(text, /This PC has no more cores than the Fabric session's 8 vCores, so it uses all 4\./);
  assert.strictEqual(await card().getByRole("link", { name: "Open the run in Fabric" }).count(), 0);

  lists.Euro = [{ ...base, name: "Euro", state: "done", machine: {}, verdict: { same: null, text: "Nothing to compare." },
    local: { status: "ok", seconds: 7200 }, fabric: { status: "ok", total_seconds: 90, cu_seconds: 300, cost: 2.345, currency: "EUR" } }];
  coresFor.Euro = { choice: "all", pc_cores: 24, engine: "python", fabric_vcores: 8, cores: 24 };
  await open("Euro");
  await card().getByText("Nothing to compare.").waitFor();
  text = await card().innerText();
  assert.match(text, /2 h 0 min/);
  assert.match(text, /about 2\.35 EUR \(estimate\)/);
  assert.match(text, /Uses all 24 cores\. Fabric's session has 8 vCores, so the comparison is about hardware as much as engines\./);
  assert.strictEqual(await card().locator(".pill-s.neu").count(), 1, "no verdict either way");
  assert.deepStrictEqual(app.errors, []);
});

test("Pick a number keeps only a count this PC has", { skip }, async () => {
  coresFor.Custom = { choice: 6, pc_cores: 24, engine: "python", fabric_vcores: 4, cores: 6 };
  await open("Custom");
  const n = card().getByRole("spinbutton", { name: "Number of cores" });
  await n.waitFor();
  assert.match(await card().innerText(), /Uses 6 of 24 cores, for example to match a bigger Fabric session\./);
  assert.strictEqual(await card().getByRole("button", { name: "Pick a number" }).getAttribute("aria-pressed"), "true");
  await n.fill("99");
  await n.press("Enter");
  await n.blur();
  assert.strictEqual(await n.inputValue(), "6", "a count this PC doesn't have goes back");
  assert.strictEqual(sent("side_by_side_cores_set").length, 0);
  await n.fill("8");
  await n.blur();
  await card().getByText("Uses 8 of 24 cores").waitFor();
  assert.deepStrictEqual(sent("side_by_side_cores_set").map((p) => p.choice), [8]);
  // Choosing the option already picked sends nothing; another one sends it.
  await card().getByRole("button", { name: "Pick a number" }).click();
  assert.strictEqual(sent("side_by_side_cores_set").length, 1);
  await card().getByRole("button", { name: "Match Fabric (4 vCores)" }).click();
  await page().waitForFunction(() => document.querySelector(".sbs")?.innerText.includes("Uses 4 cores, the vCores"));
  assert.deepStrictEqual(sent("side_by_side_cores_set").map((p) => p.choice), [8, "match"]);
  assert.deepStrictEqual(app.errors, []);
});

test("The benchmark link adds the TPC-H notebooks and opens the first; the guide link opens the guide", { skip }, async () => {
  benchmark = () => ({ notebooks: ["TPC-H data", "TPC-H queries"], added: ["TPC-H data", "TPC-H queries"] });
  await open("Bench");
  await card().getByRole("link", { name: "Add the TPC-H benchmark notebooks" }).click();
  await toastSays(/^Added TPC-H data and TPC-H queries\. Run all here once to make the data, then open TPC-H queries\./);
  assert.deepStrictEqual(sent("benchmark_notebooks"), [{ lakehouse_ref: LAKEHOUSE }]);
  assert.deepStrictEqual(sent("open_notebook"), [{ name: "TPC-H data" }]);
  assert.strictEqual(await page().evaluate(() => state.notebook.name), "TPC-H data");
  assert.strictEqual(await page().evaluate(() => localStorage.getItem("fd.sbs.TPC-H queries")), "1", "the queries open with Side by side on");
  // Without a lakehouse it says to attach one; when they're already there it says so.
  await open("Bench 2", { lakehouse: null });
  await card().getByRole("link", { name: "Add the TPC-H benchmark notebooks" }).click();
  await toastSays(/Attach a lakehouse with Add data items, then Run all here once/);
  assert.deepStrictEqual(sent("benchmark_notebooks").at(-1), { lakehouse_ref: null });
  benchmark = () => ({ notebooks: ["TPC-H data", "TPC-H queries"], added: [] });
  await open("Bench 3");
  await card().getByRole("link", { name: "Add the TPC-H benchmark notebooks" }).click();
  await toastSays(/^TPC-H data and TPC-H queries are already here\./);
  // A refusal shows plainly and opens nothing.
  benchmark = () => { throw new Error("PermissionError: The notebooks folder is read-only"); };
  await open("Bench 4");
  const opens = sent("open_notebook").length;
  await card().getByRole("link", { name: "Add the TPC-H benchmark notebooks" }).click();
  await toastSays(/^Could not add the benchmark notebooks: /);
  assert.strictEqual(sent("open_notebook").length, opens);
  // How side by side works opens the guide page.
  await open("Bench 5");
  await card().getByRole("link", { name: "How side by side works" }).click();
  await page().waitForFunction(() => window.__fdShellCalls.some((c) => c.cmd === "open_doc"));
  assert.deepStrictEqual(await page().evaluate(() => window.__fdShellCalls.filter((c) => c.cmd === "open_doc").map((c) => c.args)),
    [{ page: "side-by-side.md" }]);
  assert.deepStrictEqual(app.errors, []);
});
