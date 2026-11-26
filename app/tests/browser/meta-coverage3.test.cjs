// app/ui/meta.js beyond meta-coverage, meta-coverage2 and the other Data mesh tests: the Environments tab's Delta table
// features at publish (Preview): each table's choice, the typed confirm for a choice a reader can't read, cancelling it,
// and the engine's refusal; the card hidden with preview features off and with no tables; and the Best practices
// check's states (a run in Fabric, a failed check without a reason, a model with no tables, no findings) and its
// confirm (a warning that must be ticked, a blocked check). The engine is mocked in meta_service's shapes; nothing
// reaches Fabric.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const copy = (x) => JSON.parse(JSON.stringify(x));

// meta_service.project's delta block for a table (delta_profile.table_view).
const delta = (o = {}) => ({ features: "", reader_names: ["Power BI Direct Lake", "Spark 3.4", "the SQL analytics endpoint"], problems: [],
  reasons: { default: [], every_reader: [], deletion_vectors: ["Spark 3.4 can't read deletion vectors (Delta reader 3)."], column_mapping: [] }, ...o });
const PROJECT = {
  path: "C:/work/sales", name: "Sales", version: "7", templates: {},
  layers: [{ name: "silver", lakehouse: "LH_Silver", entities: 2, drift: "evolve", required_checks: [], reads_from: [], description: "", retention_days: 7 }],
  entities: [{ name: "silver.customers", layer: "silver", delta: delta() },
    { name: "silver.orders", layer: "silver", delta: delta({ features: "every_reader", reader_names: ["Spark 3.4"],
      problems: ["silver.orders already uses deletion vectors, so a run with this choice writes nothing."] }) },
    { name: "bronze.raw", layer: "bronze" }],
  products: [], links: [], exception_notices: [], key_tables: [], source_systems: [], errors: [], run_order: [], designations: [],
  environments: { dev: { target: "local" }, test: { target: "local" }, prod: { target: "local" } },
  project: { name: "Sales", domain: "sales" },
};

let db;
const fresh = () => ({ project: copy(PROJECT), refuse: null });
const mocks = {
  meta_project: () => copy(db.project),
  meta_runs: () => [],
  capacity_health: () => ({ observed_at: null, environments: [], partial: false }),
  meta_promote_check: () => ({ errors: [], version: "7", notes: [] }),
  meta_set_delta_features: ({ name, delta_features }) => {
    if (db.refuse) throw new Error(db.refuse);
    const e = db.project.entities.find((x) => x.name === name);
    e.delta.features = delta_features || "";
    return copy(db.project);
  },
};

before(async () => {
  if (skip) return;
  db = fresh();
  app = await openApp({ mocks });
  await startupReads(app);
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  db = fresh();
  await page().evaluate(() => {
    closeDialog(); const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    state.settings = { ...(state.settings || {}), show_preview: { value: true } };
    Object.assign(state.meta, { env: "dev", project: null });
    go("home");
  });
  app.calls.length = 0;
  app.errors.length = 0;
});

const calls = (m) => app.calls.filter((c) => c.method === m).map((c) => c.params);
const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test(document.getElementById("toast").textContent), re.source);
const card = () => page().locator(".card", { hasText: "Delta table features at publish" });
async function openEnvironments() {
  await page().evaluate(() => { state.meta.tab = "environments"; go("metadata"); });
  await card().waitFor();
}

test("each table's Delta features are listed with the readers they're checked against and any refusal", { skip }, async () => {
  await openEnvironments();
  const rows = await card().locator(".delta-tables > .row").allInnerTexts();
  assert.strictEqual(rows.length, 2, "only the tables with a Delta block");
  assert.match(rows[0], /silver\.customers[\s\S]*Checked against Power BI Direct Lake, Spark 3\.4 and the SQL analytics endpoint\./);
  assert.match(rows[1], /Checked against Spark 3\.4\.[\s\S]*silver\.orders already uses deletion vectors, so a run with this choice writes nothing\.[\s\S]*See the reader matrix/);
  assert.strictEqual(await card().locator("select").nth(1).inputValue(), "every_reader");
  assert.deepStrictEqual(await page().evaluate(() => [andList([]), andList(["a"]), andList(["a", "b"]), andList(["a", "b", "c"])]), ["", "a", "a and b", "a, b and c"]);
  assert.deepStrictEqual(app.errors, []);
});

test("a choice every reader can read saves at once; one a reader can't read asks for the table's name first", { skip }, async () => {
  await openEnvironments();
  const select = card().locator("select").first();
  await select.selectOption("every_reader");
  await toastSays(/^Saved\. Runs that publish silver\.customers now use this choice\.$/);
  assert.deepStrictEqual(calls("meta_set_delta_features"), [{ name: "silver.customers", delta_features: "every_reader", confirm_name: null }]);

  await select.selectOption("deletion_vectors");
  const d = page().getByRole("dialog", { name: "Allow deletion vectors on silver.customers?" });
  await d.waitFor();
  assert.match(await d.innerText(), /Spark 3\.4 can't read deletion vectors \(Delta reader 3\)\./);
  await d.getByRole("button", { name: "Cancel" }).click();
  assert.strictEqual(await select.inputValue(), "every_reader", "a cancelled choice goes back to the saved one");
  assert.strictEqual(calls("meta_set_delta_features").length, 1);

  await select.selectOption("deletion_vectors");
  await d.waitFor();
  await d.getByRole("textbox").fill("silver.customers");
  await d.getByRole("button", { name: "Allow it" }).click();
  await toastSays(/^Saved\. Runs that publish silver\.customers now use this choice\.$/);
  assert.deepStrictEqual(calls("meta_set_delta_features").at(-1), { name: "silver.customers", delta_features: "deletion_vectors", confirm_name: "silver.customers" });

  await select.selectOption("");
  await toastSays(/^Saved\./);
  assert.deepStrictEqual(calls("meta_set_delta_features").at(-1), { name: "silver.customers", delta_features: null, confirm_name: null });
  assert.deepStrictEqual(app.errors, []);
});

test("a choice the engine refuses goes back to the saved one and says why under the table", { skip }, async () => {
  await openEnvironments();
  db.refuse = "silver.orders already uses column mapping, so it can't be set to every reader. Rewrite the table first.";
  const select = card().locator("select").nth(1);
  await select.selectOption("column_mapping");
  await card().getByText("silver.orders already uses column mapping, so it can't be set to every reader. Rewrite the table first.").waitFor();
  assert.strictEqual(await select.inputValue(), "every_reader");
  assert.deepStrictEqual(app.errors, []);
});

test("with no tables the card says how to add one, and with preview features hidden it isn't shown", { skip }, async () => {
  db.project.entities = [{ name: "bronze.raw", layer: "bronze" }];
  await openEnvironments();
  await card().getByText("This project has no tables yet. Add an entity to choose its features.").waitFor();
  await page().evaluate(() => { state.settings.show_preview = { value: false }; state.meta.project = null; go("home"); go("metadata"); });
  await page().locator(".env-cards").waitFor();
  assert.strictEqual(await card().count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("the Best practices check says what's running, a failure without a reason, a model with no tables, and no findings", { skip }, async () => {
  const text = await page().evaluate(() => {
    const out = (s) => { const el = bpaStatus(s); document.body.append(el); const t = el.innerText; el.remove(); return t; };
    return [
      out({ model: "SM_Gold", running: { started_at: new Date().toISOString(), url: "https://app.fabric.microsoft.com/run/1" }, failed: null, last: null }),
      out({ model: "SM_Gold", running: null, failed: {}, last: null }),
      out({ model: "SM_Gold", running: null, failed: null, last: null }),
      out({ model: "SM_Gold", last: { checked_at: new Date().toISOString(), no_tables: true, findings: [], rules: 71 } }),
      out({ model: "SM_Gold", last: { checked_at: new Date().toISOString(), findings: [], counts: { Error: 0 }, package: "semantic-link-labs", version: "0.17.1" } }),
    ];
  });
  assert.match(text[0], /Checking SM_Gold in Fabric, started .*\. This card looks again every 30 seconds\.\s*Open the run in Fabric/);
  assert.match(text[1], /The last check didn't finish \(unknown\)\s*Fabric gave no reason\. Check again, and if it fails again, open the run in Fabric\./);
  assert.doesNotMatch(text[1], /hasn't been checked yet/);
  assert.match(text[2], /^SM_Gold hasn't been checked yet\.$/);
  assert.match(text[3], /The model has no tables yet, so most rules had nothing to check\.[\s\S]*No rule found anything\.[\s\S]*Checked .* with semantic-link-labs, 71 rules\./);
  assert.match(text[4], /Checked .* with semantic-link-labs 0\.17\.1\./);
  const empty = await page().evaluate(() => bpaFindingsTable([{ severity: "Info", category: "Formatting", rule: "r", object: "o" }], { serious: true, category: "" }).textContent);
  assert.strictEqual(empty, "No findings match the filter.");
  assert.deepStrictEqual(await page().evaluate(() => [bpaRuleLink("http://learn.microsoft.com/x"), bpaRuleLink("not a url"), bpaRuleLink("https://evil.example/x")]), [null, null, null]);
  assert.deepStrictEqual(app.errors, []);
});

test("the check's confirm needs a warning ticked first, and a blocked check offers only Close", { skip }, async () => {
  const plan = { name: "SM_Gold", workspace_name: "" };
  const warn = { id: "r1", result: "warning", label: "Warning", summary: "The capacity is busy", needs_acknowledgement: true,
    findings: [{ check: "capacity", outcome: "warning", title: "Capacity at 85%" }], note: "Going ahead is recorded." };
  await page().evaluate(([plan, warn]) => { window.__bpa = bpaConfirm(plan, warn, ""); }, [plan, warn]);
  const d = page().getByRole("dialog", { name: "Check SM_Gold in Fabric?" });
  await d.waitFor();
  assert.match(await d.innerText(), /Publishes or updates the notebook nb_fabric_desktop_bpa in the workspace/);
  assert.match(await d.innerText(), /Installs semantic-link-labs into that notebook's session only/);
  assert.match(await d.innerText(), /Going ahead is recorded\./);
  await d.getByRole("button", { name: "Check in Fabric" }).click();
  await toastSays(/^Tick "I've looked at these" first, or cancel\.$/);
  await d.getByRole("checkbox").check();
  await d.getByRole("button", { name: "Check in Fabric" }).click();
  assert.strictEqual(await page().evaluate(() => window.__bpa), true);

  await page().evaluate((plan) => { window.__bpa = bpaConfirm(plan, { id: "r2", result: "blocked", label: "Blocked", summary: "XMLA read is off", findings: [] }, "0.17.1"); }, plan);
  await d.waitFor();
  assert.deepStrictEqual(await d.getByRole("button").allInnerTexts().then((b) => b.filter((x) => x.trim())), ["Close"]);
  await d.locator("button.tool.primary", { hasText: "Close" }).click();
  assert.strictEqual(await page().evaluate(() => window.__bpa), false);
  assert.deepStrictEqual(app.errors, []);
});
