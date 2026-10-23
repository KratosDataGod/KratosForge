// Shortcut fixes (Preview): broken shortcuts grouped by the lakehouse they lost, finding where it went and
// repointing them all, and accepting a crossed environment (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const dialog = () => page().locator("#dialog-root .dialog");

const lakehouse = (ws, wsName, id, name) => ({ workspace_id: ws, workspace_name: wsName, item_id: id, lakehouse_id: id, name, type: "Lakehouse" });
const CATALOG = {
  workspaces: [{ id: "ws-dev", displayName: "Sales Dev" }, { id: "ws-new", displayName: "Sales New" }, { id: "ws-prod", displayName: "Sales Prod" },
    { id: "ws-dev2", displayName: "Sales Dev 2" }],
  items: [lakehouse("ws-dev", "Sales Dev", "lh-1", "LH_Silver"), lakehouse("ws-new", "Sales New", "lh-new", "LH_Gold"),
    lakehouse("ws-prod", "Sales Prod", "lh-gold", "LH_Gold"), lakehouse("ws-dev2", "Sales Dev 2", "lh-gold-dev", "LH_Gold")],
  skipped: [], favorites: [],
};
const row = (name, extra) => ({ id: `lh-1|Tables/dbo/${name}`, name, path: "Tables/dbo", full_path: `Tables/dbo/${name}`, kind: "table",
  target_type: "OneLake", target: `Tables/dbo/${name}`, target_path: `Tables/dbo/${name}`, host_workspace_id: "ws-dev", lakehouse_id: "lh-1",
  workspace_name: "Sales Dev", lakehouse_name: "LH_Silver", health: "healthy", health_detail: "", checked_at: "2026-10-22T08:00:00+00:00",
  risks: [], used_by: [], last_change: null, product: null, exposure: "identity", host_env: "dev", target_env: "", same_env_target: null, ...extra });
const gone = (name) => row(name, { workspace_id: "ws-old", item_id: "lh-gone", health: "broken", target_lakehouse_name: "LH_Gold",
  target_workspace_name: "Sales Old", health_detail: "The target table or folder isn't there any more (removed, renamed or moved)" });
const CROSS = { kind: "cross_env", text: "Dev reads Prod: a Dev or Test run reads live data" };

// What the engine holds: whether the person is a platform admin, and what was accepted.
const engine = { admin: true, accepted: null, repointMany: [], accept: [], withdraw: [] };
function center() {
  const product = row("product", { workspace_id: "ws-prod", item_id: "lh-gold", target_env: "prod", target_lakehouse_name: "LH_Gold",
    target_workspace_name: "Sales Prod", risks: [engine.accepted ? { ...CROSS, accepted: engine.accepted } : { ...CROSS }],
    same_env_target: { workspace_id: "ws-dev2", workspace_name: "Sales Dev 2", lakehouse_id: "lh-gold-dev", lakehouse_name: "LH_Gold", env: "dev" } });
  const rows = [gone("customer"), gone("orders"), gone("returns"), product];
  return { pending: false, rows, errors: {}, problems: [], requests: [], admin: engine.admin, checked_at: "2026-10-22T08:00:00+00:00",
    counts: { total: 4, lakehouses: 1, workspaces: 1, broken: 3, unreachable: 0, not_checked: 0, connection: 0, risky: engine.accepted ? 0 : 1, changed_7d: 0, waiting: 0 },
    needs: engine.accepted ? [] : [{ kind: "cross_env", id: product.id, title: `product: ${CROSS.text}`, detail: "In Sales Dev / LH_Silver. Repoint it at the same environment." }] };
}

before(async () => {
  if (skip) return;
  app = await openApp({
    storage: { "fd.shortcuts.checkOnOpen": "0" },
    mocks: {
      catalog: () => CATALOG,
      shortcut_center: () => center(),
      shortcut_find_moved: ({ shortcuts }) => ({ name: "LH_Gold", candidate: { workspace_id: "ws-new", workspace_name: "Sales New", lakehouse_id: "lh-new", lakehouse_name: "LH_Gold" },
        found: shortcuts.filter((s) => s.name !== "returns").map((s) => s.id), missing: ["lh-1|Tables/dbo/returns"] }),
      shortcut_repoint_many: (p) => { engine.repointMany.push(p); return { done: [p.items[0].name], failed: p.items.slice(1).map((s) => ({ name: s.name, error: "400 target not found. It was put back as it was." })), warnings: [], problems: [] }; },
      shortcut_accept_risk: (p) => { engine.accept.push(p); engine.accepted = { by: "ana@contoso.com", reason: p.reason, at: "2026-10-22T09:00:00+00:00" }; return engine.accepted; },
      shortcut_withdraw_risk: (p) => { engine.withdraw.push(p); engine.accepted = null; return { accepted: [] }; },
    },
  });
  await page().waitForFunction(() => typeof renderShortcuts === "function");
});
after(async () => { await app?.close(); });

async function openShortcuts(view) {
  await page().evaluate((v) => { state.user = "ana@contoso.com"; catalogPage.tab = "shortcuts"; Object.assign(scPage, { view: v, data: null, selected: null }); go("onelake"); }, view);
  await page().waitForSelector(".sc-body .sc-pad");
}

test("broken shortcuts are grouped by the lakehouse they lost, which is found again and repointed in one go", { skip }, async () => {
  await openShortcuts("health");
  const card = page().locator(".card.pad", { hasText: "LH_Gold in Sales Old isn't there any more" });
  await card.waitFor();
  await card.locator("text=A lakehouse called LH_Gold is now in Sales New, and 2 of 3 tables are there.").waitFor();
  assert.strictEqual(await card.locator(".sc-item").count(), 3);
  assert.strictEqual(await card.getByRole("button", { name: "Remove" }).count(), 3, "each item can be removed");
  assert.ok(await card.locator(".preview-tag").count() >= 2, "the new actions are tagged Preview");
  await card.getByRole("button", { name: /Repoint all 2 there/ }).click();
  await dialog().waitFor();
  assert.match(await dialog().textContent(), /Repoint 2 shortcuts\?.*now points at LH_Gold in Sales New.*put back as it was, and the rest carry on/s);
  await dialog().getByRole("button", { name: "Repoint", exact: true }).click();
  await page().waitForFunction(() => /Repointed 1 of 2/.test(document.getElementById("toast").textContent));
  assert.match(await page().textContent("#toast"), /orders: 400 target not found/);
  const sent = engine.repointMany[0];
  assert.deepStrictEqual([sent.items.map((s) => s.name), sent.workspace_id, sent.lakehouse_id, sent.lakehouse_name],
    [["customer", "orders"], "ws-new", "lh-new", "LH_Gold"]);
});

test("Repoint all to another lakehouse picks one, and Cancel changes nothing", { skip }, async () => {
  await openShortcuts("health");
  await page().getByRole("button", { name: /Repoint all to another lakehouse/ }).click();
  await dialog().getByLabel("Lakehouse").selectOption("lh-gold-dev");
  await dialog().getByRole("button", { name: "Next" }).click();
  await page().locator("#dialog-root .dialog", { hasText: "Repoint 3 shortcuts?" }).waitFor();
  assert.match(await dialog().textContent(), /LH_Gold in Sales Dev 2/);
  assert.match(await dialog().locator(".dialog-actions button").last().getAttribute("class"), /danger/);
  await dialog().getByRole("button", { name: "Cancel" }).click();
  assert.strictEqual(engine.repointMany.length, 1);
});

test("a platform admin accepts Dev reading Prod with a reason, and can withdraw it", { skip }, async () => {
  await openShortcuts("health");
  const item = page().locator(".sc-item", { hasText: "Dev reads Prod" });
  assert.ok(await item.getByRole("button", { name: /Repoint to Dev's LH_Gold/ }).isVisible());
  await item.getByRole("button", { name: /Accept/ }).click();
  await page().locator("#dialog-root .dialog[aria-label='Accept product reading Prod?']").waitFor();
  const accept = dialog().getByRole("button", { name: "Accept", exact: true });
  assert.ok(await accept.isDisabled(), "a reason is required");
  await dialog().getByLabel("Why it's fine").fill("Reads the live price list on purpose");
  await accept.click();
  await page().waitForFunction(() => !document.querySelector("#dialog-root .dialog"));
  assert.deepStrictEqual(engine.accept[0], { shortcut_id: "lh-1|Tables/dbo/product", kind: "cross_env", reason: "Reads the live price list on purpose" });
  await page().waitForFunction(() => !document.querySelector(".sc-item") || ![...document.querySelectorAll(".sc-item")].some((x) => /Dev reads Prod/.test(x.textContent)));

  await openShortcuts("all");
  await page().locator("tr", { hasText: "product" }).click();
  const panel = page().locator(".preview-panel.open");
  await panel.locator("text=Accepted by ana@contoso.com:").waitFor();
  assert.match(await panel.textContent(), /Reads the live price list on purpose/);
  await panel.getByRole("button", { name: /Withdraw/ }).click();
  await page().waitForFunction(() => /flagged again/.test(document.getElementById("toast").textContent));
  assert.deepStrictEqual(engine.withdraw[0], { shortcut_id: "lh-1|Tables/dbo/product", kind: "cross_env" });
});

test("someone who isn't a platform admin isn't offered Accept", { skip }, async () => {
  engine.admin = false;
  await openShortcuts("health");
  const item = page().locator(".sc-item", { hasText: "Dev reads Prod" });
  await item.waitFor();
  assert.strictEqual(await item.getByRole("button", { name: /Accept/ }).count(), 0);
  assert.ok(await item.getByRole("button", { name: /Repoint to Dev's LH_Gold/ }).isVisible());
  engine.admin = true;
  assert.deepStrictEqual(app.errors, []);
});
