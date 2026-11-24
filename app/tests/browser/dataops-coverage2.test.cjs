// Day-to-day data engineering (dataops.js), the paths dataops-coverage leaves out: Export lineage as
// OpenLineage from Impact (what it writes, what it leaves out, and nothing to export yet), a rollback
// preview that folds a long list of changes, and incident alerts whose saved address can't be read.
// Each dialog is drawn with mocked engine replies; what the person sees and what was sent is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const NAME = "silver.orders";
const at = (s) => new Date(Date.now() + s * 1000).toISOString();

const db = {};
function reset() {
  Object.assign(db, {
    preview: { events: 2, left_out: [{ entity: "gold.sales", why: "it hasn't run in Test yet" }] },
    exported: { events: 2, path: "C:/kf/lineage-test.jsonl" },
    releases: { problem: null, releases: [] }, rollback: { entities: [] },
    alerts: { set: false },
  });
}
reset();

let app;
const page = () => app.page;
const callsOf = (m) => app.calls.filter((c) => c.method === m).map((c) => c.params);
const dialog = (name) => page().getByRole("dialog", { name });
const toast = async (re) => {
  await page().waitForFunction((src) => { const t = document.getElementById("toast"); return !t.hidden && new RegExp(src).test(t.textContent); }, re.source);
  return page().locator("#toast").textContent();
};

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    readiness_check: () => [],
    meta_lineage: () => ({ entity: NAME, columns: { amount: { how: "derived", label: "inferred" } } }),
    meta_impact: (p) => ({ entity: NAME, column: p.column, change: p.change, counts: { breaks: 0, check: 0, table_only: 0 }, items: [] }),
    meta_openlineage_preview: () => structuredClone(db.preview),
    meta_openlineage_export: () => structuredClone(db.exported),
    meta_releases: () => structuredClone(db.releases),
    meta_rollback_preview: ({ version }) => ({ to: version, ...structuredClone(db.rollback) }),
    meta_alerts: () => structuredClone(db.alerts),
    meta_set_alerts: (p) => ({ set: !!p.teams_webhook, kind: "flow", unreadable: false }),
  } });
  await startupReads(app);
  await page().evaluate(() => { state.user = "chris@contoso.com"; state.meta.env = "test"; });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  reset();
  await page().evaluate(() => {
    closeDialog(); closeDialog(); state.meta.env = "test"; fresh.alerts = false; fresh.alertsUnreadable = false;
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
  });
  app.calls.length = 0;
});

test("Impact's Export OpenLineage says what it writes and leaves out, then exports this environment", { skip }, async () => {
  await page().evaluate((n) => impactDialog(n, "amount"), NAME);
  const impact = dialog(`Impact of a change: ${NAME}`);
  await impact.locator(".impact-tiles").waitFor();
  await impact.getByRole("button", { name: "Export OpenLineage..." }).click();
  const d = dialog("Export lineage as OpenLineage");
  await d.waitFor();
  assert.strictEqual(await impact.count(), 0, "Impact closes first");
  const text = await d.innerText();
  assert.match(text, /Writes 2 OpenLineage run events \(inputs, outputs and column lineage\), one for each table's last run in Test/);
  assert.match(text, /Left out \(1\)\s+gold\.sales: it hasn't run in Test yet/);
  assert.match(text, /No data rows, no run messages and no paths on this PC\./);
  assert.deepStrictEqual(callsOf("meta_openlineage_preview"), [{ env: "test" }]);
  await d.getByRole("button", { name: "Export" }).click();
  assert.match(await toast(/^Saved 2 OpenLineage events to /), /^Saved 2 OpenLineage events to C:\/kf\/lineage-test\.jsonl$/);
  assert.deepStrictEqual(callsOf("meta_openlineage_export"), [{ env: "test" }]);
  await d.waitFor({ state: "detached" });
  assert.deepStrictEqual(app.errors, []);
});

test("with no finished run there is nothing to export: Export stays off; one event reads as one", { skip }, async () => {
  db.preview = { events: 0, left_out: [] };
  await page().evaluate(() => openLineageDialog());
  const d = dialog("Export lineage as OpenLineage");
  await d.getByText("No table has a finished run in Test yet, so there is nothing to export. Run the project first.").waitFor();
  assert.ok(await d.getByRole("button", { name: "Export" }).isDisabled());
  assert.strictEqual(await d.getByText(/^Left out/).count(), 0);
  await d.getByRole("button", { name: "Cancel" }).click();
  await d.waitFor({ state: "detached" });
  assert.strictEqual(callsOf("meta_openlineage_export").length, 0);

  db.preview = { events: 1, left_out: [] };
  db.exported = { events: 1, path: "C:/kf/one.jsonl" };
  await page().evaluate(() => { state.meta.env = "prod"; openLineageDialog(); });
  await d.getByText(/^Writes 1 OpenLineage run event \(inputs/).waitFor();
  assert.match(await d.innerText(), /last run in Prod/);
  await d.getByRole("button", { name: "Export" }).click();
  await toast(/^Saved 1 OpenLineage event to C:\/kf\/one\.jsonl$/);
  assert.deepStrictEqual(callsOf("meta_openlineage_export"), [{ env: "prod" }]);
  assert.deepStrictEqual(app.errors, []);
});

test("a rollback preview lists the first six changes of a table and folds the rest", { skip }, async () => {
  const r = (version, more) => ({ version, current: false, kept: true, at: at(-86400), source_env: "dev", receipt_id: null, ...more });
  db.releases = { problem: null, releases: [r("1.2.0", { current: true }), r("1.1.0")] };
  db.rollback = { entities: [{ entity: NAME, state: "changed", logic: true,
    changes: Array.from({ length: 9 }, (_, i) => ({ text: `Column c${i + 1} changed` })) }] };
  await page().evaluate(() => rollbackDialog("test"));
  const d = dialog("Roll back Test");
  await d.locator(".rollback-entity").waitFor();
  const items = await d.locator(".rollback-entity li").allTextContents();
  assert.deepStrictEqual(items, ["Column c1 changed", "Column c2 changed", "Column c3 changed", "Column c4 changed",
    "Column c5 changed", "Column c6 changed", "and 3 more"]);
  assert.match(await d.locator(".rollback-entity").textContent(), /Logic changes/);
  assert.deepStrictEqual(callsOf("meta_rollback_preview"), [{ env: "test", version: "1.1.0" }]);
  assert.strictEqual(await d.getByRole("button", { name: "Roll back Test to 1.1.0" }).isDisabled(), false);
  assert.deepStrictEqual(app.errors, []);
});

test("alerts whose saved address can't be read say so, and a new address saves", { skip }, async () => {
  db.alerts = { set: false, unreadable: true };
  await page().evaluate(() => alertsDialog());
  const d = dialog("Incident alerts");
  await d.getByText("The saved alert address can't be read on this PC or account. Enter it again.").waitFor();
  assert.strictEqual(await d.getByRole("button", { name: "Turn off" }).count(), 0, "nothing set: nothing to turn off");
  await d.getByRole("textbox", { name: "Where alerts go" }).fill("flow:https://flow.example/x");
  await d.getByRole("button", { name: "Save", exact: true }).click();
  await toast(/^Alerts to the flow are on$/);
  assert.deepStrictEqual(callsOf("meta_set_alerts"), [{ teams_webhook: "flow:https://flow.example/x", test: false }]);
  assert.strictEqual(await page().evaluate(() => [fresh.alerts, fresh.alertsUnreadable].join()), "true,false");
  assert.deepStrictEqual(app.errors, []);
});
