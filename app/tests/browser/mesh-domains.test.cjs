// Data mesh > Domains: the organization's domains, moved from Platform > Set up > Domains, with the
// projects and products in each (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
let admin = false;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    meta_project: () => ({ path: "", default_path: "C:/work/new", presets: { 3: [{ name: "bronze" }] } }),
    platform_whoami: () => ({ user: "chris@contoso.com", admin, domains_owned: [] }),
    platform_domains: () => [{ id: "sales", name: "Sales", owners: "olu@contoso.com", fabric_domain: "f-sales" },
      { id: "reseller", name: "Reseller", parent_id: "sales", owners: "" }, { id: "hr", name: "HR", owners: "" }],
    mesh_map: () => ({ me: "", problem: "", domains: [], links: [],
      projects: [{ name: "crm", domain_id: "sales" }, { name: "partners", domain_id: "sales", subdomain_id: "reseller" }],
      products: [{ id: "crm/c360", project: "crm", name: "c360" }, { id: "partners/resellers", project: "partners", name: "resellers" }] }),
  } });
});
after(async () => { if (app) await app.close(); });

const row = (id) => page().textContent(`.mesh-org-domains tr[data-domain="${id}"]`);

test("with no project open, Domains lists every domain with its projects and products", { skip }, async () => {
  await page().evaluate(() => goMeta("domains"));
  await page().waitForSelector('.mesh-org-domains tr[data-domain="hr"]');
  assert.ok(await page().$('.meta-groups .tab.active:has-text("Domains")'));
  assert.deepStrictEqual(await page().$$eval(".meta-tabs button", (b) => b.map((x) => x.textContent.replace("Preview", "").trim())), ["Domains", "Map"],
    "This project needs a project, so it waits for one");
  assert.match(await row("sales"), /Sales.*olu@contoso\.com\s*2\s*2\s*From Fabric/);
  assert.match(await row("reseller"), /Reseller.*subdomain.*1\s*1\s*Only here/);
  assert.match(await row("hr"), /HR.*not set\s*0\s*0/);
  assert.ok(await page().$('.mesh-org-domains tr[data-domain="sales"] button:has-text("Access groups")'));
  assert.strictEqual(await page().$('.page-head button:has-text("New domain")'), null, "only platform admins edit domains");
  assert.ok(await page().$('.meta-groups .tab.active:has-text("Domains") .preview-tag'), "the tab carries the Preview tag");
  assert.strictEqual(await page().$(".meta-tabs .preview-tag"), null, "once, on the tab, not again on Domains and Map");
});

test("a platform admin imports, adds and edits domains here", { skip }, async () => {
  admin = true;
  await page().evaluate(() => { platformPage.me = null; goMeta("domains"); });
  await page().waitForSelector('.page-head button:has-text("Import from Fabric")');
  assert.ok(await page().$('.page-head button:has-text("New domain")'));
  assert.ok(await page().$('.mesh-org-domains tr[data-domain="sales"] button:text-is("Edit owners")'));
  assert.ok(await page().$('.mesh-org-domains tr[data-domain="hr"] button:text-is("Edit")'));
});

test("Platform no longer has Domains, and its old links open Data mesh > Domains", { skip }, async () => {
  await page().evaluate(() => goPlatform("domains"));
  await page().waitForSelector(".mesh-org-domains");
  assert.strictEqual(await page().evaluate(() => state.view), "metadata");
  await page().evaluate(() => { platformPage.tab = "domains"; go("platform"); });
  await page().waitForFunction(() => state.view === "metadata" && document.querySelector(".mesh-org-domains"));
  assert.strictEqual(await page().evaluate(() => platformPage.tab), "overview");
  assert.deepStrictEqual(app.errors, []);
});
