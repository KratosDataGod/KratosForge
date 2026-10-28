// PB-059 review findings 2 and 3: a policy's exceptions show their status, one ending within 14 days
// warns on Data mesh and the bell, a platform admin ends one early with a reason after a red confirm,
// and Include ended lists the ended ones (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const ended = [];
const policyAsked = [];
let admin = true;

const definition = { name: "No PII in Dev", description: "", category: "classification", owner: "", severity: "high", enforcement: "warn",
  mandatory: false, scopes: ["enterprise", "domain", "project", "entity"], applies_to: {}, effective_from: "", expires_on: "",
  exception_eligible: true, remediation: "", rule: "", params: {} };
const inForce = { id: "r2", policy_id: "no-pii-in-dev", scope: "project", target: "hr-analytics", until: "2026-11-05", reason: "A masked copy isn't ready yet",
  requested_by: "lee@contoso.com", approved_by: "ana@contoso.com", active: true, ending: true, days_left: 9, ended_early: false };
const later = { id: "r7", policy_id: "no-pii-in-dev", scope: "project", target: "payroll", until: "2027-03-01", reason: "Payroll needs longer",
  requested_by: "lee@contoso.com", approved_by: "ana@contoso.com", active: true, ending: false, days_left: 125, ended_early: false };
const endedEarly = { id: "r1", policy_id: "no-pii-in-dev", scope: "domain", target: "sales", until: "2026-10-01", granted_until: "2026-12-01",
  reason: "Migration", requested_by: "lee@contoso.com", approved_by: "ana@contoso.com", active: false, ending: false, days_left: null,
  ended_early: true, ended_at: "2026-10-02T09:00:00Z", ended_by: "ana@contoso.com", end_reason: "Migration finished" };

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    platform_whoami: () => ({ admin, user: "ana@contoso.com", domains_owned: [] }),
    governance_overview: () => ({ me: { admin, domains_owned: [] },
      options: { categories: { classification: "Classification requirements" }, enforcements: ["advisory", "warn", "block"], severities: ["low", "medium", "high", "critical"] },
      policies: [{ id: "no-pii-in-dev", name: "No PII in Dev", category: "classification", current_version: 1, status: "published", assignments: 1 }],
      counts: { draft: 0, published: 1, deprecated: 0, retired: 0 }, domains: [{ id: "sales", name: "Sales" }], projects: ["hr-analytics"],
      exceptions: [inForce, later], problems: [] }),
    governance_policy: (p) => {
      policyAsked.push(p);
      return { id: "no-pii-in-dev", name: "No PII in Dev", status: "published", category: "classification", owner: "", current_version: 1, draft_version: null,
        current: { version: 1, status: "published", definition, published_at: "2026-10-01T00:00:00Z", published_by: "ana@contoso.com", intact: true },
        draft: null, versions: [{ version: 1, status: "published", definition, intact: true }], history: [],
        exceptions: p.include_ended ? [endedEarly, inForce, later] : [inForce, later] };
    },
    governance_applicability: () => ({ assignments: [], projects: [] }),
    governance_end_exception: (p) => { ended.push(p); return { ...inForce, active: false, ended_early: true }; },
    governance_exceptions_ending: () => ({ ending: [{ ...inForce, for_you: true }], warn_days: 14, problems: [] }),
    meta_project: () => ({ path: "C:/work/hr", name: "hr-analytics", version: "3", templates: {}, layers: [], entities: [], products: [], links: [],
      environments: {}, exceptions: [], exception_notices: [], key_tables: [], source_systems: [], errors: [], run_order: [], designations: [],
      project: { name: "hr-analytics", domain: "sales", subdomain: "", team: "" } }),
    meta_runs: () => [],
  } });
  await page().evaluate(() => { state.user = "ana@contoso.com"; openPolicyExceptions("no-pii-in-dev"); });
  await page().waitForSelector(".gov-exceptions tr[data-exception]");
});
after(async () => { if (app) await app.close(); });

test("each exception says whether it's in force or about to end, and How this works names every write", { skip }, async () => {
  assert.match(await page().textContent('.gov-exceptions tr[data-exception="r2"]'), /hr-analytics.*Ends in 9 days.*A masked copy isn't ready yet/s);
  assert.match(await page().textContent('.gov-exceptions tr[data-exception="r7"]'), /payroll.*In force/s);
  assert.strictEqual(await page().$('.gov-exceptions tr[data-exception="r1"]'), null, "ended ones stay hidden until asked for");
  const how = await page().textContent(".gov-exceptions .how-it-works");
  for (const part of ["catalog.policy_exception", "never deleted", "catalog.audit", "catalog.policy_event"]) assert.ok(how.includes(part), part);
});

test("End early asks why in a red confirm, and cancelling ends nothing", { skip }, async () => {
  await page().click('.gov-exceptions tr[data-exception="r2"] >> button:text-is("End early")');
  await page().waitForSelector("text=End the exception for hr-analytics now?");
  const ok = '[role=dialog] >> button:text-is("End early")';
  assert.match(await page().getAttribute(ok, "class"), /danger-solid/);
  assert.ok(await page().isDisabled(ok), "no reason, no end");
  assert.match(await page().textContent("[role=dialog]"), /isn't deleted.*audit.*history.*reason is visible to anyone who can read the policy's history/s);
  await page().click('[role=dialog] >> button:text-is("Cancel")');
  await page().waitForFunction(() => document.querySelector("[role=dialog]") === null);
  assert.deepStrictEqual(ended, []);
});

test("End early sends the exception and the reason", { skip }, async () => {
  await page().click('.gov-exceptions tr[data-exception="r2"] >> button:text-is("End early")');
  await page().fill('[role=dialog] [aria-label="Why it ends early (required)"]', "  The masked copy is ready  ");
  await page().click('[role=dialog] >> button:text-is("End early")');
  for (let i = 0; i < 50 && !ended.length; i++) await page().waitForTimeout(20);
  assert.deepStrictEqual(ended, [{ id: "r2", reason: "The masked copy is ready" }]);
});

test("Include ended lists exceptions that ended, with who ended one early and why", { skip }, async () => {
  await page().waitForSelector(".gov-exceptions tr[data-exception]");
  await page().check('.gov-exceptions label:has-text("Include ended") input');
  await page().waitForSelector('.gov-exceptions tr[data-exception="r1"]');
  assert.strictEqual(policyAsked.at(-1).include_ended, true);
  const row = await page().textContent('.gov-exceptions tr[data-exception="r1"]');
  assert.match(row, /sales.*Ended early.*ana@contoso\.com: Migration finished/s);
  assert.strictEqual(await page().$('.gov-exceptions tr[data-exception="r1"] >> button:text-is("End early")'), null);
});

test("someone who isn't a platform admin sees no End early", { skip }, async () => {
  admin = false;
  await page().evaluate(() => { platformPage.me = null; openPolicyExceptions("no-pii-in-dev"); });
  await page().waitForFunction(() => document.querySelector(".gov-exceptions tr[data-exception]") && !document.querySelector('.gov-exceptions button.danger'));
  assert.strictEqual(await page().$('.gov-exceptions >> button:text-is("End early")'), null);
  admin = true;
});

test("an exception ending within 14 days shows a banner on Data mesh and a notice on the bell", { skip }, async () => {
  await page().evaluate(() => goMeta("entities"));
  await page().waitForSelector(".gov-ending");
  assert.match(await page().textContent(".gov-ending"), /1 policy exception is about to end.*no-pii-in-dev for hr-analytics ends in 9 days/s);
  const items = await page().evaluate(() => activity.items.filter((i) => i.id.startsWith("policy-exception:r2:")).map((i) => i.title));
  assert.deepStrictEqual(items, ["A policy exception is about to end"]);
  await page().click('.gov-ending >> button:text-is("Review")');
  await page().waitForSelector('.gov-exceptions tr[data-exception="r2"]');
  assert.deepStrictEqual(app.errors, []);
});
