// Data mesh > Requests: Waiting on you, Sent by you and Everything, with no project open
// (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const decided = [];
const base = { detail: {}, reason: "", note: "", decided_by: "", decided_at: "" };
let requests = [
  { ...base, id: "r1", kind: "access", target: "sales/orders", project: "finance", requested_by: "ana@contoso.com", requested_at: "2026-10-26T09:00:00+00:00", status: "waiting", can_decide: true, mine: false },
  { ...base, id: "r2", kind: "key_table", target: "promotion", requested_by: "chris@contoso.com", requested_at: "2026-10-25T09:00:00+00:00", status: "waiting", can_decide: false, mine: true },
  { ...base, id: "r3", kind: "source", target: "sap", requested_by: "bo@contoso.com", requested_at: "2026-10-24T09:00:00+00:00", status: "approved", can_decide: false, mine: false, decided_by: "chris@contoso.com", decided_at: "2026-10-24T10:00:00+00:00" },
];

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    meta_project: () => ({ path: "", default_path: "C:/work/new", presets: { 3: [{ name: "bronze" }] } }),
    platform_requests: () => requests,
    platform_decide: ({ id, approve }) => {
      decided.push([id, approve]);
      requests = requests.map((r) => (r.id === id ? { ...r, status: approve ? "approved" : "declined", can_decide: false } : r));
      return { request: requests.find((r) => r.id === id) };
    },
    platform_withdraw: ({ id }) => { requests = requests.map((r) => (r.id === id ? { ...r, status: "withdrawn" } : r)); return {}; },
  } });
  await page().evaluate(() => goMeta("requests"));
  await page().waitForSelector(".mesh-requests table");
});
after(async () => { if (app) await app.close(); });

const rows = () => page().$$eval(".mesh-requests tbody tr", (r) => r.map((x) => x.dataset.request));

test("with no project open, Requests opens on what waits on you", { skip }, async () => {
  assert.ok(await page().$('.meta-groups .tab.active:has-text("Requests")'));
  assert.ok(await page().$('.meta-groups .tab:has-text("Home")'));
  assert.strictEqual(await page().getAttribute('.mesh-requests .seg button:has-text("Waiting on you")', "aria-pressed"), "true");
  assert.match(await page().textContent('.mesh-requests .seg button:has-text("Waiting on you")'), /\(1\)/);
  assert.deepStrictEqual(await rows(), ["r1"]);
  assert.match(await page().textContent(".how-it-works"), /access to a data product given in the catalog/);
  assert.ok(await page().$(".mesh-requests .preview-tag"));
});

test("Sent by you lists your own requests, which you can withdraw", { skip }, async () => {
  await page().click('.mesh-requests .seg button:has-text("Sent by you")');
  await page().waitForSelector('.mesh-requests tr[data-request="r2"]');
  assert.deepStrictEqual(await rows(), ["r2"]);
  assert.ok(await page().$('tr[data-request="r2"] button:has-text("Withdraw")'));
  await page().click('.mesh-requests .seg button:has-text("Everything")');
  await page().waitForSelector('.mesh-requests tr[data-request="r3"]');
  assert.deepStrictEqual(await rows(), ["r1", "r2", "r3"]);
});

test("Approve decides it here, as on Platform", { skip }, async () => {
  await page().click('.mesh-requests .seg button:has-text("Waiting on you")');
  await page().click('tr[data-request="r1"] button:has-text("Approve")');
  await page().waitForFunction(() => document.querySelector(".mesh-requests")?.textContent.includes("Nothing is waiting on you"));
  assert.deepStrictEqual(decided, [["r1", true]]);
  assert.deepStrictEqual(app.errors, []);
});
