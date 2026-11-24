// Product groups (product-items.js) beyond product-groups.test.cjs: opening an item in Fabric, a product
// with no models or reports for an owner and for a reader, a remove the platform refuses, and the Add
// dialog when workspaces can't be listed, there are none, a workspace's items can't be listed or it has
// none of that kind, nothing is picked, and the platform refuses the add. The platform calls are mocked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const PID = "crm/customer_360";
const WS = "eeeeeeee-0000-0000-0000-000000000005";
const OTHER = "eeeeeeee-0000-0000-0000-000000000006";
const RPT = "ffffffff-0000-0000-0000-000000000006";
const base = { project: "crm", description: "", owner: "ana@example.com", access: "public", status: "published", version: "1",
  domain_id: "sales", consumers: [], granted: [], can_link: false, why: "Your own product", linked: false, following: false,
  ports: [{ port: "dim_customer", env: "prod", table_name: "dim_customer", lakehouse_name: "LH_Gold", workspace_name: "CRM Prod" }] };
const report = { product_id: PID, item_id: RPT, item_type: "Report", workspace_id: WS, workspace_name: "Sales Prod",
  item_name: "Customer health", added_by: "ana", added_at: "2026-10-20T10:00:00Z", endorsement: "Promoted", certified_by: "", scanned_at: "" };

let app;
const page = () => app.page;
let items, canEdit, workspaces, listItems, addItem, removeItem;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    platform_products: () => ({ project: null, can_follow: true, can_group: true, products: [{ ...base, id: PID, name: "customer_360", items }] }),
    platform_access_reviews: () => ({ every_days: 90, grants: [], due: 0 }),
    platform_whoami: () => ({ user: "chris@example.com", admin: false }),
    platform_product_trust: () => ({ product_id: PID, name: "customer_360", owner: "ana@example.com", admin: false, can_edit: canEdit, items: [] }),
    platform_product_items: () => ({ product_id: PID, items }),
    platform_remove_product_item: (p) => removeItem(p),
    platform_add_product_item: (p) => addItem(p),
    list_workspaces: () => workspaces(),
    list_items: (p) => listItems(p),
  } });
  await startupReads(app);
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  items = [report];
  canEdit = true;
  workspaces = () => [{ id: WS, displayName: "Sales Prod" }, { id: OTHER, displayName: "Archive" }];
  listItems = ({ workspace_id }) => (workspace_id === WS ? [{ id: "r2", type: "Report", displayName: "Sales report" },
    { id: "r1", type: "Report", displayName: "Churn watch" }, { id: "m1", type: "SemanticModel", displayName: "SM_Sales" }] : []);
  addItem = () => { throw new Error("PermissionError: Only the product's owner or a platform admin adds items."); };
  removeItem = () => { throw new Error("PermissionError: Only the product's owner or a platform admin removes items."); };
  await page().evaluate(() => {
    document.getElementById("dialog-root").replaceChildren();
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    state.workspaces = null; window.__fdShellCalls.length = 0;
  });
});

const group = (g) => page().locator(`.product-offer [data-group="${g}"]`);
const dialog = () => page().locator("#dialog-root [role=dialog]");
const toastSays = (text) => page().waitForFunction((t) => document.getElementById("toast").textContent === t, text);
const openProduct = async () => {
  await page().evaluate(() => { productsPage.open = null; productsPage.offer = "all"; goMeta("catalog"); });
  await page().locator('a.product-open', { hasText: "customer_360" }).click();
  await group("Report").waitFor();
};
const openAdd = async (label) => {
  await openProduct();
  await page().locator(".product-offer").getByRole("button", { name: label }).click();
  await dialog().waitFor();
};

test("an item's name opens it in Fabric; an owner sees how to add the models it lacks", { skip }, async () => {
  await openProduct();
  assert.strictEqual(await group("SemanticModel").locator("p").textContent(), "No models yet. Add one from Fabric.");
  await group("Report").getByRole("link", { name: "Customer health" }).click();
  await page().waitForFunction(() => window.__fdShellCalls.some((c) => c.cmd === "open_link"));
  assert.deepStrictEqual(await page().evaluate(() => window.__fdShellCalls.filter((c) => c.cmd === "open_link").map((c) => c.args.url)),
    [`https://app.fabric.microsoft.com/groups/${WS}/reports/${RPT}?experience=fabric-developer`]);
});

test("a reader sees who adds the models and reports a product lacks", { skip }, async () => {
  items = [];
  canEdit = false;
  await openProduct();
  assert.strictEqual(await group("SemanticModel").locator("p").textContent(), "No models yet. The product's owner or a platform admin adds them.");
  assert.strictEqual(await group("Report").locator("p").textContent(), "No reports yet. The product's owner or a platform admin adds them.");
});

test("a remove the platform refuses says why and keeps the item", { skip }, async () => {
  await openProduct();
  await group("Report").getByRole("button", { name: "Remove Customer health" }).click();
  await page().locator(".dialog-actions .danger-solid").click();
  await toastSays("Only the product's owner or a platform admin removes items.");
  assert.strictEqual(await group("Report").locator(".product-offer-item").count(), 1);
});

test("Add a report when workspaces can't be listed says why and offers none", { skip }, async () => {
  workspaces = () => { throw new Error("Fabric didn't answer."); };
  await openAdd("Add a report");
  await dialog().getByRole("alert").filter({ hasText: "Fabric didn't answer." }).waitFor();
  assert.deepStrictEqual(await dialog().getByLabel("Workspace").locator("option").allInnerTexts(), ["No workspaces"]);
  assert.strictEqual(await dialog().getByLabel("Workspace").isDisabled(), true);
});

test("Add a model with no workspace in Fabric says you can't see any", { skip }, async () => {
  workspaces = () => [];
  await openAdd("Add a model");
  await dialog().getByText("You can't see any workspace in Fabric.").waitFor();
});

test("Add a report lists one kind sorted, says when a workspace has none or can't be listed, and asks for a pick", { skip }, async () => {
  await openAdd("Add a report");
  await dialog().getByText("No report in this workspace that you can see.").waitFor();  // Archive comes first
  await dialog().getByRole("button", { name: "Add", exact: true }).click();
  assert.strictEqual(await dialog().getByRole("alert").textContent(), "Pick a report.");
  await dialog().getByLabel("Workspace").selectOption(WS);
  await dialog().locator(".product-item-pick label").first().waitFor();
  assert.deepStrictEqual(await dialog().locator(".product-item-pick label").allInnerTexts(), ["Churn watch", "Sales report"]);
  assert.strictEqual(await dialog().getByRole("alert").textContent(), "", "picking a workspace clears the message");

  listItems = () => { throw new Error("ValueError: The workspace is on a paused capacity."); };
  await dialog().getByLabel("Workspace").selectOption(OTHER);
  await dialog().getByRole("alert").filter({ hasText: "The workspace is on a paused capacity." }).waitFor();
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "platform_add_product_item"), []);
});

test("an add the platform refuses keeps the dialog open and says why", { skip }, async () => {
  await openAdd("Add a report");
  await dialog().getByLabel("Workspace").selectOption(WS);
  await dialog().locator(".product-item-pick label").first().waitFor();
  await dialog().getByRole("button", { name: "Add", exact: true }).click();
  await dialog().getByRole("alert").filter({ hasText: "Only the product's owner or a platform admin adds items." }).waitFor();
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "platform_add_product_item").at(-1).params, { product_id: PID,
    item_type: "Report", item_id: "r1", workspace_id: WS, workspace_name: "Sales Prod", item_name: "Churn watch" });
  assert.strictEqual(await group("Report").locator(".product-offer-item").count(), 1);
});
