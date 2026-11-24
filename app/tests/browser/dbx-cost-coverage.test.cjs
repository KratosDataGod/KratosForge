// Optimize > Cost: Databricks DBUs (dbx-cost.js) beyond optimize-databricks-cost.test.cjs: picking
// another connection and how many days to read, a profile that allows Databricks SQL reading without
// the confirm (and failing), and a confirmed read that fails while the Cost tab was drawn again.
// engine_connections and the billing calls are mocked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const conn = (key, label, wh) => ({ key, label, source: "cli", host: `https://adb-${key.length}.azuredatabricks.net`, warehouse_id: wh,
  cluster_id: "", catalog: "", schema: "", remote_compute: "ask" });
const RESULT = (days, wh) => ({ ok: true, schema: "databricks-billing@1", days, warehouse_id: wh, read_at: new Date().toISOString(),
  total_dbu: 1200, rows: [] });

let app;
let billing;   // answers databricks_billing
let saved;     // what databricks_billing_last gives, by connection
let gate;      // when set, a confirmed read waits for it
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["app_settings", "cost_report", "optimize_status"],
    mocks: {
      engine_connections: () => ({ connections: [conn("cli:dev", "dev (Databricks CLI)", "wh-dev"), conn("cli:prod", "prod (Databricks CLI)", "wh-prod"),
        { key: "env:x", label: "no host", source: "env", host: "" }] }),
      databricks_billing_last: ({ connection }) => saved[connection] || {},
      databricks_billing: async (p) => {
        if (p.confirmed && gate) await gate.promise;
        return billing(p);
      },
    },
  });
  await page().waitForFunction(() => typeof databricksCostSection === "function" && !!document.querySelector(".rail-item"));
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  saved = {};
  gate = null;
  await page().evaluate(() => {
    Object.assign(dbxCost, { connection: "", days: 7, reading: false });
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    optimizePage.tab = "cost"; go("optimize");
  });
  await section().getByText("Not read yet on this PC.").waitFor();
});

const section = () => page().locator("#view .dbx-cost");
const billingCalls = () => app.calls.filter((c) => c.method === "databricks_billing").map((c) => c.params);
const toastSays = (text) => page().waitForFunction((t) => document.getElementById("toast").textContent === t, text);

test("another connection and 30 days read straight away when the profile allows Databricks SQL", { skip }, async () => {
  billing = (p) => { saved[p.connection] = RESULT(p.days, p.warehouse_id); return { ok: true, needs_confirm: false }; };
  assert.deepStrictEqual(await section().getByLabel("Databricks connection").locator("option").allInnerTexts(),
    ["dev (Databricks CLI)", "prod (Databricks CLI)"], "a connection with no host isn't offered");
  await section().getByLabel("Databricks connection").selectOption("cli:prod");
  await page().waitForFunction(() => document.querySelector('.dbx-cost input[aria-label="SQL warehouse id"]')?.value === "wh-prod");
  await section().getByLabel("Billing days").selectOption("30");
  await section().getByRole("button", { name: "Read Databricks billing…" }).click();
  await section().locator(".dbx-cost-result").waitFor();
  assert.match(await section().locator(".dbx-cost-result").textContent(), /^1,200 DBU in the last 30 days · read .* on wh-prod/);
  assert.strictEqual(await section().locator(".dbx-cost-result p.muted-text").textContent(), "No DBUs billed in that time.");
  assert.strictEqual(await page().locator("#dialog-root .dialog").count(), 0, "no confirm");
  assert.deepStrictEqual(billingCalls().at(-1), { connection: "cli:prod", warehouse_id: "wh-prod", days: 30 });
});

test("an allowed read that fails says why and still shows the last read", { skip }, async () => {
  billing = () => ({ ok: false, needs_confirm: false, error: "The warehouse wh-dev doesn't exist." });
  await section().getByRole("button", { name: "Read Databricks billing…" }).click();
  await toastSays("Billing wasn't read: The warehouse wh-dev doesn't exist.");
  await section().getByText("Not read yet on this PC.").waitFor();
});

test("a confirmed read that fails after the Cost tab was drawn again says why on the section now shown", { skip }, async () => {
  billing = (p) => (p.confirmed ? { ok: false, error: "Query timed out after 120 s." }
    : { needs_confirm: true, warehouse: { id: "wh-dev", name: "Shared SQL", state: "RUNNING", auto_stop_mins: 0 } });
  let open;
  gate = { promise: new Promise((r) => { open = r; }) };
  await section().getByRole("button", { name: "Read Databricks billing…" }).click();
  const confirm = page().locator("#dialog-root .dialog");
  assert.match(await confirm.textContent(), /It is running now, with no auto-stop\./);
  await confirm.getByRole("button", { name: "Run the query" }).click();
  await section().getByRole("button", { name: "Reading…" }).waitFor();
  await page().evaluate(() => { window.__oldDbx = document.querySelector(".dbx-cost"); render(); });
  await page().waitForFunction(() => document.querySelector(".dbx-cost") !== window.__oldDbx);
  open();
  await toastSays("Billing wasn't read: Query timed out after 120 s.");
  await section().getByRole("button", { name: "Read Databricks billing…" }).waitFor();
  assert.strictEqual(await page().evaluate(() => window.__oldDbx.isConnected), false);
});
