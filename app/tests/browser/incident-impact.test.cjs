// Who's affected on an incident page (mesh Wave 1, P1): the data products built on the incident's
// table, the projects that read them, the people to tell, and Tell them after a confirm.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

let app;
const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const calls = [];
const INC = { id: "inc-1", env: "prod", title: "Orders late", state: "open", kind: "late", kind_label: "Late",
  workload: "meta:sales:silver.orders", name: "silver.orders", opened_at: "2026-10-26T08:00:00Z", opened_by: "agent-1",
  last_seen_at: "2026-10-26T09:00:00Z", occurrences: 1, notes: [] };
const IMPACT = { available: true, project: "sales", entity: "silver.orders", env: "prod", told: null,
  products: [{ id: "sales/orders", name: "orders", owner: "olu@contoso.com", domain_id: "sales", status: "published", ports: ["daily"], consumers: ["finance"] },
    { id: "sales/returns", name: "returns", owner: "", domain_id: "sales", status: "published", ports: ["all"], consumers: [] }],
  consumers: [{ project: "finance", alias: "orders", product_id: "sales/orders", port: "daily", linked_by: "ana@contoso.com" }],
  people: [{ upn: "ana@contoso.com", why: ["linked finance to sales/orders"] }, { upn: "olu@contoso.com", why: ["owns sales/orders"] }],
  unreachable: ["sales team"] };

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1440, height: 1000 },
    mocks: {
      readiness_check: () => [],
      incidents_list: () => ({ env: "prod", incidents: [INC, { ...INC, id: "inc-2", workload: null, kind: "reported", kind_label: "Reported" }], agents: { readable: true, agents: [] }, user: "chris@contoso.com" }),
      incident_impact: (p) => { calls.push(["impact", p]); return IMPACT; },
      incident_tell_impacted: (p) => { calls.push(["tell", p]); return { told: true, result: "sent", routes: ["default"], people: IMPACT.people }; },
    },
  });
});
after(async () => { if (app) await app.close(); });

test("the incident page shows who the incident reaches and tells them after a confirm", { skip }, async () => {
  await page().evaluate((inc) => { openIncident(inc); }, INC);
  const card = page().locator(".inc-impact");
  await card.locator(".inc-impact-products").waitFor();
  assert.deepStrictEqual(calls[0], ["impact", { env: "prod", id: "inc-1" }]);
  const text = await card.innerText();
  assert.match(text, /Who's affected/);
  assert.match(text, /2 data products are built on silver\.orders in Prod/);
  assert.match(text, /Read by finance/);
  assert.match(text, /No project links to it yet/);
  assert.match(text, /ana@contoso\.com · linked finance to sales\/orders/);
  assert.match(text, /Not an email, so not in the message: sales team/);
  await card.getByRole("button", { name: "Tell them", exact: true }).click();
  const dialog = page().locator(".dialog, [role=dialog]").last();
  assert.match(await dialog.innerText(), /Tell 2 people about Orders late\?[\s\S]*naming ana@contoso\.com, olu@contoso\.com/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "incident-impact.png") });
  await dialog.getByRole("button", { name: "Tell them" }).click();
  await page().waitForFunction(() => document.body.innerText.includes("Told 2 people in Teams."));
  assert.deepStrictEqual(calls.find((c) => c[0] === "tell"), ["tell", { env: "prod", id: "inc-1" }]);
  assert.deepStrictEqual(app.errors, []);
});

test("an incident that isn't about a table shows no Who's affected card", { skip }, async () => {
  await page().evaluate((inc) => { openIncident({ ...inc, id: "inc-2", workload: null }); }, INC);
  await page().locator(".inc-grid").waitFor();
  assert.strictEqual(await page().locator(".inc-impact").count(), 0);
});
