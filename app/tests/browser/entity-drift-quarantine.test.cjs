// #1374: schema drift, quarantine replay and freshness promises on an entity's page and Home
// (dataops.js; see harness.cjs for how to run it). The drift card shows the columns the source added,
// dropped and retyped, and says the source matches the contract when nothing drifted. The quarantine
// card lists the held rows and why; Replay sends exactly the rows picked or fixed, with exactly the
// fixed values; Discard asks first in a danger confirm, and Cancel sends nothing. The freshness card
// shows each environment's promise as Fresh, Late or Never loaded, its dialog saves the promise as
// entered, and the Teams alerts dialog sends the address and test choice picked. Home lists late
// tables, or says every promise is kept. An engine error shows a plain message and the card stays as
// it was. Opening the page and Home sends no write, and axe finds nothing serious on the cards and
// dialogs.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const READS = ["meta_entity", "meta_drift", "meta_quarantine", "meta_freshness"];
const PAGE_READS = ["mesh_about", "meta_runs_all", "meta_entity_shortcuts", "meta_project"];  // the page's other cards
const WRITES = ["meta_drift_apply", "meta_replay", "meta_discard_quarantine", "meta_set_freshness", "meta_set_alerts", "meta_save_entity"];
const DENIED = "HTTPError: 403 Client Error: Forbidden for url: https://onelake.dfs.fabric.microsoft.com/a1/b2/Tables/quarantine/silver_orders";
const PLAIN = /You don't have permission for that in this workspace/;
const WEBHOOK = "https://contoso.webhook.office.com/workflows/abc123";

const at = (h) => new Date(Date.now() + h * 3600000).toISOString();

// What the engine answers. meta_entity: meta_service.py meta_entity (Entity.summary in meta/project.py,
// where freshness is meta/freshness.py promise()). meta_drift: meta/drift.py detect() plus env, problem
// and schema_history rows (meta/store.py). meta_quarantine, meta_replay (RunResult.to_dict, meta/engine.py)
// and meta_freshness: dataops_service.py, with items from meta/freshness.py report().
function entity(name, freshness) {
  return { name, layer: "silver", schema: "silver", table: name.split(".")[1], owner: "ana@contoso.com", mode: "incremental",
    watermark: "updated_at", source: "bronze.orders", clean: 0, derive: 0, checks: 2, custom: [], keys: ["order_id"],
    columns: { order_id: "bigint", amount: "double", status: "string", region: "string" }, surrogate_keys: [], derived: [],
    hook_adds: [], reads: ["bronze.orders"], path: `metadata/entities/${name}.toml`, freshness, objective: null, criticality: null,
    link: null, description: "", text: `[entity]\nname = "${name}"\n`, impact: [], runs: [], preview: null, errors: [],
    keys_sql: null, keys_from: null };
}
const PROMISE = { within_hours: 26, by: "07:00", envs: ["dev", "test", "prod"] };
const status = (env, s, more = {}) => ({ entity: "silver.orders", env, expected: "every 26 h and by 07:00 each day", last_ok: at(-2),
  age_hours: 2, status: s, reasons: [], due_at: at(24), last_failed: false, failed_run: null, failure: null, ...more });
const quarantined = () => ({ entity: "silver.orders", env: "test", columns: ["order_id", "amount", "status", "note"],
  editable: ["order_id", "amount", "status"], total: 3, downstream: [],
  checks: { "not_null(amount)": 2, "range(amount)": 1 },
  rows: [
    { _qid: "q-0001", _check: "not_null(amount)", _run_id: "run-1", _loaded_at: "2026-10-31T06:00:00+00:00", order_id: 101, amount: null, status: "open", note: "web" },
    { _qid: "q-0002", _check: "range(amount)", _run_id: "run-1", _loaded_at: "2026-10-31T06:00:00+00:00", order_id: 102, amount: "-5", status: "open", note: "store" },
    { _qid: "q-0003", _check: "not_null(amount)", _run_id: "run-1", _loaded_at: "2026-10-31T06:00:00+00:00", order_id: 103, amount: null, status: "test", note: "test row" }] });

let db, fail, baseline;
function fresh() {
  return {
    entities: { "silver.orders": entity("silver.orders", PROMISE), "silver.customers": entity("silver.customers", null) },
    drift: {
      "silver.orders": { entity: "silver.orders", source: "bronze.orders", checked: true, policy: "evolve", own_policy: null, layer_policy: "evolve",
        added: [{ column: "channel", type: "string" }], dropped: [{ column: "region", type: "string", key: false }],
        retyped: [{ column: "amount", contract: "double", source: "string" }], env: "test", problem: null,
        history: [{ entity: "silver.orders", run_id: "run-1", change: "added", columns: "status", at: at(-48), detail: "status string", breaking: false, delta_version: 7, schema_version: 2 },
          { entity: "silver.orders", run_id: "run-0", change: "created", columns: "order_id, amount", at: at(-96), detail: "order_id int64, amount float64", breaking: false, delta_version: 0, schema_version: 1 }] },
      "silver.customers": { entity: "silver.customers", source: "bronze.orders", checked: true, policy: "evolve", own_policy: null, layer_policy: "evolve",
        added: [], dropped: [], retyped: [], env: "test", problem: null, history: [] },
    },
    quarantine: { "silver.orders": quarantined() },
    freshness: [status("prod", "late", { last_ok: at(-40), age_hours: 40, reasons: ["last updated 40 h ago, promised every 26 h"] }),
      status("dev", "never", { last_ok: null, age_hours: null, reasons: ["never loaded here"], due_at: null }), status("test", "fresh")],
    alerts: "",
  };
}

const failing = (method, fn) => (p) => { if (fail[method]) throw new Error(fail[method]); return fn(p); };

before(async () => {
  if (skip) return;
  db = fresh();
  fail = {};
  app = await openApp({ mocks: {
    meta_project: () => ({ path: "C:/mesh", name: "Mesh", environments: {}, entities: Object.values(db.entities), layers: [], errors: [] }),
    meta_entity: ({ name }) => structuredClone(db.entities[name]),
    meta_runs_all: () => ({ runs: [], unavailable: {} }),
    meta_entity_shortcuts: () => [],
    meta_drift: failing("meta_drift", ({ name }) => structuredClone(db.drift[name])),
    meta_quarantine: ({ name, env }) => structuredClone(db.quarantine[name] ||
      { entity: name, env, columns: [], rows: [], total: 0, checks: {}, downstream: [] }),
    meta_replay: failing("meta_replay", ({ name, ids }) => {
      const q = db.quarantine[name];
      q.rows = q.rows.filter((r) => !ids.includes(r._qid));
      q.total = q.rows.length;
      return { run_id: "run-r1", entity: name, status: "ok", steps: [], rows_read: ids.length, rows_written: ids.length, quarantined: 0,
        delta_version: 9, message: "", options: [], retryable: false, held: 0, downstream: [] };
    }),
    meta_discard_quarantine: failing("meta_discard_quarantine", ({ name, ids }) => {
      const q = db.quarantine[name];
      q.rows = q.rows.filter((r) => !ids.includes(r._qid));
      q.total = q.rows.length;
      return structuredClone(q);
    }),
    meta_freshness: () => ({ items: structuredClone(db.freshness), unavailable: {}, alerts: !!db.alerts, alerts_unreadable: false }),
    meta_set_freshness: failing("meta_set_freshness", ({ name, within_hours, by, envs }) => {
      db.entities[name].freshness = within_hours || by ? { within_hours, by, envs: envs || ["test", "prod"] } : null;
      return structuredClone(db.entities[name]);
    }),
    meta_alerts: () => ({ teams_webhook: db.alerts }),
    meta_set_alerts: failing("meta_set_alerts", ({ teams_webhook }) => { db.alerts = teams_webhook; return { teams_webhook }; }),
  } });
  await startupReads(app);
  for (const end = Date.now() + 15000; !app.calls.some((c) => c.method === "meta_freshness"); await new Promise((r) => setTimeout(r, 50))) {
    if (Date.now() > end) throw new Error("the app's startup freshness check never came");
  }
  baseline = new Set(app.calls.map((c) => c.method));
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  db = fresh();
  for (const k of Object.keys(fail)) delete fail[k];
  await page().evaluate(() => { closeDialog(); const t = document.getElementById("toast"); t.hidden = true; t.textContent = ""; state.meta.env = "test"; go("home"); });
});

const writes = (from) => app.calls.slice(from).filter((c) => WRITES.includes(c.method)).map((c) => ({ method: c.method, params: c.params }));
const pause = (ms) => new Promise((r) => setTimeout(r, ms));
const toast = () => page().locator("#toast:not([hidden])");
const drift = () => page().locator(".page .dataops.drift");
const quarantine = () => page().locator(".page .dataops.quarantine");
const freshCard = () => page().locator(".page .side .dataops", { has: page().locator("b", { hasText: "Freshness" }) });
const dialog = () => page().locator("#dialog-root .dialog");

// The entity's page once its drift card has read the source (and, with held rows, the quarantine card is there).
async function openPage(name, { held = name === "silver.orders" } = {}) {
  await page().evaluate((name) => openEntity(name), name);
  await page().waitForFunction(() => {
    const d = document.querySelector(".page .dataops.drift");
    return d && !d.textContent.includes("Checking the source");
  });
  if (held) await quarantine().waitFor();
}

test("drift shows the added, dropped and retyped columns, and says when the source matches", { skip }, async () => {
  await openPage("silver.orders");
  const card = drift();
  await card.locator(".pill-s.wrn", { hasText: "3 to review" }).waitFor();
  const rows = await card.locator(".drift-row").evaluateAll((els) => els.map((e) => e.innerText.replace(/\s+/g, " ").trim()));
  assert.ok(rows.some((r) => /^Gone region/.test(r)), `dropped column: ${rows}`);
  assert.ok(rows.some((r) => /^New channel string · add to contract/.test(r)), `added column: ${rows}`);
  assert.ok(rows.some((r) => /^Type amount double to string/.test(r)), `retyped column: ${rows}`);
  const text = await card.innerText();
  assert.match(text, /bronze\.orders in Test differs from the contract/);
  assert.match(text, /Schema changes: added status string \(Delta version 7\)/);
  assert.doesNotMatch(text, /created/, "a table's first schema isn't a change");
  assert.strictEqual(await card.getByRole("button", { name: "Save to config" }).isDisabled(), true, "nothing to save until something is picked");

  await openPage("silver.customers");
  const none = await drift().innerText();
  assert.match(none, /bronze\.orders in Test matches the contract/);
  assert.doesNotMatch(none, /to review/);
  assert.strictEqual(await drift().locator(".drift-row").count(), 0);
  assert.strictEqual(await quarantine().count(), 0, "no quarantine card without held rows");
});

test("quarantine lists each held row and why", { skip }, async () => {
  await openPage("silver.orders");
  const card = quarantine();
  const head = await card.locator(".row").first().innerText();
  assert.match(head, /Quarantine in Test · 3 rows held/);
  assert.match(head, /2 by not_null\(amount\) · 1 by range\(amount\)/);
  const rows = await card.locator("tbody tr").evaluateAll((trs) => trs.map((tr) => [...tr.children].map((td) => {
    const input = td.querySelector("input.qcell");
    return input ? input.value : td.textContent.trim();
  })));
  assert.deepStrictEqual(rows.map((r) => [r[1], r[3], r[4], r[6]]), [
    ["not_null(amount)", "101", "", "web"], ["range(amount)", "102", "-5", "store"], ["not_null(amount)", "103", "", "test row"]]);
  assert.strictEqual(await card.locator("td.ro", { hasText: "web" }).count(), 1, "a column outside the contract can't be edited");
  assert.strictEqual(await card.getByRole("button", { name: /Replay selected/ }).isDisabled(), true);
  assert.strictEqual(await card.getByRole("button", { name: "Discard selected" }).isDisabled(), true);
});

test("Replay sends exactly the rows fixed, with exactly the fixed values", { skip }, async () => {
  await openPage("silver.orders");
  const card = quarantine();
  await card.getByLabel("amount of row q-0002").fill("12.50");
  assert.strictEqual(await card.getByLabel("Select row q-0002").isChecked(), true, "fixing a value picks its row");
  // A value typed and put back is not a fix.
  await card.getByLabel("status of row q-0001").fill("closed");
  await card.getByLabel("status of row q-0001").fill("open");
  await card.getByLabel("Select row q-0001").uncheck();
  await card.locator(".muted-text", { hasText: "1 selected, 1 fixed" }).waitFor();
  const from = app.calls.length;
  await card.getByRole("button", { name: /Replay selected/ }).click();
  await toast().filter({ hasText: "1 row loaded into silver.orders" }).waitFor();
  const sent = writes(from);
  assert.strictEqual(sent.length, 1, JSON.stringify(sent));
  assert.strictEqual(sent[0].method, "meta_replay");
  const { name, env, ids, edits } = sent[0].params;
  assert.deepStrictEqual({ name, env, ids }, { name: "silver.orders", env: "test", ids: ["q-0002"] });
  const fixed = Object.fromEntries(Object.entries(edits).filter(([, v]) => Object.keys(v).length));
  assert.deepStrictEqual(fixed, { "q-0002": { amount: "12.50" } });
  await quarantine().locator("b", { hasText: "2 rows held" }).waitFor();
});

test("Discard asks first in a danger confirm; Cancel sends nothing, Discard sends the picked rows", { skip }, async () => {
  await openPage("silver.orders");
  const card = quarantine();
  await card.getByLabel("Select row q-0003").check();
  const from = app.calls.length;
  await card.getByRole("button", { name: "Discard selected" }).click();
  await dialog().waitFor();
  assert.strictEqual(await dialog().getAttribute("aria-label"), "Discard 1 row?");
  assert.match(await dialog().innerText(), /leave quarantine for good/);
  const ok = dialog().locator(".dialog-actions button", { hasText: "Discard" });
  assert.match(await ok.getAttribute("class"), /danger-solid/, "drawn as a danger action");
  assert.strictEqual(await page().evaluate(() => document.activeElement?.textContent.trim()), "Cancel", "Cancel has focus");
  await pause(300);
  assert.deepStrictEqual(writes(from), [], "nothing is sent before it is confirmed");
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await dialog().waitFor({ state: "detached" });
  await pause(300);
  assert.deepStrictEqual(writes(from), [], "Cancel sends nothing");
  assert.match(await card.locator("b").first().innerText(), /3 rows held/);

  await card.getByRole("button", { name: "Discard selected" }).click();
  await dialog().locator(".dialog-actions button", { hasText: "Discard" }).click();
  await toast().filter({ hasText: "Discarded 1 row" }).waitFor();
  assert.deepStrictEqual(writes(from), [{ method: "meta_discard_quarantine", params: { name: "silver.orders", env: "test", ids: ["q-0003"] } }]);
  await quarantine().locator("b", { hasText: "2 rows held" }).waitFor();
});

test("freshness shows each environment's promise as Fresh, Late or Never loaded", { skip }, async () => {
  await openPage("silver.orders");
  const card = freshCard();
  await card.locator(".pill-s").nth(2).waitFor();
  assert.match(await card.innerText(), /Updated every 26 h and by 07:00 each day in Dev, Test, Prod/);
  const lines = await card.locator(".row:has(.pill-s)").evaluateAll((els) => els.map((e) => e.innerText.replace(/\s+/g, " ").trim()));
  assert.strictEqual(lines.length, 3, String(lines));
  assert.match(lines.find((l) => l.includes("Prod")), /^Late Prod last updated 40 h ago, promised every 26 h$/);
  assert.match(lines.find((l) => l.includes("Dev")), /^Never loaded Dev never loaded$/);
  assert.match(lines.find((l) => l.includes("Test")), /^Fresh Test updated /);
  assert.deepStrictEqual(await card.locator(".pill-s").evaluateAll((els) => els.map((e) => e.className)),
    ["pill-s fail", "pill-s neu", "pill-s pass"]);

  await openPage("silver.customers");
  assert.match(await freshCard().innerText(), /No promise yet/);
  assert.strictEqual(await freshCard().locator(".pill-s").count(), 0);
});

test("the freshness dialog saves the promise as entered", { skip }, async () => {
  await openPage("silver.orders");
  await freshCard().getByRole("button", { name: "Change" }).click();
  await dialog().waitFor();
  assert.strictEqual(await dialog().getAttribute("aria-label"), "Freshness for silver.orders");
  await dialog().getByLabel("Updated at least every (hours)").fill("12");
  await dialog().getByLabel(/updated each day by/).fill("06:30");
  await dialog().getByLabel("Dev").uncheck();
  const from = app.calls.length;
  await dialog().getByRole("button", { name: "Save" }).click();
  await toast().filter({ hasText: "Saved: silver.orders is updated every 12 h and by 06:30 each day in Test, Prod" }).waitFor();
  assert.deepStrictEqual(writes(from), [{ method: "meta_set_freshness",
    params: { name: "silver.orders", within_hours: 12, by: "06:30", envs: ["test", "prod"] } }]);
  await freshCard().filter({ hasText: "Updated every 12 h and by 06:30 each day in Test, Prod" }).waitFor();

  // A new promise starts at 24 hours in Test and Prod.
  await openPage("silver.customers");
  await freshCard().getByRole("button", { name: "Set a promise" }).click();
  await dialog().waitFor();
  const from2 = app.calls.length;
  await dialog().getByRole("button", { name: "Save" }).click();
  await dialog().waitFor({ state: "detached" });
  assert.deepStrictEqual(writes(from2), [{ method: "meta_set_freshness",
    params: { name: "silver.customers", within_hours: 24, by: null, envs: ["test", "prod"] } }]);
});

test("the Teams alerts dialog sends the address and choice picked; Cancel sends nothing", { skip }, async () => {
  await openPage("silver.orders");
  const openAlerts = async () => {
    await freshCard().getByRole("button", { name: "Change" }).click();
    await dialog().getByRole("button", { name: /Set up Teams alerts|Change/ }).click();
    await page().locator('#dialog-root .dialog[aria-label="Teams alerts"]').waitFor();
  };
  await openAlerts();
  const from = app.calls.length;
  await dialog().getByLabel("Webhook address").fill(WEBHOOK);
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await dialog().waitFor({ state: "detached" });
  assert.deepStrictEqual(writes(from), [], "Cancel sends nothing");

  await openAlerts();
  assert.strictEqual(await dialog().getByLabel("Webhook address").inputValue(), "");
  await dialog().getByLabel("Webhook address").fill(`  ${WEBHOOK} `);
  await dialog().getByRole("button", { name: "Send a test and save" }).click();
  await toast().filter({ hasText: "Sent a test message to Teams and saved" }).waitFor();
  assert.deepStrictEqual(writes(from), [{ method: "meta_set_alerts", params: { teams_webhook: WEBHOOK, test: true } }]);

  await openAlerts();
  assert.strictEqual(await dialog().getByLabel("Webhook address").inputValue(), WEBHOOK, "the saved address is shown");
  const from2 = app.calls.length;
  await dialog().getByLabel("Webhook address").fill("");
  await dialog().getByRole("button", { name: "Save", exact: true }).click();
  await toast().filter({ hasText: "Teams alerts are off" }).waitFor();
  assert.deepStrictEqual(writes(from2), [{ method: "meta_set_alerts", params: { teams_webhook: "", test: false } }]);
});

test("Home lists late tables, and says when every promise is kept", { skip }, async () => {
  await page().evaluate(() => checkFreshness());
  const slot = page().locator("#home-fresh");
  await slot.locator(".card.attention").waitFor();
  const late = await slot.innerText();
  assert.match(late, /Late tables/);
  assert.match(late, /silver\.orders in Prod · promised every 26 h and by 07:00 each day/);
  assert.match(late, /last updated 40 h ago, promised every 26 h/);
  assert.strictEqual(await slot.locator(".card.attention").count(), 1, "only the late one");

  db.freshness = [status("prod", "fresh"), status("test", "fresh")];
  await page().evaluate(() => checkFreshness());
  await slot.locator(".fresh-ok").waitFor();
  assert.match(await slot.innerText(), /Every freshness promise is kept \(2 tables\)/);
  assert.strictEqual(await slot.locator(".card.attention").count(), 0);
});

test("an engine error shows a plain message and the card stays as it was", { skip }, async () => {
  fail.meta_drift = DENIED;
  await openPage("silver.orders");
  const d = await drift().innerText();
  assert.match(d, /Schema drift/);
  assert.match(d, PLAIN);
  assert.doesNotMatch(d, /HTTPError|onelake/, "the raw error isn't shown");
  delete fail.meta_drift;

  fail.meta_replay = DENIED;
  const card = quarantine();
  await card.getByLabel("amount of row q-0002").fill("12.50");
  await card.getByRole("button", { name: /Replay selected/ }).click();
  await toast().filter({ hasText: "Replay failed" }).waitFor();
  assert.match(await toast().innerText(), PLAIN);
  assert.match(await card.locator("b").first().innerText(), /3 rows held/);
  assert.strictEqual(await card.getByLabel("amount of row q-0002").inputValue(), "12.50", "the fix is kept to try again");
  assert.strictEqual(await card.getByLabel("Select row q-0002").isChecked(), true);

  fail.meta_set_freshness = DENIED;
  await freshCard().getByRole("button", { name: "Change" }).click();
  await dialog().getByLabel("Updated at least every (hours)").fill("12");
  await dialog().getByRole("button", { name: "Save" }).click();
  await toast().filter({ hasText: PLAIN }).waitFor();
  assert.strictEqual(await dialog().getByLabel("Updated at least every (hours)").inputValue(), "12", "the dialog stays open with what was typed");
  await dialog().getByRole("button", { name: "Cancel" }).click();
  assert.match(await freshCard().innerText(), /Updated every 26 h and by 07:00 each day in Dev, Test, Prod/);
});

test("opening the page and Home sends no write", { skip }, async () => {
  const from = app.calls.length;
  await page().evaluate(() => { go("home"); checkFreshness(); });
  await page().locator("#home-fresh .card.attention").waitFor();
  await openPage("silver.orders");
  await freshCard().locator(".pill-s").nth(2).waitFor();
  await openPage("silver.customers");
  await pause(300);
  const made = app.calls.slice(from).map((c) => c.method);
  for (const m of READS) assert.ok(made.includes(m), `${m} was read`);
  assert.deepStrictEqual(made.filter((m) => WRITES.includes(m)), []);
  assert.deepStrictEqual([...new Set(made.filter((m) => !READS.includes(m) && !PAGE_READS.includes(m) && !baseline.has(m)))], []);
});

test("axe finds nothing serious on the cards and dialogs", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  // axeFindings settles the page first: axe judges contrast on the colors as drawn.
  const check = async (where, selector) => assert.deepStrictEqual(await axeFindings(page(), selector), [], where);
  await openPage("silver.orders");
  await freshCard().locator(".pill-s").nth(2).waitFor();
  await check("drift card", ".page .dataops.drift");
  await check("quarantine card", ".page .dataops.quarantine");
  await page().evaluate(() => document.querySelectorAll(".page .side .dataops")[1].id = "fresh-under-test");
  await check("freshness card", "#fresh-under-test");
  await freshCard().getByRole("button", { name: "Change" }).click();
  await dialog().waitFor();
  await check("freshness dialog", "#dialog-root .dialog");
  await dialog().getByRole("button", { name: "Set up Teams alerts" }).click();
  await page().locator('#dialog-root .dialog[aria-label="Teams alerts"]').waitFor();
  await check("alerts dialog", "#dialog-root .dialog");
  await page().evaluate(() => closeDialog());
  await quarantine().getByLabel("Select row q-0003").check();
  await quarantine().getByRole("button", { name: "Discard selected" }).click();
  await dialog().waitFor();
  await check("discard confirm", "#dialog-root .dialog");
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await openPage("silver.customers", { held: false });
  await check("no-drift card", ".page .dataops.drift");
  await page().evaluate(() => { go("home"); checkFreshness(); });
  await page().locator("#home-fresh .card.attention").waitFor();
  await check("Home late tables", "#home-fresh");
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
