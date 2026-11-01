// #1503: the read-only views no other browser test reached (see harness.cjs for how to run it).
// Source control's History: a commit's details (git_commit_detail), two ticked commits compared
// (git_compare, older first) and a file at a commit (git_file_at). A table's History dialog
// (meta_table_history) and the VL_Sources card (meta_varlib) on the metadata pages. A Fabric run's
// results once the runner saved them (meta_fabric_result). Links' "Table in <env>" column
// (mesh_resolve_links) and the Fabric domain hint on Domains > This project (mesh_workspace_domains).
// A notebook resource's Show in folder (notebook_resource_reveal), a cell result's Inspect profile
// (notebook_frame_profile) and the local data projects a pipeline's Script can run in
// (list_local_projects).
//
// For each: it sends exactly the parameters for what was picked, sends no write, shows the engine's
// answer, a refusal reads as a sentence, and axe finds nothing serious.
//
// The real engine answers Git (a temp repository), the metadata project (meta_init's sample in a temp
// home, bronze.customers loaded twice), the platform on this PC (mesh_overview, mesh_resolve_links over
// two link files), notebooks and their resources, the cell kernel, and the pipeline and local data
// calls; most refusals are real too (a commit rewritten away, an environment with no workspace, a run
// this PC has no record of, a deleted resource file, a restarted kernel). Mocks answer only what needs
// Fabric or the desktop, in the shape the engine returns: cloud_service.py meta_fabric_result for a
// finished run (its results are read from OneLake), central/service.py mesh_workspace_domains (asks
// Fabric), notebook_resources_service.py notebook_resource_reveal (opens File Explorer), and a few
// refusals the engine can't be made to give here, each with the text it sends.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;

// Every call these views could send that changes something. None may be sent by a read-only view.
const WRITES = ["git_stage", "git_unstage", "git_stage_all", "git_commit", "git_discard", "git_apply_hunk", "git_create_branch",
  "git_switch", "git_delete_branch", "git_restore_stash", "git_forget", "git_open", "git_set_kind", "git_set_identity",
  "git_add_remote", "git_fetch", "git_pull", "git_push", "git_create", "meta_restore", "meta_publish_varlib", "meta_run",
  "meta_init", "meta_save_entity", "meta_save_source", "mesh_save_link", "mesh_delete_link", "mesh_set_project",
  "meta_set_designation", "notebook_resource_add", "notebook_resource_rename", "notebook_resource_delete", "save_notebook",
  "local_create_project", "local_forget_project", "pipeline_run_start", "restart_kernel"];

// The engine calls that go to the real engine; `refusing` takes one out for a test, so its mock answers.
const ENGINE = ["git_info", "git_create", "git_status", "git_set_identity", "git_stage_all", "git_commit", "git_log",
  "git_commit_detail", "git_compare", "git_file_at", "git_diff",
  "meta_init", "meta_project", "meta_run", "meta_entity", "meta_table_history", "meta_varlib", "meta_keys", "meta_fabric_result", "meta_runs",
  "platform_save_domain", "mesh_overview", "mesh_resolve_links",
  "new_notebook", "notebook_resources", "notebook_resource_add",
  "start_cell", "poll_cell", "interrupt_cell", "kernel_variables", "notebook_frame_profile", "restart_kernel",
  "local_create_project", "list_local_projects", "pipeline_list", "pipeline_import", "pipeline_compatibility", "pipeline_run_check", "pipeline_runs"];
const REAL = [...ENGINE];

// A real-engine call one test answers itself: it leaves ENGINE (the harness looks it up on each call)
// and `fn` answers instead, or throws the engine's text. beforeEach puts it back.
const fail = {};  // method -> the answer that takes the engine's place
const instead = (method, fn) => { fail[method] = fn; if (ENGINE.includes(method)) ENGINE.splice(ENGINE.indexOf(method), 1); };
const refusing = (method, text) => instead(method, () => { throw new Error(text); });
const answered = (method) => (p) => fail[method](p);
const mocks = {
  meta_varlib: answered("meta_varlib"),
  meta_fabric_result: answered("meta_fabric_result"),
  mesh_resolve_links: answered("mesh_resolve_links"),
  notebook_resource_reveal: answered("notebook_resource_reveal"),
  notebook_frame_profile: answered("notebook_frame_profile"),
  list_local_projects: answered("list_local_projects"),
  save_notebook: () => ({ saved: true }),
  notebook_dependencies: () => ({ cells: [] }),
  list_notebooks: () => [],
  list_workspaces: () => [],
  // central/service.py mesh_workspace_domains asks Fabric for each workspace's domain (get_workspace):
  // Prod's workspace is in the Fabric domain the platform has as Orders, under Sales.
  mesh_workspace_domains: (p) => (fail.mesh_workspace_domains ? fail.mesh_workspace_domains(p) : {
    workspaces: [{ env: "prod", designation: "", workspace_id: PROD_WS, workspace_name: "Sales Prod",
      fabric_domain: "e3c1a2b4-0000-4000-8000-00000000d0d0", domain: "sales-orders", problem: "" }],
    suggested: { domain: "sales", subdomain: "sales-orders" }, unnamed: [], imported: true }),
};

let root;  // the temp repository
let project;  // the temp metadata project
const PROD_WS = "5b2f7c1e-4d3a-4e8b-9a61-2f0c8d7e9b10";
const commits = {};  // subject -> full id
const git = (...args) => execFileSync("git", ["-C", root, ...args], { encoding: "utf8" }).trim();

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ENGINE, mocks, viewport: { width: 1440, height: 940 } });
  await startupReads(app);
  // A repository with three commits: the notebooks repository's starting files, then a change to
  // load.py with a new report.sql, then a scratch file.
  root = await page().evaluate((p) => rpc("git_create", { path: p, kind: "notebooks" }).then((st) => st.root || p),
    path.join(app.engineHome, "sales-notebooks"));
  const commit = async (subject, files) => {
    for (const [name, text] of Object.entries(files)) fs.writeFileSync(path.join(root, name), text);
    await page().evaluate(async ([r, subject]) => {
      await rpc("git_stage_all", { repo: r });
      await rpc("git_commit", { repo: r, message: subject });
    }, [root, subject]);
    commits[subject] = git("rev-parse", "HEAD");
  };
  await page().evaluate((r) => rpc("git_set_identity", { repo: r, name: "Ana Lima", email: "ana@contoso.com" }), root);
  await commit("Add the orders load", { "load.py": "rows = 10\nprint(rows)\n" });
  await commit("Raise the row limit", { "load.py": "rows = 100\nprint(rows)\n", "report.sql": "SELECT region, SUM(amount) FROM orders GROUP BY region\n" });
  await commit("Try a sample", { "scratch.py": "df.sample(5)\n" });
  // The metadata project: meta_init's sample, with bronze.customers loaded twice in Dev (Delta versions 0 and 1).
  project = path.join(app.engineHome, "orders-project");
  await page().evaluate(async (p) => {
    await rpc("meta_init", { path: p });
    for (let i = 0; i < 2; i++) await rpc("meta_run", { names: ["bronze.customers"], env: "dev" });
    // The platform on this PC knows the Sales domain and its Orders subdomain.
    await rpc("platform_save_domain", { domain: { id: "sales", name: "Sales" } });
    await rpc("platform_save_domain", { domain: { id: "sales-orders", name: "Orders", parent_id: "sales" } });
    // A local data project a pipeline's Script can run in.
    await rpc("local_create_project", { name: "Sales sandbox" });
    state.user = "ana@contoso.com";
  }, project);
  // Prod has a workspace in Fabric. Two links (metadata/links, meta/mesh.py Link): finance_rates pins
  // Dev's table, crm_accounts asks the platform, where no one has published that port yet.
  const envs = path.join(project, "environments.toml");
  fs.writeFileSync(envs, fs.readFileSync(envs, "utf8").replace(/\[prod\]\nworkspace_id = ""/,
    `[prod]\nworkspace_id = "${PROD_WS}"\nworkspace_name = "Sales Prod"`));
  fs.mkdirSync(path.join(project, "metadata", "links"));
  fs.writeFileSync(path.join(project, "metadata", "links", "finance_rates.toml"), 'alias = "finance_rates"\nproduct = "finance/rates"\n' +
    'port = "daily"\n[reads]\ndev = "prod"\n[location.dev]\ntable = "abfss://ws-finance@onelake.dfs.fabric.microsoft.com/lh-rates/Tables/dbo/rates"\n');
  fs.writeFileSync(path.join(project, "metadata", "links", "crm_accounts.toml"), 'alias = "crm_accounts"\nproduct = "crm/accounts"\n' +
    'port = "accounts"\n[reads]\ndev = "prod"\n');
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  for (const k of Object.keys(fail)) { delete fail[k]; if (REAL.includes(k) && !ENGINE.includes(k)) ENGINE.push(k); }
  for (const k of ENGINE.filter((m) => !REAL.includes(m))) ENGINE.splice(ENGINE.indexOf(k), 1);
  await page().evaluate(() => { closeMenu(); closeDialog(); const t = document.getElementById("toast"); t.hidden = true; t.textContent = ""; });
});

const writes = (from) => app.calls.slice(from).filter((c) => WRITES.includes(c.method)).map((c) => c.method);
const sent = (from, method) => app.calls.slice(from).filter((c) => c.method === method).map((c) => c.params ?? null);

// The error toast once it shows: its first line.
async function toastText() {
  await page().waitForFunction(() => { const t = document.getElementById("toast"); return !t.hidden && t.classList.contains("error"); });
  return (await page().textContent("#toast")).split("\n")[0];
}

// A refusal as a person reads it: a sentence, with no exception name, quoting or traceback.
function sentence(text, label = "") {
  assert.match(text, /^[A-Z][\s\S]*[.!?]$/, `reads as a sentence: ${text}`);
  // An exception name is one CamelCase word before a colon: GitError, KernelGone, NotSetUp.
  assert.doesNotMatch(text, /\b(?:[A-Z][a-z]+){2,}[A-Za-z]*: |Traceback|\[Errno|^'|'$/, `no exception name: ${text}`);
  if (label) assert.ok(text.startsWith(`${label}: `), `says what didn't work: ${text}`);
}

async function noSerious(scope) {
  if (!axeSource) return;
  assert.deepStrictEqual(await axeFindings(page(), scope), []);
}

// ---------- Source control > History ----------

async function openHistory() {
  await page().evaluate((r) => {
    Object.assign(gitPage, { info: null, repo: r, status: null, tab: "history", log: null, detail: null, compare: [], compared: null, selected: null });
    go("source");
  }, root);
  await page().locator(".git-history tbody tr").nth(2).waitFor();
}
const historyRow = (subject) => page().locator(".git-history tbody tr", { has: page().locator("b", { hasText: subject }) });

test("History: a commit opens with its author and files, sending that repository and commit", { skip }, async () => {
  await openHistory();
  const from = app.calls.length;
  await historyRow("Raise the row limit").locator("b", { hasText: "Raise the row limit" }).click();
  const view = page().locator(".git-commit");
  await view.locator("h2", { hasText: "Raise the row limit" }).waitFor();
  assert.deepStrictEqual(sent(from, "git_commit_detail"), [{ repo: root, commit: commits["Raise the row limit"] }]);
  assert.match(await view.textContent(), new RegExp(`Ana Lima <ana@contoso.com> · .* · ${commits["Raise the row limit"]}`));
  assert.match(await view.locator(".git-group").textContent(), /^2 files changed$/);
  assert.deepStrictEqual(await view.locator(".git-file .git-name").allTextContents(), ["load.py", "report.sql"]);
  // The file's name is a button, so the keyboard reaches its changes too.
  await view.getByRole("button", { name: "load.py", exact: true }).press("Enter");
  await page().locator("#git-diff tr.add", { hasText: "rows = 100" }).waitFor();
  assert.deepStrictEqual(sent(from, "git_diff").map((p) => [p.path, p.base, p.target]),
    [["load.py", commits["Add the orders load"], commits["Raise the row limit"]]]);
  assert.deepStrictEqual(writes(from), []);
  await noSerious(".git-page");
  assert.deepStrictEqual(app.errors, []);
});

test("History: Open shows the file as it was at that commit, read-only", { skip }, async () => {
  await openHistory();
  await historyRow("Raise the row limit").locator("b", { hasText: "Raise the row limit" }).click();
  await page().locator(".git-commit h2", { hasText: "Raise the row limit" }).waitFor();
  const from = app.calls.length;
  await page().getByRole("button", { name: "Open load.py at this commit" }).click();
  const dialog = page().getByRole("dialog");
  await dialog.getByText(`load.py at ${commits["Raise the row limit"].slice(0, 7)} (read-only)`).waitFor();
  assert.deepStrictEqual(sent(from, "git_file_at"), [{ repo: root, commit: commits["Raise the row limit"], path: "load.py" }]);
  assert.strictEqual(await dialog.locator("pre.git-pre").textContent(), "rows = 100\nprint(rows)\n");
  assert.deepStrictEqual(writes(from), []);
  await noSerious(".dialog");
  assert.deepStrictEqual(app.errors, []);
});

test("History: two ticked commits compare older first, whichever was ticked first", { skip }, async () => {
  await openHistory();
  const from = app.calls.length;
  const short = (s) => commits[s].slice(0, 7);
  await page().getByRole("checkbox", { name: `Compare ${short("Try a sample")}` }).check();
  await page().getByRole("checkbox", { name: `Compare ${short("Add the orders load")}` }).check();
  await page().getByRole("button", { name: "Compare the two commits" }).click();
  const view = page().locator(".git-commit");
  await view.locator("h2", { hasText: `${short("Add the orders load")} → ${short("Try a sample")}` }).waitFor();
  assert.deepStrictEqual(sent(from, "git_compare"), [{ repo: root, base: commits["Add the orders load"], target: commits["Try a sample"] }]);
  assert.match(await view.textContent(), /3 files differ · 2 commits in between/);
  assert.deepStrictEqual(await view.locator(".git-file .git-name").allTextContents(), ["load.py", "report.sql", "scratch.py"]);
  assert.deepStrictEqual(writes(from), []);
  await noSerious(".git-page");
  assert.deepStrictEqual(app.errors, []);
});

// Runs last of the History tests: the newest commit is rewritten away outside the app (as a force-push
// or an amend in another tool does), so the page's list still shows a commit Git no longer has.
test("History: a commit Git no longer has is refused in a sentence, for Open, Compare and the commit", { skip }, async () => {
  await openHistory();
  const gone = commits["Try a sample"];
  await historyRow("Try a sample").locator("b", { hasText: "Try a sample" }).click();
  await page().locator(".git-commit h2", { hasText: "Try a sample" }).waitFor();
  await page().getByRole("checkbox", { name: `Compare ${gone.slice(0, 7)}` }).check();
  await page().getByRole("checkbox", { name: `Compare ${commits["Add the orders load"].slice(0, 7)}` }).check();
  git("reset", "--hard", "-q", "HEAD~1");
  git("reflog", "expire", "--expire=now", "--all");
  git("gc", "-q", "--prune=now");
  const from = app.calls.length;
  const hide = () => page().evaluate(() => { document.getElementById("toast").hidden = true; });

  await page().getByRole("button", { name: "Open scratch.py at this commit" }).click();
  const open = await toastText();
  sentence(open, "Couldn't open that version");
  assert.match(open, new RegExp(`Git couldn't find ${gone}`));
  assert.strictEqual(await page().getByRole("dialog").count(), 0);
  await hide();

  await page().getByRole("button", { name: "Compare the two commits" }).click();
  sentence(await toastText(), "Couldn't compare");
  await hide();

  await historyRow("Try a sample").locator("b", { hasText: "Try a sample" }).click();
  sentence(await toastText(), "Couldn't open the commit");
  assert.deepStrictEqual(sent(from, "git_file_at"), [{ repo: root, commit: gone, path: "scratch.py" }]);
  assert.deepStrictEqual(sent(from, "git_compare"), [{ repo: root, base: commits["Add the orders load"], target: gone }]);
  assert.deepStrictEqual(sent(from, "git_commit_detail"), [{ repo: root, commit: gone }]);
  assert.deepStrictEqual(writes(from), []);
  await noSerious("#toast");
  assert.deepStrictEqual(app.errors, []);
});

// ---------- a table's History, and VL_Sources ----------

async function openTableHistory(env) {
  await page().evaluate((env) => { state.meta.env = env; openEntity("bronze.customers"); }, env);
  const button = page().locator(".page").getByRole("button", { name: /^History/ });
  await button.waitFor();
  const from = app.calls.length;
  await button.click();
  const dialog = page().getByRole("dialog");
  await dialog.getByText(`History of bronze.customers in ${env === "dev" ? "Dev" : "Test"}`).waitFor();
  return { dialog, from };
}

test("A table's History lists its versions for that table and environment", { skip }, async () => {
  const { dialog, from } = await openTableHistory("dev");
  const rows = dialog.locator("tbody tr");
  await rows.nth(1).waitFor();
  assert.deepStrictEqual(sent(from, "meta_table_history"), [{ name: "bronze.customers", env: "dev" }]);
  assert.match(await dialog.textContent(), /Versions from the last 7 days can be restored \(the bronze layer's Keep history\)/);
  const cells = await rows.evaluateAll((trs) => trs.map((tr) => [...tr.children].map((td) => td.textContent.trim())));
  assert.deepStrictEqual(cells.map((c) => [c[0], c[2].replace(/run r-[\w-]+$/, "run …"), c[3], c[4]]),
    [["1", "Write · run …", "3", "Current"], ["0", "Write · run …", "3", "Restore"]]);
  assert.deepStrictEqual(writes(from), []);
  await noSerious(".dialog");
  assert.deepStrictEqual(app.errors, []);
});

test("A table's History in an environment with no workspace says what to do, in the dialog", { skip }, async () => {
  const { dialog, from } = await openTableHistory("test");
  const panel = dialog.locator(".error-panel");
  await panel.waitFor();
  assert.deepStrictEqual(sent(from, "meta_table_history"), [{ name: "bronze.customers", env: "test" }]);
  assert.strictEqual(await panel.locator(".error-panel-label").textContent(), "Could not read the history");
  // The engine's NotSetUp from estate_service.py _entity_uri (the store of an environment with no workspace).
  assert.match(await panel.locator(".error-panel-text").textContent(), /Assign a workspace to Test first/);
  assert.doesNotMatch(await dialog.textContent(), /Reading the table's history/);
  assert.deepStrictEqual(writes(from), []);
  await noSerious(".dialog");
  assert.deepStrictEqual(app.errors, []);
});

// friendlyError (activity.js) drops the exception name only for the names it lists; the engine's
// NotSetUp and NoMetadataProject (errors.py, meta_service.py) aren't among them, so the text shows
// as "NotSetUp: Assign a workspace...". activity.js belongs to another open change.
test("A not-set-up refusal reads without the engine's exception name", { skip, todo: "friendlyError doesn't list NotSetUp or NoMetadataProject" }, async () => {
  const { dialog } = await openTableHistory("test");
  await dialog.locator(".error-panel").waitFor();
  sentence(await dialog.locator(".error-panel-text").textContent());
});

async function openSources() {
  await page().evaluate(() => { state.meta.env = "dev"; state.meta.tab = "sources"; go("home"); go("metadata"); });
  const card = page().locator(".card.estate", { hasText: "Variable library" });
  await card.waitFor();
  return card;
}

test("VL_Sources shows each variable's value in every environment, and Publish only where a workspace is", { skip }, async () => {
  const from = app.calls.length;
  const card = await openSources();
  await card.locator("tbody tr").first().waitFor();
  assert.deepStrictEqual(sent(from, "meta_varlib"), [{}]);
  const cells = await card.locator("tbody tr").evaluateAll((trs) => trs.map((tr) => [...tr.children].map((td) => td.innerText.trim())));
  // estate_service.py meta_varlib over the sample's two source systems (meta/varlib.py values)
  assert.deepStrictEqual(cells.map((c) => [c[0].split("\n")[0], c[1], c[2], c[3]]), [
    ["ENVIRONMENT", "dev", "test", "prod"],
    ["CRM_connection_type", "Files", "empty", "empty"],
    ["CRM_file_path", "landing/customers", "empty", "empty"],
    ["SHOP_connection_type", "Files", "empty", "empty"],
    ["SHOP_file_path", "landing/orders", "empty", "empty"]]);
  assert.match(await card.locator("pre").textContent(), /getLibrary\("VL_Sources"\)\nlib\.CRM_connection_type/);
  for (const env of ["Dev", "Test"]) {
    const b = card.getByRole("button", { name: new RegExp(`^Publish to ${env}`) });
    assert.ok(await b.isDisabled(), `Publish to ${env} waits for a workspace`);
    assert.strictEqual(await b.getAttribute("title"), `Assign a workspace to ${env} first`);
  }
  const prod = card.getByRole("button", { name: /^Publish to Prod/ });
  assert.ok(await prod.isEnabled());
  assert.strictEqual(await prod.getAttribute("title"), "Sales Prod, with Prod active");
  assert.deepStrictEqual(writes(from), []);
  await noSerious(".card.estate");
  assert.deepStrictEqual(app.errors, []);
});

// meta_service.py _project: the project's folder went away after the page read it.
const NO_PROJECT = "NoMetadataProject: No metadata project is open. Create one or open its folder.";

test("VL_Sources says why when the engine can't read it, and the rest of the tab stays", { skip }, async () => {
  refusing("meta_varlib", NO_PROJECT);
  const from = app.calls.length;
  const card = await openSources();
  await card.locator("p", { hasText: "No metadata project is open" }).waitFor();
  assert.deepStrictEqual(sent(from, "meta_varlib"), [{}]);
  assert.match(await card.innerText(), /^Variable library VL_Sources\n+.*No metadata project is open\. Create one or open its folder\.$/);
  assert.ok(await page().locator(".sources-grid").isVisible());
  assert.deepStrictEqual(writes(from), []);
  await noSerious(".card.estate");
  assert.deepStrictEqual(app.errors, []);
});

test("VL_Sources' not-set-up refusal reads without the engine's exception name", { skip, todo: "friendlyError doesn't list NoMetadataProject" }, async () => {
  refusing("meta_varlib", NO_PROJECT);
  const card = await openSources();
  const p = card.locator("p", { hasText: "No metadata project is open" });
  await p.waitFor();
  sentence(await p.textContent());
});

// ---------- a Fabric run's results ----------

// What meta_run answers for a run sent to Fabric (cloud_service.py _run_in_fabric).
const submitted = (request) => ({ entity: "bronze.customers, bronze.orders", status: "submitted", run_id: "", env: "dev",
  job_id: "9f1c2d3e-0000-4000-8000-00000000a1b2", request_id: request, message: "Running in Fabric on the Dev workspace's capacity" });
// One table's result as the runner saves it (meta/engine.py RunResult.to_dict).
const result = (entity, rows, more = {}) => ({ run_id: `r-20261101-0700${rows}-ab12`, entity, status: "ok", steps: [], rows_read: rows,
  rows_written: rows, quarantined: 0, delta_version: 3, message: "", options: [], retryable: false, ...more });

// The watch waits 10 s, then up to 60 s, between reads: here those waits are 20 ms, while `fn` runs.
async function watchFast(run, fn) {
  await page().evaluate((run) => {
    const real = window.setTimeout;
    window.__fdRealTimeout = real;
    window.setTimeout = (f, ms, ...a) => real(f, ms >= 10000 && ms <= 60000 ? 20 : ms, ...a);
    state.meta.tab = "runs";
    go("metadata");
    watchMetaFabricRun(run, run.entity);
  }, run);
  try { return await fn(); } finally { await page().evaluate(() => { window.setTimeout = window.__fdRealTimeout; }); }
}

test("A Fabric run's results show once the runner saved them, read for that run only", { skip }, async () => {
  const states = ["NotStarted", "InProgress", "Completed"];
  instead("meta_fabric_result", () => {
    const status = states.shift() || "Completed";
    // cloud_service.py meta_fabric_result: results only once the run is over
    return { status, message: "", results: status === "Completed"
      ? [result("bronze.customers", 3), result("bronze.orders", 0, { status: "failed", message: "No files match landing/orders/*.csv" })] : null };
  });
  const from = app.calls.length;
  const dialog = page().getByRole("dialog", { name: "1 of 2 succeeded" });
  await watchFast(submitted("fd-20261101-070000-ab12"), () => dialog.waitFor());
  assert.deepStrictEqual(sent(from, "meta_fabric_result"), Array(3).fill({ request_id: "fd-20261101-070000-ab12" }));
  const rows = await dialog.locator(".result-row").evaluateAll((r) => r.map((x) => x.innerText.replace(/\s+/g, " ").trim()));
  assert.deepStrictEqual(rows, ["Succeeded bronze.customers 3 rows written Details", "Failed bronze.orders No files match landing/orders/*.csv Details"]);
  assert.deepStrictEqual(writes(from), []);
  await noSerious(".dialog");
  assert.deepStrictEqual(app.errors, []);
});

test("A Fabric run that stopped before saving results says how it ended", { skip }, async () => {
  instead("meta_fabric_result", () => ({ status: "Failed", message: "The notebook session couldn't start: the capacity is paused.", results: null }));
  const from = app.calls.length;
  const dialog = page().getByRole("dialog", { name: "The Fabric run failed" });
  await watchFast(submitted("fd-20261101-071500-cd34"), () => dialog.waitFor());
  assert.deepStrictEqual(sent(from, "meta_fabric_result"), [{ request_id: "fd-20261101-071500-cd34" }]);
  assert.match(await dialog.innerText(), /The notebook session couldn't start: the capacity is paused\.\s+Open it from Monitor/);
  assert.deepStrictEqual(writes(from), []);
  await noSerious(".dialog");
  assert.deepStrictEqual(app.errors, []);
});

// The real engine: this PC's run list (fabric_runs.jsonl) has no run with that id.
test("A Fabric run this PC has no record of is refused in a sentence, and the bell stops showing it running", { skip }, async () => {
  const from = app.calls.length;
  const text = await watchFast(submitted("fd-20261101-073000-ef56"), toastText);
  assert.deepStrictEqual(sent(from, "meta_fabric_result"), [{ request_id: "fd-20261101-073000-ef56" }]);
  sentence(text, "Couldn't follow the Fabric run");
  assert.match(text, /no record of the Fabric run fd-20261101-073000-ef56/);
  const item = await page().evaluate(() => activity.items.find((i) => i.id === "fabric:9f1c2d3e-0000-4000-8000-00000000a1b2"));
  assert.strictEqual(item.status, "failed");
  assert.deepStrictEqual(writes(from), []);
  await noSerious("#toast");
  assert.deepStrictEqual(app.errors, []);
});

// ---------- Links, and the Fabric domain hint ----------

async function openLinks(env) {
  await page().evaluate((env) => { state.meta.env = env; state.meta.tab = "links"; go("home"); go("metadata"); }, env);
  const table = page().locator(".page table.list", { has: page().locator("th", { hasText: "Table in" }) });
  await table.waitFor();
  return table;
}
const linkCells = (table) => table.locator("tbody tr").evaluateAll((trs) => trs.map((tr) => [tr.children[0].textContent, tr.children[4].textContent]));

test("Links show where each points in the environment picked, or why not", { skip }, async () => {
  const from = app.calls.length;
  const table = await openLinks("dev");
  await page().getByText("Resolved for Dev.").waitFor();
  assert.deepStrictEqual(sent(from, "mesh_resolve_links"), [{ env: "dev" }]);
  // central/service.py _link_table: a pinned table as is; otherwise the platform's port, which no one published.
  assert.deepStrictEqual(await linkCells(table), [
    ["crm_accounts", "crm/accounts hasn't published port accounts in prod yet. Its owner publishes it when they promote to prod."],
    ["finance_rates", "ws-finance/lh-rates/Tables/dbo/rates"]]);
  // The link's own refusal opens with the product's id (crm/accounts), so it isn't capitalized.
  assert.ok(await table.locator("tbody tr").first().locator("td").nth(4).locator(".fail-text").isVisible());
  assert.deepStrictEqual(writes(from), []);
  await noSerious(".page");
  assert.deepStrictEqual(app.errors, []);
});

// A refusal of the whole check: the project's links folder can't be read (the engine's PermissionError,
// Python's repr doubling the backslashes).
test("Links say they weren't checked when the engine refuses, and the toast says why", { skip }, async () => {
  refusing("mesh_resolve_links", "PermissionError: [Errno 13] Permission denied: 'C:\\\\work\\\\orders\\\\metadata\\\\links'");
  const from = app.calls.length;
  const table = await openLinks("test");
  const text = await toastText();
  assert.deepStrictEqual(sent(from, "mesh_resolve_links"), [{ env: "test" }]);
  sentence(text, "Couldn't check where the links point");
  await page().getByText("Not checked for Test.").waitFor();
  assert.strictEqual(await page().getByText(/Resolved for/).count(), 0);
  assert.deepStrictEqual((await linkCells(table)).map((c) => c[1]), ["not checked", "not checked"]);
  assert.deepStrictEqual(writes(from), []);
  await noSerious(".page");
  assert.deepStrictEqual(app.errors, []);
});

async function openThisProject() {
  await page().evaluate(() => { state.meta.env = "dev"; state.meta.tab = "workspaces"; go("home"); go("metadata"); });
  await page().locator(".page .card", { hasText: "Project " }).first().waitFor();
}

test("This project says which Fabric domain the workspaces are in, and Use it picks it without saving", { skip }, async () => {
  const from = app.calls.length;
  await openThisProject();
  const hint = page().locator(".fabric-domain-hint");
  await hint.getByText("In Fabric, your Prod workspace (Sales Prod) is in the Orders domain.").waitFor();
  assert.deepStrictEqual(sent(from, "mesh_workspace_domains"), [{}]);
  await hint.getByRole("button", { name: "Use it" }).click();
  await hint.getByText("Picked Orders. Save to keep it.").waitFor();
  assert.strictEqual(await page().getByRole("combobox", { name: "Domain", exact: true }).inputValue(), "sales");
  assert.strictEqual(await page().getByRole("combobox", { name: "Subdomain" }).inputValue(), "sales-orders");
  assert.deepStrictEqual(writes(from), []);
  await noSerious(".page");
  assert.deepStrictEqual(app.errors, []);
});

// mesh.js loadWorkspaceDomains: the hint is extra, so a refusal leaves it out rather than showing an error.
test("This project leaves the domain hint out when Fabric refuses, and the form still works", { skip }, async () => {
  instead("mesh_workspace_domains", () => { throw new Error("HTTPError: 401 Client Error: Unauthorized for url: https://api.fabric.microsoft.com/v1/workspaces/" + PROD_WS); });
  const from = app.calls.length;
  await openThisProject();
  for (const end = Date.now() + 5000; !sent(from, "mesh_workspace_domains").length; await new Promise((r) => setTimeout(r, 25))) {
    if (Date.now() > end) throw new Error("mesh_workspace_domains never came");
  }
  await new Promise((r) => setTimeout(r, 200));
  assert.strictEqual(await page().locator(".fabric-domain-hint").textContent(), "");
  assert.ok(await page().getByRole("combobox", { name: "Domain", exact: true }).isEnabled());
  assert.deepStrictEqual(writes(from), []);
  await noSerious(".page");
  assert.deepStrictEqual(app.errors, []);
});

// ---------- a notebook resource's Show in folder ----------

let notebook;  // the notebook the resources tests use, made by the real engine
const pane = () => page().locator(".nb-res");

async function openResources() {
  if (!notebook) {
    notebook = await page().evaluate(() => rpc("new_notebook").then((nb) => { loadIntoState(nb); return nb.name; }));
  }
  await page().evaluate(() => { state.showExplorer = true; state.explorerTab = "resources"; go("notebook"); });
  await pane().locator(".nb-res-head").waitFor();
  if (!(await pane().locator('[data-file="regions.csv"]').count())) {
    await pane().locator('input[type="file"]').setInputFiles({ name: "regions.csv", mimeType: "text/csv", buffer: Buffer.from("region\nEast\nWest\n") });
    await pane().locator('[data-file="regions.csv"]').waitFor();
  }
}

test("Show in folder sends exactly that notebook and file, and Open folder that notebook only", { skip }, async () => {
  // notebook_resources_service.py notebook_resource_reveal answers the path it showed; File Explorer is the answer people see.
  instead("notebook_resource_reveal", ({ name, file_name }) => ({ shown: path.join(app.engineHome, `${name}.resources`, "builtin", file_name || "") }));
  await openResources();
  const from = app.calls.length;
  await page().getByRole("button", { name: "Actions for regions.csv" }).click();
  await page().getByRole("menuitem", { name: "Show in folder" }).click();
  for (const end = Date.now() + 5000; sent(from, "notebook_resource_reveal").length < 1; await new Promise((r) => setTimeout(r, 25))) {
    if (Date.now() > end) throw new Error("Show in folder sent nothing");
  }
  await pane().getByRole("button", { name: "Open folder" }).click();
  for (const end = Date.now() + 5000; sent(from, "notebook_resource_reveal").length < 2; await new Promise((r) => setTimeout(r, 25))) {
    if (Date.now() > end) throw new Error("Open folder sent nothing");
  }
  assert.deepStrictEqual(sent(from, "notebook_resource_reveal"), [{ name: notebook, file_name: "regions.csv" }, { name: notebook }]);
  assert.ok(await page().locator("#toast.error").isHidden(), "nothing to say: the folder opens");
  assert.deepStrictEqual(writes(from), []);
  await noSerious(".nb-res");
  assert.deepStrictEqual(app.errors, []);
});

// The real engine: the file was deleted on disk after the pane listed it, so the engine refuses before
// it opens anything (notebook_resources.py Resources.file).
test("Show in folder for a file that's gone says so in a sentence", { skip }, async () => {
  await openResources();
  const listed = await page().evaluate(() => nbResView(state.notebook).folder);
  fs.rmSync(path.join(listed, "builtin", "regions.csv"));  // notebook_resources.py: files live in <folder>/builtin
  ENGINE.push("notebook_resource_reveal");
  const from = app.calls.length;
  await page().getByRole("button", { name: "Actions for regions.csv" }).click();
  await page().getByRole("menuitem", { name: "Show in folder" }).click();
  const text = await toastText();
  assert.deepStrictEqual(sent(from, "notebook_resource_reveal"), [{ name: notebook, file_name: "regions.csv" }]);
  sentence(text, "Couldn't show the folder");
  assert.match(text, /There's no resource file named 'regions\.csv'\.$/);
  assert.deepStrictEqual(writes(from), []);
  await noSerious("#toast");
  assert.deepStrictEqual(app.errors, []);
});

// ---------- a cell result's Inspect profile ----------

const CELLS = [
  { type: "code", cell_id: "p1", meta: {}, source: "import pandas as pd\nsales = pd.DataFrame({'region': ['East', 'West', 'East', 'North', 'East'], " +
    "'amount': [5.0, 3.0, 8.0, None, 2.0]})\nsales" },
  { type: "code", cell_id: "p2", meta: {}, source: "pd.DataFrame({'n': [1, 2, 3]})" },
];

// The cell's table result, once it ran: its frame id.
async function runForTable(i) {
  await page().evaluate((cells) => {
    if (state.notebook?.name !== "Profile test") loadIntoState({ name: "Profile test", cells });
    state.showExplorer = false; go("notebook");
  }, CELLS);
  await page().evaluate((i) => runCell(state.notebook.cells[i]), i);
  return page().waitForFunction((i) => state.notebook.cells[i].outputs?.find((o) => o.frame)?.frame, i, { timeout: 60000 }).then((h) => h.jsonValue());
}
const output = (i) => page().locator(".cell").nth(i).locator(".table-output");
const outSel = (i) => page().evaluate((i) => `.cell[data-cell="${state.notebook.cells[i].id}"] .table-output`, i);  // one cell's output, for axe

test("Inspect profiles every row of that cell's result", { skip }, async () => {
  const frame = await runForTable(0);
  const from = app.calls.length;
  await output(0).getByRole("button", { name: "Inspect" }).click();
  await output(0).getByText("Profile of all 5 rows").waitFor({ timeout: 30000 });
  assert.deepStrictEqual(sent(from, "notebook_frame_profile"), [{ frame }]);
  const stats = await output(0).locator(".inspect-col").evaluateAll((cols) => cols.map((c) => ({
    name: c.querySelector(".inspect-name").firstChild.textContent,
    stats: Object.fromEntries([...c.querySelectorAll("dt")].map((dt) => [dt.textContent, dt.nextElementSibling.textContent])) })));
  // frames.py profile: region's most common values, amount's statistics (the None counts as missing)
  assert.deepStrictEqual(stats.map((s) => s.name), ["region", "amount"]);
  assert.strictEqual(stats[0].stats.Missing, "0");
  assert.deepStrictEqual(await output(0).locator(".top-values li").allInnerTexts().then((t) => t.map((x) => x.replace(/\s+/g, " "))), ["East 3", "North 1", "West 1"]);
  assert.deepStrictEqual([stats[1].stats.Missing, stats[1].stats.Min, stats[1].stats.Max, stats[1].stats.Mean], ["1", "2", "8", "4.5"]);
  assert.deepStrictEqual(writes(from), []);
  await noSerious(await outSel(0));
  assert.deepStrictEqual(app.errors, []);
});

// The real engine after a kernel restart: the result is no longer held, so Inspect says to run the cell again.
test("Inspect after the kernel restarted keeps the preview's summary and says to run the cell again", { skip }, async () => {
  const frame = await runForTable(1);
  await page().evaluate(() => rpc("restart_kernel"));
  const from = app.calls.length;
  await output(1).getByRole("button", { name: "Inspect" }).click();
  await output(1).getByText("Summary of 3 rows. Run the cell again to profile every row.").waitFor({ timeout: 30000 });
  assert.deepStrictEqual(sent(from, "notebook_frame_profile"), [{ frame }]);
  assert.strictEqual(await output(1).locator(".inspect-col .inspect-name").first().evaluate((n) => n.firstChild.textContent), "n");
  assert.deepStrictEqual(writes(from), []);
  await noSerious(await outSel(1));
  assert.deepStrictEqual(app.errors, []);
});

// kernel_proc.py _request: the cell process stopped answering. KernelGone is a RuntimeError, so
// service.py notebook_frame_profile passes it on, under its own name.
const NOT_ANSWERING = "KernelGone: The notebook's Python process isn't answering. Stop it to start a fresh one; variables will be cleared.";

test("Inspect when the kernel doesn't answer says why under the summary", { skip }, async () => {
  instead("notebook_frame_profile", () => { throw new Error(NOT_ANSWERING); });
  await page().evaluate(() => { const o = state.notebook.cells[0].outputs.find((x) => x.frame); delete o.profile; o.view = "table"; render(); });
  const frame = await page().evaluate(() => state.notebook.cells[0].outputs.find((x) => x.frame).frame);
  const from = app.calls.length;
  await output(0).getByRole("button", { name: "Inspect" }).click();
  const more = output(0).locator(".more", { hasText: "Could not profile every row" });
  await more.waitFor();
  assert.deepStrictEqual(sent(from, "notebook_frame_profile"), [{ frame }]);
  assert.match(await more.textContent(), /^Summary of 5 rows\. Could not profile every row: .*The notebook's Python process isn't answering\. Stop it to start a fresh one; variables will be cleared\.$/);
  assert.ok(await output(0).locator(".inspect-col").first().isVisible(), "the preview's summary stays");
  assert.deepStrictEqual(writes(from), []);
  await noSerious(await outSel(0));
  assert.deepStrictEqual(app.errors, []);
});

test("Inspect's kernel refusal reads without the engine's exception name", { skip, todo: "friendlyError doesn't list KernelGone" }, async () => {
  instead("notebook_frame_profile", () => { throw new Error(NOT_ANSWERING); });
  await page().evaluate(() => { const o = state.notebook.cells[0].outputs.find((x) => x.frame); delete o.profile; o.view = "table"; render(); });
  await output(0).getByRole("button", { name: "Inspect" }).click();
  const more = output(0).locator(".more", { hasText: "Could not profile every row" });
  await more.waitFor();
  sentence((await more.textContent()).replace(/^.*Could not profile every row: /, ""));
});

// ---------- the local data projects a pipeline's Script can run in ----------

// A pipeline with one Script on a lakehouse (sidecar/tests/test_pipeline_script.py script(), test_pipeline_data.py LS).
const PIPELINE = { name: "pl_totals", properties: { activities: [{ name: "Totals", type: "Script", dependsOn: [],
  linkedService: { name: "LH_Sales", properties: { type: "Lakehouse", typeProperties: { workspaceId: "ws1", artifactId: "lh1", rootFolder: "Tables" } } },
  typeProperties: { scripts: [{ type: "Query", text: "SELECT region, SUM(amount) AS total FROM orders GROUP BY region" }] } }] } };

async function openRunDialog() {
  const file = path.join(app.engineHome, "pl_totals.json");
  fs.writeFileSync(file, JSON.stringify(PIPELINE));
  await page().evaluate(() => { closeDialog(); go("create"); });
  await page().getByText("Open a Fabric pipeline").click();
  await page().setInputFiles('input[aria-label="Choose a pipeline JSON file"]', file);
  await page().waitForSelector(".pl-graph");
  const from = app.calls.length;
  await page().click(".pl-actions >> text=Run on this PC");
  const dialog = page().locator(".pl-run-dialog");
  await dialog.waitFor();
  const runsIn = dialog.getByRole("combobox", { name: "Totals runs in" });
  await runsIn.waitFor();
  return { dialog, runsIn, from };
}

test("A Script lists this PC's local data projects, and runs in the one picked", { skip }, async () => {
  const { runsIn, from } = await openRunDialog();
  assert.deepStrictEqual(sent(from, "list_local_projects"), [{}]);
  assert.deepStrictEqual(await runsIn.locator("option").allTextContents(), ["The lakehouse (read-only)", "Local data project: Sales sandbox"]);
  const checks = sent(from, "pipeline_run_check").length;
  await runsIn.selectOption({ label: "Local data project: Sales sandbox" });
  for (const end = Date.now() + 5000; sent(from, "pipeline_run_check").length === checks; await new Promise((r) => setTimeout(r, 25))) {
    if (Date.now() > end) throw new Error("picking the project didn't check the run");
  }
  assert.deepStrictEqual(sent(from, "pipeline_run_check").at(-1).sql_projects, { Totals: "sales-sandbox" });
  assert.deepStrictEqual(writes(from), []);
  await noSerious(".pl-run-dialog");
  assert.deepStrictEqual(app.errors, []);
});

// pipelines.js pipelineRunDialog: the projects are an option, so a refusal leaves only the lakehouse.
test("A Script still offers the lakehouse when the local data projects can't be read", { skip }, async () => {
  refusing("list_local_projects", "PermissionError: [Errno 13] Permission denied: 'C:\\\\Users\\\\ana\\\\AppData\\\\Local\\\\FabricDesktop\\\\local-data\\\\projects.json'");
  const { dialog, runsIn, from } = await openRunDialog();
  assert.deepStrictEqual(sent(from, "list_local_projects"), [{}]);
  assert.deepStrictEqual(await runsIn.locator("option").allTextContents(), ["The lakehouse (read-only)"]);
  assert.doesNotMatch(await dialog.innerText(), /PermissionError|Errno/);
  assert.deepStrictEqual(writes(from), []);
  await noSerious(".pl-run-dialog");
  assert.deepStrictEqual(app.errors, []);
});
