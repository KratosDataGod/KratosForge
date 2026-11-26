// app/ui/prove.js beyond prove-panel.test.cjs: a notebook with no lakehouse, a check the engine refuses and
// Check again, an earlier proof that can't be opened, a proof already running when the panel opens (followed
// to its end, with the panel closed meanwhile) and one the engine has lost, a refused rules save, a table
// compared as whole rows, by checksum or written on one side only, Verify again under other rules (with a
// receipt, a refusal and paging through the differences), the full scorecard, a receipt with files, and the
// guide link. The engine's calls are mocked (see harness.cjs for how to run it); each case drives the panel
// as a person would and checks what they see and what the app asked the engine for.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const NAME = "Daily sales";
const LAKEHOUSE = { name: "SalesLH", workspace_id: "ws-1", lakehouse_id: "lh-1", workspace_name: "Contoso Sales" };
const CELLS = [{ type: "code", cell_id: "a", source: "from fabric_desktop import lakehouse\ndf = lakehouse.read_table('sales')", meta: {} }];
const copy = (x) => JSON.parse(JSON.stringify(x));

// prove_service.prove_check: ready, with no warnings.
const CHECK_OK = { check: { ok: true, blockers: [], warnings: [], notes: [], limits: [] }, environment: { modules: [] },
  plan: { pc: "Runs every code cell in a fresh kernel on this PC." } };
// A proof record as prove_status returns it.
function record(id, state, more = {}) {
  return { id, name: NAME, state, started_at: "2026-10-30T09:00:00Z", message: "", lakehouse: { name: "SalesLH" },
    pc: { status: "ok", seconds: 4.2, cells_total: 1 }, fabric: { status: "pending" }, inputs: { pinned_tables: 1 }, tables: [],
    verdict: null, scorecard: null, receipt_id: null, ...more };
}
// A finished proof whose tables were compared three ways.
const WHOLE = {
  table: "dbo.events", result: "differs", level: "complete", method: "whole rows", counts: { only_pc: 0, only_fabric: 2 },
  examples: [{ status: "only_fabric", row: { id: 1, name: "late" }, times: 2 }], column_diffs: {},
  columns: { only_pc: ["debug_flag"], only_fabric: ["_ingested"], types: ["amount (double / decimal)"] },
  limitations: ["Rows are compared as text after rounding floats."], pc: { rows: 3 }, fabric: { rows: 5 }, rules: {},
};
const MIXED = record("p-mixed", "done", {
  tables: [WHOLE,
    { table: "dbo.big", result: "match", level: "fingerprint", method: "keyed", keys: ["id"], pc: { rows: 6000000 }, fabric: { rows: 6000000 }, rules: {} },
    { table: "dbo.scratch", result: "only_pc", note: "Only this PC wrote dbo.scratch." }],
  verdict: { verdict: "differs", problems: ["dbo.events differs", "dbo.scratch was only written on this PC"], text: "Results differ in 1 of 3 tables." },
  message: "Fabric's run took longer than usual.",
  scorecard: {
    time: { pc: { kind: "measured", seconds: 4.2 }, fabric: { kind: "estimated", total_seconds: 212, job_seconds: 190, run_seconds: 60, startup_seconds: 120 },
      faster: "This PC was 50x faster", faster_run: "Its notebook run alone was 14x faster" },
    resources: { pc: { kind: "measured", peak_memory_mb: 2048, cpu_seconds: 7.6, cores: 8, memory_gb: 32 }, fabric: { kind: "not reported", text: "Fabric doesn't report a run's memory." } },
    cost: { fabric: { kind: "estimated", cu_seconds: 1234.4, amount: 0.06, currency: "USD", assumptions: ["A Python notebook uses 2 vCores"] },
      pc: { kind: "measured", text: "This PC's run used no Fabric capacity." } },
    notes: ["Times are from one run each."],
  },
  receipt_id: "e".repeat(64),
});
// prove_verify of dbo.events under keys: 120 changed rows, 50 a page.
const page50 = (offset) => ({ ...copy(WHOLE), method: "keyed", keys: ["id"], counts: { changed: 120, only_pc: 0, only_fabric: 0 },
  examples: Array.from({ length: 50 }, (_, i) => ({ key: { id: offset + i + 1 }, status: "changed", columns: { name: { pc: "a", fabric: "b" } } })),
  column_diffs: { name: { rows: 120, max_abs: null } }, offset });
const RECEIPT = { notebook: { name: NAME }, proof: { id: "p-mixed", state: "done" },
  inputs: { tables: [], files: [{ path: "Files/raw/sales.csv", sha256: "0123456789abcdef0123" }] }, // pragma: allowlist secret (fake checksum)
  environment: { pc: { python: "3.12.4", packages: {} } }, verification: { verdict: "differs" } };

let db;
const fresh = () => ({ check: CHECK_OK, checkError: null, history: [], status: {}, verify: [], verifyError: null, rulesError: null });
const mocks = {
  save_notebook: () => ({ saved: true, cell_ids: ["a"] }),
  prove_check: () => { if (db.checkError) throw new Error(db.checkError); return copy(db.check); },
  prove_rules_get: () => ({ default: {}, tables: {} }),
  prove_rules_set: (p) => { if (db.rulesError) throw new Error(db.rulesError); return p.rules; },
  prove_list: () => copy(db.history),
  // An id maps to a record, or to an error the engine gives for it.
  prove_status: ({ id }) => {
    const s = db.status[id];
    if (!s) throw new Error(`LookupError: No proof ${id}`);
    if (s instanceof Error) throw s;
    return copy(s);
  },
  prove_verify: ({ offset }) => { if (db.verifyError) throw new Error(db.verifyError); return db.verify.shift() || page50(offset); },
  receipt_get: ({ id }) => ({ id, receipt: copy(RECEIPT) }),
  receipt_redactions: () => ({ identity: "Who ran it (user and tenant)" }),
};

before(async () => {
  if (skip) return;
  db = fresh();
  app = await openApp({ mocks });
  await startupReads(app);
});
after(async () => { await app?.close(); });

// Each case opens the notebook afresh, with or without its lakehouse, and the panel closed.
async function openNotebook(lakehouse = LAKEHOUSE) {
  await page().evaluate(({ name, cells, lakehouse: lh }) => {
    closeDialog(); const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    loadIntoState({ name, cells, ...(lh ? { lakehouse: lh } : {}) });
    state.user = "chris@contoso.com";
    state.ribbonTab = "run";
    go("notebook");
  }, { name: NAME, cells: CELLS, lakehouse });
}
beforeEach(() => { if (!skip) db = fresh(); });

const pane = () => page().locator(".provepane");
const dialog = () => page().locator("#dialog-root .dialog");
const callsFrom = (from, method) => app.calls.slice(from).filter((c) => c.method === method).map((c) => c.params);
// Waits for the app to have asked the engine; fails with the method's name if it never does.
async function waitCall(from, method, n = 1) {
  for (const end = Date.now() + 15000; callsFrom(from, method).length < n; await new Promise((r) => setTimeout(r, 25))) {
    if (Date.now() > end) throw new Error(`${method} was never called`);
  }
  return callsFrom(from, method);
}
const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test(document.getElementById("toast").textContent), re.source, { timeout: 15000 });
const proveButton = () => page().getByRole("button", { name: /^Prove/ }).first();
async function openMixed() {
  db.history = [MIXED];
  db.status["p-mixed"] = MIXED;
  await openNotebook();
  await proveButton().click();
  const label = await page().evaluate((at) => dateTimeText(at), MIXED.started_at);
  await pane().getByRole("link", { name: label }).click();
  await pane().getByText("Results differ in 1 of 3 tables.").waitFor();
}

test("without a lakehouse, the check says to attach one and asks the engine nothing", { skip }, async () => {
  await openNotebook(null);
  const from = app.calls.length;
  await proveButton().click();
  await pane().getByText("Can't prove this notebook yet", { exact: false }).waitFor();
  assert.match(await pane().innerText(), /what stops it\s+Attach a lakehouse first\. A proof pins its tables and keeps its writes there\./i);
  assert.deepStrictEqual(callsFrom(from, "prove_check"), []);
  assert.strictEqual(await pane().getByRole("button", { name: /Start proof/ }).count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("a check the engine refuses says why, and Check again reads it again", { skip }, async () => {
  db.checkError = "PermissionError: The notebook's folder can't be read";
  await openNotebook();
  const from = app.calls.length;
  await proveButton().click();
  await pane().locator(".problems").getByText("The notebook's folder can't be read").waitFor();
  db.checkError = null;
  await pane().getByRole("button", { name: "Check again" }).click();
  await pane().getByText("Ready to prove", { exact: false }).waitFor();
  assert.strictEqual(callsFrom(from, "prove_check").length, 2);
  assert.match(await pane().innerText(), /Ready to prove\. No warnings\./);

  // How a proof works opens the guide.
  await pane().getByRole("link", { name: "How a proof works" }).click();
  await page().waitForFunction(() => window.__fdShellCalls.some((c) => c.cmd === "open_doc" && c.args.page === "prove/how-proofs-work.md"));
  assert.deepStrictEqual(app.errors, []);
});

test("an earlier proof that can't be opened says so and the check stays", { skip }, async () => {
  db.history = [record("p-gone", "done", { verdict: { verdict: "same" } }), record("p-odd", "weird")];
  await openNotebook();
  await proveButton().click();
  const rows = pane().locator(".prove-sec", { hasText: "Earlier proofs" }).locator(".prove-row");
  await rows.first().waitFor();
  assert.match(await rows.nth(0).innerText(), /Same/);
  assert.match(await rows.nth(1).innerText(), /weird/, "a state with no verdict shows as it is");
  await rows.nth(0).getByRole("link").click();
  await toastSays(/Could not open that proof/);
  assert.match(await pane().innerText(), /Ready to prove/);
});

test("a proof already running is followed when Prove opens; closing the panel says it keeps going, and its end is announced", { skip }, async () => {
  db.history = [record("p-run", "fabric", { fabric: { status: "publishing", elapsed: 5 } })];
  db.status["p-run"] = db.history[0];
  await openNotebook();
  const from = app.calls.length;
  await proveButton().click();
  await pane().getByText(`Proving ${NAME}`).waitFor();
  assert.match(await pane().locator(".prove-steps li.now").innerText(), new RegExp(`Publishing ${NAME}_proof, 5(\\.0)? s`));
  await waitCall(from, "prove_status");
  assert.deepStrictEqual(callsFrom(from, "prove_status")[0], { id: "p-run" });

  await pane().getByRole("button", { name: "Close" }).click();
  await toastSays(/^The proof keeps running\. Open Prove again to follow it/);
  await pane().waitFor({ state: "detached" });

  db.status["p-run"] = record("p-run", "done", { verdict: { verdict: "same", problems: [], text: "Same results in both places." } });
  await toastSays(new RegExp(`^${NAME}: Same results in both places\\.`));
  await proveButton().click();
  await pane().getByText("Proof result").waitFor();
  assert.match(await pane().innerText(), /Same results in both places\./);
  assert.deepStrictEqual(app.errors, []);
});

test("a running proof the engine no longer knows ends as failed, saying it was lost", { skip }, async () => {
  db.history = [record("p-lost", "pc", { pc: { status: "running", cell: 1, cells_total: 1 } })];
  await openNotebook();
  await proveButton().click();
  await pane().getByText(`Proving ${NAME}`).waitFor();
  await toastSays(new RegExp(`^${NAME}: Lost track of the proof: LookupError: No proof p-lost`));
  await pane().getByText("Proof result").waitFor();
  assert.match(await pane().innerText(), /^failed$[\s\S]*Lost track of the proof/m);
});

test("a refused rules save says why and keeps the dialog", { skip }, async () => {
  db.rulesError = "ValueError: abs must be a number";
  await openNotebook();
  await proveButton().click();
  await pane().getByRole("link", { name: "Change rules" }).click();
  await dialog().getByLabel("Numbers: absolute difference").fill("lots");
  await dialog().getByRole("button", { name: "Save" }).click();
  await dialog().locator(".field-error").getByText("abs must be a number").waitFor();
  assert.ok(await dialog().isVisible());
  await dialog().getByRole("button", { name: "Cancel" }).click();
});

test("tables compared as whole rows, by checksum, or written on one side show how, with the verdict's other problems", { skip }, async () => {
  await openMixed();
  const text = await pane().innerText();
  assert.match(text, /dbo\.scratch was only written on this PC/, "every problem after the first is listed");
  assert.match(text, /Fabric's run took longer than usual\./);
  const table = (name) => pane().locator(".prove-table", { hasText: name });
  assert.match(await table("dbo.events").innerText(), /Differs\s+Whole rows/);
  assert.match(await table("dbo.big").innerText(), /Same\s+Checksum only/);
  assert.match(await table("dbo.scratch").innerText(), /This PC only\s+Only this PC wrote dbo\.scratch\./);

  await table("dbo.big").click();
  await dialog().getByText(/Too many rows to compare one by one \(6,000,000\)/).waitFor();
  assert.match(await dialog().locator(".dialog-head").innerText(), /dbo\.big: same/);
  await dialog().locator(".dialog-actions").getByRole("button", { name: "Close" }).click();

  await table("dbo.scratch").click();
  await dialog().getByText("Only this PC wrote dbo.scratch.").waitFor();
  assert.strictEqual(await dialog().getByText("Verify again under other rules").count(), 0, "one side can't be verified again");
  await dialog().locator(".dialog-actions").getByRole("button", { name: "Close" }).click();

  await table("dbo.events").click();
  const d = dialog();
  await d.getByText("Every row compared as a whole row, in any order.").waitFor();
  const t = await d.innerText();
  assert.match(t, /Rows are compared as text after rounding floats\./);
  assert.match(t, /Columns only on this PC: debug_flag/);
  assert.match(t, /Columns only in Fabric: _ingested/);
  assert.match(t, /Different types: amount \(double \/ decimal\)/);
  assert.strictEqual(await d.getByRole("tab", { name: "Only in Fabric 2" }).getAttribute("aria-selected"), "true", "the side that has rows opens");
  assert.deepStrictEqual(await d.locator(".prove-grid thead th").allInnerTexts(), ["id", "name", "Times"]);
  assert.deepStrictEqual(await d.locator(".prove-grid tbody td").allInnerTexts(), ["1", "late", "2"]);
  await d.getByRole("tab", { name: "Only on this PC 0" }).click();
  await d.getByText("None.").waitFor();
  await d.locator(".dialog-actions").getByRole("button", { name: "Close" }).click();
  assert.deepStrictEqual(app.errors, []);
});

test("Verify again sends the rules typed, pages through the differences, saves a receipt, and says why when refused", { skip }, async () => {
  await openMixed();
  await pane().locator(".prove-table", { hasText: "dbo.events" }).click();
  const d = dialog();
  await d.getByText("Verify again under other rules").waitFor();
  await d.getByLabel("Columns that identify a row").fill("id");
  await d.getByLabel("Columns to ignore").fill(" _ingested ");
  await d.getByLabel("Absolute difference").fill("0.5");
  const from = app.calls.length;
  await d.getByRole("button", { name: "Verify again", exact: true }).click();
  await d.getByText("Every row compared, matched by id.").waitFor();
  assert.deepStrictEqual(callsFrom(from, "prove_verify"), [{ id: "p-mixed", table: "dbo.events",
    rules: { keys: ["id"], ignore: ["_ingested"], abs: 0.5, rel: 1e-9 }, offset: 0, save: false }]);
  // The side shown stays the one picked; Changed has the keyed differences.
  assert.strictEqual(await d.getByRole("tab", { name: "Only in Fabric 0" }).getAttribute("aria-selected"), "true");
  await d.getByRole("tab", { name: "Changed 120" }).click();
  assert.match(await d.innerText(), /name: 120 rows differ/);
  assert.match(await d.innerText(), /Showing rows 1 to 50 of the differing rows\./);
  assert.deepStrictEqual(await d.locator(".prove-grid tbody tr").first().locator("td").allInnerTexts(), ["1", "name", "a", "b", ""]);

  await d.getByRole("button", { name: "Next" }).click();
  await d.getByText("Showing rows 51 to 100 of the differing rows.").waitFor();
  assert.strictEqual(callsFrom(from, "prove_verify").at(-1).offset, 50);
  await d.getByRole("button", { name: "Previous" }).click();
  await d.getByText("Showing rows 1 to 50 of the differing rows.").waitFor();
  assert.strictEqual(callsFrom(from, "prove_verify").at(-1).offset, 0);

  db.verify.push({ ...page50(0), receipt_id: "123456789abcdef0" });
  await d.getByRole("button", { name: "Verify and save a receipt" }).click();
  await d.getByText("Saved as receipt 123456789abc.").waitFor();
  assert.strictEqual(callsFrom(from, "prove_verify").at(-1).save, true);

  // A result back to whole rows opens on this PC's side.
  db.verify.push({ ...copy(WHOLE), counts: { only_pc: 1, only_fabric: 0 }, examples: [{ status: "only_pc", row: { id: 9 }, times: 1 }] });
  await d.getByRole("button", { name: "Verify again", exact: true }).click();
  await d.getByText("Every row compared as a whole row, in any order.").waitFor();
  assert.strictEqual(await d.getByRole("tab", { name: "Only on this PC 1" }).getAttribute("aria-selected"), "true");

  db.verifyError = "LookupError: The proof's outputs were deleted";
  await d.getByRole("button", { name: "Verify again", exact: true }).click();
  await d.locator(".field-error").getByText("The proof's outputs were deleted").waitFor();
  await d.locator(".dialog-actions").getByRole("button", { name: "Close" }).click();
  assert.deepStrictEqual(app.errors, []);
});

test("the full scorecard shows each figure with what kind of number it is, and the receipt lists its files", { skip }, async () => {
  await openMixed();
  await pane().getByRole("link", { name: "Full scorecard" }).click();
  const d = dialog();
  await d.getByRole("region", { name: "Scorecard" }).waitFor();
  const cards = await d.locator(".prove-card").allInnerTexts();
  assert.strictEqual(cards.length, 6);
  assert.match(cards[0], /Time on this PC\s+Measured\s+4\.2 s\s+This PC was 50x faster\. Its notebook run alone was 14x faster/);
  assert.match(cards[1], /Time in Fabric\s+Estimate\s+3 min 32 s\s+Publish to finish\. Job 3 min 10 s, notebook run 1 min 0 s, session start 2 min 0 s\./);
  assert.match(cards[2], /Resources on this PC\s+Measured\s+2\.0 GB peak\s+8 s of CPU on 8 cores, 32 GB\./);
  assert.match(cards[3], /Resources in Fabric\s+Not available\s+Not reported\s+Fabric doesn't report a run's memory\./);
  assert.match(cards[4], /Fabric capacity\s+Estimate\s+about 1,234 CU-s\s+About 0\.06 USD at pay-as-you-go prices\.\s+A Python notebook uses 2 vCores/);
  assert.match(cards[5], /Fabric capacity of this PC's run\s+Measured\s+No Fabric job\s+This PC's run used no Fabric capacity\./);
  assert.match(await d.innerText(), /Times are from one run each\./);
  await d.locator(".dialog-actions").getByRole("button", { name: "Close" }).click();

  await pane().getByRole("button", { name: "Receipt" }).click();
  await dialog().getByText("Content matches its id").waitFor();
  const r = await dialog().innerText();
  assert.match(r, /Files\/raw\/sales\.csv\s+0123456789ab…/);
  assert.match(r, /This PC\s+Python 3\.12\.4/);
  assert.doesNotMatch(r, /Fabric\s+Python/, "no Fabric environment, no line for it");
  assert.match(r, /Verdict\s+differs/);
  await dialog().locator(".dialog-actions").getByRole("button", { name: "Close" }).click();
  assert.deepStrictEqual(app.errors, []);
});
