// Day-to-day data engineering (dataops.js), the paths the entity, compare and lineage tests leave out:
// column lineage, Impact with nothing affected and its Open link, the check before a contract save,
// settling schema drift and saving it, replaying held rows (a refused replay, rows held again, and
// rebuilding downstream), the release card's empty and unreadable states and Copy record, the
// rollback preview's removed and unreadable tables, a compare by checksum, and freshness: a promise
// with no time, alerts that can't be read, late tables and incidents in the bell, and Home's late card.
// Each card or dialog is drawn with mocked engine replies; what the person sees and what was sent is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const NAME = "silver.orders";
const at = (s) => new Date(Date.now() + s * 1000).toISOString();

// meta_service.meta_lineage: every column and where it comes from; with a column, its steps up and down.
const COLUMNS = { order_id: { how: "key", label: "declared" }, amount: { how: "derived", label: "inferred" }, score: { how: "custom", label: "opaque" } };
const COLUMN = {
  amount: { upstream: [
    { entity: NAME, column: "amount", how: "derived", label: "inferred", expr: "price * qty",
      inputs: [{ entity: "bronze.orders", column: "price" }, { value: "1.2", column: "rate" }], note: "rounded to 2 places", custom_may_change: ["hooks.fix:run"] },
    { entity: "bronze.orders", column: "price", how: "source", label: "declared" }],
  downstream: [{ entity: "gold.sales", column: "revenue", how: "derived" }] },
  order_id: { upstream: [{ entity: "bronze.orders", column: "id", how: "source", label: "declared" }], downstream: [] },
  score: { upstream: [], downstream: [] },
};
const IMPACT = { entity: NAME, column: "amount", change: "drop", counts: { breaks: 1, check: 1, table_only: 2 }, items: [
  { kind: "table", name: "gold.sales", severity: "breaks", detail: "", entity: "gold.sales", why: ["revenue reads amount"], chain: ["silver.orders.amount", "gold.sales.revenue"] },
  { kind: "python", name: "hooks.fix:run", severity: "check", detail: "", entity: null, why: ["reads every column"], chain: [] }] };

const db = {};
function reset() {
  Object.assign(db, {
    impact: structuredClone(IMPACT), edit: { changes: [] }, editFail: false, applyErrors: [],
    replay: { status: "ok", rows_written: 2, held: 0, downstream: [], message: "" },
    last: { release: null, problem: null }, promote: { tables: [], problem: null },
    releases: { problem: null, releases: [] }, preview: { entities: [] },
    compare: null, freshness: { items: [], alerts: false }, watch: { envs: [] },
  });
}
reset();

let app;
const page = () => app.page;
const callsOf = (m) => app.calls.filter((c) => c.method === m);
const dialog = () => page().locator("#dialog-root .dialog");
const toast = async (re) => {
  await page().waitForFunction((src) => { const t = document.getElementById("toast"); return !t.hidden && new RegExp(src).test(t.textContent); }, re.source);
  return page().locator("#toast").textContent();
};
// Draws a card into a box of its own: `src` is the expression that makes it.
const mount = (src) => page().evaluate((src) => {
  let box = document.getElementById("t-box");
  if (!box) { box = document.createElement("div"); box.id = "t-box"; document.body.append(box); }
  box.replaceChildren(new Function(`return ${src}`)());
}, src);
const box = () => page().locator("#t-box");

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    readiness_check: () => [],
    meta_lineage: ({ column }) => column ? structuredClone(COLUMN[column]) : { entity: NAME, columns: structuredClone(COLUMNS) },
    meta_impact: (p) => ({ ...structuredClone(db.impact), column: p.column, change: p.change }),
    meta_edit_impact: () => { if (db.editFail) throw new Error("RuntimeError: the lineage couldn't be worked out"); return structuredClone(db.edit); },
    meta_drift_apply: () => ({ errors: db.applyErrors }),
    meta_replay: () => structuredClone(db.replay),
    meta_last_release: () => structuredClone(db.last),
    meta_promote_impact: () => structuredClone(db.promote),
    meta_releases: () => structuredClone(db.releases),
    meta_rollback_preview: ({ version }) => ({ to: version, ...structuredClone(db.preview) }),
    meta_versions: () => { throw new Error("FileNotFoundError: no Delta log"); },
    meta_compare: () => structuredClone(db.compare),
    meta_freshness: () => structuredClone(db.freshness),
    incidents_watch: () => structuredClone(db.watch),
    meta_alerts: () => { throw new Error("OSError: settings file locked"); },
    meta_set_freshness: () => ({ freshness: { within_hours: 6, by: null, envs: ["prod"] } }),
  } });
  await startupReads(app);
  await page().evaluate(() => {
    state.user = "chris@contoso.com"; state.meta.env = "test";
    // Spies for where a click leads, so each check stays on this file's code.
    window.__led = [];
    window.openEntity = (name) => window.__led.push(["openEntity", name]);
    window.runEntities = (names, opts) => window.__led.push(["runEntities", names, opts]);
    window.showRun = (id, env) => window.__led.push(["showRun", id, env]);
    Object.defineProperty(navigator, "clipboard", { value: { writeText: async (t) => { window.__copied = t; } }, configurable: true });
  });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  reset();
  await page().evaluate(() => {
    closeMenu(); closeDialog(); window.__led = []; activity.items = [];
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    document.getElementById("t-box")?.replaceChildren();
  });
});
const led = () => page().evaluate(() => window.__led);

test("column lineage opens on the column picked, shows each step up and what reads it further on", { skip }, async () => {
  await page().evaluate((n) => columnLineage(n, "amount"), NAME);
  await dialog().locator(".lineage-detail b", { hasText: "Where amount comes from" }).waitFor();
  assert.deepStrictEqual(await dialog().locator(".lineage-col").allTextContents(), ["order_idkey, declared", "amountderived, inferred", "scorecustom, opaque"]);
  assert.match(await dialog().locator(".lineage-col.on").textContent(), /^amount/);
  const rows = await dialog().locator(".lineage-detail tbody tr").allTextContents();
  assert.match(rows[0], /silver\.orders\.amountderivedinferredprice \* qtybronze\.orders\.price, 1\.2 \(rate\)rounded to 2 placescustom code may change it \(hooks\.fix:run\)/);
  assert.match(rows[1], /bronze\.orders\.pricesourcedeclared/);
  await dialog().getByRole("button", { name: "gold.sales.revenue" }).click();
  assert.deepStrictEqual(await led(), [["openEntity", "gold.sales"]]);
  await dialog().waitFor({ state: "detached" });
});

test("a column nothing reads says so, and Impact of a change opens Impact on that column", { skip }, async () => {
  await page().evaluate((n) => columnLineage(n), NAME);
  await dialog().getByText("Pick a column to see where it comes from.").waitFor();
  await dialog().locator(".lineage-col", { hasText: "order_id" }).click();
  await dialog().getByText("Nothing in this project reads silver.orders.order_id. Reports in Power BI aren't shown here yet.").waitFor();
  await dialog().getByRole("button", { name: "Impact of a change..." }).click();
  await page().getByRole("dialog", { name: `Impact of a change: ${NAME}` }).locator(".impact-tiles").waitFor();
  assert.strictEqual(await dialog().getByLabel("Column").inputValue(), "order_id");
  assert.deepStrictEqual(callsOf("meta_impact").at(-1).params, { name: NAME, column: "order_id", change: "drop" });
});

test("Impact lists Breaks then Check, opens a table that breaks, and says when nothing is touched", { skip }, async () => {
  await page().evaluate((n) => impactDialog(n, "amount"), NAME);
  await dialog().locator(".impact-list").waitFor();
  assert.deepStrictEqual(await dialog().locator(".impact-group").allTextContents(), ["Breaks (1)", "Check (1)"]);
  assert.match(await dialog().locator(".impact-row").nth(1).textContent(), /Checkhooks\.fix:run your Python.*reads every column/);
  assert.match(await dialog().locator(".impact-chain").textContent(), /How gold\.sales is reached: silver\.orders\.amount → gold\.sales\.revenue/);
  db.impact = { entity: NAME, column: "amount", change: "rename", counts: { breaks: 0, check: 0, table_only: 0 }, items: [] };
  await dialog().getByLabel("Change").selectOption("rename");
  await dialog().getByText("Nothing in this project reads silver.orders.amount in a way this change touches.").waitFor();
  assert.deepStrictEqual(callsOf("meta_impact").at(-1).params, { name: NAME, column: "amount", change: "rename" });
  db.impact = structuredClone(IMPACT);
  await dialog().getByLabel("Change").selectOption("retype");
  await dialog().getByRole("button", { name: "Open gold.sales" }).click();
  assert.deepStrictEqual(await led(), [["openEntity", "gold.sales"]]);
});

test("before a save, a contract change that breaks things asks first; a check that can't run never blocks", { skip }, async () => {
  db.editFail = true;
  assert.strictEqual(await page().evaluate((n) => confirmContractChange(n, "x"), NAME), true);
  db.editFail = false;
  assert.strictEqual(await page().evaluate((n) => confirmContractChange(n, "x"), NAME), true);
  db.edit = { changes: [
    { ...structuredClone(IMPACT), change: "rename", column: "amount", new_name: "total", counts: { breaks: 2, check: 0, table_only: 0 } },
    { ...structuredClone(IMPACT), change: "retype", column: "status", counts: { breaks: 1, check: 0, table_only: 0 } }] };
  const ask = async () => page().evaluate((n) => { window.__ok = confirmContractChange(n, "x"); }, NAME);
  await ask();
  await dialog().waitFor();
  const text = await dialog().textContent();
  assert.match(text, /This change breaks 3 things/);
  assert.match(text, /You're renaming amount to total in silver\.orders\. These read it by its old name:/);
  assert.match(text, /You're changing the type of status in silver\.orders\. These read it:/);
  await dialog().getByRole("button", { name: "Cancel" }).click();
  assert.strictEqual(await page().evaluate(() => window.__ok), false);
  db.edit = { changes: [{ ...structuredClone(IMPACT), change: "drop", column: "amount" }] };
  await ask();
  await dialog().getByText("You're removing amount in silver.orders. These read it:").waitFor();
  assert.match(await dialog().textContent(), /This change breaks 1 thing(?!s)/);
  await dialog().getByRole("button", { name: "Save anyway" }).click();
  assert.strictEqual(await page().evaluate(() => window.__ok), true);
});

const DRIFT = { entity: NAME, source: "bronze.orders", own_policy: null, layer_policy: "evolve", env: "test", problem: null,
  added: [{ column: "channel", type: "string" }, { column: "region_code", type: "string" }],
  dropped: [{ column: "region", type: "string", key: false }, { column: "order_id", type: "bigint", key: true }],
  retyped: [{ column: "amount", contract: "double", source: "string" }], history: [] };

test("drift: Save to config is on only once something is picked, and sends exactly what was picked", { skip }, async () => {
  await mount(`driftPanel({ name: "${NAME}" }, ${JSON.stringify(DRIFT)})`);
  const save = box().getByRole("button", { name: "Save to config" });
  assert.ok(await save.isDisabled());
  assert.match(await box().textContent(), /5 to review.*bronze\.orders in Test differs from the contract/);
  // A key column can be renamed but not removed.
  assert.deepStrictEqual(await box().getByLabel("What to do about order_id").locator("option").allTextContents(),
    ["Leave it (runs stop)", "Renamed to channel", "Renamed to region_code"]);
  const added = box().locator("label.drift-row", { hasText: "channel" }).getByRole("checkbox");
  await added.check();
  assert.ok(await save.isEnabled());
  await added.uncheck();
  assert.ok(await save.isDisabled());
  await box().getByLabel("What to do about region").selectOption("__remove");
  await box().getByLabel("What to do about region").selectOption("region_code");
  await box().getByLabel("What to do about order_id").selectOption("channel");
  await box().locator("label.drift-row", { hasText: "double to string" }).getByRole("checkbox").check();
  await box().locator("label.drift-row", { hasText: "region_code" }).getByRole("checkbox").check();
  await box().getByLabel("When the source adds columns").selectOption("strict");
  db.applyErrors = ["region_code: a key can't be renamed"];
  await save.click();
  await toast(/^Saved with 1 problem to fix$/);
  assert.deepStrictEqual(callsOf("meta_drift_apply").at(-1).params, { name: NAME, add: { region_code: "string" }, retype: { amount: "string" },
    rename: { region: "region_code", order_id: "channel" }, remove: [], policy: "strict" });
});

test("drift: Remove from contract alone is enough to save, and a clean save says the next run uses it", { skip }, async () => {
  await mount(`driftPanel({ name: "${NAME}" }, ${JSON.stringify({ ...DRIFT, added: [] })})`);
  await box().getByLabel("What to do about region").selectOption("__remove");
  await box().getByRole("button", { name: "Save to config" }).click();
  await toast(/^Saved\. The next run uses the new contract\.$/);
  assert.deepStrictEqual(callsOf("meta_drift_apply").at(-1).params.remove, ["region"]);
  await mount(`driftPanel({ name: "${NAME}" }, ${JSON.stringify({ ...DRIFT, problem: "HTTPError: 403 Client Error: Forbidden for url: https://onelake.dfs.fabric.microsoft.com/x" })})`);
  assert.match(await box().textContent(), /Couldn't read the source in Test: You don't have permission/);
});

const HELD = { entity: NAME, env: "test", columns: ["order_id", "amount"], editable: ["amount"], total: 40, checks: { "not_null(amount)": 2, "": 1 },
  rows: [{ _qid: "q1", _check: "not_null(amount)", _loaded_at: "2026-10-31", order_id: 1, amount: null },
    { _qid: "q2", _check: "", _loaded_at: "", order_id: 2, amount: null }] };

test("quarantine: Select all picks every row; a replay the engine refuses logs why and loads nothing", { skip }, async () => {
  await mount(`quarantinePanel({ name: "${NAME}" }, ${JSON.stringify(HELD)})`);
  assert.match(await box().textContent(), /Quarantine in Test · 40 rows held.*2 by not_null\(amount\) · 1 by a check/);
  assert.match(await box().textContent(), /Showing the newest 2 of 40\./);
  await box().getByLabel("Select all").check();
  assert.match(await box().textContent(), /2 selected/);
  await box().getByLabel("Select all").uncheck();
  assert.ok(await box().getByRole("button", { name: /Replay selected/ }).isDisabled());
  await box().getByLabel("Select all").check();
  db.replay = { status: "failed", message: "The contract has errors: amount" };
  await box().getByRole("button", { name: /Replay selected/ }).click();
  await toast(/^Nothing was replayed: The contract has errors: amount$/);
  assert.deepStrictEqual(callsOf("meta_replay").at(-1).params, { name: NAME, env: "test", ids: ["q1", "q2"], edits: {} });
  assert.deepStrictEqual(await page().evaluate(() => activity.items.filter((i) => i.title.startsWith("Replay")).map((i) => [i.status, i.title, i.text])),
    [["failed", "Replay silver.orders · Test", "The contract has errors: amount"]]);
});

test("quarantine: rows held again are counted, and a replay with downstream tables offers to rebuild them", { skip }, async () => {
  await mount(`quarantinePanel({ name: "${NAME}" }, ${JSON.stringify(HELD)})`);
  await box().getByLabel("Select row q1").check();
  db.replay = { status: "ok", rows_written: 0, held: 1, downstream: [], message: "" };
  await box().getByRole("button", { name: /Replay selected/ }).click();
  await toast(/^0 rows loaded into silver\.orders, 1 held again \(they still fail a check\)$/);
  db.replay = { status: "ok", rows_written: 1, held: 0, downstream: ["gold.sales", "gold.daily"], message: "" };
  await box().getByRole("button", { name: /Replay selected/ }).click();
  await dialog().waitFor();
  assert.match(await dialog().textContent(), /1 row loaded into silver\.orders\..*Rebuild them to include the replayed rows: gold\.sales, gold\.daily\./);
  await dialog().getByRole("button", { name: "Not now" }).click();
  await dialog().waitFor({ state: "detached" });
  assert.deepStrictEqual(await led(), []);
  await box().getByRole("button", { name: /Replay selected/ }).click();
  await dialog().getByRole("button", { name: "Rebuild downstream" }).click();
  assert.deepStrictEqual(await led(), [["runEntities", ["gold.sales", "gold.daily"], { backfill: "full" }]]);
});

test("the last release says when nothing was promoted, when it can't be read, and copies its record", { skip }, async () => {
  db.last = { release: null, problem: null };
  await mount(`lastRelease("prod")`);
  await box().getByText("Nothing has been promoted to Prod yet.").waitFor();
  db.last = { release: null, problem: "HTTPError: 403 Client Error: Forbidden for url: https://onelake.dfs.fabric.microsoft.com/x" };
  await mount(`lastRelease("test")`);
  await box().getByText(/^Can't read Test's releases yet: You don't have permission/).waitFor();
  db.last = { problem: null, release: { version: "2.0.0", source_env: "dev", at: at(-60), by: "ana@contoso.com", receipt_id: "abcdef1234567890",
    receipt: "{\"version\":\"2.0.0\"}", verified: false, commit: null, dirty: null } };
  await mount(`lastRelease("dev")`);
  const card = box().locator(".release-card");
  await card.waitFor();
  assert.match(await card.textContent(), /Last release to Dev.*Codenot in Git.*Recordabcdef12…doesn't match its id/);
  assert.strictEqual(await card.getByRole("button", { name: /Roll back/ }).count(), 0, "Dev runs its own folder");
  await card.getByRole("button", { name: "Copy record" }).click();
  await toast(/^Record copied$/);
  assert.strictEqual(await page().evaluate(() => window.__copied), "{\"version\":\"2.0.0\"}");
  db.last.release = { ...db.last.release, dirty: false };
  await mount(`lastRelease("dev")`);
  await box().locator(".release-card", { hasText: "nothing committed yet" }).waitFor();
});

test("the promote card says when no logic changed, and folds a long list of changes", { skip }, async () => {
  db.promote = { problem: null, tables: [{ entity: NAME, state: "same", changes: [] }] };
  await mount(`promoteImpact("dev", "test")`);
  await box().getByText("No table's logic differs from what last ran in Test.").waitFor();
  db.promote = { problem: null, tables: [{ entity: NAME, state: "changed", last_run: at(-3600),
    changes: Array.from({ length: 8 }, (_, i) => ({ text: `Change ${i + 1}` })) }] };
  await mount(`promoteImpact("dev", "test")`);
  const list = box().locator(".promote-impact .gate-list li");
  await list.first().waitFor();
  assert.deepStrictEqual(await list.allTextContents(), ["Change 1", "Change 2", "Change 3", "Change 4", "Change 5", "Change 6", "and 2 more"]);
});

test("Roll back: an unreadable list says so; with no earlier release kept, nothing can be picked", { skip }, async () => {
  db.releases = { problem: "HTTPError: 403 Client Error: Forbidden for url: https://onelake.dfs.fabric.microsoft.com/x", releases: [] };
  await page().evaluate(() => rollbackDialog("test"));
  await toast(/^Can't read Test's releases: You don't have permission/);
  assert.strictEqual(await dialog().count(), 0);
  db.releases = { problem: null, releases: [{ version: "1.0.0", current: true, kept: true, at: at(-60), receipt_id: null }] };
  await page().evaluate(() => rollbackDialog("test"));
  await dialog().waitFor();
  assert.match(await dialog().textContent(), /Test keeps no earlier release to go back to\./);
  assert.ok(await dialog().getByRole("button", { name: "Roll back", exact: true }).isDisabled());
  assert.strictEqual(callsOf("meta_rollback_preview").length, 0);
});

test("Roll back: the preview names removed tables, ones it can't compare, and settings-only changes", { skip }, async () => {
  const r = (version, more) => ({ version, current: false, kept: true, at: at(-86400), source_env: "dev", receipt_id: null, ...more });
  db.releases = { problem: null, releases: [r("1.2.0", { current: true }), r("1.1.0", { source_env: "test", by: "ana" }), r("1.0.0")] };
  db.preview = { entities: [
    { entity: "gold.old", state: "removed", changes: [] },
    { entity: "gold.odd", state: "unreadable", problem: "Its file isn't valid TOML", changes: [] },
    { entity: NAME, state: "changed", logic: false, changes: [{ text: "Owner: ana → bo" }] }] };
  await page().evaluate(() => rollbackDialog("test"));
  await dialog().locator(".rollback-list").waitFor();
  const rows = await dialog().locator(".rollback-entity").allTextContents();
  assert.match(rows[0], /gold\.oldRemovedNot in 1\.1\.0\. Its table stays; nothing runs it\./);
  assert.match(rows[1], /gold\.oddCan't compareIts file isn't valid TOML/);
  assert.match(rows[2], /silver\.ordersSettings changeOwner: ana → bo/);
  assert.match(await dialog().locator(".rollback-row").nth(1).textContent(), /ana \(rollback\)/);
  db.preview = { entities: [] };
  await dialog().getByRole("radio", { name: "Roll back to 1.0.0" }).check();
  await dialog().getByText("No entity's settings differ between the two releases; other project files may.").waitFor();
  assert.strictEqual(await dialog().getByRole("button", { name: "Roll back Test to 1.0.0" }).count(), 1);
});

test("Roll back on a Prod workspace says why it asks for the name, and waits for it", { skip }, async () => {
  const r = (version, more) => ({ version, current: false, kept: true, at: at(-86400), source_env: "test", receipt_id: null, ...more });
  db.releases = { problem: null, confirm_name: "Sales Prod", releases: [r("1.2.0", { current: true }), r("1.1.0")] };
  db.preview = { entities: [] };
  await page().evaluate(() => rollbackDialog("prod"));
  await dialog().getByText("Sales Prod is a Prod workspace, so this rollback runs only after you type its name exactly to confirm.").waitFor();
  const go = dialog().getByRole("button", { name: "Roll back Prod to 1.1.0" });
  assert.ok(await go.isDisabled(), "the rollback waits for the name");
  await dialog().getByRole("textbox", { name: "Type Sales Prod to confirm" }).fill("Sales Prod");
  assert.ok(!(await go.isDisabled()));
  await dialog().getByRole("button", { name: "Cancel" }).click();
  db.releases = { problem: null, releases: [] };
});

test("a compare by checksum says the sides are identical, and lists only the totals that moved", { skip }, async () => {
  db.compare = { entity: NAME, keys: [], compared_on_keys: false, method: "hashed", checksum: { match: true },
    left: { env: "test", version: null }, right: { env: "dev", version: null }, rows: { left: 10, right: 10 },
    columns: { only_left: [], only_right: [], type_changed: [] }, only_left: { count: 0, rows: [] }, only_right: { count: 0, rows: [] },
    changed: { count: 0, rows: [] }, totals: [{ column: "amount", left: 5, right: 5, diff: 0 }] };
  await page().evaluate((n) => compareDialog(n), NAME);
  await dialog().locator(".cmp-result").waitFor();
  assert.match(await dialog().locator(".pass-box").textContent(), /Identical: the same rows in Test and Dev, by checksum\./);
  assert.match(await dialog().locator(".cmp-result").textContent(), /Compared: counts, totals and a checksum/);
  // A side whose versions can't be read offers only Now.
  assert.deepStrictEqual(await dialog().getByLabel("Left version").locator("option").allTextContents(), ["Now"]);
  db.compare = { ...db.compare, checksum: { match: false }, rows: { left: 10, right: 12 },
    totals: [{ column: "amount", left: 5, right: 7, diff: 2 }, { column: "qty", left: 1, right: 1, diff: 0 }, { column: "tax", left: 1, right: 1, diff: 0 }] };
  await dialog().getByRole("button", { name: "Compare" }).click();
  await dialog().getByText("2 other number columns have the same total.").waitFor();
  assert.strictEqual(await dialog().locator(".pass-box").count(), 0);
  assert.deepStrictEqual(await dialog().locator(".cmp-result tbody tr").allTextContents(), ["amount57+2"]);
});

test("a freshness promise needs a number of hours or a time; alerts that can't be read say so", { skip }, async () => {
  await page().evaluate(() => { fresh.alertsUnreadable = true; });
  await page().evaluate((n) => freshnessDialog({ name: n, freshness: null }), NAME);
  await dialog().waitFor();
  assert.match(await dialog().textContent(), /The saved alert address can't be read on this PC or account\. Enter it again\./);
  await dialog().locator('input[type="number"]').fill("");
  await dialog().getByRole("button", { name: "Save" }).click();
  await toast(/^Give a number of hours, a time of day, or both$/);
  assert.strictEqual(callsOf("meta_set_freshness").length, 0);
  await dialog().getByRole("button", { name: "Set up alerts" }).click();
  await page().getByRole("dialog", { name: "Incident alerts" }).getByText("Couldn't read whether alerts are set. Saving a new address replaces any saved one.").waitFor();
  await page().evaluate(() => { fresh.alertsUnreadable = false; });
});

test("the freshness check puts late tables and new incidents in the bell, once each", { skip }, async () => {
  db.freshness = { alerts: true, alerts_unreadable: false, project_error: "TOMLDecodeError: line 3",
    items: [{ entity: NAME, env: "prod", status: "late", last_ok: "2026-11-01T00:00:00Z", reasons: ["last updated 40 h ago", "promised every 26 h"] },
      { entity: "gold.sales", env: "prod", status: "fresh", last_ok: "2026-11-18T00:00:00Z", reasons: [] }] };
  db.watch = { envs: [{ env: "prod", mode: "agent", incidents: [{ id: "i1", state: "open", title: "gold.sales failed", text: "Its last run failed." },
    { id: "i2", state: "resolved", title: "old", text: "" }], notified: { error: "HTTPError: 400 Client Error: Bad Request for url: https://contoso.webhook.office.com/x" } }] };
  // Preview hidden: late tables are logged here, and incidents aren't.
  await page().evaluate(async () => { state.settings = { ...(state.settings || {}), show_preview: { value: false } }; await checkFreshness(); await checkFreshness(); });
  let items = await page().evaluate(() => activity.items.map((i) => [i.status, i.title, i.text]));
  assert.deepStrictEqual(items.filter(([, t]) => /late/.test(t)), [["failed", "silver.orders is late · Prod", "last updated 40 h ago; promised every 26 h."]]);
  assert.ok(items.some(([s, t, x]) => s === "error" && t === "Freshness not checked" && /metadata project couldn't be read/.test(x)));
  assert.ok(items.some(([s, t, x]) => s === "error" && t === "Teams alert not sent" && /^It will go with the next check\./.test(x)));
  assert.ok(!items.some(([, t]) => t === "gold.sales failed"));
  // Preview shown: open incidents go to the bell, once.
  await page().evaluate(async () => { activity.items = []; state.settings.show_preview = { value: true }; await checkFreshness(); await checkFreshness(); });
  items = await page().evaluate(() => activity.items.map((i) => [i.status, i.title, i.text]));
  assert.deepStrictEqual(items.filter(([, t]) => t === "gold.sales failed"), [["failed", "gold.sales failed", "Its last run failed. Raised in Prod's incident list."]]);
  assert.ok(!items.some(([, t]) => /late|old/.test(t) && t !== "gold.sales failed"));
});

test("Home's late card says why, opens the entity in its environment and the failed run", { skip }, async () => {
  await page().evaluate(() => go("home"));
  await page().locator("#home-fresh").waitFor({ state: "attached" });
  await page().evaluate(() => {
    fresh.items = [{ entity: "silver.orders", env: "prod", status: "late", expected: "every 26 h", last_ok: null,
      reasons: ["never loaded here"], failure: "Spark ran out of memory", failed_run: "run-9" }];
    renderFreshHome();
  });
  const card = page().locator("#home-fresh .card.attention");
  assert.match(await card.textContent(), /silver\.orders in Prod · promised every 26 h.*never loaded here.*Never loaded.*Its last run failed: Spark ran out of memory/);
  await card.getByRole("button", { name: "Open run" }).click();
  await card.getByRole("button", { name: "Open entity" }).click();
  assert.deepStrictEqual(await led(), [["showRun", "run-9", "prod"], ["openEntity", "silver.orders"]]);
  assert.strictEqual(await page().evaluate(() => state.meta.env), "prod");
  await page().evaluate(() => { state.meta.env = "test"; fresh.items = []; });
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
