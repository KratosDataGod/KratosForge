// app/ui/app.js beyond the other page tests: the Workspaces page's stars, pins and filters, a workspace's
// own page (its lakehouses, Open in notebook, New lakehouse with Fabric's name rule), a lakehouse's page
// and its tree of tables and files, the notebook's OneLake catalog dialog (Add data items: scopes,
// favorites, endorsement, the filter, a lakehouse's contents, Attach and Remove), renaming a notebook from
// the breadcrumb, the rail's open workspace, and the startup component check in each way it can go
// (Python missing, an update that didn't take, an update or an engine install that fails, Windows
// blocking the engine, a developer install missing a package), the OneLake catalog page (search by table,
// stars, the Type filter, sorting) and the command bar's page commands. Engine calls are mocked (see harness.cjs);
// the shell's answers for the component check are set per case in the page.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const ME = "chris@contoso.com";
const copy = (x) => JSON.parse(JSON.stringify(x));

const WORKSPACES = [{ id: "w1", displayName: "Sales" }, { id: "w2", displayName: "Ops" }];
// service.catalog: lakehouses across every workspace, with this PC's favorites.
const CATALOG = {
  workspaces: WORKSPACES,
  items: [
    { item_id: "lh1", lakehouse_id: "lh1", name: "LH_Bronze", workspace_id: "w1", workspace_name: "Sales", type: "Lakehouse" },
    { item_id: "lh2", lakehouse_id: "lh2", name: "LH_Gold", workspace_id: "w1", workspace_name: "Sales", type: "Lakehouse" },
    { item_id: "lh3", lakehouse_id: "lh3", name: "OpsLake", workspace_id: "w2", workspace_name: "Ops", type: "Lakehouse" },
  ],
  favorites: [], skipped: ["Finance"],
};
const FILES = { Files: [{ name: "raw", path: "Files/raw", is_dir: true }, { name: "notes.txt", path: "Files/notes.txt", size: 17 }],
  "Files/raw": [{ name: "a.csv", path: "Files/raw/a.csv", size: 2048 }], "Files/empty": [] };

let db;
const fresh = () => ({ marks: { favorites: [], pins: [] }, favorites: [], moreItems: [], endorsements: { pending: false, available: true, items: { lh2: "Certified", lh3: "Promoted" } } });
const mocks = {
  list_workspaces: () => copy(WORKSPACES),
  workspace_marks: () => copy(db.marks),
  set_workspace_mark: ({ workspace_id, mark, on }) => {
    db.marks[mark] = on ? [...db.marks[mark], workspace_id] : db.marks[mark].filter((id) => id !== workspace_id);
    return copy(db.marks);
  },
  list_items: () => [],
  list_lakehouses: ({ workspace_id }) => (workspace_id === "w1" ? [{ id: "lh1", displayName: "LH_Bronze" }] : []),
  list_tables: () => ({ dbo: ["orders", "customers"], staging: [] }),
  list_files: ({ directory }) => copy(FILES[directory] || []),
  create_lakehouse: ({ name }) => ({ id: "lh9", displayName: name }),
  new_notebook: () => ({ name: "Notebook 7", cells: [] }),
  save_notebook: () => ({ saved: true }),
  rename_notebook: () => ({ renamed: true }),
  catalog: () => { const c = copy(CATALOG); c.items.push(...copy(db.moreItems)); return { ...c, favorites: [...db.favorites] }; },
  catalog_tables: () => ({ pending: false, items: { lh1: ["orders", "customers"], lh2: ["sales_gold"], lh3: [] } }),
  meta_project: () => ({ layers: [{ name: "gold", lakehouse: "LH_Gold" }] }),
  endorsements: () => copy(db.endorsements),
  set_favorite: ({ lakehouse_id, favorite }) => {
    db.favorites = favorite ? [...db.favorites, lakehouse_id] : db.favorites.filter((id) => id !== lakehouse_id);
    return [...db.favorites];
  },
};

before(async () => {
  if (skip) return;
  db = fresh();
  app = await openApp({ mocks });
  await startupReads(app);
  // The component check's shell answers, per case: each command maps to an answer, or a list of answers in turn.
  // An answer can emit component-log lines, wait for window.__release(), throw, or return a value.
  await page().evaluate(() => {
    window.__shellSpec = null;
    const real = window.__TAURI__.core.invoke;
    window.__TAURI__.core.invoke = async (cmd, args) => {
      const spec = window.__shellSpec;
      if (!spec || !(cmd in spec)) return real(cmd, args);
      window.__fdShellCalls.push({ cmd, args });
      const s = spec[cmd];
      const one = Array.isArray(s) ? (s.length > 1 ? s.shift() : s[0]) : s;
      for (const line of one.emit || []) window.__fdEmit("component-log", line);
      if (one.hold) await new Promise((r) => { window.__release = r; });
      if (one.throw) throw one.throw;
      return one.value;
    };
  });
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  db = fresh();
  await page().evaluate((me) => {
    window.__shellSpec = null; window.__fdShellCalls.length = 0;
    document.getElementById("dialog-root").replaceChildren();
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    state.user = me; state.workspaces = null; state.endorsements = null; state.tree = {};
    Object.assign(workspacesPage, { marks: null, filter: "all" }); workspacesPage.open.clear();
    go("home");
  }, ME);
});

const dialog = () => page().locator("#dialog-root .dialog");
const callsFrom = (from, method) => app.calls.slice(from).filter((c) => c.method === method).map((c) => c.params);
const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test(document.getElementById("toast").textContent), re.source);

// ---------- Workspaces, a workspace, a lakehouse ----------

test("Workspaces stars and pins a workspace, and its filters say when nothing is starred or pinned", { skip }, async () => {
  await page().evaluate(() => go("workspaces"));
  const row = (name) => page().locator(".ws-list > tbody > tr", { hasText: name });
  await row("Ops").waitFor();
  await page().getByRole("button", { name: "Favorites 0" }).click();
  await page().getByText("No favorite workspaces yet. Click the star next to one.").waitFor();
  await page().getByRole("button", { name: "Pinned 0" }).click();
  await page().getByText("No pinned workspaces yet. Click the pin next to one.").waitFor();
  await page().getByRole("button", { name: "All 2" }).click();

  const from = app.calls.length;
  await row("Ops").getByRole("button", { name: "Pin to the top" }).click();
  await row("Ops").getByRole("button", { name: "Unpin" }).waitFor();
  assert.deepStrictEqual(await page().locator(".ws-list > tbody > tr td:nth-child(4)").allInnerTexts(), ["Ops", "Sales"], "a pinned workspace comes first");
  await row("Sales").getByRole("button", { name: "Add to favorites" }).click();
  await row("Sales").getByRole("button", { name: "Remove from favorites" }).waitFor();
  assert.deepStrictEqual(callsFrom(from, "set_workspace_mark"), [{ workspace_id: "w2", mark: "pins", on: true }, { workspace_id: "w1", mark: "favorites", on: true }]);
  await page().getByRole("button", { name: "Favorites 1" }).click();
  assert.deepStrictEqual(await page().locator(".ws-list > tbody > tr td:nth-child(4)").allInnerTexts(), ["Sales"]);
  await page().getByRole("button", { name: "Pinned 1" }).click();
  assert.deepStrictEqual(await page().locator(".ws-list > tbody > tr td:nth-child(4)").allInnerTexts(), ["Ops"]);
  await row("Ops").getByRole("button", { name: "Unpin" }).click();
  await page().getByText("No pinned workspaces yet.", { exact: false }).waitFor();
  assert.deepStrictEqual(callsFrom(from, "set_workspace_mark").at(-1), { workspace_id: "w2", mark: "pins", on: false });
  assert.deepStrictEqual(app.errors, []);
});

test("a workspace's page lists its lakehouses; its lakehouse page shows the tables and files a click opens", { skip }, async () => {
  await page().evaluate(() => go("workspaces"));
  await page().locator(".ws-list > tbody > tr", { hasText: "Ops" }).locator("td").nth(3).click();
  await page().getByRole("heading", { name: "Ops" }).waitFor();
  await page().getByText("No lakehouses in this workspace yet.").waitFor();
  // The rail keeps the open workspace; the breadcrumb goes back to Workspaces.
  assert.match(await page().locator("#breadcrumb").innerText(), /Workspaces\s*\/\s*Ops/);
  await page().locator("#rail-open").getByRole("button", { name: "Close Ops" }).click();
  await page().waitForFunction(() => state.view === "home" && !state.openWorkspace);

  await page().evaluate(() => go("workspaces"));
  await page().locator(".ws-list > tbody > tr", { hasText: "Sales" }).locator("td").nth(3).click();
  const row = page().locator("table.list tbody tr", { hasText: "LH_Bronze" });
  await row.waitFor();
  await row.locator("td").first().click();
  await page().getByRole("heading", { name: "LH_Bronze" }).waitFor();
  assert.match(await page().locator(".page .sub").first().innerText(), /Lakehouse in Sales\./);
  assert.match(await page().locator("#breadcrumb").innerText(), /Sales\s*\/\s*LH_Bronze/);

  const from = app.calls.length;
  const tree = page().locator(".lakehouse-tree");
  await tree.getByRole("button", { name: "Tables" }).click();
  await tree.getByRole("button", { name: "orders" }).waitFor();
  assert.deepStrictEqual(callsFrom(from, "list_tables"), [{ workspace_id: "w1", lakehouse_id: "lh1" }]);
  assert.match(await tree.innerText(), /staging\s+No tables/);
  await tree.getByRole("button", { name: "dbo" }).click();
  await tree.getByRole("button", { name: "orders" }).waitFor({ state: "detached" });
  await tree.getByRole("button", { name: "Files", exact: true }).click();
  await tree.getByRole("button", { name: "raw" }).click();
  await tree.getByRole("button", { name: /a\.csv/ }).waitFor();
  assert.match(await tree.getByRole("button", { name: /a\.csv/ }).innerText(), /a\.csv\s*2\.0 KB/);
  assert.match(await tree.getByRole("button", { name: /notes\.txt/ }).innerText(), /17 B/);
  assert.deepStrictEqual(callsFrom(from, "list_files").map((p) => p.directory), ["Files", "Files/raw"]);

  // The breadcrumb's workspace goes back to its page.
  await page().locator("#breadcrumb").getByRole("button", { name: "Sales" }).click();
  await page().getByRole("heading", { name: "Sales" }).waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("New lakehouse keeps to Fabric's name rule, creates it in that workspace and opens it; Open in notebook starts one on it", { skip }, async () => {
  await page().evaluate(() => openWorkspace({ id: "w1", displayName: "Sales" }));
  await page().getByRole("button", { name: "New lakehouse" }).click();
  const name = dialog().getByLabel(/Name \(created in Sales with schemas enabled\)/);
  await name.fill("2024 sales-data");
  await dialog().getByRole("button", { name: "Create" }).click();
  await dialog().getByText("Use only letters, numbers and underscores, starting with a letter. For example LH_2024_sales_data.").waitFor();
  await name.fill("x".repeat(300));
  await dialog().getByRole("button", { name: "Create" }).click();
  await dialog().getByText("A lakehouse name can be at most 256 characters.").waitFor();
  const from = app.calls.length;
  await name.fill("SalesSilver");
  await name.press("Enter");
  await page().getByRole("heading", { name: "SalesSilver" }).waitFor();
  await dialog().waitFor({ state: "detached" });  // Enter must not reopen the prompt from the button focus returns to
  assert.deepStrictEqual(callsFrom(from, "create_lakehouse"), [{ workspace_id: "w1", name: "SalesSilver" }]);

  await page().evaluate(() => openWorkspace({ id: "w1", displayName: "Sales" }));
  await page().getByRole("button", { name: "Open in notebook" }).click();
  await page().waitForFunction(() => state.view === "notebook" && state.notebook?.lakehouse?.lakehouse_id === "lh1");
  assert.match(await page().evaluate(() => state.notebook.cells[0].source), /from fabric_desktop import lakehouse/);
  assert.ok(await page().evaluate(() => state.notebook.dirty), "the new notebook has unsaved changes");
  assert.deepStrictEqual(app.errors, []);
});

// ---------- the notebook's OneLake catalog and its name ----------

test("Add data items lists lakehouses by scope, stars favorites, shows a lakehouse's contents, and Attach and Remove change the notebook", { skip }, async () => {
  await page().evaluate(() => { loadIntoState({ name: "Catalog test", cells: [] }); state.ribbonTab = "home"; go("notebook"); });
  const from = app.calls.length;
  await page().getByRole("button", { name: "Add data items" }).first().click();
  const d = dialog();
  await d.locator(".catalog-list table").waitFor();
  await page().waitForFunction(() => /Endorsed\s*2/.test(document.querySelector(".catalog-nav").innerText));
  assert.deepStrictEqual(callsFrom(from, "endorsements"), [{ workspace_ids: ["w1", "w2"] }]);
  assert.match(await d.locator(".catalog-nav").innerText(), /All items\s*3[\s\S]*Favorites\s*0[\s\S]*Sales\s*2[\s\S]*Ops\s*1/);
  assert.match(await d.locator(".catalog-foot").innerText(), /Skipped 1 workspace that could not be read: Finance\./);
  const rows = () => d.locator(".catalog-list tbody tr");
  assert.match(await rows().nth(1).innerText(), /LH_Gold\s+Sales\s+Certified\s+gold/, "endorsement and the metadata layer show");
  assert.ok(await d.getByRole("button", { name: "Attach" }).isDisabled());

  await d.locator(".catalog-scope", { hasText: "Favorites" }).click();
  await d.getByText("No favorites yet. Click the star next to a lakehouse.").waitFor();
  await d.locator(".catalog-scope", { hasText: "All items" }).click();
  await rows().nth(2).getByRole("button", { name: "Add to favorites" }).click();
  await d.locator(".catalog-scope", { hasText: "Favorites" }).getByText("1").waitFor();
  assert.deepStrictEqual(callsFrom(from, "set_favorite"), [{ lakehouse_id: "lh3", favorite: true }]);
  await d.locator(".catalog-scope", { hasText: "Favorites" }).click();
  assert.deepStrictEqual(await rows().locator("td:nth-child(2)").allInnerTexts(), ["OpsLake"]);
  await d.locator(".catalog-scope", { hasText: "Endorsed" }).click();
  assert.deepStrictEqual(await rows().locator("td:nth-child(2)").allInnerTexts(), ["LH_Gold", "OpsLake"]);
  await d.locator(".catalog-scope", { hasText: "Sales" }).click();
  assert.match(await d.locator(".catalog-head").innerText(), /Sales\s*2 lakehouses/);
  await d.getByLabel("Filter lakehouses").fill("bronze");
  assert.deepStrictEqual(await rows().locator("td:nth-child(2)").allInnerTexts(), ["LH_Bronze"]);
  await d.getByLabel("Filter lakehouses").fill("nothing like it");
  await d.getByText("No lakehouses match.").waitFor();
  await d.getByLabel("Filter lakehouses").fill("");

  // Selecting one shows its contents; Attach puts it on the notebook.
  await rows().first().click();
  assert.match(await d.locator(".catalog-detail").innerText(), /LH_Bronze\s+Sales workspace\s+Contents/);
  await d.locator(".catalog-detail").getByRole("button", { name: "Tables" }).click();
  await d.locator(".catalog-detail").getByRole("button", { name: "customers" }).waitFor();
  await d.getByRole("button", { name: "Attach" }).click();
  await page().waitForFunction(() => state.notebook.lakehouse?.lakehouse_id === "lh1" && state.showExplorer && state.notebook.dirty);
  await page().locator(".lh-head").getByText("LH_Bronze").waitFor();

  // Opening it again marks the attached one, and a double click attaches another.
  await page().getByRole("button", { name: "Add data items" }).first().click();
  await d.locator(".catalog-list tbody tr.selected", { hasText: "LH_Bronze" }).waitFor();
  assert.match(await d.locator(".catalog-list tbody tr", { hasText: "LH_Bronze" }).innerText(), /Attached/);
  await d.locator(".catalog-list tbody tr", { hasText: "OpsLake" }).dblclick();
  await page().waitForFunction(() => state.notebook.lakehouse?.lakehouse_id === "lh3");

  // Remove lakehouse takes it off the notebook.
  await page().locator(".lh-head").getByRole("button", { name: "Remove lakehouse" }).click();
  await page().waitForFunction(() => state.notebook.lakehouse === null);
  assert.deepStrictEqual(app.errors, []);
});

test("Endorsed is off with the reason when endorsement can't be read", { skip }, async () => {
  db.endorsements = { pending: false, available: false, items: {}, reason: "Endorsement needs a Fabric admin's read access." };
  await page().evaluate(() => { loadIntoState({ name: "Catalog test 2", cells: [] }); state.ribbonTab = "home"; go("notebook"); });
  await page().getByRole("button", { name: "Add data items" }).first().click();
  const endorsed = dialog().locator(".catalog-scope", { hasText: "Endorsed" });
  await page().waitForFunction(() => document.querySelector(".catalog-foot")?.textContent.includes("Endorsement needs a Fabric admin's read access."));
  assert.ok(await endorsed.isDisabled());
  assert.strictEqual(await endorsed.getAttribute("title"), "Endorsement needs a Fabric admin's read access.");
  await dialog().getByRole("button", { name: "Cancel" }).click();
});

test("the breadcrumb renames the notebook after saving it; the same name changes nothing", { skip }, async () => {
  await page().evaluate(() => { loadIntoState({ name: "Draft", cells: [] }); go("notebook"); });
  const from = app.calls.length;
  await page().locator("#breadcrumb").getByRole("button", { name: "Draft" }).click();
  await dialog().getByLabel("Name").press("Enter");
  await dialog().waitFor({ state: "detached" });
  assert.deepStrictEqual(callsFrom(from, "rename_notebook"), []);
  await page().locator("#breadcrumb").getByRole("button", { name: "Draft" }).click();
  await dialog().getByLabel("Name").fill("Daily sales");
  await dialog().getByRole("button", { name: "Rename" }).click();
  await page().locator("#breadcrumb").getByRole("button", { name: "Daily sales" }).waitFor();
  assert.deepStrictEqual(callsFrom(from, "rename_notebook"), [{ name: "Draft", new_name: "Daily sales" }]);
  assert.strictEqual(callsFrom(from, "save_notebook").length, 1, "it saves before renaming");
  assert.ok(await page().evaluate(() => openNotebooks.has("Daily sales") && !openNotebooks.has("Draft")));
});

// ---------- the startup component check ----------

const REPORT = { app: { version: "1.4.0", commit: "aaaaaaa1111" }, sidecar: { version: "1.4.0", commit: "aaaaaaa1111" },
  latest: { channel: "stable", commit: "aaaaaaa1111" }, python_ok: true, python: "Python 3.12.4" };
const report = (over = {}) => ({ ...copy(REPORT), ...over });
// Starts the check with these shell answers, without waiting for it.
async function check(spec, afterInstall = false) {
  await page().evaluate(([s, again]) => { window.__shellSpec = s; window.__checkDone = false; checkComponents(again).then(() => { window.__checkDone = true; }); },
    [spec, afterInstall]);
}
const startRow = (key) => page().locator(".startup-row").nth({ app: 0, python: 1, sidecar: 2 }[key]);
const detail = (key) => startRow(key).locator(".startup-detail");
const waitDetail = (key, re) => page().waitForFunction(([i, src]) => new RegExp(src).test(document.querySelectorAll(".startup-row")[i]?.lastChild.textContent || ""),
  [{ app: 0, python: 1, sidecar: 2 }[key], re.source]);
async function continueAnyway() {
  await page().getByRole("button", { name: "Continue anyway" }).click();
  await page().waitForFunction(() => window.__checkDone && !document.querySelector(".startup-row"));
}

test("without Python the check says how to get it; Retry checks again and a good report closes it", { skip }, async () => {
  await check({ check_components: [{ throw: "Python wasn't found." }, { value: report() }] });
  await waitDetail("python", /^Python wasn't found\. Install Python 3\.12 or later from python\.org/);
  assert.strictEqual(await startRow("python").getAttribute("data-status"), "error");
  assert.strictEqual(await detail("app").innerText(), "Needs Python");
  assert.strictEqual(await detail("sidecar").innerText(), "Needs Python");
  await page().getByRole("button", { name: "Retry" }).click();
  await page().waitForFunction(() => window.__checkDone && !document.querySelector(".startup-row"));
  assert.strictEqual(await page().evaluate(() => window.__fdShellCalls.filter((c) => c.cmd === "check_components").length), 2);

  await check({ check_components: { throw: "Python wasn't found." } });
  await waitDetail("python", /Python wasn't found/);
  await continueAnyway();
});

test("an update that didn't take, and a Python too old, are errors; Continue anyway goes on", { skip }, async () => {
  await check({ check_components: { value: report({ app: { version: "1.4.0", commit: "aaaaaaa1111", updated_to: "bbbbbbb2222" },
    python_ok: false, python: "Python 3.9.1" }) } });
  await waitDetail("app", /^The update didn't take: this is still build aaaaaaa, not bbbbbbb\. Download kratosforge\.exe again/);
  assert.strictEqual(await detail("python").innerText(), "Python 3.9.1 is too old. KratosForge needs Python 3.12 or later.");
  assert.strictEqual(await detail("sidecar").innerText(), "Needs Python 3.12 or later");
  await continueAnyway();
});

test("a developer install says what it lacks, with the build an organization rolls out", { skip }, async () => {
  await check({ check_components: { value: report({ auto_update: false, latest: { channel: "stable", commit: "bbbbbbb2222" },
    sidecar: { version: "1.4.0.dev0", editable: true, error: "ModuleNotFoundError: No module named 'yaml'" } }) } });
  await waitDetail("sidecar", /^1\.4\.0\.dev0 does not start: ModuleNotFoundError: No module named 'yaml'\. This Python \(Python 3\.12\.4\) is missing a package/);
  assert.match(await detail("sidecar").innerText(), /python -m pip install -e \. -c constraints\.txt$/);
  assert.strictEqual(await detail("app").innerText(), "Build aaaaaaa. Build bbbbbbb is out; your organization rolls out updates");
  await continueAnyway();

  // Not installed at all, and the update check couldn't reach the channel.
  await check({ check_components: { value: report({ latest: { channel: "beta", commit: null, error: "offline" },
    sidecar: { version: null, editable: true } }) } });
  await waitDetail("sidecar", /^Not installed\. In the sidecar folder, run: python -m pip install -e \.$/);
  assert.strictEqual(await detail("app").innerText(), "Build aaaaaaa (beta channel). Could not check for a newer build: offline");
  assert.strictEqual(await startRow("app").getAttribute("data-status"), "warn");
  await continueAnyway();
});

test("Windows blocking the engine explains Smart App Control or the App Control policy instead of reinstalling", { skip }, async () => {
  await check({ check_components: { value: report({ latest: { channel: "stable", commit: "bbbbbbb2222", refused: "The update channel's version.json has no checksum." },
    sidecar: { version: "1.4.0", commit: "aaaaaaa1111", error: "DLL load failed", blocked: { by: "smart_app_control", packages: { polars: ["a.pyd", "b.pyd"], numpy: ["c.pyd"] } } } }) } });
  await waitDetail("sidecar", /^Windows Smart App Control blocked it\.$/);
  const help = page().locator(".startup-help");
  assert.match(await help.innerText(), /It stopped files the local engine needs: polars \(2 files\), numpy \(1 file\)\. Nothing is wrong with the install/);
  assert.match(await help.innerText(), /Smart App Control can't allow just one app/);
  assert.strictEqual(await detail("app").innerText(), "Build aaaaaaa. The update channel's version.json has no checksum.");
  await continueAnyway();
});

test("an engine update streams pip's output; a block found while installing explains it, and a plain failure keeps the output", { skip }, async () => {
  const old = { version: "1.3.0", commit: "ccccccc3333" };
  await check({ check_components: { value: report({ sidecar: old }) },
    update_sidecar: { emit: ["Collecting polars", "blocked {\"by\": \"app_control\", \"packages\": {\"polars\": [\"x.pyd\"]}, \"location\": \"C:\\\\KratosForge\\\\python\"}"],
      hold: true, throw: "pip failed\nCollecting polars" } });
  await waitDetail("sidecar", /^Updating to build aaaaaaa…$/);
  await page().locator(".startup-log", { hasText: "Collecting polars" }).waitFor();
  await page().evaluate(() => window.__release());
  await waitDetail("sidecar", /^An App Control policy on this PC blocked it\.$/);
  assert.match(await page().locator(".startup-help").innerText(), /Ask your IT admin to allow the Python packages in C:\\KratosForge\\python\./);
  assert.ok(await page().locator(".startup-log").isHidden(), "the pip output gives way to the explanation");
  await continueAnyway();

  await check({ check_components: { value: report({ sidecar: { version: null } }) },
    update_sidecar: { emit: ["Collecting polars"], throw: "Collecting polars\nERROR: No matching distribution found for polars" } });
  await waitDetail("sidecar", /^Update failed\. The pip output is below\.$/);
  assert.strictEqual((await page().locator(".startup-log").innerText()).trim(), "Collecting polars\nERROR: No matching distribution found for polars");
  await continueAnyway();

  // A successful update closes the check by itself.
  await check({ check_components: { value: report({ sidecar: old }) }, update_sidecar: { value: {} } });
  await page().waitForFunction(() => window.__checkDone && !document.querySelector(".startup-row"));
  assert.ok(await page().evaluate(() => window.__fdShellCalls.some((c) => c.cmd === "update_sidecar")));
});

test("a newer build downloads with its progress, and a failed update says which build you are still on", { skip }, async () => {
  await check({ check_components: { value: report({ latest: { channel: "stable", commit: "bbbbbbb2222" } }) },
    update_app: { emit: ["Build bbbbbbb was replaced on the update channel by build ddddddd", "progress 40"], hold: true, throw: "The download was cut off." } });
  await waitDetail("app", /^Downloading build ddddddd… 40%$/);
  assert.deepStrictEqual(await page().evaluate(() => window.__fdShellCalls.find((c) => c.cmd === "update_app").args), { commit: "bbbbbbb2222" });
  await page().evaluate(() => window.__release());
  await waitDetail("app", /^Update to build bbbbbbb failed: The download was cut off\. You are still on aaaaaaa\.$/);
  await continueAnyway();
});

test("after installing a bundled engine that still doesn't match, the check says to restart, or to ask IT", { skip }, async () => {
  const bundled = (over) => report({ bundled: true, python: "Python 3.12.4", sidecar: { version: "1.3.0", commit: "eeeeeee5555" }, ...over });
  await check({ check_components: { value: bundled() } }, true);
  await waitDetail("sidecar", /^The latest engine is build eeeeeee, but this app is aaaaaaa\. Restart KratosForge to update it\.$/);
  assert.strictEqual(await detail("python").innerText(), "Python 3.12.4, part of the engine");
  await continueAnyway();
  await check({ check_components: { value: bundled({ auto_update: false }) } }, true);
  await waitDetail("sidecar", /Your organization rolls out updates: ask IT for the current build of kratosforge\.exe\.$/);
  await continueAnyway();
});

// ---------- the OneLake catalog page ----------

test("the OneLake catalog finds items by table name, stars one, narrows by type and sorts by a column", { skip }, async () => {
  db.moreItems = [
    { item_id: "nb1", name: "Daily load", workspace_id: "w2", workspace_name: "Ops", type: "Notebook" },
    { item_id: "dp1", name: "Sales pipeline", workspace_id: "w1", workspace_name: "Sales", type: "DataPipeline" },
  ];
  await page().evaluate(() => {
    Object.assign(catalogPage, { data: null, tables: null, tab: "catalog", scope: "all", type: "all", query: "", wsQuery: "",
      sort: "name", dir: 1, groupBy: "none", env: "all", typePanel: false });
    catalogPage.open.clear(); catalogPage.typeOpen.clear();
    go("onelake");
  });
  const names = () => page().locator(".catalog-list tbody tr td:nth-child(3)").allInnerTexts();
  const row = (name) => page().locator(".catalog-list tbody tr", { hasText: name });
  // Table names arrive after the items; the note says so until they do.
  await page().waitForFunction(() => document.querySelectorAll(".catalog-list tbody tr").length === 5 && catalogPage.tables);
  assert.deepStrictEqual(await names(), ["Daily load", "LH_Bronze", "LH_Gold", "OpsLake", "Sales pipeline"]);
  assert.strictEqual(await page().locator(".onelake-catalog .monitor-band .muted-text").last().innerText(), "Skipped 1 workspace: Finance.");
  assert.strictEqual(await row("LH_Gold").locator("td").nth(5).innerText(), "Certified");
  assert.strictEqual(await row("LH_Gold").locator("td").nth(6).innerText(), "gold", "the layer comes from the project");

  await page().getByLabel("Search the catalog").fill("sales_gold");
  await page().waitForFunction(() => document.querySelectorAll(".catalog-list tbody tr").length === 1);
  assert.deepStrictEqual(await names(), ["LH_Gold"], "a lakehouse is found by one of its tables");
  await page().getByLabel("Search the catalog").fill("");

  const from = app.calls.length;
  await row("OpsLake").getByRole("button", { name: "Add to favorites" }).click();
  await row("OpsLake").getByRole("button", { name: "Remove from favorites" }).waitFor();
  assert.deepStrictEqual(callsFrom(from, "set_favorite"), [{ lakehouse_id: "lh3", favorite: true }]);
  await page().locator(".catalog-scope", { hasText: "Favorites" }).click();
  await page().waitForFunction(() => document.querySelectorAll(".catalog-list tbody tr").length === 1);
  assert.deepStrictEqual(await names(), ["OpsLake"]);
  await page().locator(".catalog-scope", { hasText: "All items" }).click();

  // The Type filter: groups with their counts, a group opens to its types, and Clear goes back to all.
  await page().getByRole("button", { name: "Type: All (5)" }).click();
  const panel = page().locator(".type-panel");
  const groups = await panel.locator(".type-row-wrap .type-row").evaluateAll((rows) =>
    rows.map((r) => `${r.querySelector(".label").textContent} ${r.querySelector(".count").textContent}`));
  assert.deepStrictEqual(groups, ["Data 3", "Processes 2"]);
  await panel.getByRole("button", { name: "Show Processes types" }).click();
  await panel.getByRole("menuitemradio", { name: /Data pipeline/ }).waitFor();
  await panel.getByRole("menuitemradio", { name: /^Processes/ }).click();
  await page().getByRole("button", { name: "Type: Processes (2)" }).waitFor();
  assert.deepStrictEqual(await names(), ["Daily load", "Sales pipeline"]);
  await page().getByRole("button", { name: "Type: Processes (2)" }).click();
  await page().locator(".type-panel").getByRole("menuitemradio", { name: /^Notebook/ }).click();
  await page().getByRole("button", { name: "Type: Notebook (1)" }).waitFor();
  assert.deepStrictEqual(await names(), ["Daily load"]);
  await page().locator(".catalog-head").getByRole("button", { name: "Clear" }).click();
  await page().getByRole("button", { name: "Type: All (5)" }).waitFor();

  // A column header sorts by it, and again the other way.
  await page().getByRole("button", { name: "Endorsement", exact: true }).click();
  assert.deepStrictEqual((await names()).slice(0, 2), ["LH_Gold", "OpsLake"], "Certified, then Promoted, then the rest");
  await page().getByRole("button", { name: "Endorsement ▲" }).click();
  await page().getByRole("button", { name: "Endorsement ▼" }).waitFor();
  assert.deepStrictEqual((await names()).slice(-2), ["OpsLake", "LH_Gold"]);
  assert.deepStrictEqual(app.errors, []);
});

// ---------- the command bar ----------

test("the command bar's page commands open their page and panel; New lakehouse first asks for a workspace", { skip }, async () => {
  await page().evaluate(() => { state.notebook = null; renderChrome(); });
  const run = async (label) => {
    await page().keyboard.press("Control+K");
    await page().getByRole("combobox", { name: "Command bar" }).fill(label);
    await page().locator("#palette-list").getByRole("option", { name: label, exact: true }).click();
  };
  await run("Move this notebook to another place");
  await toastSays(/^Open a notebook first\.$/);

  const from = app.calls.length;
  await run("New lakehouse");
  await dialog().getByLabel("Workspace").selectOption("w2");
  await dialog().getByRole("button", { name: "Next" }).click();
  await dialog().getByLabel("Name (created in Ops with schemas enabled)").waitFor();
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await dialog().waitFor({ state: "detached" });
  assert.deepStrictEqual(callsFrom(from, "create_lakehouse"), [], "nothing is created without a name");

  const cases = [
    ["OneLake catalog", () => state.view === "onelake" && catalogPage.tab === "catalog"],
    ["OneLake shortcuts", () => state.view === "onelake" && catalogPage.tab === "shortcuts" && scPage.view === "all"],
    ["Broken shortcuts", () => state.view === "onelake" && scPage.view === "health"],
    ["Shortcut changes", () => state.view === "onelake" && scPage.view === "changes"],
    ["OneLake access", () => state.view === "onelake" && catalogPage.tab === "access"],
    ["Fabric portal", () => state.view === "fabric"],
    ["Runs", () => state.view === "monitor" && monitorPage.panel === "runs"],
    ["KratosForge schedules", () => state.view === "monitor" && monitorPage.panel === "schedules"],
    ["Fabric schedules", () => state.view === "monitor" && monitorPage.panel === "fabric_schedules"],
    ["Fabric Monitoring hub", () => state.view === "monitor" && monitorPage.panel === "fabric"],
    ["Cost", () => state.view === "optimize" && optimizePage.tab === "cost"],
  ];
  for (const [label, ok] of cases) {
    await run(label);
    await page().waitForFunction(ok);
    await page().evaluate(() => { catalogPage.tab = "catalog"; go("home"); });
  }
});
