// #1424: the notebook's Prove panel (prove.js; see harness.cjs for how to run it). The check shows its
// findings (blockers, warnings, the plan, packages, the comparison rules) before anything runs, and
// sends no write. Start proof runs the risk check and then asks first, because the Fabric run uses
// capacity; Cancel sends nothing. A running proof shows each stage, and Cancel proof sends that
// proof's id. A matching result, a mismatch with its differences and a failed run each show what the
// engine returned. Change rules saves exactly what was set; the receipt lists its redactions and Save
// sends the ones ticked. Delete sandbox outputs asks first with the danger confirm, and Cancel sends
// nothing. An engine error shows a plain message, and axe finds nothing serious in each state.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads, axeSource, axeFindings, settleForAxe } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const NAME = "Daily sales";
const LAKEHOUSE = { name: "SalesLH", workspace_id: "ws-1", lakehouse_id: "lh-1", workspace_name: "Contoso Sales" };
const CELLS = [{ type: "code", cell_id: "a", source: "from fabric_desktop import lakehouse\ndf = lakehouse.read_table('sales')", meta: {} },
  { type: "code", cell_id: "b", source: "import requests\nlakehouse.write_table('dbo.sales_out', df)", meta: {} }];
const READS = ["prove_check", "prove_rules_get", "prove_list", "prove_status", "risk_check", "receipt_get", "receipt_redactions"];
const WRITES = ["prove_start", "prove_cancel", "prove_verify", "prove_delete_outputs", "prove_rules_set", "receipt_save", "risk_acknowledge"];
const at = (s) => new Date(Date.now() + s * 1000).toISOString();

// What prove_service.py answers. prove_check: {check (proof.check), environment (envprofile.check), plan}.
const CHECK_OK = {
  check: { ok: true, blockers: [], engine: "python", limits: ["This check reads the code; it can't see calls built at run time."],
    warnings: [{ cell: 2, cell_id: "b", text: "calls the network; the proof can't contain or compare what it changes", line: 1, code: "import requests" }],
    notes: [] },
  environment: { modules: [{ module: "requests", pc: { status: "installed", version: "2.32.3" }, fabric: { status: "provided", version: "2.31.0" } },
    { module: "json", pc: { status: "standard" }, fabric: { status: "standard" } }] },
  plan: { pc: "Runs every code cell in a fresh kernel on this PC.",
    fabric: `Publishes a copy as ${NAME}_proof in Contoso Sales and runs it there. Your published notebook isn't changed.`,
    inputs: "Every table is read at the version it has when the proof starts.",
    outputs: "Writes go to Files/_fabric_desktop/proofs/<proof>/ in SalesLH, not to its tables.", identity: "Both runs use your sign-in." },
};
const CHECK_BLOCKED = {
  check: { ok: false, engine: "python", limits: [], notes: [], warnings: [],
    blockers: [{ cell: 1, cell_id: "a", text: "is SQL that creates or changes tables; in a proof, write with lakehouse.write_table", line: 3, code: "CREATE TABLE x" }] },
  environment: { modules: [] }, plan: {},
};
// prove_rules_get / prove_rules_set: {"default": rules, "tables": {table: rules}} (_normalized_rules).
let rules = { default: { abs: 0.001 }, tables: { "dbo.sales_out": { keys: ["order_id"] } } };
// risk_service.risk_check: risk.analyze's result; a pass shows nothing (riskGate).
const RISK_PASS = { id: "r-pass", result: "pass", label: "Pass", summary: "Nothing found that should stop this run", can_proceed: true,
  needs_acknowledgement: false, note: "Read only: nothing was started, changed or paused to work this out.", reads: [],
  findings: [{ check: "capacity", outcome: "pass", label: "Pass", title: "fabcap is running (read 12 minutes ago)", detail: "", evidence: [] }] };

// A proof record as prove_start / prove_status return it (_copy of the record).
const ID = "20261101-101500-a1b2c3";
function record(state, more = {}) {
  return { id: ID, name: NAME, state, started_at: at(-60), finished_at: null, message: "",
    lakehouse: { name: "SalesLH", workspace_id: "ws-1", workspace_name: "Contoso Sales", lakehouse_id: "lh-1" },
    workspace_id: "ws-1", workspace_name: "Contoso Sales", machine: {}, check: CHECK_OK.check, rules, parameters: {}, code: {},
    pc: { status: "pending", cell: null, cells_total: 2, cells: {} }, fabric: { status: "pending", engine: "python" },
    inputs: {}, shared: {}, tables: [], verdict: null, scorecard: null, receipt_id: null, ...more };
}
const STAGES = {
  preparing: record("preparing", { inputs: { pinned_tables: 3 } }),
  pc: record("pc", { inputs: { pinned_tables: 3 }, pc: { status: "running", cell: 2, cells_total: 2, cells: {} } }),
  fabric: record("fabric", { inputs: { pinned_tables: 3 }, pc: { status: "ok", seconds: 4.2, cells_total: 2 },
    fabric: { status: "running", job_status: "InProgress", elapsed: 95, url: "https://app.fabric.microsoft.com/groups/ws-1/synapsenotebooks/n1" } }),
  verifying: record("verifying", { inputs: { pinned_tables: 3 }, pc: { status: "ok", seconds: 4.2 }, fabric: { status: "ok", total_seconds: 212 } }),
  cancelled: record("cancelled", { message: "Cancelled. Nothing was written to the lakehouse's tables.", finished_at: at(0), receipt_id: "c".repeat(64) }),
};
// Finished proofs (_proof_verify, _verdict, scorecard.build): one that matches, one that differs, one that failed.
const card = { time: { pc: { kind: "measured", seconds: 4.2 }, fabric: { kind: "measured", total_seconds: 212 }, faster: "This PC was 50x faster." },
  cost: { fabric: { kind: "estimated", cu_seconds: 1234.4 } } };
const sideRows = (n) => ({ rows: n, columns: 3 });
const MATCH = { ...record("done"), id: "p-match", started_at: "2026-10-30T09:00:00Z", receipt_id: "a".repeat(64), scorecard: card,
  tables: [{ table: "dbo.sales_out", pc_written: true, fabric_written: true, result: "match", level: "complete", method: "keyed",
    keys: ["order_id"], counts: { only_pc: 0, only_fabric: 0, changed: 0 }, column_diffs: {}, examples: [], pc: sideRows(120), fabric: sideRows(120) }],
  verdict: { verdict: "same", level: "complete", problems: [], text: "Same results in both places (1 table, every row compared)." } };
const DIFFERS = { ...record("done"), id: "p-differs", started_at: "2026-10-29T09:00:00Z", receipt_id: "b".repeat(64), scorecard: card,
  tables: [{ table: "dbo.sales_out", pc_written: true, fabric_written: true, result: "differs", level: "complete", method: "keyed",
    keys: ["order_id"], counts: { only_pc: 1, only_fabric: 0, changed: 1 }, pc: sideRows(120), fabric: sideRows(119),
    column_diffs: { amount: { rows: 1, max_abs: 0.5, max_rel: 0.004 }, region: { rows: 0 } },
    examples: [{ key: { order_id: 17 }, status: "changed", columns: { amount: { pc: 120.5, fabric: 120 } } },
      { key: { order_id: 42 }, status: "only_pc" }], offset: 0, rules: { keys: ["order_id"] } }],
  verdict: { verdict: "differs", level: "complete", problems: [], text: "Results differ in 1 of 1 table." } };
const FAILED = { ...record("failed"), id: "p-failed", started_at: "2026-10-28T09:00:00Z", receipt_id: "d".repeat(64),
  message: "HTTPError: 409 Client Error: Conflict for url: https://api.fabric.microsoft.com/v1/workspaces/ws-1/items",
  pc: { status: "ok", seconds: 4.2 }, fabric: { status: "failed" },
  verdict: { verdict: "failed", problems: [], text: "The run failed on Fabric, so the results can't be compared." } };
const DONE = { [MATCH.id]: MATCH, [DIFFERS.id]: DIFFERS, [FAILED.id]: FAILED };
// receipt_get: {id, receipt}; receipt_redactions: receipts.REDACTIONS.
const REDACTIONS = { identity: "Who ran it (user and tenant)", locations: "Workspace and lakehouse names and ids",
  values: "Row values shown as examples of differences", machine: "This PC's name and hardware" };
const RECEIPT = { schema: 1, kind: "proof", created_at: "2026-10-30T09:05:00+00:00", notebook: { name: NAME, code: { revision: "f".repeat(64), cells: ["x", "y"] } },
  proof: { id: MATCH.id, state: "done" }, inputs: { tables: [{ name: "dbo.sales", version: 7 }], files: [] },
  environment: { pc: { python: "3.12.4", packages: { pandas: "2.2.2" } }, fabric: { python: "3.11.9", packages: { pandas: "2.1.4" } } },
  verification: MATCH.verdict };

let mode = { check: CHECK_OK, checkError: null };
let stage = null;  // the running proof's stage prove_status answers, or null when none is running

before(async () => {
  if (skip) return;
  app = await openApp({ viewport: { width: 1440, height: 940 }, mocks: {
    save_notebook: () => ({ saved: true, cell_ids: CELLS.map((c) => c.cell_id) }),
    prove_check: () => { if (mode.checkError) throw new Error(mode.checkError); return mode.check; },
    prove_rules_get: () => rules,
    prove_rules_set: (p) => { rules = p.rules; return rules; },
    prove_list: () => [MATCH, DIFFERS, FAILED],
    risk_check: () => RISK_PASS,
    prove_start: () => (stage = "preparing", STAGES.preparing),
    prove_status: ({ id }) => {
      if (DONE[id]) return DONE[id];
      if (id === ID && stage) return STAGES[stage];
      throw new Error(`LookupError: No proof ${id}`);
    },
    prove_cancel: () => ({ cancelled: true }),
    prove_delete_outputs: ({ id }) => { DONE[id] = { ...DONE[id], outputs_deleted_at: at(0) }; return { deleted: `_fabric_desktop/proofs/${id}` }; },
    receipt_get: ({ id }) => ({ id, receipt: RECEIPT }),
    receipt_redactions: () => REDACTIONS,
    receipt_save: ({ id, hide }) => ({ path: `C:\\Users\\chris\\Downloads\\receipt-${id.slice(0, 12)}${hide.length ? "-redacted" : ""}.html`, format: "html", hidden: hide }),
  } });
  await startupReads(app);
  await page().evaluate(({ name, cells, lakehouse }) => {
    loadIntoState({ name, cells, lakehouse });
    state.user = "chris@contoso.com";
    state.ribbonTab = "run";
    go("notebook");
  }, { name: NAME, cells: CELLS, lakehouse: LAKEHOUSE });
});
after(async () => { await app?.close(); });

const pane = () => page().locator(".provepane");
const dialog = () => page().getByRole("dialog");
const sent = (method) => app.calls.filter((c) => c.method === method);
const writesSince = (n) => app.calls.slice(n).filter((c) => WRITES.includes(c.method)).map((c) => c.method);
async function axeClean(scope, what) {
  if (!axeSource) return;
  await settleForAxe(page());
  assert.deepStrictEqual(await axeFindings(page(), scope), [], `axe on ${what}`);
}
async function openPanel() {
  await page().getByRole("button", { name: /^Prove/ }).first().click();
  await pane().getByText("Ready to prove", { exact: false }).or(pane().getByText("Can't prove")).or(pane().locator(".problems")).first().waitFor();
}
async function closeDialogIfOpen() {
  if (await dialog().count()) await dialog().getByRole("button", { name: "Close" }).first().click();
  await dialog().waitFor({ state: "detached" });
}
// Opens an earlier proof from the check's "Earlier proofs" list, by its start time as the panel writes it.
async function openEarlier(r) {
  const label = await page().evaluate((at) => dateTimeText(at), r.started_at);
  await pane().getByRole("link", { name: label }).click();
  await pane().getByRole("heading", { name: "Proof result" }).or(pane().getByText("Proof result")).first().waitFor();
}
async function backToCheck() {
  await pane().getByRole("button", { name: "New proof" }).click();
  await pane().getByText("Ready to prove", { exact: false }).waitFor();
}

test("The check shows its findings before anything runs, and sends no write", { skip }, async () => {
  const from = app.calls.length;
  await openPanel();
  const text = await pane().innerText();
  assert.match(text, /Ready to prove\. 1 warning\./);
  assert.match(text, /Warnings \(kept in the receipt\)/i);
  assert.match(text, /Cell 2, line 1: calls the network/);
  for (const line of Object.values(CHECK_OK.plan).slice(0, 4)) assert.ok(text.includes(line), `the plan says: ${line}`);
  assert.match(text, /requests\s+2\.32\.3 \/ provided 2\.31\.0/, "a package that isn't standard shows both versions");
  assert.ok(!/\bjson\b/.test(text), "a standard-library module isn't listed");
  assert.match(text, /dbo\.sales_out\s+keys order_id/);
  assert.match(text, /match within 0\.001 or 1e-9/);
  assert.match(text, /Uses Fabric capacity/);
  assert.deepStrictEqual(writesSince(from), [], "opening the panel writes nothing");
  assert.deepStrictEqual(sent("prove_check").at(-1).params.name, NAME);
  await axeClean(".provepane", "the check");

  // A check with a blocker says why, names the cell, and offers no Start proof.
  mode.check = CHECK_BLOCKED;
  await pane().getByRole("button", { name: "Check again" }).click();
  await pane().getByText("Can't prove this notebook yet", { exact: false }).waitFor();
  assert.match(await pane().innerText(), /What stops it[\s\S]*Cell 1, line 3: is SQL that creates or changes tables/i);
  assert.strictEqual(await pane().getByRole("button", { name: /Start proof/ }).count(), 0);
  await axeClean(".provepane", "a blocked check");
  // A blocked check has no Check again of its own: closing and opening Prove checks again.
  mode.check = CHECK_OK;
  await pane().getByRole("button", { name: "Close" }).click();
  await pane().waitFor({ state: "detached" });
  await openPanel();
  await pane().getByText("Ready to prove", { exact: false }).waitFor();
  assert.deepStrictEqual(writesSince(from), [], "checking again writes nothing");
});

test("Change rules saves exactly what was set", { skip }, async () => {
  await pane().getByRole("link", { name: "Change rules" }).click();
  const d = dialog();
  await d.getByText("Saved in the notebook", { exact: false }).waitFor();
  await axeClean(".dialog", "the rules dialog");
  const tables = d.getByRole("textbox", { name: "Table" });
  assert.strictEqual(await tables.count(), 2, "one row per saved table, and an empty one");
  assert.strictEqual(await tables.nth(0).inputValue(), "dbo.sales_out");
  await d.getByRole("textbox", { name: "Columns to ignore" }).nth(0).fill("loaded_at");
  await tables.nth(1).fill("dbo.returns");
  await d.getByRole("textbox", { name: "Key columns" }).nth(1).fill(" order_id, line ");
  await d.getByLabel("Numbers: absolute difference").fill("0.01");
  await d.getByLabel("Relative difference").fill("");
  await d.getByLabel("Timestamps to the").selectOption("ms");
  const before = sent("prove_rules_set").length;
  await d.getByRole("button", { name: "Save" }).click();
  await dialog().waitFor({ state: "detached" });
  assert.strictEqual(sent("prove_rules_set").length, before + 1);
  assert.deepStrictEqual(sent("prove_rules_set").at(-1).params, { name: NAME, rules: {
    default: { abs: 0.01, timestamps: "ms" },
    tables: { "dbo.sales_out": { keys: ["order_id"], ignore: ["loaded_at"] }, "dbo.returns": { keys: ["order_id", "line"] } } } });
  const text = await pane().innerText();
  assert.match(text, /dbo\.returns\s+keys order_id, line/);
  assert.match(text, /dbo\.sales_out\s+keys order_id, ignoring loaded_at/);
  assert.match(text, /to the millisecond/);
});

test("Start proof asks first because Fabric uses capacity; Cancel sends nothing", { skip }, async () => {
  const from = app.calls.length;
  await pane().getByRole("button", { name: /Start proof/ }).click();
  const d = dialog();
  await d.getByText("Fabric capacity", { exact: false }).waitFor();
  assert.ok(sent("risk_check").length, "the risk check ran before asking");
  assert.deepStrictEqual(sent("risk_check").at(-1).params, { id: `notebook:${NAME}`, action: "run_in_fabric", env: null, refresh: false });
  await axeClean(".dialog", "the start confirm");
  assert.deepStrictEqual(writesSince(from), [], "nothing starts while it asks");
  await d.getByRole("button", { name: "Cancel" }).click();
  await dialog().waitFor({ state: "detached" });
  await page().waitForTimeout(300);
  assert.deepStrictEqual(writesSince(from), [], "Cancel sends nothing");
  assert.match(await pane().innerText(), /Ready to prove/, "the panel still shows the check");
});

test("A running proof shows each stage, and Cancel proof sends that proof's id", { skip }, async () => {
  await pane().getByRole("button", { name: /Start proof/ }).click();
  await dialog().getByRole("button", { name: "Start proof" }).click();
  await pane().getByText(`Proving ${NAME}`).waitFor();
  assert.strictEqual(sent("prove_start").length, 1, "one start, after the confirm");
  const start = sent("prove_start")[0].params;
  assert.strictEqual(start.name, NAME);
  assert.deepStrictEqual(start.rules, rules, "the proof runs with the saved rules");
  const step = (title) => pane().locator(".prove-steps li", { hasText: title });
  await step("Check and pin the inputs").and(page().locator(".now")).waitFor();
  assert.match(await step("Check and pin the inputs").innerText(), /3 tables pinned/);
  await axeClean(".provepane", "a running proof");
  for (const [s, title, want] of [["pc", "Run on this PC", /Cell 2 of 2/], ["fabric", "Run in Fabric", /Running, 1 min 35 s/],
    ["verifying", "Verify the results", null]]) {
    stage = s;
    await step(title).and(page().locator(".now")).waitFor({ timeout: 15000 });
    if (want) assert.match(await step(title).innerText(), want);
    if (s === "fabric") {
      assert.strictEqual(await step("Run in Fabric").getByRole("link", { name: "Open the run in Fabric" }).count(), 1);
      assert.match(await step("Run on this PC").innerText(), /✓ Run on this PC\s+4\.2 s/);
    }
  }
  assert.strictEqual(await pane().locator(".prove-steps li.done").count(), 3, "the stages before Verify are done");
  const before = sent("prove_cancel").length;
  await pane().getByRole("button", { name: "Cancel proof" }).click();
  for (const end = Date.now() + 5000; sent("prove_cancel").length === before && Date.now() < end;) await page().waitForTimeout(25);
  assert.deepStrictEqual(sent("prove_cancel").map((c) => c.params), [{ id: ID }]);
  stage = "cancelled";
  await pane().getByText("Proof result").waitFor({ timeout: 15000 });
  assert.match(await pane().innerText(), /Cancelled\./);
  await backToCheck();
  stage = null;
});

test("A matching result, a mismatch with its differences and a failed run show what the engine returned", { skip }, async () => {
  const from = app.calls.length;
  await openEarlier(MATCH);
  let text = await pane().innerText();
  assert.match(text, /Same results in both places \(1 table, every row compared\)\./);
  assert.match(text, /dbo\.sales_out\s+Same\s+By order_id/);
  assert.match(text, /This PC\s+Measured\s+4\.2 s/);
  assert.match(text, /Fabric\s+Measured\s+3 min 32 s/);
  assert.match(text, /Fabric capacity\s+Estimate\s+about 1,234 CU-s/);
  assert.strictEqual(await pane().locator(".pill-s", { hasText: "Same" }).count() >= 1, true);
  await axeClean(".provepane", "a matching result");
  await backToCheck();

  await openEarlier(DIFFERS);
  text = await pane().innerText();
  assert.match(text, /Results differ in 1 of 1 table\./);
  assert.match(text, /dbo\.sales_out\s+Differs/);
  await pane().getByRole("button", { name: /dbo\.sales_out/ }).click();
  const d = dialog();
  await d.getByText("Every row compared, matched by order_id.").waitFor();
  assert.match(await d.getByRole("tab", { name: /Changed/ }).innerText(), /Changed 1/);
  assert.match(await d.innerText(), /amount: 1 rows differ, largest 0\.500/);
  const cells = await d.locator(".prove-grid tbody tr").first().locator("td").allInnerTexts();
  assert.deepStrictEqual(cells, ["17", "amount", "120.5", "120", "0.500"]);
  await axeClean(".dialog", "the differences");
  await d.getByRole("tab", { name: /Only on this PC 1/ }).click();
  assert.deepStrictEqual(await d.locator(".prove-grid tbody tr").first().locator("td").allInnerTexts(), ["42"]);
  await closeDialogIfOpen();
  await backToCheck();

  await openEarlier(FAILED);
  text = await pane().innerText();
  assert.match(text, /The run failed on Fabric, so the results can't be compared\./);
  assert.ok(text.includes(FAILED.message), "the engine's reason is shown under the verdict");
  await axeClean(".provepane", "a failed run");
  assert.deepStrictEqual(writesSince(from), [], "looking at results writes nothing");
  await backToCheck();
});

test("The receipt shows its redactions and Save sends the ones ticked", { skip }, async () => {
  await openEarlier(MATCH);
  await pane().getByRole("button", { name: "Receipt" }).click();
  const d = dialog();
  await d.getByText("Content matches its id").waitFor();
  assert.deepStrictEqual(sent("receipt_get").at(-1).params, { id: MATCH.receipt_id });
  assert.match(await d.innerText(), /dbo\.sales\s+version 7/);
  for (const [k, label] of Object.entries(REDACTIONS)) {
    const box = d.getByRole("checkbox", { name: label });
    assert.strictEqual(await box.isChecked(), k !== "machine", `${label} starts ${k !== "machine" ? "ticked" : "unticked"}`);
  }
  await axeClean(".dialog", "the receipt");
  await d.getByRole("checkbox", { name: REDACTIONS.values }).uncheck();
  await d.getByRole("checkbox", { name: REDACTIONS.machine }).check();
  await d.getByRole("button", { name: "Save to Downloads" }).click();
  await dialog().waitFor({ state: "detached" });
  assert.deepStrictEqual(sent("receipt_save").at(-1).params, { id: MATCH.receipt_id, format: "html", hide: ["identity", "locations", "machine"] });
  await page().getByText(`Saved to C:\\Users\\chris\\Downloads\\receipt-${MATCH.receipt_id.slice(0, 12)}-redacted.html`).waitFor();

  // A receipt file is saved whole: the boxes turn off and nothing is left out.
  await pane().getByRole("button", { name: "Receipt" }).click();
  await dialog().getByText("Content matches its id").waitFor();
  await dialog().getByLabel("Format").selectOption("json");
  assert.strictEqual(await dialog().getByRole("checkbox", { name: REDACTIONS.identity }).isDisabled(), true);
  await dialog().getByRole("button", { name: "Save to Downloads" }).click();
  await dialog().waitFor({ state: "detached" });
  assert.deepStrictEqual(sent("receipt_save").at(-1).params, { id: MATCH.receipt_id, format: "json", hide: [] });
});

test("Delete sandbox outputs asks first with the danger confirm; Cancel sends nothing", { skip }, async () => {
  const from = app.calls.length;
  await pane().getByRole("button", { name: "Delete sandbox outputs" }).click();
  const d = dialog();
  await d.getByText(`Deletes Files/_fabric_desktop/proofs/${MATCH.id}/ in SalesLH`, { exact: false }).waitFor();
  const del = d.getByRole("button", { name: "Delete" });
  assert.match(await del.getAttribute("class"), /danger-solid/, "the danger confirm draws Delete red");
  assert.strictEqual(await d.getByRole("button", { name: "Cancel" }).evaluate((b) => b === document.activeElement), true, "Cancel has focus");
  await axeClean(".dialog", "the delete confirm");
  assert.deepStrictEqual(writesSince(from), [], "nothing is deleted while it asks");
  await d.getByRole("button", { name: "Cancel" }).click();
  await dialog().waitFor({ state: "detached" });
  await page().waitForTimeout(300);
  assert.deepStrictEqual(writesSince(from), [], "Cancel sends nothing");

  await pane().getByRole("button", { name: "Delete sandbox outputs" }).click();
  await dialog().getByRole("button", { name: "Delete" }).click();
  await pane().getByText("This proof's outputs were deleted; its receipt stays.").waitFor();
  assert.deepStrictEqual(sent("prove_delete_outputs").map((c) => c.params), [{ id: MATCH.id }]);
  assert.strictEqual(await pane().getByRole("button", { name: "Delete sandbox outputs" }).count(), 0);
  await backToCheck();
});

test("An engine error shows a plain message", { skip }, async () => {
  mode.checkError = "ValueError: The notebook's workspace couldn't be found. Pick one in Publish first.";
  await pane().getByRole("button", { name: "Check again" }).click();
  await pane().locator(".problems").waitFor();
  const text = await pane().locator(".problems").innerText();
  assert.strictEqual(text, "The notebook's workspace couldn't be found. Pick one in Publish first.");
  assert.ok(!/Error|\[object/.test(text), `plain: ${text}`);
  await axeClean(".provepane", "an engine error");
  mode.checkError = null;
  await pane().getByRole("button", { name: "Check again" }).click();
  await pane().getByText("Ready to prove", { exact: false }).waitFor();
  assert.deepStrictEqual(app.errors, []);
});
