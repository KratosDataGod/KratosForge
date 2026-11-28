// OneLake > Access > All workspaces (access-cache.js), the parts access-cache.test.cjs leaves out: the
// not-set-up states, a cache that can't be read, every filter and the search, the detail's lakehouse
// roles and its buttons, and the protect dialog's checks. Engine calls are mocked (see harness.cjs).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";

const ago = (min) => new Date(Date.now() - min * 60000).toISOString();
const summary = (people) => ({ people: people.filter((p) => p.type === "User").length, groups: people.filter((p) => p.type === "Group").length,
  apps: people.filter((p) => !["User", "Group"].includes(p.type)).length, guests: people.filter((p) => p.guest).length });
const ws = (id, name, people, lakehouses, extra = {}) => ({
  workspace_id: id, workspace_name: name, people, lakehouses, checked_at: ago(30), changed_at: ago(120), stale: false,
  problem: null, known: true, summary: summary(people), ...extra,
});
const READY = {
  ready: true, state: "ready", message: null, can_write: true,
  workspaces: [
    ws("w-ops", "Ops Prod", [
      { id: "sp1", name: "deploy-bot", type: "ServicePrincipal", role: "Admin", guest: false },
      { id: "sp2", name: "etl-app", type: "ServicePrincipal", role: "Admin", guest: false },
      { id: "u1", name: "Ivy", type: "User", role: "Viewer", guest: true },
    ], [
      { item_id: "lh-ops", item_name: "LH_Ops", known: true, stale: true, problem: "Its roles couldn't be read: 403", roles: [
        { name: "Readers", paths: ["/Tables/dbo/orders", "/Files/raw"], entra: ["a"], item_members: [] },
        { name: "Analysts", paths: ["*"], entra: ["a", "b"], item_members: ["ReadAll", "Write"] },
      ] },
      { item_id: "lh-new", item_name: "LH_New", known: false, stale: true, problem: null, roles: [] },
    ]),
    ws("w-quiet", "Quiet Dev", [{ id: "g1", name: "Quiet readers", type: "Group", role: "Viewer", guest: false }], [], { known: false }),
    ws("w-empty", "Empty Test", [], [], { known: false, checked_at: null, changed_at: null }),
    ws("w-one", "One Admin", [{ id: "u2", name: "Lee", type: "User", role: "Admin", guest: false }],
      [{ item_id: "lh-one", item_name: "LH_One", known: true, stale: false, problem: null, roles: [] }]),
  ],
};

let cache;           // what access_cache answers (an Error is thrown)
let refreshes;
let protects;
let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    mocks: {
      access_cache: () => { if (cache instanceof Error) throw cache; return cache; },
      access_cache_refresh: (p) => { refreshes.push(p); return { checked: 1, changed: 0, problems: 0, at: ago(0) }; },
      access_find_person: ({ text }) => ({ id: `id-${text}`, name: text, type: "" }),
      access_cache_protect: (p) => { protects.push(p); return { changed: false, changes: [] }; },
      catalog: () => ({ workspaces: [], items: [
        { type: "Lakehouse", item_id: "lh-a", name: "LH_Archive", workspace_id: "w-a", workspace_name: "Archive" },
        { type: "Lakehouse", item_id: "lh-ops", name: "LH_Ops", workspace_id: "w-ops", workspace_name: "Ops Prod" }] }),
      access_lakehouse: () => { throw new Error("Not needed in this test"); },
    },
  });
  await page().waitForFunction(() => typeof renderAccessAll === "function");
});

after(async () => { await app?.close(); });

// Open All workspaces afresh with `data` as the cache.
async function show(data) {
  cache = data;
  refreshes = [];
  protects = [];
  await page().evaluate(() => {
    Object.assign(accessCachePage, { data: null, filter: "all", query: "", open: null, busy: false });
    Object.assign(accessPage, { view: "all", extra: [], data: null });
    state.user = "chris@contoso.com"; catalogPage.tab = "access"; go("onelake"); render();
  });
  app.errors.length = 0;
}

const slotText = () => page().locator(".page .estate").last().innerText();
const rowNames = async () => (await page().locator(".estate table.list tbody tr td:first-child button").allTextContents());

test("a cache that isn't set up offers to set it up only to someone who may write", { skip }, async () => {
  await show({ ready: false, state: "missing", can_write: true, message: "The central lakehouse has no access tables yet.", workspaces: [] });
  await page().getByText("The access cache isn't set up yet").waitFor();
  assert.match(await slotText(), /no access tables yet/);
  assert.strictEqual(await page().getByRole("button", { name: "Set up and refresh" }).isEnabled(), true);

  await show({ ready: false, state: "missing", can_write: false, message: "", workspaces: [] });
  await page().getByText("Setting it up needs write access to the central lakehouse.").waitFor();
  assert.strictEqual(await page().getByRole("button", { name: "Set up and refresh" }).count(), 0);

  await show({ ready: false, state: null, message: "No central lakehouse is chosen.", workspaces: [] });
  await page().getByText("No central lakehouse is chosen.").waitFor();
  assert.strictEqual(await page().getByRole("button", { name: "Set up and refresh" }).count(), 0);
  assert.doesNotMatch(await slotText(), /needs write access/);
  assert.deepStrictEqual(app.errors, []);
});

test("a cache that can't be read says so and where the details are", { skip }, async () => {
  await show(new Error("The central lakehouse couldn't be reached"));
  await page().getByText("Couldn't read the access cache. Details are in Activity (the bell).").waitFor();
  assert.match(await page().locator("#toast").textContent(), /Could not read the access cache/);
  assert.deepStrictEqual(app.errors, []);
});

test("an empty cache says how to fill it", { skip }, async () => {
  await show({ ...READY, workspaces: [] });
  await page().getByText("Nothing cached yet. Refresh changes reads every workspace you can open.").waitFor();
  assert.match(await slotText(), /^From the access cache$/m);
  assert.match(await slotText(), /0 workspaces you can open\. Kept/);
});

test("the filters and the search narrow the rows", { skip }, async () => {
  await show(READY);
  await page().getByText(/From the access cache, checked/).waitFor();
  assert.match(await slotText(), /4 workspaces you can open\. Kept/);
  assert.deepStrictEqual(await rowNames(), ["Ops Prod", "Quiet Dev", "Empty Test", "One Admin"]);
  assert.match(await slotText(), /Roles never read: listing them needs Member or Admin/);
  const seg = page().locator(".estate .seg");
  await seg.getByRole("button", { name: "Worth a look" }).click();
  await page().waitForFunction(() => document.querySelectorAll(".estate table.list tbody tr").length === 2);
  assert.deepStrictEqual(await rowNames(), ["Ops Prod", "One Admin"]);
  await seg.getByRole("button", { name: "Write roles by name" }).click();
  await page().waitForFunction(() => document.querySelectorAll(".estate table.list tbody tr").length === 1);
  assert.deepStrictEqual(await rowNames(), ["One Admin"]);
  assert.match(await seg.locator("button.on").textContent(), /Write roles by name/);
  await seg.getByRole("button", { name: "Out of date" }).click();
  await page().waitForFunction(() => document.querySelector(".estate table.list tbody tr td button")?.textContent === "Ops Prod");
  assert.deepStrictEqual(await rowNames(), ["Ops Prod"]);
  await seg.getByRole("button", { name: "All", exact: true }).click();
  await page().getByLabel("Find a workspace or person").fill("quiet");
  await page().waitForFunction(() => document.querySelectorAll(".estate table.list tbody tr").length === 1);
  assert.deepStrictEqual(await rowNames(), ["Quiet Dev"]);
  await page().getByLabel("Find a workspace or person").fill("ivy");
  await page().waitForFunction(() => document.querySelector(".estate table.list tbody tr td button")?.textContent === "Ops Prod");
  assert.deepStrictEqual(await rowNames(), ["Ops Prod"]);
  await page().getByLabel("Find a workspace or person").fill("nobody here");
  await page().getByText("Nothing matches.").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("a workspace's detail shows apps with Admin, its guests and each lakehouse's table roles", { skip }, async () => {
  await show(READY);
  await page().getByRole("button", { name: "Ops Prod" }).click();
  await page().getByRole("heading", { name: "Ops Prod" }).waitFor();
  const detail = await page().locator(".estate .card.pad").last().innerText();
  assert.match(detail, /2 apps have Admin: deploy-bot, etl-app\./);
  assert.match(detail, /1 guest from another organization \(Ivy, Viewer\)\./);
  assert.match(detail, /Its roles couldn't be read: 403/);
  assert.match(detail, /Readers: dbo\.orders, Files\.raw · 1 member/);
  assert.match(detail, /Analysts: Everything · 2 members · everyone with ReadAll, Write/);
  assert.match(detail, /LH_New\nOut of date/);
  assert.doesNotMatch(detail, /LH_New\nOut of date\nNo data access roles/);
  const kinds = await page().locator(".estate .card.pad").last().locator("table.list tbody tr td:nth-child(2)").allTextContents();
  assert.deepStrictEqual(kinds, ["App", "App", "User"]);
  assert.strictEqual(await page().locator(".estate .card.pad").last().locator(".pill-s.warn").textContent(), "Guest");
  assert.deepStrictEqual(app.errors, []);
});

test("a workspace with nothing to fix and no one read says so", { skip }, async () => {
  await show(READY);
  await page().getByRole("button", { name: "Empty Test" }).click();
  await page().getByRole("heading", { name: "Empty Test" }).waitFor();
  const detail = await page().locator(".estate .card.pad").last().innerText();
  assert.match(detail, /Nothing to fix from what the cache saw\./);
  assert.match(detail, /Not read yet\./);
  assert.match(detail, /No lakehouses\./);
});

test("Refresh this workspace refreshes only that workspace", { skip }, async () => {
  await show(READY);
  await page().getByRole("button", { name: "One Admin" }).click();
  await page().getByRole("button", { name: "Refresh this workspace" }).click();
  await page().waitForFunction(() => /Checked 1: 0 changed$/.test(document.getElementById("toast").textContent));
  assert.deepStrictEqual(refreshes, [{ workspace_ids: ["w-one"] }]);
  assert.match(await page().locator(".estate table.list tbody tr.selected").innerText(), /One Admin/);
});

test("Table access opens that lakehouse in Table access", { skip }, async () => {
  await show(READY);
  await page().getByRole("button", { name: "Ops Prod" }).click();
  await page().locator(".estate .card.pad").last().getByRole("button", { name: "Table access" }).first().click();
  await page().getByRole("heading", { name: "Table access in a lakehouse" }).waitFor();
  await page().waitForFunction(() => document.querySelector("select[aria-label=Lakehouse]")?.value === "lh-ops");
  assert.deepStrictEqual(await page().evaluate(() => ({ view: accessPage.view, lakehouse: accessPage.lakehouse })), { view: "tables", lakehouse: "lh-ops" });
});

test("Open in Fabric opens the workspace's list in the Fabric view", { skip }, async () => {
  await show(READY);
  await page().getByRole("button", { name: "One Admin" }).click();
  await page().getByRole("button", { name: "Open in Fabric" }).click();
  const where = await page().evaluate(() => ({ view: state.view, page: fabricPage.page }));
  assert.deepStrictEqual(where, { view: "fabric", page: "https://app.fabric.microsoft.com/groups/w-one/list?experience=fabric-developer" });
});

test("a lakehouse without data access roles is a guided fix that opens it in Fabric", { skip }, async () => {
  await show(READY);
  await page().getByRole("button", { name: "One Admin" }).click();
  const detail = page().locator(".estate .card.pad").last();
  await detail.getByText("LH_One has no data access roles: OneLake security is probably off, so every Viewer reads every table.").waitFor();
  assert.match(await detail.innerText(), /LH_One\nNo data access roles/);
  await detail.getByRole("button", { name: "Open the lakehouse in Fabric" }).click();
  const where = await page().evaluate(() => ({ view: state.view, page: fabricPage.page }));
  assert.strictEqual(where.view, "fabric");
  assert.match(where.page, /^https:\/\/app\.fabric\.microsoft\.com\/groups\/w-one\/.+\/lh-one\?experience=fabric-developer$/);
});

test("someone who can't write sees why there is no refresh and no protect", { skip }, async () => {
  await show({ ...READY, can_write: false });
  await page().getByText("Refreshing needs write access to the central lakehouse.").waitFor();
  assert.strictEqual(await page().getByRole("button", { name: "Refresh changes" }).count(), 0);
  assert.strictEqual(await page().getByRole("button", { name: "Who may read it" }).count(), 0);
});

test("Who may read it needs someone before it continues, and says when nothing changed", { skip }, async () => {
  await show(READY);
  await page().getByRole("button", { name: "Who may read it" }).click();
  const dialog = page().getByRole("dialog", { name: "Who may read the access cache" });
  await dialog.getByText("No one yet. A group of platform admins is easiest to keep up to date.").waitFor();
  await dialog.getByRole("button", { name: "Continue" }).click();
  await page().waitForFunction(() => document.getElementById("toast").textContent === "Add at least one person or group");
  assert.strictEqual(protects.length, 0);
  await dialog.getByLabel("Add someone").fill("ops@contoso.com");
  await dialog.getByRole("button", { name: "Add", exact: true }).click();
  await dialog.getByText("ops@contoso.com", { exact: true }).waitFor();
  assert.strictEqual(await dialog.getByLabel("Add someone").inputValue(), "");
  await dialog.getByRole("button", { name: "Continue" }).click();
  await page().waitForFunction(() => document.getElementById("toast").textContent === "Already set up that way");
  assert.deepStrictEqual(protects, [{ members: ["id-ops@contoso.com"] }]);
  assert.ok(app.calls.some((c) => c.method === "access_find_person" && c.params.text === "ops@contoso.com" && c.params.workspace_id === ""));
  assert.deepStrictEqual(app.errors, []);
});
