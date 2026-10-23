// Governance plan step 10: Platform > Requests' New request dialog for a workspace, workspace access,
// a domain and a policy exception, their rows, and the confirmation before an approval makes
// something in Fabric (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const CAP = "11111111-2222-3333-4444-555555555555";
const GROUP = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
const sent = [];
const decided = [];
const requests = [
  { id: "r1", kind: "workspace", target: "Sales Forecasting - Dev", status: "waiting", can_decide: true, requested_by: "ana@contoso.com",
    requested_at: "2026-10-22T10:00:00Z", reason: "Forecast models for the Q1 plan",
    detail: { name: "Sales Forecasting - Dev", capacity_id: CAP, capacity_name: "Shared F64", domain_id: "sales", domain_name: "Sales",
      admins: [{ type: "Group", id: GROUP, email: "", name: "Sales Engineers" }] } },
  { id: "r2", kind: "exception", target: "no-pii-in-dev", status: "waiting", can_decide: true, requested_by: "lee@contoso.com",
    requested_at: "2026-10-22T11:00:00Z", reason: "A masked copy isn't ready yet",
    detail: { policy: "no-pii-in-dev", policy_name: "No PII in Dev", scope: "project", scope_target: "hr-analytics", until: "2026-11-30" } },
  { id: "r3", kind: "workspace_access", target: "Sales Prod", status: "waiting", can_decide: true, requested_by: "lee@contoso.com",
    requested_at: "2026-10-22T12:00:00Z", reason: "",
    detail: { workspace_id: "w1", workspace_name: "Sales Prod", role: "Contributor", principal: { type: "User", email: "lee@contoso.com", name: "lee@contoso.com" } } },
  { id: "r4", kind: "domain", target: "emea", status: "approved", can_decide: false, requested_by: "lee@contoso.com",
    requested_at: "2026-10-21T12:00:00Z", decided_by: "ana@contoso.com", decided_at: "2026-10-21T13:00:00Z",
    detail: { name: "EMEA", parent_id: "sales", owners: "ana@contoso.com" } },
];

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    platform_whoami: () => ({ admin: true, user: "ana@contoso.com" }),
    platform_requests: () => requests,
    platform_request: (p) => { sent.push(p); return { id: "new", status: "waiting", ...p }; },
    platform_decide: (p) => { decided.push(p); return { ...requests.find((r) => r.id === p.id), status: "approved", next: ["Put it in the Sales domain in Fabric's admin portal"] }; },
    platform_domains: () => [{ id: "sales", name: "Sales" }, { id: "emea", name: "EMEA", parent_id: "sales" }],
    list_capacities: () => [{ id: CAP, displayName: "Shared F64" }],
    list_workspaces: () => [{ id: "w1", displayName: "Sales Prod" }],
    governance_overview: () => ({ policies: [{ id: "no-pii-in-dev", name: "No PII in Dev", current_version: 1, status: "published" }],
      domains: [{ id: "sales", name: "Sales" }], projects: ["hr-analytics", "crm"], exceptions: [], problems: [] }),
  } });
  await page().evaluate(() => { state.user = "ana@contoso.com"; goPlatform("requests"); });
  await page().waitForSelector('tr[data-request="r1"]');
});
after(async () => { if (app) await app.close(); });

test("the request rows say what each new kind asks for", { skip }, async () => {
  const row = async (id) => page().textContent(`tr[data-request="${id}"]`);
  assert.match(await row("r1"), /Workspace.*Sales Forecasting - Dev.*Sales domain, Shared F64, admins Sales Engineers/s);
  assert.match(await row("r2"), /Exception.*No PII in Dev for project hr-analytics.*Until 2026-11-30/s);
  assert.match(await row("r3"), /Workspace access.*Sales Prod Contributor for lee@contoso.com/s);
  assert.match(await row("r1"), /Approve and create/);
});

test("How this works names each write an approval makes", { skip }, async () => {
  const text = await page().textContent(".how-it-works");
  for (const part of ["a workspace in Fabric on its capacity", "a role in a workspace", "a domain saved in the platform",
    "an exception recorded in the platform", "stays waiting", "no email yet"]) assert.ok(text.includes(part), part);
});

test("approving a workspace says what will be made, as you, and cancelling makes nothing", { skip }, async () => {
  await page().click('tr[data-request="r1"] >> button:text-is("Approve and create")');
  await page().waitForSelector("text=Approve and create Sales Forecasting - Dev?");
  const text = await page().textContent("[role=dialog]");
  assert.match(text, /As you, KratosForge creates the workspace on Shared F64 and makes Sales Engineers its admins\. Then the request is approved\. If any step fails, it stays waiting and says which\./);
  assert.match(text, /put it in the Sales domain in Fabric's admin portal/);
  await page().click('[role=dialog] >> button:text-is("Cancel")');
  assert.deepStrictEqual(decided, []);
  await page().click('tr[data-request="r1"] >> button:text-is("Approve and create")');
  await page().click('[role=dialog] >> button:text-is("Create and approve")');
  await page().waitForFunction(() => document.querySelector("[role=dialog]") === null);
  for (let i = 0; i < 50 && !decided.length; i++) await page().waitForTimeout(20);
  assert.strictEqual(decided[0].id, "r1");
  assert.strictEqual(decided[0].approve, true);
});

test("approving workspace access and an exception is confirmed too", { skip }, async () => {
  await page().waitForSelector('tr[data-request="r3"]');
  await page().click('tr[data-request="r3"] >> button:text-is("Approve")');
  await page().waitForSelector("text=Approve Contributor on Sales Prod?");
  assert.match(await page().textContent("[role=dialog]"), /As you, KratosForge gives lee@contoso.com the Contributor role in Sales Prod/);
  await page().click('[role=dialog] >> button:text-is("Cancel")');
  await page().click('tr[data-request="r2"] >> button:text-is("Approve")');
  await page().waitForSelector("text=Grant an exception to No PII in Dev?");
  assert.match(await page().textContent("[role=dialog]"), /Excepted until 2026-11-30.*ends on its own/s);
  await page().click('[role=dialog] >> button:text-is("Cancel")');
});

test("New request picks the kind, tags the new ones Preview and keeps the older four", { skip }, async () => {
  await page().click('button:text-is("New request")');
  await page().waitForSelector('[role=dialog] >> text="A new workspace"');
  const kinds = await page().$$eval(".req-kind", (bs) => bs.map((b) => b.textContent));
  assert.deepStrictEqual(kinds.map((k) => k.split(" Preview")[0].trim()),
    ["A new workspace", "Access to a workspace", "A domain", "An exception to a policy"]);
  assert.ok(kinds.every((k) => k.includes("Preview")));
  const also = await page().textContent(".req-dialog .row");
  for (const k of ["Key table", "Source", "Product access", "Shortcut"]) assert.ok(also.includes(k), k);
});

test("a workspace request sends its name, domain, capacity and admins", { skip }, async () => {
  await page().waitForSelector('[aria-label="Workspace name"]');
  await page().fill('[aria-label="Workspace name"]', "Sales Forecasting - Dev");
  await page().selectOption('[aria-label="Domain"]', "sales");
  await page().fill('[aria-label="Admins"]', `${GROUP}, lee@contoso.com`);
  await page().fill('[aria-label="Why"]', "Forecast models");
  await page().click('[role=dialog] >> button:text-is("Send request")');
  for (let i = 0; i < 50 && !sent.length; i++) await page().waitForTimeout(20);
  assert.deepStrictEqual(sent[0], { kind: "workspace", target: "Sales Forecasting - Dev", reason: "Forecast models",
    detail: { name: "Sales Forecasting - Dev", domain_id: "sales", capacity_id: CAP, capacity_name: "Shared F64", admins: [GROUP, "lee@contoso.com"] } });
});

test("an exception request names the policy, the place, the date and why", { skip }, async () => {
  await page().waitForFunction(() => document.querySelector("[role=dialog]") === null);
  await page().click('button:text-is("New request")');
  await page().click('.req-kind[data-kind="exception"]');
  await page().waitForSelector('[aria-label="Policy"]');
  await page().selectOption('[aria-label="Project"]', "hr-analytics");
  await page().fill('[aria-label="Until"]', "2026-11-30");
  await page().fill('[aria-label="Why"]', "A masked copy isn't ready yet");
  await page().click('[role=dialog] >> button:text-is("Send request")');
  for (let i = 0; i < 50 && sent.length < 2; i++) await page().waitForTimeout(20);
  assert.deepStrictEqual(sent[1], { kind: "exception", target: "no-pii-in-dev", reason: "A masked copy isn't ready yet",
    detail: { policy: "no-pii-in-dev", scope: "project", scope_target: "hr-analytics", until: "2026-11-30" } });
  assert.deepStrictEqual(app.errors, []);
});
