// #2358: Monitor > KratosForge > Operations > Data health (see harness.cjs for how to run it). The list shows
// each watched table's freshness, volume and schema, with open alerts on top and the rule beside a volume
// alert; a table opens with its chart and alert actions, and Acknowledge sends only that alert; Watch a
// table... adds one; numbers the log didn't hold show "Can't tell", never a number; an empty list, an
// engine error, a list started again and a table gone from the project each show a plain message; and axe finds nothing serious.
// axe covers dialogs: data-health.js:dhWatchDialog
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const RULE = { baseline: 20, minimum: 10, spreads: 3, floor: 0.1, text: "Usual is the median rows written by the last 20 loads." };

const loads = (rows, last) => rows.map((n, i) => ({ version: 400 + i, at: "2026-11-17T08:00:00Z", operation: "WRITE", mode: "Append",
  rows: n, run_id: i === rows.length - 1 ? last : null }));
const sales = {
  entity: "gold.fact_sales", env: "dev", version: 412, checked_at: "2026-11-17T10:00:00Z",
  freshness: { state: "fresh", within_hours: 24, age_hours: 2, last_version: 412, last_change: "2026-11-17T08:00:00Z" },
  volume: { state: "high", loads: loads([...Array(12).fill(498000), 5200000], "r-20261117-080000-0123456789ab"), latest: { version: 412, rows: 5200000 },
    usual: { enough: true, have: 12, need: 10, median: 498000, spread: 22000, band: 66000 }, unread: 0, distance: 4702000, rule: RULE },
  schema: { state: "same", added: [], removed: [], retyped: [], version: 412, since_version: 411 },
  alerts: [{ kind: "volume", title: "x", version: 412, rule: RULE.text }],
};
const customer = {
  entity: "gold.dim_customer", env: "dev", version: 88,
  freshness: { state: "late", within_hours: 24, age_hours: 26, last_version: 88 },
  volume: { state: "unread", loads: loads([null], null), latest: { version: 88, rows: null }, usual: { enough: false, have: 0, need: 10 }, unread: 0, rule: RULE,
    why: "The latest load didn't record how many rows it wrote." },
  schema: { state: "unread", why: "Couldn't read the schema at version 87; its log entry may have been removed." },
  alerts: [{ kind: "freshness", title: "y", version: 88, quiet: { at: "2026-11-17T09:00:00Z", until: null } }],
};
const orders = {
  entity: "silver.orders", env: "dev", version: 7,
  freshness: { state: "no_promise", within_hours: null, age_hours: 0.5, last_version: 7 },
  volume: { state: "not_enough_history", loads: loads([10, 10, 10, 10, 10, 10, 10], null), latest: { version: 7, rows: 10 },
    usual: { enough: false, have: 6, need: 10 }, unread: 0, rule: RULE },
  schema: { state: "changed", added: ["region"], removed: [], retyped: [], version: 7, since_version: 6 },
  alerts: [{ kind: "schema", title: "z", version: 7 }],
};
const gone = { entity: "silver.renamed_away", env: "dev", state: "not_in_project",
  why: "silver.renamed_away isn't in the metadata project any more. Stop watching it to take it off the list." };
const full = { env: "dev", tables: [sales, customer, orders], entities: ["gold.dim_customer", "gold.fact_sales", "gold.fact_returns", "silver.orders"], rule: RULE };
let mode = "full";
const sent = [];

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    readiness_check: () => [],
    meta_data_health_list: () => {
      if (mode === "error") throw new Error("The Python sidecar stopped (exit code 1)");
      if (mode === "reset") return { ...full, tables: [gone], reset: { at: "2026-11-17T09:00:00Z", copy: "data_health.json.corrupt-20261117T090000000000Z" } };
      return mode === "empty" ? { ...full, tables: [] } : full;
    },
    meta_data_health_quiet: (p) => { sent.push(["quiet", p]); return { ...p, at: "now", until: null }; },
    meta_data_health_watch: (p) => { sent.push(["watch", p]); return p; },
    meta_data_health: (p) => { sent.push(["check", p]); return sales; },
  } });
  await startupReads(app);
});
after(async () => { if (app) await app.close(); });

const open = () => page().evaluate(() => { dhPage.open = null; dhPage.env = "dev"; monitorPage.panel = "data_health"; go("home"); go("monitor"); });

async function rows() {
  await open();
  const handle = await page().waitForFunction(() => {
    const trs = [...document.querySelectorAll(".page.monitor .dh-panel tbody tr")];
    return trs.length === 3 && trs.map((tr) => [...tr.children].map((td) => td.textContent.trim()));
  });
  return handle.jsonValue();
}

test("the list shows freshness, volume and schema, never a guessed number", { skip }, async () => {
  const list = await rows();
  assert.deepStrictEqual(list, [
    ["gold.fact_sales", "2 h ago", "10.4× usual", "Same"],
    ["gold.dim_customer", "26 h, promise 24 h", "Rows not recorded", "Can't tell"],
    ["silver.orders", "30 min ago", "Not enough history (6 of 10 loads)", "1 column added"],
  ]);
  // Open alerts: the acknowledged freshness alert isn't one; the volume alert carries its rule.
  const alerts = await page().locator(".page.monitor .dh-alert").allTextContents();
  assert.strictEqual(alerts.length, 2);
  assert.match(alerts[0], /^Volumegold\.fact_sales: version 412 wrote 5,200,000 rows, 10\.4× usual/);
  assert.match(alerts[0], /Usual: median 498,000 rows a load, spread ±22,000, over the last 12 loads\./);
  assert.match(alerts[1], /^Schemasilver\.orders: 1 column added in version 7/);
  assert.match(await page().textContent(".page.monitor .dh-panel"), /Read from each table's Delta log; no Fabric job\./);
});

test("a table opens with its chart, and Acknowledge sends only that alert", { skip }, async () => {
  await rows();
  await page().click(".page.monitor .dh-panel tbody tr:nth-child(1)");
  await page().waitForSelector(".page.monitor .dh-note");
  const note = await page().textContent(".page.monitor .dh-note");
  assert.match(note, /Open the run that wrote it/);
  assert.match(note, /Snooze 1 day/);
  // Open an incident: Report an incident with the alert filled in, for this environment.
  await page().locator(".page.monitor .dh-note button", { hasText: "Open an incident" }).click();
  await page().waitForSelector(".dialog .inc-report");
  assert.match(await page().inputValue(".dialog .inc-report input[type=text]"), /^gold\.fact_sales: version 412 wrote 5,200,000 rows, 10\.4× usual$/);
  assert.match(await page().inputValue(".dialog .inc-report textarea"), /Usual: median 498,000 rows a load.*Found by Data health/);
  assert.strictEqual(await page().inputValue(".dialog .inc-report select"), "dev");
  await page().locator(".dialog button", { hasText: "Cancel" }).click();
  await page().waitForFunction(() => !document.querySelector(".dialog"));
  assert.strictEqual(await page().locator(".page.monitor .dh-chart rect.dh-bar").count(), 13);
  assert.strictEqual(await page().locator(".page.monitor .dh-chart rect.dh-bar.hi").count(), 1);
  const tiles = await page().locator(".page.monitor .dh-tile").allTextContents();
  assert.deepStrictEqual(tiles, ["2 h agoFreshness · promise 24 h", "498,000 rowsUsual load · spread ±22,000", "SameSchema · since version 411"]);
  sent.length = 0;
  await page().locator(".page.monitor .dh-note button", { hasText: "Acknowledge" }).click();
  await page().waitForFunction(() => document.querySelector(".page.monitor .dh-note"));
  assert.deepStrictEqual(sent, [["quiet", { name: "gold.fact_sales", env: "dev", kind: "volume", version: 412, snooze_days: 0 }]]);
});

test("an acknowledged alert shows as acknowledged, with no actions", { skip }, async () => {
  await rows();
  await page().click(".page.monitor .dh-panel tbody tr:nth-child(2)");
  await page().waitForSelector(".page.monitor .dh-note.quiet");
  const note = await page().textContent(".page.monitor .dh-note.quiet");
  assert.match(note, /last changed 26 h ago; its promise is 24 h Acknowledged\./);
  assert.strictEqual(await page().locator(".page.monitor .dh-note.quiet button").count(), 0);
  const tiles = await page().locator(".page.monitor .dh-tile").allTextContents();
  assert.match(tiles[2], /^Can't tell/);
});

test("Watch a table... offers only tables not watched yet", { skip }, async () => {
  await rows();
  await page().locator(".page.monitor .dh-panel button", { hasText: "Watch a table..." }).click();
  const options = await page().locator(".dialog select option").allTextContents();
  assert.deepStrictEqual(options, ["gold.fact_returns"]);
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), ".dialog"), []);
  sent.length = 0;
  await page().locator(".dialog button", { hasText: "Watch" }).click();
  await page().waitForFunction(() => !document.querySelector(".dialog"));
  assert.deepStrictEqual(sent, [["watch", { name: "gold.fact_returns", env: "dev", watch: true }]]);
});

test("an empty list and an engine error each show a plain message", { skip }, async () => {
  for (const [m, words] of [["empty", /^No tables watched in Dev\./], ["error", /^Couldn't read Data health: /]]) {
    mode = m;
    try {
      await open();
      const handle = await page().waitForFunction(() => {
        const p = document.querySelector(".page.monitor .dh-panel .monitor-empty");
        return !!p && !/^Reading/.test(p.textContent) && p.textContent.trim();
      });
      assert.match(await handle.jsonValue(), words);
    } finally { mode = "full"; }
  }
});

test("a list started again says so, and a table gone from the project says why", { skip }, async () => {
  mode = "reset";
  try {
    await open();
    await page().waitForSelector(".page.monitor .dh-reset");
    assert.match(await page().textContent(".page.monitor .dh-reset"),
      /couldn't be read, so it started again empty on .+\. The old file was kept as data_health\.json\.corrupt-20261117T090000000000Z in the app's folder\. Watch your tables again to bring them back\./);
    const row = await page().textContent(".page.monitor .dh-panel tbody tr");
    assert.match(row, /silver\.renamed_away\s*Not in the project .*Stop watching it/);
    if (axeSource) assert.deepStrictEqual(await axeFindings(page(), ".page.monitor"), []);
  } finally { mode = "full"; }
});

test("axe finds nothing serious on the list or a table", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  await rows();
  assert.deepStrictEqual(await axeFindings(page(), ".page.monitor"), []);
  await page().click(".page.monitor .dh-panel tbody tr:nth-child(1)");
  await page().waitForSelector(".page.monitor .dh-chart");
  assert.deepStrictEqual(await axeFindings(page(), ".page.monitor"), []);
});
