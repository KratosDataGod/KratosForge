// Best practices: checks and templates (Preview): Governance > Templates, Check now on the open project,
// and a data product's checklist (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const options = { categories: { naming: "Naming standards", ownership: "Ownership and stewardship", classification: "Classification requirements", contract: "Data contract requirements" },
  statuses: ["draft", "published", "deprecated", "retired"], severities: ["low", "medium", "high", "critical"],
  enforcements: ["advisory", "warn", "block"], scopes: ["enterprise", "domain", "project", "entity"], stricter: ["higher", "lower", "superset", "subset", "true", "fixed"] };
const def = (name, category, rule) => ({ name, category, rule, description: `${name}.`, severity: "medium", enforcement: "warn",
  scopes: ["enterprise"], applies_to: {}, params: {}, mandatory: false, exception_eligible: true, remediation: "", owner: "" });
let templates = [
  { id: "table-names", name: "Table names", category: "naming", description: "Gold tables are snake_case and start dim_ or fact_.", builtin: true, can_edit: false, definition: def("Table names", "naming", "naming.table_name") },
  { id: "product-owner", name: "Every product has an owner", category: "ownership", description: "A published product names an owner and a steward.", builtin: true, can_edit: false, definition: def("Every product has an owner", "ownership", "ownership.product_owner") },
  { id: "sensitive-columns-labelled", name: "Sensitive columns are labelled", category: "classification", description: "Columns marked PII or PHI carry a sensitivity label.", builtin: true, can_edit: false, definition: def("Sensitive columns are labelled", "classification", "classification.sensitive_columns") },
  { id: "data-contract", name: "Data contract", category: "contract", description: "Fresh within N hours, row count checked, business terms linked.", builtin: true, can_edit: false, definition: def("Data contract", "contract", "contract.freshness") },
  { id: "no-pii-in-dev", name: "No PII in Dev", category: "classification", description: "", builtin: false, can_edit: true, created_by: "ana@contoso.com", definition: def("No PII in Dev", "classification", "") },
];
let last = null;
const result = () => ({ project: "Sales Forecasting", checked_at: new Date(Date.now() - 5 * 60e3).toISOString(), where: "this PC",
  enforcement: "Warn only: nothing is blocked.", problems: [], inactive: [], offline: true,
  package_note: "Checked offline with the policy package taken 2 days ago (2026-10-26 09:00 UTC) from the platform on this PC (platform-prod.sqlite): crm.json.",
  counts: { to_look_at: 2, unknown: 0, excepted: 0, passing: 1, policies: 3 },
  policies: [
    { id: "table-names", name: "Table names", enforcement: "warn", severity: "medium", status: "findings", checked: 3, passed: 2, unknown: [], not_run: [], missing: [], rules: ["naming.table_name"],
      findings: [{ rule_id: "naming.table_name", severity: "Warning", rule: "Table names", category: "naming", object_type: "entity", object: "gold.customer_orders",
        description: "customer_orders doesn't match the naming pattern.", fix: { action: "open_entity", label: "Open the entity", entity: "gold.customer_orders" } }] },
    { id: "labels", name: "Sensitive columns are labelled", enforcement: "warn", severity: "high", status: "findings", checked: 1, passed: 0, unknown: [], not_run: [], missing: [], rules: ["classification.sensitive_columns"],
      findings: [{ rule_id: "classification.sensitive_columns", severity: "Error", rule: "Sensitive columns are labelled", category: "classification", object_type: "column",
        object: "silver.patients#ssn", description: "ssn is marked PII but has no label.",
        fix: { action: "label", label: "Label it", entity: "silver.patients", column: "ssn", labels: ["confidential", "restricted"] } }] },
    { id: "owners", name: "Every product has an owner", enforcement: "warn", severity: "high", status: "pass", checked: 4, passed: 4, findings: [], unknown: [], not_run: [], missing: [], rules: ["ownership.product_owner"] },
  ] });

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    platform_whoami: () => ({ admin: true, user: "ana@contoso.com", domains_owned: [] }),
    governance_overview: () => ({ me: { admin: true, domains_owned: [] }, options, policies: [{ id: "labels", name: "Sensitive columns are labelled", current_version: 1, status: "published" }],
      counts: { draft: 0, published: 1, deprecated: 0, retired: 0 }, domains: [], projects: ["crm"], exceptions: [], problems: [] }),
    governance_templates: () => ({ templates, can_add: true, authors: "admins", admin: true }),
    governance_template_use: (p) => ({ id: p.id, name: p.name }),
    governance_template_delete: ({ id }) => { templates = templates.filter((t) => t.id !== id); return { id, deleted: true }; },
    governance_template_save: ({ template }) => ({ id: "x", name: template.name }),
    governance_policy: ({ id }) => ({ id, name: "Sales contract", status: "draft", category: "contract", versions: [], current: null, draft: null, history: [], current_version: null, draft_version: 1 }),
    governance_last_check: () => ({ project: "Sales Forecasting", result: last }),
    governance_check_now: () => { last = result(); return last; },
    mesh_save_about: () => ({}),
    platform_products: () => ({ project: null, products: [{ id: "crm/customer_360", project: "crm", name: "customer_360", description: "", owner: "me", access: "public", status: "published", version: "1", ports: [], consumers: [] }] }),
    platform_product_trust: () => ({ product_id: "crm/customer_360", name: "customer_360", owner: "me", admin: false, can_edit_terms: false, items: [], terms: [] }),
    governance_product_checklist: () => ({ product_id: "crm/customer_360", project: "crm", source: { template: "data-contract", name: "Data contract", builtin: true }, open_project: true, problems: [],
      rows: [{ rule_id: "ownership.product_owner", label: "Owner and steward", mark: "pass", detail: "Owner me, steward bo." },
        { rule_id: "contract.description", label: "Description", mark: "fail", detail: "Has no description." },
        { rule_id: "contract.terms_linked", label: "Business terms linked", mark: "fail", detail: "Links no business term." },
        { rule_id: "contract.freshness", label: "Fresh within 24 h", mark: "pass", detail: "Last loaded 2026-10-24 08:00 UTC in Prod." },
        { rule_id: "contract.endorsed", label: "Endorsement", mark: "unknown", detail: "Unknown: needs a tenant scan." }] }),
  } });
});
after(async () => { if (app) await app.close(); });

const callsOf = (method) => app.calls.filter((c) => c.method === method).map((c) => JSON.parse(JSON.stringify(c.params)));
const openView = async (view, selector) => {
  await page().evaluate((v) => { govPage.view = v; govPage.open = null; goPlatform("governance"); }, view);
  await page().waitForSelector(selector);
};

test("Templates lists built-in and your own templates, each with Use", { skip }, async () => {
  await openView("templates", ".bp-templates");
  const rows = await page().$$eval(".bp-templates tr[data-template]", (trs) => trs.map((tr) => [tr.dataset.template, tr.querySelector(".bp-source").textContent]));
  assert.deepStrictEqual(rows, [["table-names", "Built in"], ["product-owner", "Built in"], ["sensitive-columns-labelled", "Built in"], ["data-contract", "Built in"], ["no-pii-in-dev", "Yours"]]);
  const text = await page().textContent(".bp-templates");
  for (const part of ["Table names", "(Naming)", "(Ownership)", "(Classification)", "(Data contract)", "Platform admins choose who can add templates."]) assert.ok(text.includes(part), part);
  assert.ok(await page().$('.bp-templates button:has-text("New template")'));
  assert.strictEqual(await page().$$eval(".bp-templates tr[data-template] button:text-is('Use')", (b) => b.length), 5);
  assert.strictEqual(await page().$('tr[data-template="table-names"] button:has-text("Delete")'), null, "built-in templates are read-only");
});

test("How this works names every write", { skip }, async () => {
  const how = await page().textContent(".how-it-works");
  for (const part of ["Template create, edit and delete write catalog.policy_template", "Use writes a policy draft",
    "Check now writes its last result, to governance_checks.json on this PC", "the project's policy package, to governance_packages/<project>.json on this PC", "Label it writes the column's classification to catalog.enrichment"]) assert.ok(how.includes(part), part);
});

test("Use makes a policy draft from the template and opens it", { skip }, async () => {
  await page().click('tr[data-template="data-contract"] button:text-is("Use")');
  await page().fill('[aria-label="Policy id"]', "sales-contract");
  await page().fill('[aria-label="Policy name"]', "Sales contract");
  await page().click('[role=dialog] button:has-text("Make the draft")');
  await page().waitForSelector('h2:has-text("Sales contract")');
  assert.deepStrictEqual(callsOf("governance_template_use"), [{ template: "data-contract", id: "sales-contract", name: "Sales contract" }]);
});

test("deleting your template asks first, drawn as a danger", { skip }, async () => {
  await openView("templates", ".bp-templates");
  await page().click('tr[data-template="no-pii-in-dev"] button:has-text("Delete")');
  await page().waitForSelector('[role=dialog] >> text="Delete the template No PII in Dev?"');
  assert.ok(await page().$(".dialog-actions .danger-solid"));
  await page().click('.dialog-actions button:has-text("Cancel")');
  assert.deepStrictEqual(callsOf("governance_template_delete"), []);
  await page().click('tr[data-template="no-pii-in-dev"] button:has-text("Delete")');
  await page().click(".dialog-actions .danger-solid");
  await page().waitForFunction(() => !document.querySelector('tr[data-template="no-pii-in-dev"]'));
  assert.deepStrictEqual(callsOf("governance_template_delete"), [{ id: "no-pii-in-dev" }]);
});

test("Check now runs on this PC, says how many to look at and that nothing is blocked", { skip }, async () => {
  await openView("check", ".bp-check-head");
  assert.match(await page().textContent(".bp-summary"), /Sales Forecasting: not checked yet/);
  await page().click('.bp-check-head button:has-text("Check now")');
  await page().waitForSelector('.bp-summary:has-text("2 to look at")');
  assert.strictEqual((await page().textContent(".bp-summary")).trim(), "Sales Forecasting: 2 to look at");
  assert.strictEqual((await page().textContent(".bp-when")).trim(), "Checked on this PC 5 min ago. Warn only: nothing is blocked. "
    + "Checked offline with the policy package taken 2 days ago (2026-10-26 09:00 UTC) from the platform on this PC (platform-prod.sqlite): crm.json.");
  const names = await page().textContent('[data-policy="table-names"]');
  assert.match(names, /Table names/);
  assert.match(names, /gold\.customer_orders customer_orders doesn't match the naming pattern\./);
  assert.ok(await page().$('[data-policy="table-names"] button:has-text("Open the entity")'), "no rename: a link to the entity");
  const pass = await page().textContent('[data-policy="owners"]');
  assert.match(pass, /✓/);
  assert.match(pass, /4 of 4 pass/);
});

test("Label it asks first, says what changes, then saves the label", { skip }, async () => {
  await page().click('[data-policy="labels"] button:has-text("Label it")');
  await page().waitForSelector(".bp-label-what");
  assert.match(await page().textContent(".bp-label-what"), /Sets the classification of ssn in silver\.patients in the platform's catalog \(catalog\.enrichment\)\. The project's files don't change\./);
  await page().selectOption('[aria-label="Label"]', "restricted");
  await page().click('.dialog-actions button:text-is("Label it")');
  for (let i = 0; i < 50 && !callsOf("mesh_save_about").length; i++) await page().waitForTimeout(20);
  assert.deepStrictEqual(callsOf("mesh_save_about"), [{ name: "silver.patients", column: "ssn", fields: { classification: "restricted" } }]);
});

test("Ask for an exception opens the exception request with the policy and project filled in", { skip }, async () => {
  await page().waitForSelector('[data-policy="labels"] button:has-text("Ask for an exception")');
  await page().click('[data-policy="labels"] button:has-text("Ask for an exception")');
  await page().waitForSelector('[aria-label="Policy"]');
  assert.strictEqual(await page().inputValue('[aria-label="Policy"]'), "labels");
  assert.strictEqual(await page().inputValue('[aria-label="Scope"]'), "project");
  assert.strictEqual(await page().inputValue('[aria-label="Project"]'), "Sales Forecasting");
  await page().click('.dialog-actions button:has-text("Cancel")');
});

test("a data product's page shows its checklist from the contract template", { skip }, async () => {
  await page().evaluate(() => { productsPage.open = null; catalogPage.tab = "products"; go("onelake"); render(); });
  await page().click('a.product-open:has-text("customer_360")');
  await page().waitForSelector(".bp-checklist .bp-rows");
  assert.deepStrictEqual(callsOf("governance_product_checklist"), [{ product_id: "crm/customer_360" }]);
  const marks = await page().$$eval(".bp-checklist li", (lis) => lis.map((li) => [li.querySelector(".bp-mark").textContent, li.querySelector("b").textContent]));
  assert.deepStrictEqual(marks, [["✓", "Owner and steward"], ["✗", "Description"], ["✗", "Business terms linked"], ["✓", "Fresh within 24 h"], ["!", "Endorsement"]]);
  const text = await page().textContent(".bp-checklist");
  assert.match(text, /From the built-in Data contract template: no contract policy is in force for crm\./);
  assert.match(text, /Unknown: needs a tenant scan\./);
});
