// #1455: the access actions in app/ui/estate.js, in a real browser (see harness.cjs for how to run it).
// OneLake > Access > Environments: changing someone's workspace role asks first and names the workspace,
// Cancel puts the old role back and sends nothing, and confirming sends exactly that workspace, role and
// assignment; taking someone's access away asks in a danger confirm. Give access sends exactly the
// workspace, role and person picked. OneLake > Access > Table access: saving a data access role sends
// exactly the name, tables and members entered, finding a person sends what was typed, deleting a role
// asks in a danger confirm (with the DefaultReader warning for DefaultReader) and Cancel sends nothing,
// and Who can read sends exactly the lakehouse and table and shows the answer. Optimize > Cost and
// savings: removing a usage import asks in a danger confirm, and importing sends the file and the
// options chosen. An engine refusal shows a plain message. Opening these views sends no write, axe
// finds nothing serious, and there are no page errors.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const ME = "chris@contoso.com";
const READS = ["access_review", "catalog", "access_lakehouse", "cost_report", "optimize_status"];  // optimize_status: the Optimize page's band
const WRITES = ["access_set_role", "access_save_role", "access_delete_role", "usage_import", "usage_import_delete"];
// Asks the engine something without changing anything, but only after the person acts.
const LOOKUPS = ["access_find_person", "access_who_can_read", "usage_preview"];
const DENIED = "HTTPError: 403 Client Error: Forbidden for url: https://api.fabric.microsoft.com/v1/workspaces/6f1c2a40-1111-4a00-8000-000000000002/roleAssignments";
const PLAIN_403 = "You don't have permission for that in this workspace. A workspace admin can make you a Contributor or above.";

// Fabric ids are GUIDs: the engine checks them (estate_service.py _guid).
const WS_DEV = "6f1c2a40-1111-4a00-8000-000000000001", WS_PROD = "6f1c2a40-1111-4a00-8000-000000000002";
const LH = "9b2d7e10-2222-4b00-8000-000000000001";
const ANA = "a0000000-0000-4000-8000-00000000a001", GROUP = "a0000000-0000-4000-8000-00000000a002";
const RAJ = "a0000000-0000-4000-8000-00000000a003", APP = "a0000000-0000-4000-8000-00000000a004";
const ASG = { anaDev: "c0000000-0000-4000-8000-0000000000d1", anaProd: "c0000000-0000-4000-8000-0000000000d2",
  groupProd: "c0000000-0000-4000-8000-0000000000d3" };
const ROLES = ["Admin", "Member", "Contributor", "Viewer"];  // access.py ROLES

// estate_service.access_review: access.review(targets) | {"roles": ROLES}; one row per person
// (access.principal), with their role and assignment id in each workspace, keyed by the target's key.
function freshReview() {
  return {
    workspaces: [
      { key: "dev", label: "Dev", env: "dev", workspace_id: WS_DEV, workspace_name: "Sales Dev", error: null },
      { key: "prod", label: "Prod", env: "prod", workspace_id: WS_PROD, workspace_name: "Sales Prod", error: null },
    ],
    people: [
      { id: ANA, name: "Ana Lopez", type: "User", email: "ana@contoso.com", guest: false, flags: [],
        roles: { dev: { role: "Contributor", assignment_id: ASG.anaDev }, prod: { role: "Viewer", assignment_id: ASG.anaProd } } },
      { id: GROUP, name: "Sales readers", type: "Group", email: "", guest: false, flags: [],
        roles: { prod: { role: "Viewer", assignment_id: ASG.groupProd } } },
    ],
    flagged: 0,
    roles: ROLES,
  };
}

// service.catalog(all_types=True): one row per item.
const CATALOG = {
  workspaces: [{ id: WS_DEV, displayName: "Sales Dev", type: "Workspace" }],
  items: [{ workspace_id: WS_DEV, workspace_name: "Sales Dev", item_id: LH, name: "LH_Sales", type: "Lakehouse", description: "", lakehouse_id: LH }],
  skipped: [], favorites: [],
};

// estate_service.access_lakehouse: the roles as access.describe_role gives them, the workspace's people
// (access.principal | {role}), and its schemas and tables as paths.
function freshLakehouse() {
  return {
    workspace_id: WS_DEV, item_id: LH, enabled: true, problem: null,
    people: [
      { id: ANA, name: "Ana Lopez", type: "User", email: "ana@contoso.com", guest: false, role: "Contributor" },
      { id: GROUP, name: "Sales readers", type: "Group", email: "", guest: false, role: "Viewer" },
    ],
    schemas: ["/Tables/dbo"],
    tables: ["/Tables/dbo/customers", "/Tables/dbo/orders"],
    roles: [
      { name: "DefaultReader", paths: ["*"], entra: [], item_members: ["ReadAll"] },
      { name: "SalesReaders", paths: ["/Tables/dbo/orders"], entra: [GROUP], item_members: [] },
    ],
  };
}

// The usage file, as a Capacity Metrics app Timepoint export (sidecar/tests/test_usage_import.py OPS).
const CSV = `Workspace,Item kind,Item name,Operation,Start,End,Status,User,Duration (s),Total CU (s),Timepoint CU (s),Billing type,Operation ID
Sales Dev,Notebook,nb_sales,Notebook run,10/06/2026 2:00:05 PM,10/06/2026 2:04:00 PM,Succeeded,ana@contoso.com,235,"1,880.5",0.65,Billable,op-1
Sales Dev,Notebook,nb_other,Notebook run,10/06/2026 3:00:05 PM,10/06/2026 3:01:00 PM,Succeeded,bo@contoso.com,55,440,0.1,Non-billable,op-3
`;
const measured = (value, metric = "fabric_capacity_cu") => ({ classification: "measured", label: "Measured", metric, unit: "cu_hours", value,
  currency: null, parts: 2, unknown_parts: 0, complete: true, by_classification: { measured: 2 }, assumptions: [],
  methods: ["Total CU (s) column of the Operations (Timepoint page) export"], sources: ["Capacity Metrics app export (metrics.csv)"] });
const zero = (metric, unit, currency = null) => ({ classification: "measured", label: "Measured", metric, unit, value: 0, currency, parts: 0,
  unknown_parts: 0, complete: true, by_classification: {}, assumptions: [], methods: [], sources: [] });
// usage/store.py Store.summaries (SUMMARY keys), as usage_service.usage_import answers and imports_list lists.
const IMPORT = {
  id: "u-0948f39629fcafb8", provider: "metrics_app", provider_label: "Capacity Metrics app export", layout: "operations",
  layout_label: "Operations (Timepoint page)", file_name: "metrics.csv", sha256: "0948f39629fcafb857dbe15b912526e9a75fe273fa105caa253b95f856c98d5c",
  imported_at: new Date(Date.now() - 3600e3).toISOString(), options: { tz: "UTC", decimal: ".", date_order: "mdy", period_start: null, period_end: null },
  period: { start: "2026-10-06T14:00:05+00:00", end: "2026-10-06T15:01:00+00:00" }, workspaces: ["Sales Dev"], capacities: [], rows: 2,
  total: measured(0.644583), cost: [], skipped: [], notes: [],
};
// estate_service.cost_report: costs.report(...) | {unavailable, capacities, usage}; usage is
// usage_service._usage_reconcile (usage/reconcile.py reconcile | {"imports_list"}). Captured from the engine.
function costReport() {
  const since = new Date(Date.now() - 30 * 864e5).toISOString(), now = new Date().toISOString();
  const imports = db.imports;
  return {
    days: 30, since, settings: { price_per_cu_hour: 0.18, currency: "USD", python_cu: 1.0, spark_cu: 8.0 },
    total: { runs: 0, seconds: 0, fabric_runs: 0, local_runs: 0, unknown_runs: 0, uncalibrated_runs: 0, cu_hours: 0, cost: 0,
      scenario_cu_hours: 0, scenario_saved: 0, evidence: { fabric_job_cu: zero("fabric_job_cu", "cu_hours"),
        fabric_cost: zero("fabric_cost", "currency", "USD"), avoided_cu: zero("fabric_job_cu_avoided", "cu_hours"),
        avoided_cost: zero("fabric_cost_avoided", "currency", "USD") } },
    by_env: [], by_item: [], by_day: [], unavailable: {}, capacities: { observed_at: null, environments: [], partial: false },
    usage: {
      period: { start: since, end: now }, imports: imports.length, rows: imports.length ? 2 : 0, rows_outside_period: 0,
      imported: imports.length ? measured(0.644583) : null,
      matched: { rows: 0, runs: 0, measured: null, estimated: null, measured_to_estimate: null },
      unmatched: { rows: imports.length ? 2 : 0, measured: imports.length ? measured(0.644583) : null,
        by_reason: imports.length ? { "No run of this item started from this app at that time": 2 } : {} },
      runs: { total: 0, matched: 0, no_row: 0, not_covered: 0, no_item_name: 0 }, by_run: [], tolerance_s: 300, billed: [],
      imports_list: imports.map((i) => ({ ...i })),
    },
  };
}
// usage_service.usage_preview: usage.read without its rows. The dates in CSV can be read both ways, so
// the first look asks for the date order (usage/reader.py NeedsChoice "date_order").
function preview({ file_name, content, options }) {
  const base = { file_name, sha256: IMPORT.sha256, provider: { id: "metrics_app", label: "Capacity Metrics app export" },
    layout: { id: "operations", label: "Operations (Timepoint page)", timed: true },
    columns: { used: { workspace: "Workspace", item: "Item name", start: "Start", end: "End", cu: "Total CU (s)" },
      ignored: ["User", "Timepoint CU (s)"], not_stored: ["User"], missing: [] }, notes: [] };
  if (!content.startsWith("Workspace,")) return { ...base, ready: false, problem: "This isn't a usage export.", needs: null };
  if (!options.date_order) {
    return { ...base, ready: false, needs: { option: "date_order", choices: { mdy: "Month first: 04/30/2026", dmy: "Day first: 30/04/2026" } },
      problem: "The dates can be read two ways (is 03/04 the 3rd of April or March 4th?). Choose the date order the export uses." };
  }
  return { ...base, ready: true, problem: null, needs: null, options: { decimal: ".", period_start: null, period_end: null, ...options },
    rows_read: 2, to_import: 2, duplicates_in_file: 0, already_imported: 0, replaces_earlier: 0, skipped: [],
    period: IMPORT.period, workspaces: ["Sales Dev"], capacities: [], items: 2, total: measured(0.644583), cost: [] };
}

let db;
const fail = {};  // method -> the engine's error text, for the next calls to it
const copy = (x) => JSON.parse(JSON.stringify(x));
const guard = (method, fn) => (p) => { if (fail[method]) throw new Error(fail[method]); return fn(p); };
function fresh() { return { review: freshReview(), lh: freshLakehouse(), imports: [{ ...IMPORT }] }; }

const mocks = {
  access_review: guard("access_review", () => copy(db.review)),
  // estate_service.access_set_role: update, delete (role ""), add by id, or add by email; {"ok": True}.
  access_set_role: guard("access_set_role", ({ workspace_id, role, assignment_id, principal_id }) => {
    const w = db.review.workspaces.find((x) => x.workspace_id === workspace_id);
    const p = db.review.people.find((x) => x.id === principal_id);
    if (w && p) { if (role) p.roles[w.key] = { role, assignment_id: assignment_id || "c0000000-0000-4000-8000-0000000000e1" }; else delete p.roles[w.key]; }
    return { ok: true };
  }),
  catalog: () => copy(CATALOG),
  access_lakehouse: guard("access_lakehouse", () => copy(db.lh)),
  // estate_service.access_save_role / access_delete_role: the lakehouse afresh (access_lakehouse).
  access_save_role: guard("access_save_role", ({ name, paths, members, original_name }) => {
    if (db.lh.roles.some((r) => r.name === name) && name !== original_name) throw new Error(`ValueError: There is already a role named ${name}.`);
    db.lh.roles = db.lh.roles.filter((r) => r.name !== original_name && r.name !== name).concat({ name, paths, entra: members, item_members: [] });
    return copy(db.lh);
  }),
  access_delete_role: guard("access_delete_role", ({ name }) => { db.lh.roles = db.lh.roles.filter((r) => r.name !== name); return copy(db.lh); }),
  // estate_service.access_find_person: {id, name, email, type}, or LookupError.
  access_find_person: guard("access_find_person", ({ text }) => {
    if (text === "raj@contoso.com") return { id: RAJ, name: "Raj Patel", email: "raj@contoso.com", type: "User" };
    throw new Error(`LookupError: No one with the email ${text} in your organization.`);
  }),
  // estate_service.access_who_can_read: {readers (access.who_can_read), partial, problem}.
  access_who_can_read: guard("access_who_can_read", ({ table }) => ({
    readers: [
      { id: ANA, name: "Ana Lopez", type: "User", email: "ana@contoso.com", guest: false, how: "Workspace Contributor" },
      ...(table === "/Tables/dbo/orders" ? [{ id: GROUP, name: "Sales readers", type: "Group", email: "", guest: false, how: "Data access role SalesReaders" }] : []),
    ], partial: false, problem: null })),
  cost_report: () => costReport(),
  usage_preview: guard("usage_preview", preview),
  usage_import: guard("usage_import", (p) => {
    const ok = preview(p);
    if (!ok.ready) throw new Error(`ValueError: ${ok.problem}`);
    db.imports.push({ ...IMPORT, options: ok.options });
    return { ...IMPORT, options: ok.options, already_imported: 0, duplicates_in_file: 0 };
  }),
  usage_import_delete: guard("usage_import_delete", ({ import_id }) => {
    const had = db.imports.length;
    db.imports = db.imports.filter((i) => i.id !== import_id);
    return { deleted: db.imports.length < had };
  }),
};

let baseline;  // the methods the app calls on its own

before(async () => {
  if (skip) return;
  db = fresh();
  app = await openApp({ mocks });
  // The app's startup reads, and its first approval polls 5 s in (integrations_pending, editor_pending).
  await startupReads(app);
  for (const end = Date.now() + 15000; !["integrations_pending", "editor_pending"].every((m) => app.calls.some((c) => c.method === m));
    await new Promise((r) => setTimeout(r, 50))) {
    if (Date.now() > end) throw new Error("the app's startup polls never came");
  }
  baseline = new Set(app.calls.map((c) => c.method));
  await page().evaluate((me) => { state.user = me; }, ME);
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  db = fresh();
  for (const k of Object.keys(fail)) delete fail[k];
  await page().evaluate(() => {
    closeMenu(); closeDialog(); const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    accessPage.data = null; accessPage.lh = null; accessPage.lakehouse = null; catalogPage.data = null; go("home");
  });
});

const sent = (from, methods = WRITES) => app.calls.slice(from).filter((c) => methods.includes(c.method)).map((c) => ({ method: c.method, params: c.params }));
const pause = (ms) => new Promise((r) => setTimeout(r, ms));
const dialog = () => page().locator("#dialog-root .dialog");
async function toastText() {
  const handle = await page().waitForFunction(() => { const t = document.getElementById("toast"); return !t.hidden && t.textContent; });
  return handle.jsonValue();
}
function plainly(said) {
  assert.doesNotMatch(said, /\[object Object\]|Error:|HTTPError|Forbidden for url|undefined/);
}

// ---------- Environments: workspace roles ----------

async function openRoles() {
  await page().evaluate(() => { catalogPage.tab = "access"; accessPage.view = "roles"; go("onelake"); });
  await page().getByLabel("Ana Lopez in Prod").waitFor();
}
const roleSel = (who, ws) => page().getByLabel(`${who} in ${ws}`, { exact: true });

test("changing a workspace role asks first and names the workspace; Cancel puts the old role back and sends nothing", { skip }, async () => {
  await openRoles();
  const from = app.calls.length;
  await roleSel("Ana Lopez", "Prod").selectOption("Contributor");
  await dialog().waitFor();
  assert.match(await dialog().textContent(), /^\s*Change access\?/);
  assert.match(await dialog().textContent(), /This will change Ana Lopez Contributor on Sales Prod in Fabric now\./);
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await dialog().waitFor({ state: "detached" });
  await pause(300);
  assert.strictEqual(await roleSel("Ana Lopez", "Prod").inputValue(), "Viewer");
  assert.deepStrictEqual(sent(from), []);
});

test("confirming a role change sends exactly that workspace, role and assignment", { skip }, async () => {
  await openRoles();
  const from = app.calls.length;
  await roleSel("Ana Lopez", "Prod").selectOption("Contributor");
  await dialog().getByRole("button", { name: "Change", exact: true }).click();
  assert.match(await toastText(), /^Access changed for Ana Lopez/);
  assert.deepStrictEqual(sent(from), [{ method: "access_set_role", params: { workspace_id: WS_PROD, role: "Contributor",
    assignment_id: ASG.anaProd, principal_id: ANA, principal_type: "User" } }]);
  // Read afresh: the new role shows.
  await page().waitForFunction(() => document.querySelector("[aria-label='Ana Lopez in Prod']")?.value === "Contributor");
});

test("giving a role in a workspace where someone has none sends no assignment, and their kind", { skip }, async () => {
  await openRoles();
  const from = app.calls.length;
  await roleSel("Sales readers", "Dev").selectOption("Viewer");
  assert.match(await dialog().textContent(), /This will give Sales readers Viewer on Sales Dev in Fabric now\./);
  await dialog().getByRole("button", { name: "Change", exact: true }).click();
  await toastText();
  assert.deepStrictEqual(sent(from), [{ method: "access_set_role", params: { workspace_id: WS_DEV, role: "Viewer",
    assignment_id: "", principal_id: GROUP, principal_type: "Group" } }]);
});

test("No access takes someone's access away, so it asks in a danger confirm naming the workspace", { skip }, async () => {
  await openRoles();
  const from = app.calls.length;
  await roleSel("Ana Lopez", "Dev").selectOption("");
  await dialog().waitFor();
  assert.match(await dialog().textContent(), /This will remove Ana Lopez from Sales Dev in Fabric now\./);
  const ok = dialog().getByRole("button", { name: "Change", exact: true });
  assert.match(await ok.getAttribute("class"), /danger-solid/);
  // A danger confirm starts on Cancel, so Enter doesn't take the access away.
  assert.strictEqual(await page().evaluate(() => document.activeElement?.textContent.trim()), "Cancel");
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await dialog().waitFor({ state: "detached" });
  assert.strictEqual(await roleSel("Ana Lopez", "Dev").inputValue(), "Contributor");
  assert.deepStrictEqual(sent(from), []);
  await roleSel("Ana Lopez", "Dev").selectOption("");
  await dialog().getByRole("button", { name: "Change", exact: true }).click();
  await toastText();
  assert.deepStrictEqual(sent(from), [{ method: "access_set_role", params: { workspace_id: WS_DEV, role: "",
    assignment_id: ASG.anaDev, principal_id: ANA, principal_type: "User" } }]);
});

test("a refused role change shows a plain message and the old role comes back", { skip }, async () => {
  fail.access_set_role = DENIED;
  await openRoles();
  await roleSel("Ana Lopez", "Prod").selectOption("Admin");
  await dialog().getByRole("button", { name: "Change", exact: true }).click();
  const said = await toastText();
  assert.strictEqual(said.split("\n")[0], `Access not changed: ${PLAIN_403}`);
  plainly(said);
  await page().waitForFunction(() => document.querySelector("[aria-label='Ana Lopez in Prod']")?.value === "Viewer");
});

async function openGrant() {
  await openRoles();
  await page().locator(".head-actions").getByRole("button", { name: "Give access" }).click();
  await dialog().getByLabel("Who").waitFor();
}

test("Give access sends exactly the workspace, role and person picked, by email", { skip }, async () => {
  await openGrant();
  const from = app.calls.length;
  await dialog().getByLabel("Who").fill("  raj@contoso.com ");
  await dialog().getByLabel("Workspace").selectOption(WS_PROD);
  await dialog().getByLabel("Role").selectOption("Member");
  await dialog().getByRole("button", { name: "Give access" }).click();
  assert.match(await toastText(), /^raj@contoso\.com is now Member/);
  await dialog().waitFor({ state: "detached" });
  assert.deepStrictEqual(sent(from), [{ method: "access_set_role", params: { workspace_id: WS_PROD, role: "Member", email: "raj@contoso.com" } }]);
});

test("Give access by object ID sends the id and its kind, and a refusal keeps the dialog with a plain message", { skip }, async () => {
  await openGrant();
  const from = app.calls.length;
  // Viewer is picked to start with.
  assert.strictEqual(await dialog().getByLabel("Role").inputValue(), "Viewer");
  await dialog().getByLabel("Who").fill(APP);
  await dialog().getByLabel("Kind (for an object ID)").selectOption("ServicePrincipal");
  await dialog().getByLabel("Workspace").selectOption(WS_DEV);
  fail.access_set_role = DENIED;
  await dialog().getByRole("button", { name: "Give access" }).click();
  const said = await toastText();
  assert.strictEqual(said.split("\n")[0], `Access not given: ${PLAIN_403}`);
  plainly(said);
  assert.strictEqual(await dialog().count(), 1, "the dialog stays open with what was picked");
  assert.strictEqual(await dialog().getByLabel("Who").inputValue(), APP);
  delete fail.access_set_role;
  await dialog().getByRole("button", { name: "Give access" }).click();
  await dialog().waitFor({ state: "detached" });
  const want = { method: "access_set_role", params: { workspace_id: WS_DEV, role: "Viewer", principal_id: APP, principal_type: "ServicePrincipal" } };
  assert.deepStrictEqual(sent(from), [want, want]);
});

// ---------- Table access: OneLake data access roles ----------

async function openTables() {
  await page().evaluate(() => { catalogPage.tab = "access"; accessPage.view = "tables"; go("onelake"); });
  await page().getByRole("row", { name: /SalesReaders/ }).waitFor();
}
const roleRow = (name) => page().locator(".estate table.list tbody tr", { has: page().locator("td:first-child b", { hasText: new RegExp(`^${name}$`) }) });

test("saving a new data access role sends exactly the name, tables and members entered; finding a person sends what was typed", { skip }, async () => {
  await openTables();
  await page().getByRole("button", { name: "New role" }).click();
  await dialog().getByLabel("Name (letters and digits)").fill("  CustomerReaders ");
  await dialog().getByLabel("dbo.customers", { exact: true }).check();
  await dialog().getByLabel("Ana Lopez (Contributor)").check();
  const from = app.calls.length;
  await dialog().getByLabel("Someone else").fill("raj@contoso.com");
  await dialog().getByRole("button", { name: "Add", exact: true }).click();
  await dialog().getByLabel("Raj Patel", { exact: true }).waitFor();
  assert.ok(await dialog().getByLabel("Raj Patel", { exact: true }).isChecked());
  assert.deepStrictEqual(sent(from, LOOKUPS), [{ method: "access_find_person", params: { text: "raj@contoso.com", workspace_id: WS_DEV } }]);
  assert.strictEqual(await dialog().getByLabel("Someone else").inputValue(), "");
  await dialog().getByRole("button", { name: "Save role" }).click();
  await dialog().waitFor({ state: "detached" });
  assert.match(await toastText(), /^Saved role CustomerReaders/);
  assert.deepStrictEqual(sent(from), [{ method: "access_save_role", params: { workspace_id: WS_DEV, item_id: LH, name: "CustomerReaders",
    paths: ["/Tables/dbo/customers"], members: [ANA, RAJ], original_name: null } }]);
  await roleRow("CustomerReaders").waitFor();
  // Raj has no workspace role, so the lakehouse's people don't name him: the list shows his object ID.
  assert.match(await roleRow("CustomerReaders").textContent(), new RegExp(`dbo\\.customers.*Ana Lopez.*${RAJ}`));
});

test("editing a role sends its old name, what it read and what was added", { skip }, async () => {
  await openTables();
  await roleRow("SalesReaders").getByRole("button", { name: "Edit" }).click();
  assert.strictEqual(await dialog().getByLabel("Name (letters and digits)").inputValue(), "SalesReaders");
  assert.ok(await dialog().getByLabel("dbo.orders", { exact: true }).isChecked());
  assert.ok(await dialog().getByLabel("Sales readers (Viewer)").isChecked());
  await dialog().getByLabel("dbo.customers", { exact: true }).check();
  const from = app.calls.length;
  await dialog().getByRole("button", { name: "Save role" }).click();
  await dialog().waitFor({ state: "detached" });
  assert.deepStrictEqual(sent(from), [{ method: "access_save_role", params: { workspace_id: WS_DEV, item_id: LH, name: "SalesReaders",
    paths: ["/Tables/dbo/orders", "/Tables/dbo/customers"], members: [GROUP], original_name: "SalesReaders" } }]);
});

test("a person who can't be found, and a refused save, say so plainly and keep the dialog", { skip }, async () => {
  await openTables();
  await page().getByRole("button", { name: "New role" }).click();
  await dialog().getByLabel("Someone else").fill("nobody@contoso.com");
  await dialog().getByRole("button", { name: "Add", exact: true }).click();
  let said = await toastText();
  assert.strictEqual(said.split("\n")[0], "Couldn't find them: No one with the email nobody@contoso.com in your organization.");
  plainly(said);
  assert.strictEqual(await dialog().getByLabel("Someone else").inputValue(), "nobody@contoso.com");
  await page().evaluate(() => { const t = document.getElementById("toast"); t.hidden = true; t.textContent = ""; });
  await dialog().getByLabel("Name (letters and digits)").fill("SalesReaders");
  await dialog().getByLabel("dbo.customers", { exact: true }).check();
  await dialog().getByLabel("Ana Lopez (Contributor)").check();
  await dialog().getByRole("button", { name: "Save role" }).click();
  said = await toastText();
  assert.strictEqual(said.split("\n")[0], "Role not saved: There is already a role named SalesReaders.");
  plainly(said);
  assert.strictEqual(await dialog().count(), 1);
  assert.strictEqual(db.lh.roles.length, 2);
});

test("deleting a role asks in a danger confirm; Cancel sends nothing; DefaultReader carries its warning", { skip }, async () => {
  await openTables();
  const from = app.calls.length;
  await roleRow("SalesReaders").getByRole("button", { name: "Delete" }).click();
  await dialog().waitFor();
  assert.match(await dialog().textContent(), /Delete role SalesReaders\?.*Its members lose what this role let them read\./);
  assert.match(await dialog().getByRole("button", { name: "Delete", exact: true }).getAttribute("class"), /danger-solid/);
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await dialog().waitFor({ state: "detached" });
  await pause(300);
  assert.deepStrictEqual(sent(from), []);

  await roleRow("DefaultReader").getByRole("button", { name: "Delete" }).click();
  await dialog().waitFor();
  assert.match(await dialog().textContent(), /DefaultReader is what lets people with read permission see every table\. Without it they only see what other roles give them\./);
  assert.match(await dialog().getByRole("button", { name: "Delete", exact: true }).getAttribute("class"), /danger-solid/);
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await dialog().waitFor({ state: "detached" });
  await pause(300);
  assert.deepStrictEqual(sent(from), []);

  await roleRow("DefaultReader").getByRole("button", { name: "Delete" }).click();
  await dialog().getByRole("button", { name: "Delete", exact: true }).click();
  assert.match(await toastText(), /^Deleted role DefaultReader/);
  assert.deepStrictEqual(sent(from), [{ method: "access_delete_role", params: { workspace_id: WS_DEV, item_id: LH, name: "DefaultReader" } }]);
  await roleRow("DefaultReader").waitFor({ state: "detached" });
  assert.strictEqual(await roleRow("SalesReaders").count(), 1);
});

test("a refused delete shows a plain message and the role stays", { skip }, async () => {
  fail.access_delete_role = "HTTPError: 403 Client Error: Forbidden for url: https://onelake.dfs.fabric.microsoft.com/v1/workspaces/x/items/y/dataAccessRoles";
  await openTables();
  await roleRow("SalesReaders").getByRole("button", { name: "Delete" }).click();
  await dialog().getByRole("button", { name: "Delete", exact: true }).click();
  const said = await toastText();
  assert.strictEqual(said.split("\n")[0], `Role not deleted: ${PLAIN_403}`);
  plainly(said);
  assert.strictEqual(await roleRow("SalesReaders").count(), 1);
});

test("Who can read sends exactly the lakehouse and table, and shows the answer", { skip }, async () => {
  await openTables();
  const from = app.calls.length;
  const who = page().locator(".who-reads");
  await who.getByLabel("Table").selectOption("/Tables/dbo/orders");
  await who.locator(".row.tight", { hasText: "Sales readers" }).waitFor();
  const rows = await who.locator(".row.tight").allTextContents();
  assert.deepStrictEqual(rows, ["Ana LopezWorkspace Contributor", "Sales readersData access role SalesReaders"]);
  assert.deepStrictEqual(sent(from, LOOKUPS), [{ method: "access_who_can_read", params: { workspace_id: WS_DEV, item_id: LH, table: "/Tables/dbo/orders" } }]);
  assert.deepStrictEqual(sent(from), []);
});

test("a refused Who can read says so plainly and doesn't keep saying it's working", { skip }, async () => {
  fail.access_who_can_read = "HTTPError: 403 Client Error: Forbidden for url: https://api.fabric.microsoft.com/v1/workspaces/x/roleAssignments";
  await openTables();
  const who = page().locator(".who-reads");
  await who.getByLabel("Table").selectOption("/Tables/dbo/customers");
  const said = await toastText();
  assert.strictEqual(said.split("\n")[0], `Could not work it out: ${PLAIN_403}`);
  plainly(said);
  await page().waitForFunction(() => !document.querySelector(".who-reads").textContent.includes("Working it out"));
});

// ---------- Optimize > Cost and savings: measured usage imports ----------

async function openCost() {
  await page().evaluate(() => { optimizePage.tab = "cost"; go("optimize"); });
  await page().locator(".usage-card").waitFor();
}

test("removing a usage import asks in a danger confirm; Cancel sends nothing; confirming sends that import", { skip }, async () => {
  await openCost();
  await page().locator(".usage-imports summary").click();
  const from = app.calls.length;
  const remove = page().locator(".usage-imports").getByRole("button", { name: "Remove" });
  await remove.click();
  await dialog().waitFor();
  assert.match(await dialog().textContent(), /Remove metrics\.csv\?.*Its rows are removed from this PC\. Your exported file isn't touched/);
  assert.match(await dialog().getByRole("button", { name: "Remove", exact: true }).getAttribute("class"), /danger-solid/);
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await dialog().waitFor({ state: "detached" });
  await pause(300);
  assert.deepStrictEqual(sent(from), []);
  await remove.click();
  await dialog().getByRole("button", { name: "Remove", exact: true }).click();
  await page().locator(".usage-imports").waitFor({ state: "detached" });
  assert.deepStrictEqual(sent(from), [{ method: "usage_import_delete", params: { import_id: IMPORT.id } }]);
});

test("a refused remove shows a plain message and the import stays", { skip }, async () => {
  fail.usage_import_delete = "ValueError: 'u-zz' isn't an import id";
  await openCost();
  await page().locator(".usage-imports summary").click();
  await page().locator(".usage-imports").getByRole("button", { name: "Remove" }).click();
  await dialog().getByRole("button", { name: "Remove", exact: true }).click();
  const said = await toastText();
  assert.strictEqual(said.split("\n")[0], "Not removed: 'u-zz' isn't an import id");
  plainly(said);
  assert.strictEqual(await page().locator(".usage-imports").count(), 1);
});

test("importing sends the file and the options chosen, after the preview asks what it must", { skip }, async () => {
  db.imports = [];
  await openCost();
  await page().getByRole("button", { name: "Import usage file" }).click();
  await dialog().getByLabel("Times are in").fill("+02:00");
  await dialog().getByLabel("Times are in").press("Tab");  // as a person moves on to pick the file
  const go = dialog().getByRole("button", { name: "Import", exact: true });
  assert.ok(await go.isDisabled());
  const from = app.calls.length;
  await dialog().getByLabel("File").setInputFiles({ name: "metrics.csv", mimeType: "text/csv", buffer: Buffer.from(CSV) });
  await dialog().getByRole("button", { name: "Day first: 30/04/2026" }).click();
  await dialog().locator("text=Recognized as").waitFor();
  await page().waitForFunction(() => !document.querySelector("#dialog-root .dialog .tool.primary").disabled);
  assert.deepStrictEqual(sent(from, ["usage_preview"]).map((c) => c.params.options), [{ tz: "+02:00" }, { tz: "+02:00", date_order: "dmy" }]);
  assert.deepStrictEqual(sent(from), [], "the preview saves nothing");
  await go.click();
  await dialog().waitFor({ state: "detached" });
  assert.match(await toastText(), /^Imported 2 rows of measured usage/);
  assert.deepStrictEqual(sent(from), [{ method: "usage_import", params: { file_name: "metrics.csv", content: CSV, options: { tz: "+02:00", date_order: "dmy" } } }]);
  await page().locator(".usage-imports").waitFor();
});

test("a refused import shows a plain message and keeps the dialog", { skip }, async () => {
  db.imports = [];
  fail.usage_import = "ValueError: This file was already imported on 2026-10-31 as 'metrics.csv'.";
  await openCost();
  await page().getByRole("button", { name: "Import usage file" }).click();
  await dialog().getByLabel("File").setInputFiles({ name: "metrics.csv", mimeType: "text/csv", buffer: Buffer.from(CSV) });
  await dialog().getByRole("button", { name: "Month first: 04/30/2026" }).click();
  await page().waitForFunction(() => !document.querySelector("#dialog-root .dialog .tool.primary").disabled);
  await dialog().getByRole("button", { name: "Import", exact: true }).click();
  const said = await toastText();
  assert.strictEqual(said.split("\n")[0], "Not imported: This file was already imported on 2026-10-31 as 'metrics.csv'.");
  plainly(said);
  assert.strictEqual(await dialog().count(), 1);
});

// ---------- reading only ----------

test("opening these views and the dialogs sends no write", { skip }, async () => {
  const from = app.calls.length;
  await openRoles();
  await page().locator(".head-actions").getByRole("button", { name: "Give access" }).click();
  await dialog().getByLabel("Who").waitFor();
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await openTables();
  await page().getByRole("button", { name: "New role" }).click();
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await roleRow("SalesReaders").getByRole("button", { name: "Edit" }).click();
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await openCost();
  await page().locator(".usage-imports summary").click();
  await page().getByRole("button", { name: "Import usage file" }).click();
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await pause(300);
  const made = app.calls.slice(from).map((c) => c.method);
  for (const m of READS) assert.ok(made.includes(m), `${m} was read`);
  assert.deepStrictEqual(made.filter((m) => WRITES.includes(m) || LOOKUPS.includes(m)), []);
  // Anything else is the app's own polling (the approval polls repeat), never a new kind of call.
  assert.deepStrictEqual([...new Set(made.filter((m) => !READS.includes(m) && !baseline.has(m)))], []);
});

test("axe finds nothing serious on the roles, table access and usage views and their dialogs", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  const check = async (where, selector) => assert.deepStrictEqual(await axeFindings(page(), selector), [], where);
  await openRoles();
  await check("workspace roles", ".page");
  await roleSel("Ana Lopez", "Dev").selectOption("");
  await dialog().waitFor();
  await check("remove confirm", "#dialog-root .dialog");
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await page().locator(".head-actions").getByRole("button", { name: "Give access" }).click();
  await check("give access", "#dialog-root .dialog");
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await openTables();
  await page().locator(".who-reads").getByLabel("Table").selectOption("/Tables/dbo/orders");
  await page().locator(".who-reads .row.tight").first().waitFor();
  await check("table access", ".page");
  await roleRow("SalesReaders").getByRole("button", { name: "Edit" }).click();
  await check("role dialog", "#dialog-root .dialog");
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await roleRow("DefaultReader").getByRole("button", { name: "Delete" }).click();
  await check("delete confirm", "#dialog-root .dialog");
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await openCost();
  await page().locator(".usage-imports summary").click();
  await check("usage", ".usage-card");
  await page().getByRole("button", { name: "Import usage file" }).click();
  await check("import dialog", "#dialog-root .dialog");
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
