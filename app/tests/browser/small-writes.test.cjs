// #1494: four small writes that had no browser test (see harness.cjs for how to run it).
// - SQL query > Save as table (query.js saveAsTable): save_sql with the selected query and the typed
//   name; when the table exists, a danger confirm (type the name) asks before a second save_sql with
//   mode "overwrite", and Cancel sends no overwrite.
// - Data mesh > Domains > Access groups (entra-groups.js): Check in Entra and Save to Downloads send
//   the domain with the ticked environments, the subdomains choice and, for Save, the picked script.
// - Validate notebook > Save report (validate.js): notebook_validate_save with that clean run's id,
//   against the real engine, which writes the report to its Downloads folder.
// - Home > Recent notebooks > Run several > Don't start the rest (home-notebooks.js): notebook_batch_stop
//   with that batch's id, against the real engine, which leaves its stop file.
// Engine refusals read as a sentence, without the exception's name; opening each place sends no write;
// axe finds nothing serious on each dialog and panel.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const noAxe = skip || (!axeSource && "axe-core is not installed");
let app;
const page = () => app.page;

const LH = { workspace_id: "ws1", lakehouse_id: "lh1", name: "Sales" };
const WRITES = ["save_sql", "platform_entra_check", "platform_entra_save", "platform_entra_link", "platform_entra_unlink",
  "platform_entra_naming_save", "notebook_validate_start", "notebook_validate_save", "notebook_cleanup_apply",
  "notebook_batch_start", "notebook_batch_stop", "notebook_batch_fabric"];
const fail = {};  // method -> the engine's error text, for the next calls to it
const failing = (method, fn) => (p) => { if (fail[method]) throw new Error(fail[method]); return fn(p); };

// ---- Save as table: service.py save_sql. mode "new" writes nothing when the table exists and answers
// {table, exists: true}; otherwise it writes and answers {table, rows, message: repr(WriteResult)}
// (lakehouse.py WriteResult.__repr__).
let tables;
const freshTables = () => new Set(["sales", "sales_summary"]);
const saveSql = failing("save_sql", ({ table, mode = "new" }) => {
  // lakehouse.py table_ref: the name is checked before anything is read or written.
  if (!table || /[/\\]/.test(table)) {
    throw new Error(`ValueError: '${table}' isn't a table name: a schema or table name can't be empty or hold / or \\.`);
  }
  if (mode === "new" && tables.has(table)) return { table, exists: true };
  const version = tables.has(table) ? 3 : 0;
  tables.add(table);
  const text = `Wrote 42 rows to ${table} (Delta version ${version})`;
  return { table, rows: 42, message: version ? text : `${text}. New table: refresh the lakehouse in Fabric if it shows under Unidentified` };
});

// ---- Access groups: central/entra_service.py. platform_entra_groups and _script as the existing
// entra-groups test shapes them; platform_entra_check answers {status: {"domain|role|env": {exists, id}}};
// platform_entra_save answers the script with the new file's path (_entra_script, save=True).
const DOMAINS = [{ id: "sales", name: "Sales", owners: "ana@contoso.com" },
  { id: "sales-emea", name: "Sales EMEA", parent_id: "sales", owners: "ana@contoso.com" }];
const naming = { pattern: "{PREFIX}-{DOMAIN}-{SUBDOMAIN}-{ROLE}-{ENV}", prefix: "SG-FAB", case: "upper", domain_part: "id",
  words: { domain_admins: "DOMAIN-ADMINS", domain_contributors: "DOMAIN-CONTRIBUTORS", workspace_admins: "WSADMINS", creators: "CREATORS",
    readers: "READERS", viewers: "VIEWERS", stewards: "STEWARDS" }, roles_off: [] };
const ENV_WORD = { dev: "Dev", test: "Test", prod: "Prod" };
function entraRows({ envs = ["dev", "test", "prod"], subdomains = false }) {
  const doms = subdomains ? DOMAINS : DOMAINS.slice(0, 1);
  return doms.flatMap((d) => [
    { role: "domain_admins", label: "Domain owners", env: "" },
    ...envs.map((env) => ({ role: "readers", label: `Data readers, ${ENV_WORD[env]}`, env })),
  ].map((r) => {
    const suggested = ["SG-FAB", d.id.toUpperCase(), r.role === "readers" ? "READERS" : "DOMAIN-ADMINS", r.env.toUpperCase()].filter(Boolean).join("-");
    return { domain_id: d.id, domain: d.name, ...r, suggested, mail_nickname: suggested.toLowerCase(), who: "Someone", gets: "Something",
      linked: null, name: suggested };
  }));
}
// NO_GRAPH in entra_service.py, raised as PermissionError when the directory can't be read.
const NO_GRAPH = "KratosForge can't read groups in your directory: Group.Read.All needs an Entra admin's consent "
  + "for the Fabric CLI app in your organization. Paste the group's object ID instead (Microsoft Entra "
  + "admin center > Groups > the group > Object ID).";
const DOWNLOADS = "C:\\Users\\chris\\Downloads";

// ---- Run several: notebook_batch_service.py. notebook_batch_start is answered here, without starting
// the background process, by writing the batch file as the engine does (<root>/notebook_batches/<id>.json,
// root = %LOCALAPPDATA%/FabricDesktop). notebook_batch and notebook_batch_stop are the real engine's.
const NOTEBOOKS = [{ name: "Daily sales", modified: 1761480000 }, { name: "Weekly returns", modified: 1761400000 },
  { name: "Stock levels", modified: 1761300000 }];
const detail = (name) => ({ name, workspace_id: "", workspace_name: "", lakehouse: "", compute: "local", last_run: null });
const batchDir = () => path.join(app.engineHome, "FabricDesktop", "notebook_batches");
let nextBatch = 0;
function batchStart({ names, parallel }) {
  const id = `b10c${String(++nextBatch).padStart(8, "0")}`;  // 12 hex digits, as _BATCH_ID wants
  const batch = { id, started: new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00"), parallel, state: "starting",
    beat: Date.now() / 1000, items: names.map((name) => ({ name, status: "waiting" })) };
  fs.mkdirSync(batchDir(), { recursive: true });
  fs.writeFileSync(path.join(batchDir(), `${id}.json`), JSON.stringify(batch));
  return batch;
}

// ---- Validate: the real engine (validate_service.py), with no lakehouse, so nothing reaches OneLake.
const CELLS = [
  { type: "code", cell_id: "v1", source: "total = 41 + 1", meta: {} },
  { type: "code", cell_id: "v2", source: "print(total)", meta: {} },
];

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["sql_format", "sql_analyze", "notebook_validate_check", "notebook_validate_start", "notebook_validate_status",
      "notebook_validate_list", "notebook_validate_cancel", "notebook_validate_save", "notebook_cleanup_plan",
      "notebook_batch", "notebook_batch_stop"],
    mocks: {
      environment: () => ({ python: "Python 3 (test)", user: "chris@contoso.com" }),
      readiness_check: () => [],
      list_tables: () => ({ dbo: [...tables].sort() }),
      table_columns: () => [],
      save_sql: (p) => saveSql(p),
      platform_whoami: () => ({ admin: true, user: "chris@contoso.com" }),
      platform_domains: () => DOMAINS,
      platform_entra_groups: (p) => ({ domain: DOMAINS[0], naming, owners: ["ana@contoso.com"], can_link: true, can_name: true, rows: entraRows(p) }),
      platform_entra_script: ({ kind }) => ({ kind, text: `# ${kind} script`, new: 2, steps: [] }),
      platform_entra_check: failing("platform_entra_check", (p) => ({ status: Object.fromEntries(entraRows(p).map((r) =>
        [`${r.domain_id}|${r.role}|${r.env}`, r.env === "prod" ? { exists: true, id: "7c1e2f3a-1111-2222-3333-44445555a90d" } : { exists: false, id: null }])) })),
      platform_entra_save: failing("platform_entra_save", ({ kind }) => {
        const ext = { powershell: "ps1", az: "sh", csv: "csv", roles: "ps1" }[kind];
        return { kind, text: `# ${kind} script`, new: 2, steps: [], path: `${DOWNLOADS}\\Sales Entra groups.${ext}` };
      }),
      list_notebooks: () => NOTEBOOKS,
      notebook_details: ({ names }) => names.map(detail),
      item_pins: () => [],
      notebook_batch_start: (p) => batchStart(p),
      save_notebook: () => ({ saved: true }),
    },
  });
  await startupReads(app);
  await page().waitForFunction(() => state.view === "home" && homeCache.done, null, { timeout: 20000 });
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  tables = freshTables();
  for (const k of Object.keys(fail)) delete fail[k];
  await page().evaluate(() => { dialogOnClose = null; closeMenu(); closeDialog(); const t = document.getElementById("toast"); t.hidden = true; t.textContent = ""; });
});

const writes = (from) => app.calls.slice(from).filter((c) => WRITES.includes(c.method)).map((c) => ({ method: c.method, params: c.params }));
const pause = (ms) => new Promise((r) => setTimeout(r, ms));
async function toastSays(re) {
  const handle = await page().waitForFunction((src) => {
    const t = document.getElementById("toast");
    return !t.hidden && new RegExp(src).test(t.textContent) && t.textContent;
  }, re.source);
  return (await handle.jsonValue()).split("\n")[0];
}
// A sentence a person reads: no exception name, traceback or errno in front, and it ends like one.
function sentence(text) {
  assert.doesNotMatch(text, /\b[A-Z]\w*(Error|Exception):|Traceback|\[Errno|\[WinError/, text);
  assert.match(text, /^[A-Z].*[.)]$/s, text);
}

// ---------- Save as table ----------

const SQL = "SELECT 1 AS warmup;\nSELECT region, sum(amount) AS total FROM sales GROUP BY region";
const PICKED = "SELECT region, sum(amount) AS total FROM sales GROUP BY region";

async function openSql() {
  await page().evaluate((lh) => openSqlQuery(lh), LH);
  await page().waitForSelector(".sql-edit .cm-editor");
  // The second statement selected: Save as table writes what is selected.
  await page().evaluate(([text, picked]) => {
    const v = sqlPage.editor.view;
    const from = text.indexOf(picked);
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: text }, selection: { anchor: from, head: from + picked.length } });
    v.focus();
  }, [SQL, PICKED]);
}

async function saveAs(name) {
  await openSql();
  await page().getByRole("button", { name: "Save as table" }).click();
  const ask = page().getByRole("dialog", { name: "Save as table" });
  await ask.waitFor();
  await ask.getByRole("textbox").fill(name);
  await ask.getByRole("button", { name: "Save", exact: true }).click();
  await ask.waitFor({ state: "detached" });
}

const firstSave = (table) => ({ method: "save_sql", params: { query: PICKED, lakehouse_ref: LH, table, dialect: "spark" } });

test("Save as table sends the selected query and the typed name, and a new table is written at once", { skip }, async () => {
  const from = app.calls.length;
  await saveAs("region_totals");
  const said = await toastSays(/^Wrote 42 rows to region_totals/);
  assert.match(said, /Delta version 0\)\. New table/);
  assert.deepStrictEqual(writes(from), [firstSave("region_totals")]);
  assert.strictEqual(await page().getByRole("dialog").count(), 0, "a new table needs no confirm");
  // The explorer reads the tables again and lists it.
  await page().locator(".explorer-content", { hasText: "region_totals" }).waitFor();
});

test("overwriting a table asks first in a danger confirm that wants the name typed, then sends mode overwrite", { skip }, async () => {
  const from = app.calls.length;
  await saveAs("sales_summary");
  const confirm = page().getByRole("dialog", { name: "Replace the table?" });
  await confirm.waitFor();
  assert.match(await confirm.innerText(), /Sales already has a table called sales_summary\. Saving replaces all of its rows/);
  assert.deepStrictEqual(writes(from), [firstSave("sales_summary")], "nothing is written before the answer");
  const replace = confirm.getByRole("button", { name: "Replace" });
  assert.match(await replace.getAttribute("class"), /\bdanger-solid\b/);
  assert.ok(await replace.isDisabled(), "Replace waits for the name");
  await confirm.getByRole("textbox", { name: "Type sales_summary to confirm" }).fill("sales_summar");
  assert.ok(await replace.isDisabled());
  await confirm.getByRole("textbox", { name: "Type sales_summary to confirm" }).fill("sales_summary");
  await replace.click();
  await confirm.waitFor({ state: "detached" });
  assert.match(await toastSays(/^Wrote 42 rows to sales_summary/), /\(Delta version 3\)$/);
  assert.deepStrictEqual(writes(from), [firstSave("sales_summary"),
    { method: "save_sql", params: { query: PICKED, lakehouse_ref: LH, table: "sales_summary", mode: "overwrite", dialect: "spark" } }]);
});

test("Cancel on the Replace confirm sends no overwrite", { skip }, async () => {
  const from = app.calls.length;
  await saveAs("sales_summary");
  const confirm = page().getByRole("dialog", { name: "Replace the table?" });
  await confirm.waitFor();
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await confirm.waitFor({ state: "detached" });
  await pause(300);  // nothing should happen: give it the chance
  assert.deepStrictEqual(writes(from), [firstSave("sales_summary")]);
  assert.doesNotMatch(await page().textContent("#toast"), /^Wrote/);
});

test("Cancel on the name sends nothing", { skip }, async () => {
  const from = app.calls.length;
  await openSql();
  await page().getByRole("button", { name: "Save as table" }).click();
  const ask = page().getByRole("dialog", { name: "Save as table" });
  await ask.getByRole("button", { name: "Cancel" }).click();
  await ask.waitFor({ state: "detached" });
  await pause(300);
  assert.deepStrictEqual(writes(from), []);
});

test("a table name the engine refuses reads as a sentence", { skip }, async () => {
  const from = app.calls.length;
  await saveAs("sales/2026");
  const said = await toastSays(/^Could not save the table/);
  assert.strictEqual(said, "Could not save the table: 'sales/2026' isn't a table name: a schema or table name can't be empty or hold / or \\.");
  sentence(said.replace(/^Could not save the table: /, "A table: "));
  assert.deepStrictEqual(writes(from), [firstSave("sales/2026")]);
});

// ---------- Access groups ----------

async function openGroups() {
  await page().evaluate(() => { state.user = "chris@contoso.com"; goPlatform("domains"); });
  await page().locator('tr[data-domain="sales"]').getByRole("button", { name: "Access groups" }).click();
  const dialog = page().getByRole("dialog", { name: "Access groups for Sales" });
  await dialog.locator('tr[data-role="readers"][data-env="prod"]').waitFor();
  return dialog;
}

// Test unticked, subdomains ticked: the picks the writes must carry.
async function pick(dialog) {
  await dialog.getByRole("checkbox", { name: "Test" }).uncheck();
  await dialog.locator('tr[data-role="readers"][data-env="test"]').waitFor({ state: "detached" });
  await dialog.getByRole("checkbox", { name: /Include subdomains/ }).check();
  await dialog.locator('tr[data-domain="sales-emea"]').first().waitFor();
}
const PICKS = { domain_id: "sales", envs: ["dev", "prod"], subdomains: true };

test("Check in Entra sends the domain with the ticked environments and subdomains, and shows which exist", { skip }, async () => {
  const dialog = await openGroups();
  await pick(dialog);
  const from = app.calls.length;
  await dialog.getByRole("button", { name: "Check in Entra" }).click();
  await dialog.locator('tr[data-domain="sales"][data-role="readers"][data-env="prod"]', { hasText: "Already in Entra" }).waitFor();
  assert.deepStrictEqual(writes(from), [{ method: "platform_entra_check", params: PICKS }]);
  assert.match(await dialog.locator('tr[data-domain="sales"][data-role="readers"][data-env="dev"]').innerText(), /New: in the script/);
  assert.match(await dialog.locator('tr[data-domain="sales-emea"][data-role="readers"][data-env="prod"]').innerText(), /Already in Entra/);
});

test("Save to Downloads sends the picks with the script tab chosen, and says where it went", { skip }, async () => {
  const dialog = await openGroups();
  await pick(dialog);
  await dialog.getByRole("button", { name: "az CLI" }).click();
  await page().waitForFunction(() => /az script/.test(document.querySelector(".entra-script")?.textContent || ""));
  const from = app.calls.length;
  await dialog.getByRole("button", { name: "Save to Downloads" }).click();
  assert.strictEqual(await toastSays(/^Saved to/), `Saved to ${DOWNLOADS}\\Sales Entra groups.sh`);
  assert.deepStrictEqual(writes(from), [{ method: "platform_entra_save", params: { ...PICKS, kind: "az" } }]);
});

test("Check in Entra without directory consent reads as the engine's sentence", { skip }, async () => {
  fail.platform_entra_check = `PermissionError: ${NO_GRAPH}`;
  const dialog = await openGroups();
  await dialog.getByRole("button", { name: "Check in Entra" }).click();
  const shown = dialog.locator(".entra-create .fail-text");
  await shown.waitFor();
  const said = (await shown.innerText()).trim();
  assert.strictEqual(said, NO_GRAPH);
  sentence(said);
  assert.match(await dialog.locator('tr[data-role="readers"][data-env="prod"]').innerText(), /New: in the script/, "nothing is marked as found");
});

test("Save to Downloads that this PC refuses reads as a sentence", { skip }, async () => {
  // _entra_script's write_text into a Downloads folder Windows won't let it write.
  fail.platform_entra_save = `PermissionError: [Errno 13] Permission denied: '${DOWNLOADS.replace(/\\/g, "\\\\")}\\\\Sales Entra groups.ps1'`;
  const dialog = await openGroups();
  await dialog.getByRole("button", { name: "Save to Downloads" }).click();
  const shown = dialog.locator(".entra-create .fail-text");
  await shown.waitFor();
  const said = (await shown.innerText()).trim();
  assert.strictEqual(said, `This PC didn't allow access to ${DOWNLOADS}\\Sales Entra groups.ps1. Close any program that has it open, or pick a folder you can write to.`);
  sentence(said);
  const t = page().locator("#toast");
  assert.ok(await t.isHidden() || !/^Saved/.test(await t.textContent()), `no Saved message: ${await t.textContent()}`);
});

// ---------- Validate > Save report (real engine) ----------

async function openValidate() {
  await page().evaluate((cells) => {
    loadIntoState({ name: "Daily sales", cells });
    state.ribbonTab = "run";
    go("notebook");
  }, CELLS);
  await page().getByRole("button", { name: /^Validate/ }).click();
  const pane = page().locator(".validatepane");
  await pane.getByRole("button", { name: /Run clean check|Validate again/ }).waitFor();
  return pane;
}

let ranId = null;
async function cleanRun(pane) {
  await pane.getByRole("button", { name: /Run clean check|Validate again/ }).click();
  await pane.getByRole("button", { name: "Save report" }).waitFor({ timeout: 60000 });
  ranId = await page().evaluate(() => state.notebook.validate.record.id);
  assert.match(ranId, /^validate-\d{8}-\d{6}-[0-9a-f]{6}$/);
}

test("Save report sends that clean run's id, and the engine writes the report to Downloads", { skip }, async () => {
  const pane = await openValidate();
  await cleanRun(pane);
  const from = app.calls.length;
  await pane.getByRole("button", { name: "Save report" }).click();
  const said = await toastSays(/^Saved /);
  assert.deepStrictEqual(writes(from), [{ method: "notebook_validate_save", params: { id: ranId } }]);
  const file = said.replace(/^Saved /, "");
  assert.strictEqual(path.dirname(file), path.join(app.engineHome, "Downloads"));
  assert.match(path.basename(file), new RegExp(`^validate-Daily-sales-${ranId.slice(9, 24)}(-\\d+)?\\.md$`));
  assert.match(fs.readFileSync(file, "utf8"), /Daily sales/);
});

test("Save report that the engine can't write reads as a sentence", { skip }, async () => {
  const pane = await openValidate();
  if (!ranId) await cleanRun(pane);
  // Downloads is a file, not a folder: the engine's mkdir refuses (FileExistsError).
  const downloads = path.join(app.engineHome, "Downloads");
  const kept = `${downloads}.kept`;
  if (fs.existsSync(downloads)) fs.renameSync(downloads, kept);
  fs.writeFileSync(downloads, "");
  try {
    const from = app.calls.length;
    await pane.getByRole("button", { name: "Save report" }).click();
    const said = await toastSays(/^Could not save the report/);
    // The engine says the home folder as ~ (server.py), so the person's user name stays out of the message.
    assert.strictEqual(said, "Could not save the report: Something already exists at ~/Downloads on this PC. Pick another name or folder.");
    sentence(said);
    assert.deepStrictEqual(writes(from), [{ method: "notebook_validate_save", params: { id: ranId } }]);
  } finally {
    fs.rmSync(downloads, { force: true });
    if (fs.existsSync(kept)) fs.renameSync(kept, downloads);
  }
});

// ---------- Run several > Don't start the rest (real engine for the batch) ----------

async function home() {
  await page().evaluate(() => { homeRun.picked.clear(); go("home"); });
  await page().locator(".home-run-bar").waitFor();
}

async function runBatch(names) {
  await home();
  for (const n of names) await page().check(`input[aria-label="Select ${n}"]`);
  await page().locator(".home-run-bar").getByRole("button", { name: `Run ${names.length} notebooks` }).click();
  const confirm = page().getByRole("dialog", { name: `Run ${names.length} notebooks?` });
  await confirm.getByRole("button", { name: `Run ${names.length} notebooks` }).click();
  await confirm.waitFor({ state: "detached" });
  await page().locator(".home-run-bar", { hasText: `Running ${names.length}: 0 done` }).waitFor();
  return page().evaluate(() => homeRun.batch.id);
}

// Ends the batch as its background process would, so the next test can start one.
async function endBatch(id) {
  const file = path.join(batchDir(), `${id}.json`);
  if (fs.existsSync(file)) {
    const b = JSON.parse(fs.readFileSync(file, "utf8"));
    fs.writeFileSync(file, JSON.stringify({ ...b, state: "done", items: b.items.map((i) => ({ ...i, status: "skipped" })) }));
  } else {
    await page().evaluate(() => { homeRun.batch = { ...homeRun.batch, state: "done" }; });
  }
  await page().waitForFunction(() => ["done", "lost"].includes(homeRun.batch?.state), null, { timeout: 10000 });
}

test("Don't start the rest sends that batch's id, and the engine leaves its stop file", { skip }, async () => {
  const from = app.calls.length;
  const id = await runBatch(["Daily sales", "Stock levels"]);
  assert.deepStrictEqual(writes(from), [{ method: "notebook_batch_start", params: { names: ["Daily sales", "Stock levels"], parallel: 2 } }]);
  assert.match(id, /^[0-9a-f]{12}$/);
  const at = app.calls.length;
  await page().locator(".home-run-bar").getByRole("button", { name: "Don't start the rest" }).click();
  await page().locator(".home-run-bar", { hasText: "The rest won't start" }).waitFor();
  assert.deepStrictEqual(writes(at), [{ method: "notebook_batch_stop", params: { id } }]);
  assert.ok(fs.existsSync(path.join(batchDir(), `${id}.stop`)), "the engine wrote the stop file");
  assert.strictEqual(await page().locator(".home-run-bar").getByRole("button", { name: "Don't start the rest" }).count(), 0);
  await endBatch(id);
});

test("Don't start the rest on a batch the engine no longer has reads as a sentence", { skip }, async () => {
  const id = await runBatch(["Weekly returns", "Stock levels"]);
  fs.rmSync(path.join(batchDir(), `${id}.json`));  // gone from this PC, as after a cleanup
  const from = app.calls.length;
  await page().locator(".home-run-bar").getByRole("button", { name: "Don't start the rest" }).click();
  const said = await toastSays(/^Could not stop the rest/);
  assert.strictEqual(said, "Could not stop the rest: That run of several notebooks isn't on this PC");
  assert.doesNotMatch(said, /LookupError/);
  assert.deepStrictEqual(writes(from), [{ method: "notebook_batch_stop", params: { id } }]);
  assert.ok(!fs.existsSync(path.join(batchDir(), `${id}.stop`)), "no stray stop file");
  await endBatch(id);
});

// ---------- opening, axe, errors ----------

test("opening the SQL page, Access groups, Validate and Home sends no write", { skip }, async () => {
  const from = app.calls.length;
  await openSql();
  await page().getByRole("button", { name: "Save as table" }).waitFor();
  const dialog = await openGroups();
  await dialog.getByRole("button", { name: "Graph PowerShell" }).click();
  await page().evaluate(() => { dialogOnClose = null; closeDialog(); });
  const pane = await openValidate();
  await pane.getByRole("tab", { name: /Clean up/ }).click();
  await home();
  await pause(300);
  const made = app.calls.slice(from).map((c) => c.method);
  for (const m of ["platform_entra_groups", "platform_entra_script", "notebook_validate_check", "notebook_cleanup_plan", "list_notebooks"]) {
    assert.ok(made.includes(m), `${m} was read`);
  }
  assert.deepStrictEqual(made.filter((m) => WRITES.includes(m)), []);
});

test("axe finds nothing serious on the save dialogs, Access groups, the Validate panel and the batch bar", { skip: noAxe }, async () => {
  const check = async (where, selector) => assert.deepStrictEqual(await axeFindings(page(), selector), [], where);
  await openSql();
  await page().getByRole("button", { name: "Save as table" }).click();
  await page().getByRole("dialog", { name: "Save as table" }).getByRole("textbox").fill("sales_summary");
  await check("Save as table", "#dialog-root .dialog");
  await page().getByRole("dialog", { name: "Save as table" }).getByRole("button", { name: "Save", exact: true }).click();
  await page().getByRole("dialog", { name: "Replace the table?" }).waitFor();
  await check("Replace the table?", "#dialog-root .dialog");
  await page().getByRole("dialog", { name: "Replace the table?" }).getByRole("button", { name: "Cancel" }).click();
  fail.platform_entra_check = `PermissionError: ${NO_GRAPH}`;
  const dialog = await openGroups();
  await dialog.getByRole("button", { name: "Check in Entra" }).click();
  await dialog.locator(".entra-create .fail-text").waitFor();
  await check("Access groups", "#dialog-root .dialog");
  await page().evaluate(() => { dialogOnClose = null; closeDialog(); });
  const pane = await openValidate();
  if (!ranId) await cleanRun(pane);
  await pane.getByRole("button", { name: "Save report" }).waitFor();
  await check("Validate panel", ".validatepane");
  const id = await runBatch(["Daily sales", "Weekly returns"]);
  await check("batch bar", ".home-recent");
  await endBatch(id);
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
