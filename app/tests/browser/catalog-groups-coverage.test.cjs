// OneLake > Catalog grouping (catalog-groups.js) for workspace names the main test doesn't use: an environment
// in brackets ("Sales [Prod]"), as a prefix ("DEV_Retail"), after a dash ("Retail - Prod"), after a dot
// ("Ops.QA"), and a workspace named only "Dev". Metadata gives one Keys workspace to both Test and Prod: the
// catalog groups it under Test (the first), while a warehouse change in it asks as Prod (the strictest,
// sqlchanges.js). The page is opened from file:// (no reload here) and nothing is written.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const ME = "chris@contoso.com";

// Workspaces as service.py catalog() lists them, and one item in each.
const WS = [
  ["w-sales-prod", "Sales [Prod]", "Sales Gold", "Lakehouse"],
  ["w-sales-dev", "Sales [Dev]", "Sales Bronze", "Lakehouse"],
  ["w-ret-dev", "DEV_Retail", "Retail Load", "Notebook"],
  ["w-ret-prod", "Retail - Prod", "Retail Report", "Report"],
  ["w-ops", "Ops.QA", "Ops Checks", "Notebook"],
  ["w-dev", "Dev", "Scratchpad", "Notebook"],
  ["w-keys", "Shared Keys", "Keys", "Lakehouse"],
  ["w-lone", "Lone Space", "Notes", "Notebook"],
];
const CATALOG = {
  workspaces: WS.map(([id, displayName]) => ({ id, displayName, type: "Workspace" })),
  items: WS.map(([ws, wsName, name, type], i) => ({ workspace_id: ws, workspace_name: wsName, item_id: `i${i}`, name, type, description: "",
    ...(type === "Lakehouse" ? { lakehouse_id: `i${i}` } : {}) })),
  skipped: [], favorites: [],
};
// environments.toml as meta/project.py summary() reads it: Test and Prod share the Keys workspace (#293).
const META = { path: "C:/Users/chris/Documents/FabricDesktop/metadata", layers: [], entities: [],
  environments: {
    test: { workspace_id: "w-ops", workspace_name: "Ops.QA", target: "onelake", keys_workspace_id: "w-keys", keys_workspace_name: "Shared Keys" },
    prod: { workspace_id: "w-ret-prod", workspace_name: "Retail - Prod", target: "onelake", keys_workspace_id: "w-keys", keys_workspace_name: "Shared Keys" },
  } };
const MOCKS = {
  catalog: () => CATALOG,
  meta_project: () => META,
  catalog_tables: () => ({ pending: false, items: {} }),
  endorsements: () => ({ pending: false, available: false, items: {}, reason: "Endorsement shows on a data product's page." }),
};
const WRITES = ["set_favorite", "meta_set_environment", "meta_create_workspace", "sql_exec"];

let app;
const page = () => app.page;

async function groupBy(mode) {
  await page().getByLabel("Group by").selectOption(mode);
  await page().waitForFunction((m) => catalogPage.groupBy === m, mode);
}

async function openAll() {
  for (let i = 0; i < 40; i++) {
    const closed = page().locator(".onelake-catalog .group-head[aria-expanded='false']");
    if (!(await closed.count())) return;
    await closed.first().click();
  }
  throw new Error("groups kept closing");
}

// [head, [item names]] per group; in "both", [solution, [[env: workspaces, items]...]].
function readTable(nested) {
  return page().evaluate((nested) => {
    const out = [];
    let cur = null;
    for (const tr of document.querySelectorAll(".onelake-catalog table.list > tbody > tr")) {
      if (tr.classList.contains("group-row")) {
        const pill = tr.querySelector(".env-pill")?.textContent.trim() || "";
        const label = tr.querySelector(".group-label")?.textContent.trim() || "";
        const head = [pill, label].filter(Boolean).join(": ");
        if (nested && tr.classList.contains("depth-0")) { out.push([head, []]); cur = null; continue; }
        cur = [head, []];
        (nested ? out.at(-1)[1] : out).push(cur);
        continue;
      }
      if (tr.classList.contains("sub-row") || !cur) continue;
      cur[1].push(tr.querySelectorAll("td")[2].textContent.trim());
    }
    return out;
  }, nested);
}

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: MOCKS, storage: { "fd.catalog.groupBy": "none" } });
  await page().evaluate((me) => { state.user = me; catalogPage.tab = "catalog"; go("onelake"); }, ME);
  await page().locator(".onelake-catalog table.list").waitFor();
  await page().waitForFunction(() => catalogPage.envWs["w-keys"] === "test");
});

after(async () => { await app?.close(); });

test("Environment: brackets, a prefix, a dash and a dot each name the environment; Metadata's Keys workspace is Test", { skip }, async () => {
  await groupBy("env");
  await openAll();
  assert.deepStrictEqual(await readTable(false), [
    ["Dev", ["Retail Load", "Sales Bronze", "Scratchpad"]],
    ["Test", ["Keys", "Ops Checks"]],
    ["Prod", ["Retail Report", "Sales Gold"]],
    ["No environment", ["Notes"]],
  ]);
  const chips = await page().locator(".catalog-envs .chip").allTextContents();
  assert.deepStrictEqual(chips, ["All", "Dev 3", "Test 2", "Prod 2"]);
});

test("Solution: the environment word comes out of the name, a name that is only the word stays", { skip }, async () => {
  await groupBy("solution");
  await openAll();
  assert.deepStrictEqual(await readTable(false), [
    ["Dev", ["Scratchpad"]], ["Lone Space", ["Notes"]], ["Ops", ["Ops Checks"]],
    ["Retail", ["Retail Load", "Retail Report"]], ["Sales", ["Sales Bronze", "Sales Gold"]], ["Shared Keys", ["Keys"]],
  ]);
  const sales = page().locator(".onelake-catalog .group-row.depth-0", { hasText: "Sales" });
  assert.match(await sales.innerText(), /Sales\s+2 workspaces\s+·\s+2 items/);
});

test("Solution, then environment: each solution's workspaces under Dev, Test and Prod in that order", { skip }, async () => {
  await groupBy("both");
  await openAll();
  assert.deepStrictEqual(await readTable(true), [
    ["Dev", [["Dev: Dev", ["Scratchpad"]]]],
    ["Lone Space", [["No environment: Lone Space", ["Notes"]]]],
    ["Ops", [["Test: Ops.QA", ["Ops Checks"]]]],
    ["Retail", [["Dev: DEV_Retail", ["Retail Load"]], ["Prod: Retail - Prod", ["Retail Report"]]]],
    ["Sales", [["Dev: Sales [Dev]", ["Sales Bronze"]], ["Prod: Sales [Prod]", ["Sales Gold"]]]],
    ["Shared Keys", [["Test: Shared Keys", ["Keys"]]]],
  ]);
});

test("Picking an environment chip lists only that environment's items", { skip }, async () => {
  await groupBy("none");
  await page().locator(".catalog-envs .chip", { hasText: "Prod 2" }).click();
  await page().waitForFunction(() => catalogPage.env === "prod");
  const names = await page().locator(".onelake-catalog table.list > tbody > tr:not(.sub-row) td:nth-child(3)").allTextContents();
  assert.deepStrictEqual(names.map((n) => n.trim()), ["Retail Report", "Sales Gold"]);
  await page().locator(".catalog-envs .chip", { hasText: "All" }).click();
  await page().waitForFunction(() => catalogPage.env === "all");
});

test("A warehouse change in the shared Keys workspace asks as Prod, the strictest, and wants the name typed", { skip }, async () => {
  const ref = { workspace_id: "w-keys", workspace_name: "Shared Keys", item_id: "wh-keys", name: "WH_Keys", kind: "warehouse" };
  const plan = { statements: [{ statement: 1, permission: "warehouse_write", reason: "UPDATE changes rows in dbo.keys" },
    { statement: 2, permission: "read", reason: "SELECT reads" }] };
  await page().evaluate(([ref, plan]) => { window.__answer = whChangeConfirm(ref, plan); }, [ref, plan]);
  const dialog = page().locator("#dialog-root .dialog");
  await dialog.waitFor();
  assert.match(await dialog.innerText(), /Change WH_Keys in Prod\?/);
  assert.match(await dialog.innerText(), /Environment\s+Prod/);
  assert.match(await dialog.innerText(), /Statement 1: UPDATE changes rows in dbo\.keys/);
  assert.match(await dialog.innerText(), /and 1 statement that reads or sets a variable/);
  const run = dialog.getByRole("button", { name: "Run in Prod" });
  assert.ok(await run.isDisabled());
  await dialog.getByLabel("Warehouse name").fill("WH_Keys");
  assert.ok(await run.isEnabled());
  await dialog.getByRole("button", { name: "Cancel" }).click();
  assert.strictEqual(await page().evaluate(() => window.__answer), false);
  // The catalog still has it as Test: grouping and the warehouse confirm read the same Metadata differently on purpose.
  assert.strictEqual(await page().evaluate(() => catalogPage.envWs["w-keys"]), "test");
});

test("grouping and the confirm send no write, and no page errors", { skip }, () => {
  assert.deepStrictEqual(app.calls.filter((c) => WRITES.includes(c.method)).map((c) => c.method), []);
  assert.deepStrictEqual(app.errors, []);
});
