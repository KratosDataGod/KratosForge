// Governance plan step 5: Platform > Set up > Domains > New domain creates it in Fabric, and says plainly when
// this account can't (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
let refuse = false;
const calls = [];
const overview = (p) => ({ env: p.env, counts: {}, landing: { sources: [] }, problems: [], me: { admin: true }, needs_you: [], mine: [],
  status: { source: "settings", default_names: {}, environments: {} }, recent: [] });

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { platform_whoami: () => ({ admin: true }), platform_overview: overview, readiness_check: () => [],
    landing_status: () => ({ sources: [] }),
    platform_domains: () => [{ id: "sales", name: "Sales", fabric_domain: "f-sales", owners: "" }, { id: "hr", name: "HR", owners: "" }],
    platform_create_domain: (p) => {
      if (refuse) throw new Error("Only a Fabric administrator can create domains in Fabric, and Fabric refused this account. Nothing was changed.");
      calls.push(["create", p]); return [];
    },
    platform_save_domain: (p) => { calls.push(["save", p]); return []; } } });
});
after(async () => { if (app) await app.close(); });

const newDomain = async (name, parent = "") => {
  await page().evaluate(() => { state.user = "chris@contoso.com"; goPlatform("domains"); });
  await page().click('button:has-text("New domain")');
  await page().fill("[role=dialog] label:has-text('Name') input", name);
  await page().selectOption("[role=dialog] select", parent);
};

test("a new domain is created in Fabric by default", { skip }, async () => {
  await newDomain("Finance");
  assert.ok(await page().isChecked("[role=dialog] input[type=checkbox]"));
  await page().click('[role=dialog] button:has-text("Create in Fabric")');
  await page().waitForFunction(() => !document.querySelector("[role=dialog]"));
  assert.deepStrictEqual(JSON.parse(JSON.stringify(calls.pop())), ["create", { domain: { name: "Finance", parent_id: "", description: "", owners: "" }, in_fabric: true }]);
});

test("a subdomain of a domain that isn't in Fabric can only be saved in the platform", { skip }, async () => {
  await newDomain("Payroll", "hr");
  assert.ok(await page().isDisabled("[role=dialog] input[type=checkbox]"));
  assert.match(await page().textContent("[role=dialog]"), /HR isn't in Fabric/);
  await page().click('[role=dialog] button:has-text("Save")');
  await page().waitForFunction(() => !document.querySelector("[role=dialog]"));
  assert.strictEqual(calls.pop()[0], "save");
});

test("someone who isn't a Fabric admin is told so and can keep it in the platform only", { skip }, async () => {
  refuse = true;
  await newDomain("Legal");
  await page().click('[role=dialog] button:has-text("Create in Fabric")');
  await page().waitForSelector('[role=dialog] >> text="Legal wasn\'t created in Fabric"');
  assert.match(await page().textContent("[role=dialog]"), /Only a Fabric administrator/);
  await page().click('[role=dialog] button:has-text("Save in the platform only")');
  await page().waitForFunction(() => !document.querySelector("[role=dialog]"));
  assert.deepStrictEqual(JSON.parse(JSON.stringify(calls.pop())), ["save", { domain: { name: "Legal", parent_id: "", description: "", owners: "" } }]);
  refuse = false;
});
