// Plan step 7: OneLake > Access > All workspaces, from the access cache, with the engine's calls
// mocked (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const ago = (min) => new Date(Date.now() - min * 60000).toISOString();
const ws = (id, name, people, lakehouses, extra = {}) => ({
  workspace_id: id, workspace_name: name, people, lakehouses, checked_at: ago(14), changed_at: ago(600), stale: false,
  problem: null, known: true,
  summary: { people: people.filter((p) => p.type === "User").length, groups: people.filter((p) => p.type === "Group").length,
    apps: people.filter((p) => !["User", "Group"].includes(p.type)).length, guests: people.filter((p) => p.guest).length },
  ...extra,
});
const user = (name, role, guest = false) => ({ id: name, name, type: "User", role, guest });
const CACHE = {
  ready: true, state: "ready", message: null, can_write: true,
  workspaces: [
    ws("w-hr", "HR Prod", [{ id: "g1", name: "HR readers", type: "Group", role: "Viewer", guest: false }], []),
    ws("w-sales", "Sales Prod", [user("Ann", "Admin"), user("Raj", "Member"), user("Bo", "Member", true),
      { id: "g2", name: "Sales readers", type: "Group", role: "Viewer", guest: false }],
    [{ item_id: "lh1", item_name: "LH_Sales_Gold", known: true, stale: false, problem: null, roles: [] },
      { item_id: "lh2", item_name: "LH_Sales_Silver", known: true, stale: false, problem: null,
        roles: [{ name: "DefaultReader", paths: ["*"], entra: [], item_members: ["ReadAll"] }] }]),
    ws("w-fin", "Finance Dev", [user("Cy", "Contributor")], [], { stale: true, problem: "The workspace roles couldn't be read: 403" }),
  ],
};

let app;
let refreshes;

before(async () => {
  if (skip) return;
  refreshes = [];
  app = await openApp({
    mocks: {
      access_cache: () => CACHE,
      access_cache_refresh: (p) => {
        refreshes.push(p);
        if (!p.approve) throw new Error("The access cache's tables aren't set up in the central lakehouse. Confirm to continue.");
        return { checked: 5, changed: 2, problems: 1, at: ago(0) };
      },
      catalog: () => ({ items: [], workspaces: [] }),
    },
  });
  await app.page.waitForFunction(() => typeof renderAccessAll === "function");
  await app.page.evaluate(() => { state.user = "chris@contoso.com"; catalogPage.tab = "access"; go("onelake"); });
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

const page = () => app.page;
const shot = async (name) => { if (SHOTS) await page().screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true }); };

test("All workspaces lists the cache, flags what's worth a look and filters", { skip }, async () => {
  await page().waitForSelector("text=From the access cache");
  const rows = await page().locator(".estate table.list tbody tr").allTextContents();
  assert.strictEqual(rows.length, 3);
  assert.match(rows.join("|"), /Finance Dev.*Out of date/);
  await page().getByRole("button", { name: "Guests" }).click();
  assert.deepStrictEqual((await page().locator(".estate table.list tbody tr").allTextContents()).map((t) => t.split(/\d/)[0]), ["Sales Prod"]);
  await page().getByRole("button", { name: "All", exact: true }).click();
  await page().getByRole("button", { name: "Sales Prod" }).click();
  const detail = await page().locator("text=Guided fixes").locator("..").textContent();
  assert.match(detail, /3 people have Contributor or higher by name/);
  assert.match(detail, /LH_Sales_Gold has no data access roles/);
  await shot("access-all-workspaces");
  assert.deepStrictEqual(app.errors, []);
});

test("refresh asks before setting up the cache, then refreshes with approval", { skip }, async () => {
  await page().getByRole("button", { name: "Refresh changes" }).click();
  await page().waitForSelector("text=Set up the access cache?");
  await page().getByRole("button", { name: "Set up and refresh" }).click();
  await page().waitForFunction(() => document.body.textContent.includes("2 changed"));
  assert.deepStrictEqual(refreshes.map((r) => !!r.approve), [false, true]);
});

test("a guided fix opens the workspace in Environments", { skip }, async () => {
  await page().getByRole("button", { name: "Sales Prod" }).click();
  await page().getByRole("button", { name: "Review in Workspace roles" }).first().click();
  const extra = await page().evaluate(() => ({ view: accessPage.view, extra: accessPage.extra.map((x) => x.workspace_id) }));
  assert.deepStrictEqual(extra, { view: "roles", extra: ["w-sales"] });
});
