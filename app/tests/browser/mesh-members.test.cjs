// Data mesh > Domains > This project: the project's members, and its waiting ask to join a domain
// (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
let members = [{ user: "chris@contoso.com", role: "owner" }, { user: "dee@contoso.com", role: "owner" }, { user: "lee@contoso.com", role: "member" }];
let ask = { id: "r1", kind: "join_domain", target: "orders", status: "waiting", requested_by: "chris@contoso.com", mine: true,
  requested_at: new Date(Date.now() - 2 * 86400000).toISOString(),
  detail: { domain: "finance", subdomain: "", domain_name: "Finance", owners: "olu@contoso.com, ana@contoso.com" } };
let catalogDomain = "sales";

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    platform_domains: () => [{ id: "sales", name: "Sales", owners: "" }, { id: "finance", name: "Finance", owners: "olu@contoso.com" }],
    meta_project: () => ({
      path: "C:/work/orders", name: "Orders", version: "3", templates: {}, layers: [], entities: [],
      products: [], links: [], environments: {}, exceptions: [], key_tables: [], source_systems: [], errors: [], run_order: [], designations: [],
      project: { name: "orders", domain: "sales" },
    }),
    meta_runs: () => [],
    mesh_overview: () => ({ project: { name: "orders", domain: "sales", subdomain: "", team: "", description: "", upgraded: true },
      domains: [{ id: "sales", name: "Sales" }, { id: "finance", name: "Finance" }], products: [], links: [], layers: [], environments: {},
      designations: [], published: [], projects: [{ name: "orders", domain_id: catalogDomain }], platform: {}, problems: [], errors: [] }),
    mesh_members: () => ({ project: "orders", members, earlier: false, can_change: true, domain_request: ask }),
    mesh_set_member: ({ user, role }) => {
      members = role ? [...members.filter((m) => m.user !== user), { user, role }] : members.filter((m) => m.user !== user);
      return { project: "orders", members, earlier: false, can_change: true, domain_request: ask };
    },
    platform_withdraw: () => { ask = null; return { id: "r1", status: "withdrawn" }; },
    mesh_set_project: () => ({ project: { name: "orders", domain: "finance", upgraded: true } }),
  } });
  await page().evaluate(() => { state.user = "chris@contoso.com"; state.meta.tab = "workspaces"; go("metadata"); });
  await page().waitForSelector(".mesh-members table");
});
after(async () => { if (app) await app.close(); });

const rowText = (user) => page().textContent(`.mesh-members tr:has-text("${user}")`);

test("Members lists owners first, with what an owner can change", { skip }, async () => {
  assert.deepStrictEqual(await page().$$eval(".mesh-members tbody td:first-child", (t) => t.map((x) => x.textContent)),
    ["chris@contoso.com", "dee@contoso.com", "lee@contoso.com"]);
  assert.match(await rowText("chris@contoso.com"), /Owner\s*You/);
  assert.match(await rowText("lee@contoso.com"), /Member\s*Make owner\s*Remove/);
  assert.ok(await page().$('.mesh-members button:has-text("Add member")'));
  assert.ok(await page().$(".mesh-members .preview-tag"), "Members is Preview");
  assert.match(await page().textContent(".how-it-works"), /catalog\.project_member/);
  assert.match(await page().textContent(".how-it-works"), /catalog\.request/);
});

test("the waiting ask to join a domain says who decides, and Withdraw takes it back", { skip }, async () => {
  const notice = await page().textContent(".mesh-join");
  assert.match(notice, /Waiting on Finance's owners/);
  assert.match(notice, /olu@contoso\.com, ana@contoso\.com/);
  await page().click('.mesh-join button:has-text("Withdraw")');
  await page().waitForFunction(() => !document.querySelector(".mesh-join") && document.querySelector(".mesh-members table"));
  assert.ok(app.calls.some((c) => c.method === "platform_withdraw" && c.params.id === "r1"));
});

test("Add member and Remove change the members; Remove asks first", { skip }, async () => {
  await page().click('.mesh-members button:has-text("Add member")');
  await page().fill('.dialog input[type="email"]', "kim@contoso.com");
  await page().click('.dialog button:has-text("Add")');
  await page().waitForSelector('.mesh-members tr:has-text("kim@contoso.com")');
  assert.ok(app.calls.some((c) => c.method === "mesh_set_member" && c.params.user === "kim@contoso.com" && c.params.role === "member"));
  await page().click('.mesh-members tr:has-text("lee@contoso.com") button:has-text("Remove")');
  await page().waitForSelector(".dialog");
  assert.match(await page().textContent(".dialog"), /Remove lee@contoso\.com\?/);
  await page().click('.dialog button:has-text("Remove")');
  await page().waitForFunction(() => document.querySelector(".mesh-members table")
    && ![...document.querySelectorAll(".mesh-members td")].some((t) => t.textContent === "lee@contoso.com"));
  assert.ok(app.calls.some((c) => c.method === "mesh_set_member" && c.params.user === "lee@contoso.com" && c.params.role === ""));
});

test("once the catalog has the project in another domain, Use it writes project.toml", { skip }, async () => {
  catalogDomain = "finance";
  await page().evaluate(() => goMeta("workspaces"));
  await page().waitForSelector(".mesh-moved");
  assert.match(await page().textContent(".mesh-moved"), /The catalog has orders in Finance.*project\.toml says sales/);
  await page().click('.mesh-moved button:has-text("Use it")');
  await page().waitForFunction(() => document.querySelector(".mesh-members"));
  assert.ok(app.calls.some((c) => c.method === "mesh_set_project" && c.params.fields.domain === "finance"));
  assert.deepStrictEqual(app.errors, []);
});
