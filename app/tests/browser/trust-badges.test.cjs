// Governance steps 12 and 8: a data product's page shows its endorsement from Fabric's admin tenant
// scan (unknown until a Fabric admin has scanned) and its business terms linked to Purview
// (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;

const WS = "aaaaaaaa-0000-0000-0000-000000000001";
const LH = "bbbbbbbb-0000-0000-0000-000000000002";
const PID = "crm/customer_360";
const TERM_URL = "https://purview.microsoft.com/datacatalog/governance/main/catalog/glossary/term/123";
const product = { id: PID, project: "crm", name: "customer_360", description: "One row per customer", owner: "ana@example.com",
  access: "public", status: "published", version: "1", domain_id: "sales", ports: [{ port: "customers", env: "prod" }], consumers: [],
  can_link: false, why: "Your own product", linked: false };
const item = { item_id: LH, workspace_id: WS, workspace_name: "CRM Prod", item_type: "Lakehouse", item_name: "LH_Gold", env: "prod",
  endorsement: null, certified_by: "", scanned_at: "", scanned_by: "" };

let admin = false;
let older = false;
let endorsement = null;
let terms = [];
let more = [];
const trust = () => ({ product_id: PID, name: "customer_360", owner: "ana@example.com", admin, can_edit_terms: admin,
  items: [{ ...item, ...(endorsement || {}) }, ...more], terms });
const mocks = {
  platform_products: () => ({ project: null, products: [product] }),
  platform_product_trust: () => {
    if (older) throw new Error("LookupError: The platform can't show endorsement and business terms yet: UDF_Platform is older than this app. After an update, a platform admin runs Platform > Settings > Finish or repair once.");
    return trust();
  },
  platform_scan_endorsement: () => {
    endorsement = { endorsement: "Certified", certified_by: "Ana Lopez", scanned_at: new Date(Date.now() - 3 * 3600e3).toISOString(), scanned_by: "chris@example.com" };
    return { workspaces: 1, items: 1, recorded: 1, not_reported: [], trust: trust() };
  },
  platform_add_product_term: ({ term, url }) => { terms = [...terms, { term, url, added_by: "chris@example.com", added_at: "2026-10-24T10:00:00Z" }]; return trust(); },
  platform_remove_product_term: ({ term }) => { terms = terms.filter((t) => t.term !== term); return trust(); },
};

before(async () => {
  if (skip) return;
  app = await openApp({ mocks });
});
after(async () => { if (app) await app.close(); });

const openProduct = async () => {
  await page().evaluate(() => { productsPage.open = null; catalogPage.tab = "products"; go("onelake"); render(); });
  await page().click('a.product-open:has-text("customer_360")');
  await page().waitForSelector(".trust-endorsement .trust-item, .trust-endorsement .fail-text");
};
const callsOf = (method) => app.calls.filter((c) => c.method === method).map((c) => JSON.parse(JSON.stringify(c.params)));
const shellLinks = () => page().evaluate(() => window.__fdShellCalls.filter((c) => c.cmd === "open_link").map((c) => c.args.url));

test("everyone else sees a grey Endorsement unknown until a Fabric admin has scanned", { skip }, async () => {
  admin = false;
  await openProduct();
  assert.strictEqual((await page().textContent(".endorse-head")).trim(), "Endorsement unknown");
  assert.ok(await page().$(".endorse-head .pill-s.neu"));
  assert.strictEqual((await page().textContent(".trust-last-scan")).trim(), "", "no scan yet, so no scan date");
  const text = await page().textContent(".trust-endorsement");
  assert.match(text, /Fabric only shows endorsement to Fabric admins\. Once an admin has scanned, everyone sees the last scan's badge here\./);
  assert.strictEqual(await page().$(".trust-scan"), null, "no Scan now for someone who isn't a platform admin");
  assert.strictEqual(await page().$('button:has-text("Add a term")'), null);
  assert.match(await page().textContent(".trust-terms"), /The product's owner or a platform admin adds them/);
  await page().click('.trust-endorsement a:has-text("See it in Fabric")');
  assert.deepStrictEqual(await shellLinks(), [`https://app.fabric.microsoft.com/groups/${WS}/lakehouses/${LH}?experience=fabric-developer`]);
});

test("the page says how it works, naming every write", { skip }, async () => {
  const how = await page().textContent(".how-it-works");
  assert.match(how, /Scan now writes what the scan read to catalog\.endorsement/);
  assert.match(how, /Adding or removing a term writes catalog\.product_term/);
  assert.match(how, /Nothing reads Purview/);
  assert.ok(await page().$("h2.trust-name ~ .preview-tag"), "the page shows it is in Preview");
});

test("a platform admin scans; the badge, who certified it and the scan's age show, with no way to set it here", { skip }, async () => {
  admin = true;
  await openProduct();
  await page().click(".trust-scan");
  await page().waitForSelector('.endorse-head:has-text("Certified")');
  assert.deepStrictEqual(callsOf("platform_scan_endorsement"), [{ product_id: PID }]);
  const text = await page().textContent(".trust-endorsement");
  assert.match(text, /Certified by Ana Lopez in Fabric\./);
  assert.match(text, /From the tenant scan 3 h ago\./);
  assert.match(text, /To change it, open the lakehouse's settings in Fabric\. KratosForge can't set endorsement\./);
  assert.ok(await page().$(".endorse-head .pill-s.pass"));
  assert.ok(await page().$('.trust-endorsement a:has-text("Open in Fabric")'));
  assert.ok(await page().$(".trust-scan"), "Scan now stays to read it again");
  assert.strictEqual((await page().textContent(".trust-last-scan")).trim(), "Last tenant scan: 3 h ago");
});

test("items that differ show one Endorsement varies badge, each item's own below, and the newest scan's date", { skip }, async () => {
  more = [{ ...item, item_id: "cccccccc-0000-0000-0000-000000000003", item_name: "LH_Silver", env: "test" },
    { ...item, item_id: "dddddddd-0000-0000-0000-000000000004", item_name: "SM_Sales", item_type: "SemanticModel", endorsement: "Promoted",
      scanned_at: new Date(Date.now() - 30 * 3600e3).toISOString(), scanned_by: "chris@example.com" }];
  try {
    await openProduct();
    const head = page().locator(".endorse-head .endorse-badge");
    assert.strictEqual(await head.count(), 1);
    assert.strictEqual((await head.textContent()).trim(), "Endorsement varies");
    assert.ok(await page().$(".endorse-head .pill-s.neu.varies"));
    assert.match(await page().textContent(".trust-endorsement .trust-varies"), /Its items' endorsements differ/);
    const own = async (name) => (await page().textContent(`.trust-endorsement .trust-item:has(.mono:text-is("${name}")) .endorse-badge`)).trim();
    assert.strictEqual(await own("LH_Gold"), "Certified");
    assert.strictEqual(await own("LH_Silver"), "Endorsement unknown");
    assert.strictEqual(await own("SM_Sales"), "Promoted");
    assert.strictEqual((await page().textContent(".trust-last-scan")).trim(), "Last tenant scan: 3 h ago", "the newest of the items' scans");
  } finally {
    more = [];
  }
});

test("Add a term takes only a Purview https link, then lists it with Open in Purview", { skip }, async () => {
  await page().click('button:has-text("Add a term")');
  await page().waitForSelector('[role=dialog] >> text="Copy the term\'s address from Purview. KratosForge doesn\'t read Purview."');
  await page().fill('[aria-label="Term"]', "Active customer");
  for (const bad of ["http://purview.microsoft.com/term/1", "https://purview.microsoft.com.evil.example/term", "https://user@purview.microsoft.com/t"]) {
    await page().fill('[aria-label="Link to the term in Purview"]', bad);
    await page().click('[role=dialog] button:has-text("Add")');
    assert.match(await page().textContent("[role=dialog] .fail-text"), /an https link on purview\.microsoft\.com or web\.purview\.azure\.com/);
  }
  assert.deepStrictEqual(callsOf("platform_add_product_term"), [], "a bad link is never sent");
  await page().fill('[aria-label="Link to the term in Purview"]', TERM_URL);
  await page().click('[role=dialog] button:has-text("Add")');
  await page().waitForSelector('.trust-terms td:has-text("Active customer")');
  assert.deepStrictEqual(callsOf("platform_add_product_term"), [{ product_id: PID, term: "Active customer", url: TERM_URL }]);
  await page().click('.trust-terms a:has-text("Open in Purview")');
  assert.strictEqual((await shellLinks()).pop(), TERM_URL);
});

test("removing a term asks first, drawn as a danger", { skip }, async () => {
  await page().click('.trust-terms tr:has-text("Active customer") button:has-text("Remove")');
  await page().waitForSelector('[role=dialog] >> text="Remove Active customer?"');
  assert.ok(await page().$(".dialog-actions .danger-solid"));
  await page().click('.dialog-actions button:has-text("Cancel")');
  assert.deepStrictEqual(callsOf("platform_remove_product_term"), []);
  await page().click('.trust-terms tr:has-text("Active customer") button:has-text("Remove")');
  await page().click(".dialog-actions .danger-solid");
  await page().waitForSelector('.trust-terms >> text="No business terms yet. Add one with its link in Purview."');
  assert.deepStrictEqual(callsOf("platform_remove_product_term"), [{ product_id: PID, term: "Active customer" }]);
});

test("an older UDF_Platform says to run Finish or repair", { skip }, async () => {
  older = true;
  try {
    await openProduct();
    assert.match(await page().textContent(".trust-endorsement .fail-text"),
      /After an update, a platform admin runs Platform > Settings > Finish or repair once\./);
    assert.doesNotMatch(await page().textContent(".trust-endorsement"), /LookupError/);
  } finally {
    older = false;
  }
});

test("no page errors", { skip }, async () => {
  assert.deepStrictEqual(app.errors, []);
});
