// #1373: Compare on a Data mesh entity's page, and the promote impact, the last release and Roll back on
// Project > Environments and promote (see harness.cjs for how to run it). Compare lists each
// environment's Delta versions, sends exactly the two environments and versions picked ("Now" as no
// version), and shows the result's row and schema differences, or that there are none. The promote
// impact and the last release show what the engine returned. Roll back opens its own dialog first
// (the releases, what changes, Cancel and "Roll back Test to <release>"): Cancel sends nothing, and
// confirming sends that environment and release, after which the page shows the new last release.
// The code has no separate danger confirm for Roll back: the dialog is the step that asks first.
// An engine error shows a plain message and the page stays as it was. Opening the page and Compare
// send no write, and axe finds nothing serious on the Compare dialog, its result and the Roll back dialog.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const ME = "chris@contoso.com";
const NAME = "silver.orders";
const READS = ["meta_entity", "meta_versions", "meta_compare", "meta_promote_impact", "meta_last_release", "meta_releases",
  "meta_rollback_preview"];
// What the entity page and the Data mesh page also read as they draw (meta.js, dataops.js, platform.js).
const PAGE_READS = ["meta_project", "meta_runs_all", "meta_entity_shortcuts", "meta_promote_check", "capacity_health", "mesh_about",
  "meta_drift", "meta_quarantine", "meta_freshness"];
const WRITES = ["meta_rollback", "meta_promote", "meta_save_entity", "meta_delete_entity", "meta_set_environment", "meta_set_compute",
  "meta_replay", "meta_discard_quarantine", "meta_drift_apply", "meta_run", "meta_restore"];
const DENIED = "HTTPError: 403 Client Error: Forbidden for url: https://onelake.dfs.fabric.microsoft.com/w1/LH_Meta/Tables/deployments";
const PLAIN = "You don't have permission for that in this workspace. A workspace admin can make you a Contributor or above.";

const at = (s) => new Date(Date.now() + s * 1000).toISOString();

// The entity as meta_service.meta_entity describes it (as in entity-editor.test.cjs).
const ENTITY = {
  name: NAME, layer: "silver", mode: "merge", watermark: "updated_at", owner: ME,
  path: `metadata/entities/${NAME}.toml`, text: `[entity]\nname = "${NAME}"\n`,
  errors: [], columns: { order_id: "string", status: "string", amount: "double" }, keys: ["order_id"], surrogate_keys: [],
  derived: [], hook_adds: [], runs: [], custom: [], checks: 2, reads: ["bronze.orders"], impact: [], source: null,
};

const PROJECT = { path: "C:/mesh", name: "Sales", version: "1.4.0", errors: [], layers: [{ name: "silver", lakehouse: "LH_Silver" }],
  environments: { dev: {}, test: { workspace_id: "w1", workspace_name: "Sales Test", target: "onelake" },
    prod: { workspace_id: "w2", workspace_name: "Sales Prod", target: "onelake" } } };

// dataops_service.meta_versions: the table's Delta versions in an environment, newest first.
const v = (version, ago, operation) => ({ version, at: at(-ago), operation, run_id: `run-${version}` });
const VERSIONS = {
  dev: [v(5, 600, "MERGE"), v(4, 86400, "MERGE"), v(3, 172800, "WRITE")],
  test: [v(2, 3600, "MERGE"), v(1, 90000, "WRITE")],
  prod: [v(7, 7200, "MERGE"), v(6, 200000, "MERGE")],
};

// meta/compare.py compare(): rows, keys only on one side, changed values by key, columns and totals.
// Prod v6 against Dev now is identical; every other pair differs. Rows are text, as compare() casts them.
function compared(p) {
  const base = { entity: NAME, keys: ["order_id"], compared_on_keys: true, method: "full",
    left: { env: p.left_env, version: p.left_version }, right: { env: p.right_env, version: p.right_version } };
  if (p.left_env === "prod" && p.left_version === 6 && p.right_env === "dev" && p.right_version === null) {
    return { ...base, rows: { left: 1200, right: 1200 }, columns: { only_left: [], only_right: [], type_changed: [] },
      only_left: { count: 0, rows: [] }, only_right: { count: 0, rows: [] }, changed: { count: 0, by_column: {}, rows: [] },
      totals: [{ column: "amount", left: 5000.5, right: 5000.5, diff: 0 }] };
  }
  return { ...base, rows: { left: 1200, right: 1203 },
    columns: { only_left: [], only_right: ["discount"], type_changed: [{ column: "amount", left: "Decimal(18, 2)", right: "Float64" }] },
    only_left: { count: 1, rows: [{ order_id: "1007", status: "open" }] },
    only_right: { count: 4, rows: [{ order_id: "1101", status: "open" }, { order_id: "1102", status: "shipped" }] },
    changed: { count: 2, by_column: { status: 2, amount: 1 }, rows: [
      { key: { order_id: "1001" }, changes: [{ column: "status", left: "open", right: "shipped" }, { column: "amount", left: "10.0", right: "12.0" }] },
      { key: { order_id: "1002" }, changes: [{ column: "status", left: "open", right: "cancelled" }] }] },
    totals: [{ column: "amount", left: 5000.5, right: 5020.5, diff: 20 }] };
}

// meta/promote.py releases(): every release to an environment, newest first, and whether its files are kept.
const release = (version, ago, more = {}) => ({ version, source_env: "dev", at: at(-ago), commit: `${version.replace(/\./g, "")}c9a2b7e41d`,
  branch: "main", dirty: false, pushed: true, by: ME, receipt_id: `${version.replace(/\./g, "")}9e8d7c6b5a4f3e2d1c0b`, kept: true, verified: true, ...more });
function fresh() {
  return {
    test: [release("1.4.0", 3600, { engine_version: "0.9.2", lock_hash: "a1b2c3d4e5f6a7b8" }), release("1.3.0", 86400 * 4),
      release("1.2.0", 86400 * 9, { kept: false }), release("1.1.0", 86400 * 12)],
    // Promoted before release records: no receipt, so nothing to check.
    prod: [release("1.2.0", 86400 * 8, { source_env: "test", receipt_id: null, commit: null, branch: null, dirty: null, pushed: null })],
  };
}
let db = fresh();
const fail = {};  // method -> the engine's error text
const copy = (x) => JSON.parse(JSON.stringify(x));
const failing = (method) => { if (fail[method]) throw new Error(fail[method]); };

const mocks = {
  readiness_check: () => [],
  meta_project: () => copy(PROJECT),
  meta_entity: () => copy(ENTITY),
  meta_runs_all: () => ({ runs: [] }),
  meta_entity_shortcuts: () => [],
  meta_promote_check: ({ source }) => ({ errors: [], notes: [], version: source === "dev" ? "1.4.0" : db.test[0].version }),
  meta_versions: ({ env }) => copy(VERSIONS[env]),
  meta_compare: (p) => { failing("meta_compare"); return compared(p); },
  // dataops_service.meta_promote_impact: Test's run log says which tables' logic changed; Prod can't be read.
  meta_promote_impact: ({ source, target }) => target === "prod" ? { source, target, tables: [], problem: DENIED }
    : { source, target, problem: null, removed: [], tables: [
      { entity: "gold.sales", state: "new", last_run: null, rows: null, changes: [] },
      { entity: "silver.customers", state: "same", last_run: at(-7200), rows: 800, changes: [] },
      { entity: NAME, state: "changed", last_run: at(-7200), rows: 1200, changes: [
        { kind: "changed", text: "Merge keys: order_id → order_id, line_no", logic: true },
        { kind: "added", text: "Check added: amount >= 0", logic: true }] }] },
  // meta_service.meta_last_release: the newest meta.deployments row for the environment.
  meta_last_release: ({ env }) => {
    const r = db[env]?.[0];
    if (!r) return { env, release: null, problem: null };
    const { kept, ...out } = copy(r);
    return { env, release: { ...out, receipt: r.receipt_id ? JSON.stringify({ version: r.version }) : null }, problem: null };
  },
  meta_releases: ({ env }) => ({ env, problem: null, releases: db[env].map((r, i) => ({ ...copy(r), current: i === 0 })) }),
  meta_rollback_preview: ({ env, version }) => ({ env, from: db[env][0].version, to: version, entities: [
    { entity: NAME, state: "changed", logic: true, changes: [{ kind: "changed", text: `Merge keys: order_id, line_no → order_id (as in ${version})`, logic: true }] }] }),
  // meta_service.meta_rollback / meta/promote.py rollback(): recorded as a new release in that environment.
  meta_rollback: ({ env, version }) => {
    failing("meta_rollback");
    if (env === "dev") throw new Error("ValueError: Dev runs this folder; roll back with Git instead.");
    const current = db[env][0];
    const earlier = db[env].find((r) => r.version === version);
    if (!earlier || !earlier.kept || current.version === version) throw new Error(`Blocked by the rollback gate:\n- ${version} can't be rolled back to.`);
    const row = { ...copy(earlier), source_env: env, at: at(0), by: ME, receipt_id: "77aa66bb55cc44dd33ee" };
    db[env].unshift(row);
    return { env, version, replaced: current.version, receipt_id: row.receipt_id };
  },
};

let baseline;
before(async () => {
  if (skip) return;
  app = await openApp({ mocks });
  await startupReads(app);
  baseline = new Set(app.calls.map((c) => c.method));
  await page().evaluate((me) => { state.user = me; state.meta.env = "dev"; }, ME);
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  db = fresh();
  for (const k of Object.keys(fail)) delete fail[k];
  await page().evaluate(() => { closeMenu(); closeDialog(); const t = document.getElementById("toast"); t.hidden = true; t.textContent = ""; go("home"); });
});

const sent = (from, method) => app.calls.slice(from).filter((c) => c.method === method).map((c) => c.params);
const writes = (from) => app.calls.slice(from).filter((c) => WRITES.includes(c.method)).map((c) => ({ method: c.method, params: c.params }));
const pause = (ms) => new Promise((r) => setTimeout(r, ms));
const dialog = (name) => page().getByRole("dialog", { name });
async function toastText() {
  const handle = await page().waitForFunction(() => { const t = document.getElementById("toast"); return !t.hidden && t.textContent; });
  return handle.jsonValue();
}

async function openEntityPage() {
  await page().evaluate((name) => openEntity(name), NAME);
  await page().locator(".page .head-actions").getByRole("button", { name: "Compare", exact: true }).waitFor();
}

// Opens Compare from the entity page and waits for its first result (Now on both sides).
async function openCompare() {
  await openEntityPage();
  await page().locator(".page .head-actions").getByRole("button", { name: "Compare", exact: true }).click();
  const d = dialog(`Compare ${NAME}`);
  await d.locator(".cmp-result, .cmp-out .muted-text:text('Nothing to show.')").first().waitFor();
  return d;
}

// The version choices a side offers once that environment's versions have loaded.
async function versions(d, side, n) {
  const sel = d.getByLabel(`${side} version`);
  await page().waitForFunction(([el, n]) => el.options.length === n, [await sel.elementHandle(), n]);
  return sel.locator("option").allTextContents();
}

async function pick(d, side, env, version, n) {
  await d.getByLabel(`${side} environment`).selectOption(env);
  await versions(d, side, n);
  await d.getByLabel(`${side} version`).selectOption(version === null ? "" : String(version));
}

// The result as shown: its text and the changed-rows table.
async function resultNow(d, has) {
  const box = d.locator(".cmp-result", { hasText: has });
  await box.waitFor();
  return { text: await box.innerText(), changed: await box.locator("h3:text-is('Changed rows') + .output tbody tr")
    .evaluateAll((trs) => trs.map((tr) => [...tr.children].map((td) => td.textContent))) };
}

async function openEnvironments() {
  await page().evaluate(() => goMeta("environments"));
  await page().locator(".release-card").nth(1).waitFor();
  await page().locator(".promote-impact").waitFor();
}
const releaseCard = (env) => page().locator(".release-card", { hasText: `Last release to ${env}` });

async function openRollback(version = null) {
  await openEnvironments();
  await releaseCard("Test").getByRole("button", { name: /Roll back/ }).click();
  const d = dialog("Roll back Test");
  await d.locator(".rollback-what b", { hasText: "What goes back in Test" }).waitFor();
  if (version) {
    await d.getByRole("radio", { name: `Roll back to ${version}` }).check();
    await d.locator(".rollback-entity", { hasText: `(as in ${version})` }).waitFor();
  }
  return d;
}

test("Compare lists each environment's versions and first compares Test with Dev, both as they are now", { skip }, async () => {
  const from = app.calls.length;
  const d = await openCompare();
  assert.deepStrictEqual(sent(from, "meta_compare"), [{ name: NAME, left_env: "test", right_env: "dev", left_version: null, right_version: null }]);
  assert.strictEqual(await d.getByLabel("Left environment").inputValue(), "test");
  assert.strictEqual(await d.getByLabel("Right environment").inputValue(), "dev");
  // The newest version is "Now"; each earlier one is listed with when and what wrote it.
  const left = await versions(d, "Left", 2);
  assert.strictEqual(left[0], "Now");
  assert.match(left[1], /^Version 1 · .+ · WRITE$/);
  const right = await versions(d, "Right", 3);
  assert.strictEqual(right[0], "Now");
  assert.match(right[1], /^Version 4 · .+ · MERGE$/);
  assert.match(right[2], /^Version 3 · .+ · WRITE$/);
  assert.deepStrictEqual(sent(from, "meta_versions").map((p) => p.env).sort(), ["dev", "test"]);
  assert.ok(sent(from, "meta_versions").every((p) => p.name === NAME));
  await pick(d, "Left", "prod", 6, 2);
  assert.match((await versions(d, "Left", 2))[1], /^Version 6 · .+ · MERGE$/);
});

test("Compare sends exactly the environments and versions picked, and shows the row and schema differences", { skip }, async () => {
  const d = await openCompare();
  await pick(d, "Left", "prod", null, 2);
  await pick(d, "Right", "dev", 4, 3);
  const from = app.calls.length;
  await d.getByRole("button", { name: "Compare", exact: true }).click();
  const shown = await resultNow(d, "Prod");
  assert.deepStrictEqual(sent(from, "meta_compare"), [{ name: NAME, left_env: "prod", right_env: "dev", left_version: null, right_version: 4 }]);
  const t = shown.text;
  assert.match(t, /1,200\s*rows in Prod\b/);
  assert.match(t, /1,203\s*rows in Dev v4/);
  assert.match(t, /1\s*keys only in Prod\b/);
  assert.match(t, /4\s*keys only in Dev v4/);
  assert.match(t, /2\s*rows with changed values/);
  assert.match(t, /Compared: every row by key/);
  assert.doesNotMatch(t, /Identical/);
  // Schema: a column only on one side and a column whose type changed.
  assert.match(t, /Columns differ\s*discount only in Dev v4\s*amount: Decimal\(18, 2\) in Prod, Float64 in Dev v4/);
  assert.match(t, /Which columns changed\s*status · 2 rows\s*amount · 1 row/);
  assert.match(t, /Totals\s*Column\s*Prod\s*Dev v4\s*Difference\s*amount\s*5,000\.5\s*5,020\.5\s*\+20/);
  assert.deepStrictEqual(shown.changed, [["1001", "status", "open", "shipped"], ["", "amount", "10.0", "12.0"], ["1002", "status", "open", "cancelled"]]);
  assert.match(t, /Only in Prod\s*order_id\s*status\s*1007\s*open/);
  assert.match(t, /Only in Dev v4 \(first 2 of 4\)\s*order_id\s*status\s*1101\s*open\s*1102\s*shipped/);
});

test("Compare with no differences says the two sides are identical", { skip }, async () => {
  const d = await openCompare();
  await pick(d, "Left", "prod", 6, 2);
  await pick(d, "Right", "dev", null, 3);
  const from = app.calls.length;
  await d.getByRole("button", { name: "Compare", exact: true }).click();
  const shown = await resultNow(d, "Identical");
  assert.deepStrictEqual(sent(from, "meta_compare"), [{ name: NAME, left_env: "prod", right_env: "dev", left_version: 6, right_version: null }]);
  assert.match(shown.text, /Identical: the same keys and the same values in Prod v6 and Dev\./);
  assert.match(shown.text, /1,200\s*rows in Prod v6\s*1,200\s*rows in Dev\s*0\s*keys only in Prod v6\s*0\s*keys only in Dev\s*0\s*rows with changed values/);
  assert.doesNotMatch(shown.text, /Columns differ|Changed rows|Only in/);
  assert.strictEqual(await d.locator(".cmp-stat.bad").count(), 0);
});

test("Compare: an engine error shows a plain message, the dialog stays and the entity page is as it was", { skip }, async () => {
  const d = await openCompare();
  const pageBefore = await page().locator(".page").innerText();
  fail.meta_compare = DENIED;
  await d.getByRole("button", { name: "Compare", exact: true }).click();
  const said = await toastText();
  assert.strictEqual(said.split("\n")[0], `Could not compare: ${PLAIN}`);
  assert.doesNotMatch(said, /HTTPError|403|onelake/);
  await d.locator(".cmp-out", { hasText: "Nothing to show." }).waitFor();
  assert.strictEqual(await d.count(), 1);
  await d.locator(".dialog-actions").getByRole("button", { name: "Close" }).click();
  await d.waitFor({ state: "detached" });
  assert.strictEqual(await page().locator(".page").innerText(), pageBefore);
});

test("the promote impact and the last release show what the engine returned", { skip }, async () => {
  const from = app.calls.length;
  await openEnvironments();
  assert.deepStrictEqual(sent(from, "meta_promote_impact").sort((a, b) => a.target.localeCompare(b.target)),
    [{ source: "test", target: "prod" }, { source: "dev", target: "test" }]);
  const impact = await page().locator(".promote-impact").innerText();
  assert.match(impact, /What this promote changes\s*Logic changed\s*silver\.orders\s*last ran 2 h ago\s*Compare Dev with Test/);
  assert.match(impact, /Merge keys: order_id → order_id, line_no\s*Check added: amount >= 0/);
  assert.match(impact, /Never run in Test yet: gold\.sales/);
  assert.doesNotMatch(impact, /silver\.customers/, "a table whose logic is the same isn't listed");
  assert.match(await page().locator(".meta-body").innerText(), new RegExp(`Can't read Prod yet: ${PLAIN.replace(/\./g, "\\.")}`));
  // The changed table's Compare opens with Test on the left and Dev on the right.
  const f2 = app.calls.length;
  await page().locator(".promote-impact").getByRole("button", { name: "Compare Dev with Test" }).click();
  await resultNow(dialog(`Compare ${NAME}`), "Test");
  assert.deepStrictEqual(sent(f2, "meta_compare"), [{ name: NAME, left_env: "test", right_env: "dev", left_version: null, right_version: null }]);
  await page().evaluate(() => closeDialog());
  // The last release to each environment, as meta.deployments records it.
  const test_ = await releaseCard("Test").innerText();
  assert.match(test_, /Metadata version\s*1\.4\.0/);
  assert.match(test_, /Code\s*commit 140c9a2 on main, pushed/);
  assert.match(test_, /Engine\s*KratosForge 0\.9\.2, lock a1b2…a7b8/);
  assert.match(test_, new RegExp(`Promoted\\s*from Dev by ${ME}, 1 h ago`));
  assert.match(test_, /Record\s*1409e8d7…\s*checks out/);
  const prod = await releaseCard("Prod").innerText();
  assert.match(prod, /Metadata version\s*1\.2\.0/);
  assert.match(prod, /Code\s*not recorded/);
  assert.match(prod, /Promoted\s*from Test by/);
  assert.match(prod, /Record\s*none \(promoted before release records\)/);
  assert.doesNotMatch(prod, /Engine/);
});

test("Roll back asks first: its dialog lists the releases and what changes, and Cancel sends nothing", { skip }, async () => {
  const before = JSON.stringify(db);
  const from = app.calls.length;
  const d = await openRollback();
  assert.deepStrictEqual(sent(from, "meta_releases"), [{ env: "test" }]);
  assert.deepStrictEqual(sent(from, "meta_rollback_preview"), [{ env: "test", version: "1.3.0" }]);
  assert.strictEqual(await d.getByRole("radio", { name: "Current release" }).isDisabled(), true);
  assert.strictEqual(await d.getByRole("radio", { name: "Roll back to 1.3.0" }).isChecked(), true);
  assert.strictEqual(await d.getByRole("radio", { name: "Roll back to 1.2.0" }).isDisabled(), true, "files not kept");
  const text = await d.innerText();
  assert.match(text, /Puts Test back on the files an earlier promote kept there/);
  // Each release: version, when and who, where its files stood in Git, and whether it can be gone back to.
  const rows = await d.locator(".rollback-row").evaluateAll((ls) => ls.map((l) => [...l.children].slice(1).map((c) => c.textContent)));
  assert.deepStrictEqual(rows.map((r) => [r[0], r[2], r[3]]), [
    ["1.4.0", "commit 140c9a2 on main, pushed", "Running now"], ["1.3.0", "commit 130c9a2 on main, pushed", "checks out"],
    ["1.2.0", "commit 120c9a2 on main, pushed", "files not kept"], ["1.1.0", "commit 110c9a2 on main, pushed", "checks out"]]);
  assert.strictEqual(rows[0][1], `1 h ago, ${ME}`);
  assert.match(text, /What goes back in Test\s*silver\.orders\s*Logic changes/);
  assert.match(text, /Tables keep their data/);
  assert.strictEqual(await d.getByRole("button", { name: "Roll back Test to 1.3.0" }).count(), 1);
  await d.getByRole("button", { name: "Cancel" }).click();
  await d.waitFor({ state: "detached" });
  await pause(300);
  assert.deepStrictEqual(writes(from), []);
  assert.strictEqual(JSON.stringify(db), before);
  assert.match(await releaseCard("Test").innerText(), /Metadata version\s*1\.4\.0/);
});

test("Roll back sends that environment and the release picked, and the page then shows the new last release", { skip }, async () => {
  const d = await openRollback("1.1.0");
  const from = app.calls.length;
  assert.deepStrictEqual(writes(from), [], "picking a release sends no write");
  await d.getByRole("button", { name: "Roll back Test to 1.1.0" }).click();
  await d.waitFor({ state: "detached" });
  assert.deepStrictEqual(writes(from), [{ method: "meta_rollback", params: { env: "test", version: "1.1.0" } }]);
  assert.strictEqual(await toastText(), "Test is back on metadata 1.1.0");
  await page().waitForFunction(() => /Metadata version\s*1\.1\.0/.test(
    [...document.querySelectorAll(".release-card")].find((c) => c.textContent.includes("Last release to Test"))?.innerText || ""));
  const card = await releaseCard("Test").innerText();
  assert.match(card, new RegExp(`Promoted\\s*from Test by ${ME}, just now`));
  assert.match(card, /Record\s*77aa66bb…\s*checks out/);
  assert.match(await releaseCard("Prod").innerText(), /Metadata version\s*1\.2\.0/, "Prod is untouched");
});

test("Roll back: an engine error shows a plain message, the dialog stays and Test stays on its release", { skip }, async () => {
  fail.meta_rollback = DENIED;
  const before = JSON.stringify(db);
  const d = await openRollback();
  const from = app.calls.length;
  await d.getByRole("button", { name: "Roll back Test to 1.3.0" }).click();
  const said = await toastText();
  assert.strictEqual(said.split("\n")[0], PLAIN);
  assert.doesNotMatch(said, /HTTPError|403|onelake/);
  assert.deepStrictEqual(writes(from), [{ method: "meta_rollback", params: { env: "test", version: "1.3.0" } }]);
  assert.strictEqual(await d.count(), 1, "the dialog stays open");
  assert.strictEqual(JSON.stringify(db), before);
  await d.getByRole("button", { name: "Cancel" }).click();
  await d.waitFor({ state: "detached" });
  assert.match(await releaseCard("Test").innerText(), /Metadata version\s*1\.4\.0/);
});

test("opening the entity page, Compare, the promote cards and the Roll back dialog send no write", { skip }, async () => {
  const from = app.calls.length;
  const d = await openCompare();
  await pick(d, "Left", "prod", 6, 2);
  await d.getByRole("button", { name: "Compare", exact: true }).click();
  await resultNow(d, "Identical");
  await d.locator(".dialog-actions").getByRole("button", { name: "Close" }).click();
  await openRollback("1.1.0");
  await page().evaluate(() => closeDialog());
  await pause(300);
  const made = app.calls.slice(from).map((c) => c.method);
  for (const m of READS) assert.ok(made.includes(m), `${m} was read`);
  assert.deepStrictEqual(made.filter((m) => WRITES.includes(m)), []);
  // Anything else the pages called is one of the reads the entity and Environments pages make on their own.
  const others = [...new Set(made.filter((m) => !READS.includes(m) && !baseline.has(m)))];
  assert.deepStrictEqual(others.filter((m) => !PAGE_READS.includes(m)), []);
});

test("axe finds nothing serious on the Compare dialog, its result and the Roll back dialog", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  const check = async (where) => assert.deepStrictEqual(await axeFindings(page(), "#dialog-root .dialog"), [], where);
  const d = await openCompare();
  await check("Compare dialog with differences");
  await pick(d, "Left", "prod", 6, 2);
  await pick(d, "Right", "dev", null, 3);
  await d.getByRole("button", { name: "Compare", exact: true }).click();
  await resultNow(d, "Identical");
  await check("Compare result, identical");
  await page().evaluate(() => closeDialog());
  await openRollback();
  await check("Roll back dialog");
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
