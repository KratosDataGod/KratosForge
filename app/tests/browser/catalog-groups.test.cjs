// #1398: OneLake > Catalog grouped by environment, by solution, or both (catalog-groups.js, drawn by
// renderOneLake in app.js), with items across fifteen workspaces (see harness.cjs for how to run it).
// Each grouping (None, Environment, Solution, Solution then environment) shows every item exactly once,
// under the right group, in a stable order. A workspace the Metadata environments give to Dev, Test or
// Prod (or as an environment's Keys workspace) is grouped there whatever its name says; otherwise a
// Dev / Test / Prod word in the name decides ("Devops Tools", "Developer Sandbox" and "Prodigy Labs"
// have none); a workspace with neither goes under No environment. "Adv Wrks DE 2 Dev", "... Test" and
// "... Prod" sit together by solution. The choice is kept after a reload, group headers are reached
// and opened from the keyboard (focus stays on the header), axe finds nothing serious in each grouping,
// and none of this sends a write.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const ME = "chris@contoso.com";
const READS = ["catalog", "meta_project", "catalog_tables", "endorsements"];
const WRITES = ["set_favorite", "meta_set_environment", "meta_create_workspace", "meta_init", "meta_open"];

// Workspaces as service.py catalog() lists them: {id, displayName, type}.
const WS = [
  ["w-adv-dev", "Adv Wrks DE 2 Dev"], ["w-adv-test", "Adv Wrks DE 2 Test"], ["w-adv-prod", "Adv Wrks DE 2 Prod"],
  ["w-sales", "Sales Test"],          // Metadata gives it to Prod: Prod, not Test
  ["w-hr", "HR Analytics"],           // Metadata Dev, no word in the name
  ["w-keys", "Shared Keys"],          // Test's Keys workspace in Metadata
  ["w-devops", "Devops Tools"],       // "Devops" is not the word Dev
  ["w-devsand", "Developer Sandbox"], // nor is "Developer"
  ["w-prodigy", "Prodigy Labs"],      // nor is "Prodigy" Prod
  ["w-prodsup", "Production Support"], // "Production" is a word catalog-groups.js reads as Prod
  ["w-bridge", "Test-Prod Bridge"],   // two words: the last one (Prod) decides
  ["w-mkt", "marketing dev"],         // lowercase
  ["w-uat", "Finance (UAT)"], ["w-prd", "Finance_PRD"],
  ["w-empty", "Empty Dev"],           // no items: no group
];
// Items as service.py catalog(all_types=True) rows: workspace_id, workspace_name, item_id, name, type, description.
const ITEMS = [
  ["w-adv-dev", "Adv Orders", "Lakehouse"], ["w-adv-dev", "Adv Load", "Notebook"],
  ["w-adv-test", "Adv Orders Test", "Lakehouse"],
  ["w-adv-prod", "Adv Orders Prod", "Lakehouse"], ["w-adv-prod", "Adv Sales Report", "Report"],
  ["w-sales", "Sales Model", "SemanticModel"], ["w-hr", "HR Headcount", "Notebook"], ["w-keys", "LH_Keys", "Lakehouse"],
  ["w-devops", "Deploy Pipeline", "DataPipeline"], ["w-devsand", "Scratch", "Notebook"], ["w-prodigy", "Prodigy Notes", "Notebook"],
  ["w-prodsup", "Support Tickets", "Warehouse"], ["w-bridge", "Bridge Sync", "Notebook"], ["w-mkt", "Campaigns", "Lakehouse"],
  ["w-uat", "Budget UAT", "Report"], ["w-prd", "Budget", "Report"],
];
const wsName = Object.fromEntries(WS);
const CATALOG = {
  workspaces: [{ id: "w-me", displayName: "My workspace", type: "Personal" }, ...WS.map(([id, displayName]) => ({ id, displayName, type: "Workspace" }))],
  // Listed in workspace order, not by name, so the table's sort is what orders the groups' rows.
  items: ITEMS.map(([ws, name, type], i) => ({ workspace_id: ws, workspace_name: wsName[ws], item_id: `i${i}`, name, type, description: "",
    ...(type === "Lakehouse" ? { lakehouse_id: `i${i}` } : {}) })),
  skipped: [], favorites: [],
};
// meta/project.py summary(): "environments" is environments.toml as read, one table per environment
// (meta_service.py meta_set_environment writes workspace_id, workspace_name and target; keys_workspace_id
// is Keys workspace, #293).
const META = { path: "C:/Users/chris/Documents/FabricDesktop/metadata", layers: [], entities: [],
  environments: {
    dev: { workspace_id: "w-hr", workspace_name: "HR Analytics", target: "onelake" },
    test: { workspace_id: "w-adv-test", workspace_name: "Adv Wrks DE 2 Test", target: "onelake",
      keys_workspace_id: "w-keys", keys_workspace_name: "Shared Keys" },
    prod: { workspace_id: "w-sales", workspace_name: "Sales Test", target: "onelake" },
  } };
const MOCKS = {
  catalog: () => CATALOG,
  meta_project: () => META,
  catalog_tables: () => ({ pending: false, items: {} }),
  endorsements: () => ({ pending: false, available: false, items: {}, reason: "Endorsement shows on a data product's page." }),
  set_favorite: () => [],
};

// What each grouping should show, worked out by hand. Rows keep the table's sort (by name).
const ENV_OF = { "Adv Load": "Dev", "Adv Orders": "Dev", "Campaigns": "Dev", "HR Headcount": "Dev",
  "Adv Orders Test": "Test", "Budget UAT": "Test", "LH_Keys": "Test",
  "Adv Orders Prod": "Prod", "Adv Sales Report": "Prod", "Bridge Sync": "Prod", "Budget": "Prod", "Sales Model": "Prod", "Support Tickets": "Prod",
  "Deploy Pipeline": "", "Prodigy Notes": "", "Scratch": "" };
const BY_NAME = ["Adv Load", "Adv Orders", "Adv Orders Prod", "Adv Orders Test", "Adv Sales Report", "Bridge Sync", "Budget", "Budget UAT",
  "Campaigns", "Deploy Pipeline", "HR Headcount", "LH_Keys", "Prodigy Notes", "Sales Model", "Scratch", "Support Tickets"];
const EXPECTED = {
  env: [
    ["Dev", ["Adv Load", "Adv Orders", "Campaigns", "HR Headcount"]],
    ["Test", ["Adv Orders Test", "Budget UAT", "LH_Keys"]],
    ["Prod", ["Adv Orders Prod", "Adv Sales Report", "Bridge Sync", "Budget", "Sales Model", "Support Tickets"]],
    ["No environment", ["Deploy Pipeline", "Prodigy Notes", "Scratch"]],
  ],
  solution: [
    ["Adv Wrks DE 2", ["Adv Load", "Adv Orders", "Adv Orders Prod", "Adv Orders Test", "Adv Sales Report"]],
    ["Developer Sandbox", ["Scratch"]], ["Devops Tools", ["Deploy Pipeline"]],
    ["Finance", ["Budget", "Budget UAT"]], ["HR Analytics", ["HR Headcount"]], ["marketing", ["Campaigns"]],
    ["Prodigy Labs", ["Prodigy Notes"]], ["Sales", ["Sales Model"]], ["Shared Keys", ["LH_Keys"]],
    ["Support", ["Support Tickets"]], ["Test Bridge", ["Bridge Sync"]],
  ],
  both: [
    ["Adv Wrks DE 2", [["Dev: Adv Wrks DE 2 Dev", ["Adv Load", "Adv Orders"]], ["Test: Adv Wrks DE 2 Test", ["Adv Orders Test"]],
      ["Prod: Adv Wrks DE 2 Prod", ["Adv Orders Prod", "Adv Sales Report"]]]],
    ["Developer Sandbox", [["No environment: Developer Sandbox", ["Scratch"]]]],
    ["Devops Tools", [["No environment: Devops Tools", ["Deploy Pipeline"]]]],
    ["Finance", [["Test: Finance (UAT)", ["Budget UAT"]], ["Prod: Finance_PRD", ["Budget"]]]],
    ["HR Analytics", [["Dev: HR Analytics", ["HR Headcount"]]]],
    ["marketing", [["Dev: marketing dev", ["Campaigns"]]]],
    ["Prodigy Labs", [["No environment: Prodigy Labs", ["Prodigy Notes"]]]],
    ["Sales", [["Prod: Sales Test", ["Sales Model"]]]],
    ["Shared Keys", [["Test: Shared Keys", ["LH_Keys"]]]],
    ["Support", [["Prod: Production Support", ["Support Tickets"]]]],
    ["Test Bridge", [["Prod: Test-Prod Bridge", ["Bridge Sync"]]]],
  ],
};

let app;
const page = () => app.page;
const writes = () => app.calls.filter((c) => WRITES.includes(c.method)).map((c) => c.method);

async function openCatalog() {
  await page().evaluate((me) => { state.user = me; catalogPage.tab = "catalog"; go("onelake"); }, ME);
  await page().locator(".onelake-catalog table.list").waitFor();
  // The Metadata environments arrive in their own read: wait until they're applied.
  await page().waitForFunction(() => catalogPage.envWs["w-sales"] === "prod");
}

async function groupBy(mode) {
  await page().getByLabel("Group by").selectOption(mode);
  await page().waitForFunction((m) => catalogPage.groupBy === m, mode);
}

// Opens every closed group header, one at a time (each click draws the table again).
async function openAll() {
  for (let i = 0; i < 40; i++) {
    const closed = page().locator(".onelake-catalog .group-head[aria-expanded='false']");
    if (!(await closed.count())) return;
    await closed.first().click();
  }
  throw new Error("groups kept closing");
}

// The table as drawn: [{depth, head, items}] for each group header and the item rows under it, or one
// headless group for an ungrouped table. head is "<env pill>: <label>", or whichever of the two is shown.
function readTable() {
  return page().evaluate(() => {
    const out = [];
    let cur = null;
    for (const tr of document.querySelectorAll(".onelake-catalog table.list > tbody > tr")) {
      if (tr.classList.contains("group-row")) {
        const pill = tr.querySelector(".env-pill")?.textContent.trim() || "";
        const label = tr.querySelector(".group-label")?.textContent.trim() || "";
        cur = { depth: tr.classList.contains("depth-1") ? 1 : 0, head: [pill, label].filter(Boolean).join(": "), items: [], env: [] };
        out.push(cur);
        continue;
      }
      if (tr.classList.contains("sub-row")) continue;
      if (!cur) { cur = { depth: 0, head: "", items: [], env: [] }; out.push(cur); }
      cur.items.push(tr.querySelectorAll("td")[2].textContent.trim());
      cur.env.push(tr.querySelectorAll("td")[4].querySelector(".env-pill")?.textContent.trim() || "");
    }
    return out;
  });
}

// Every item shows exactly once across the table, and each row's own pill names its environment.
function checkOnce(groups) {
  const names = groups.flatMap((g) => g.items);
  const dup = names.filter((n, i) => names.indexOf(n) !== i);
  assert.deepStrictEqual(dup, [], `shown twice: ${dup.join(", ")}`);
  assert.deepStrictEqual([...names].sort(), [...BY_NAME].sort(), "every item, once");
  for (const g of groups) g.items.forEach((n, i) => assert.strictEqual(g.env[i], ENV_OF[n], `${n}'s environment pill`));
}

const shape = (groups, mode) => {
  if (mode !== "both") return groups.map((g) => [g.head, g.items]);
  const out = [];
  for (const g of groups) {
    if (g.depth === 0) out.push([g.head, []]);
    else out.at(-1)[1].push([g.head, g.items]);
  }
  return out;
};

before(async () => {
  if (skip) return;
  // csp: this file reloads and reads localStorage back (harness.cjs).
  app = await openApp({ mocks: MOCKS, csp: true });
  await startupReads(app);
  await openCatalog();
});

after(async () => { await app?.close(); });

test("No grouping lists every item once, sorted by name, each with its environment", { skip }, async () => {
  assert.strictEqual(await page().getByLabel("Group by").inputValue(), "none");
  const groups = await readTable();
  assert.strictEqual(groups.length, 1);
  assert.strictEqual(groups[0].head, "");
  assert.deepStrictEqual(groups[0].items, BY_NAME);
  checkOnce(groups);
});

for (const [mode, label] of [["env", "Environment"], ["solution", "Solution"], ["both", "Solution, then environment"]]) {
  test(`${label}: every item once, under the right group, in a stable order`, { skip }, async () => {
    await groupBy(mode);
    // The first group starts open and the rest closed.
    const heads = page().locator(".onelake-catalog .group-row.depth-0 .group-head");
    assert.strictEqual(await heads.first().getAttribute("aria-expanded"), "true");
    assert.strictEqual(await heads.count(), EXPECTED[mode].length);
    assert.strictEqual(await page().locator(".onelake-catalog .group-row.depth-0 .group-head[aria-expanded='true']").count(), 1);
    await openAll();
    const groups = await readTable();
    checkOnce(groups);
    assert.deepStrictEqual(shape(groups, mode), EXPECTED[mode]);
    // The same again when the table is drawn afresh (another page and back): a stable order.
    await page().evaluate(() => go("home"));
    await openCatalog();
    await openAll();
    assert.deepStrictEqual(shape(await readTable(), mode), EXPECTED[mode]);
    if (axeSource) {
      // The page has the shell's CSP, which refuses an inline script tag: put axe in by evaluating it.
      await page().evaluate(`${axeSource};0`);
      assert.deepStrictEqual(await axeFindings(page(), ".onelake-catalog"), [], `axe in ${label}`);
    }
  });
}

test("Metadata decides first, then the name's word, else No environment", { skip }, async () => {
  const envOf = await page().evaluate((ws) => Object.fromEntries(ws.map(([id, name]) => [name, workspaceEnv(id, name, catalogPage.envWs)])), WS);
  assert.deepStrictEqual(envOf, {
    "Adv Wrks DE 2 Dev": "dev", "Adv Wrks DE 2 Test": "test", "Adv Wrks DE 2 Prod": "prod",
    "Sales Test": "prod", "HR Analytics": "dev", "Shared Keys": "test",
    "Devops Tools": "none", "Developer Sandbox": "none", "Prodigy Labs": "none",
    "Production Support": "prod", "Test-Prod Bridge": "prod", "marketing dev": "dev",
    "Finance (UAT)": "test", "Finance_PRD": "prod", "Empty Dev": "dev",
  });
  // The Environments chips count the same way.
  const chips = await page().locator(".catalog-envs .chip").allTextContents();
  assert.deepStrictEqual(chips, ["All", "Dev 4", "Test 3", "Prod 6"]);
});

test("Adv Wrks DE 2 Dev, Test and Prod sit together by solution", { skip }, async () => {
  await groupBy("solution");
  const head = page().locator(".onelake-catalog .group-row.depth-0", { hasText: "Adv Wrks DE 2" });
  assert.strictEqual(await head.count(), 1);
  assert.match(await head.innerText(), /Adv Wrks DE 2\s+3 workspaces\s+·\s+5 items/);
});

test("The grouping is kept after a reload", { skip }, async () => {
  await groupBy("both");
  assert.strictEqual(await page().evaluate(() => localStorage.getItem("fd.catalog.groupBy")), "both");
  await page().reload();
  await page().waitForFunction(() => typeof openSqlQuery === "function");
  await openCatalog();
  assert.strictEqual(await page().getByLabel("Group by").inputValue(), "both");
  await page().locator(".onelake-catalog .group-row.depth-1").first().waitFor();
  await openAll();
  assert.deepStrictEqual(shape(await readTable(), "both"), EXPECTED.both);
  await groupBy("env");
  await page().reload();
  await page().waitForFunction(() => typeof openSqlQuery === "function");
  await openCatalog();
  assert.strictEqual(await page().getByLabel("Group by").inputValue(), "env");
});

test("Group headers are reached and opened from the keyboard", { skip }, async () => {
  await groupBy("env");
  const select = page().getByLabel("Group by");
  await select.focus();
  // Tab on from Group by reaches the column headers, then the first group's header.
  const first = page().locator(".onelake-catalog .group-head").first();
  for (let i = 0; i < 12 && !(await first.evaluate((el) => el === document.activeElement)); i++) await page().keyboard.press("Tab");
  assert.ok(await first.evaluate((el) => el === document.activeElement), "Tab reaches the first group header");
  assert.match(await first.innerText(), /^Dev/);
  // Enter closes it and focus stays on it; Space opens it again.
  await page().keyboard.press("Enter");
  const dev = page().locator(".onelake-catalog .group-head", { hasText: "Dev" }).first();
  assert.strictEqual(await dev.getAttribute("aria-expanded"), "false");
  assert.ok(await dev.evaluate((el) => el === document.activeElement), "focus stays on the header it opened");
  await page().keyboard.press("Space");
  assert.strictEqual(await dev.getAttribute("aria-expanded"), "true");
  // Tab past Dev's items to Test's header and open it with Enter.
  const testHead = page().locator(".onelake-catalog .group-head", { hasText: "Test" }).first();
  assert.strictEqual(await testHead.getAttribute("aria-expanded"), "false");
  for (let i = 0; i < 60 && !(await testHead.evaluate((el) => el === document.activeElement)); i++) await page().keyboard.press("Tab");
  assert.ok(await testHead.evaluate((el) => el === document.activeElement), "Tab reaches the Test header");
  await page().keyboard.press("Enter");
  assert.strictEqual(await testHead.getAttribute("aria-expanded"), "true");
  assert.ok(await testHead.evaluate((el) => el === document.activeElement));
  const groups = await readTable();
  assert.deepStrictEqual(groups.find((g) => g.head === "Test").items, EXPECTED.env[1][1]);
});

test("Grouping the catalog sends no write", { skip }, () => {
  assert.deepStrictEqual(writes(), []);
  for (const m of READS) assert.ok(app.calls.some((c) => c.method === m), `${m} was read`);
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
