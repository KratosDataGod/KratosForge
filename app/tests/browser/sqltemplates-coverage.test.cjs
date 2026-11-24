// SQL from a template (sqltemplates.js) beyond sql-templates.test.cjs: your templates can't be listed or
// imported, a template in a Git repository that can't be used or has a kind the gallery doesn't know,
// Export and Delete refused, Delete cancelled, and writing a template: what's missing, the check with
// its defaults (and when it can't run), Save to a repository and a save refused. The template calls and
// the compatibility check are mocked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const WH = { kind: "warehouse", workspace_id: "ws1", workspace_name: "Sales Dev", item_id: "wh1", name: "WH_Gold" };
const repo = (name, title, sql, extra = {}) => ({ place: "repo1", place_label: "sales-repo", name, title, group: "", description: "",
  endpoint: false, sql, ...extra });
const LISTED = { places: [{ id: "mine", label: "My templates" }, { id: "repo1", label: "sales-repo" }], notes: [], templates: [
  repo("broken.sql", "Broken one", "", { problem: "Its header isn't valid JSON." }),
  repo("odd.sql", "Odd kind", "SELECT <x, blob, 1>")] };
const refuse = (what) => () => { throw new Error(`PermissionError: ${what}`); };

let app;
let list, analyze;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    sql_templates_list: () => list(),
    sql_template_import: refuse("My templates is read-only."),
    sql_template_export: refuse("Downloads is full."),
    sql_template_delete: refuse("The repository's folder is read-only."),
    sql_template_save: refuse("The repository's folder is read-only."),
    sql_analyze: (p) => analyze(p),
  } });
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  list = () => LISTED;
  analyze = () => ({ outcome: { status: "no_known_problems", text: "No known problems." }, diagnostics: [] });
  await page().evaluate(() => {
    closeDialog();
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
  });
});

const open = (opts = {}) => page().evaluate(({ wh, opts }) => {
  openSqlTemplates(wh, (title, sql) => { window.__opened = { title, sql }; }, { draft: () => "", ...opts });
}, { wh: WH, opts });
const dialog = () => page().locator("#dialog-root .dialog");
const note = () => dialog().locator(".sqlt-own-note");
const toastSays = (text) => page().waitForFunction((t) => document.getElementById("toast").textContent.startsWith(t), text);
const card = (title) => dialog().locator(".sqlt-card", { has: page().locator("b", { hasText: title }) });

test("your templates that can't be listed say why, and an import refused says why", { skip }, async () => {
  list = refuse("The data folder can't be read.");
  await open();
  await page().waitForFunction(() => /couldn't be listed/.test(document.querySelector(".sqlt-own-note").textContent));
  assert.strictEqual(await note().textContent(), "Your templates couldn't be listed: PermissionError: The data folder can't be read.");
  assert.ok(await dialog().locator(".sqlt-card").count() > 0, "the built-in templates are still offered");
  const chooser = page().waitForEvent("filechooser");
  await dialog().getByRole("button", { name: "Import" }).click();
  await (await chooser).setFiles({ name: "top.sql", mimeType: "text/plain", buffer: Buffer.from("SELECT 1") });
  await toastSays("Couldn't import the template: PermissionError: My templates is read-only.");
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "sql_template_import").at(-1).params,
    { place: "mine", file_name: "top.sql", text: "SELECT 1" });
});

test("a repository's template that can't be used says why; Export and Delete refused say why; Cancel keeps it", { skip }, async () => {
  await open();
  await dialog().getByRole("button", { name: "sales-repo" }).click();
  await card("Broken one").click();
  assert.strictEqual(await dialog().locator(".sqlt-detail [role=alert]").textContent(),
    "This template can't be used: Its header isn't valid JSON. Edit it, or fix its file.");
  assert.match(await dialog().locator(".sqlt-detail .sqlt-own").textContent(), /^sales-repo, in Git · broken\.sql/);

  await dialog().getByRole("button", { name: "Export" }).click();
  await toastSays("Couldn't export the template: PermissionError: Downloads is full.");

  await dialog().getByRole("button", { name: "Delete" }).click();
  const confirm = page().locator("#dialog-root .dialog", { hasText: "Delete template" });
  assert.match(await confirm.textContent(),
    /Delete Broken one\? Its file is removed from the sql-templates folder of sales-repo; commit the change in Source control to remove it for your team\./);
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await dialog().locator(".sqlt-detail h3", { hasText: "Broken one" }).waitFor();
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "sql_template_delete"), []);

  await dialog().getByRole("button", { name: "Delete" }).click();
  await page().locator("#dialog-root .dialog", { hasText: "Delete template" }).locator(".dialog-actions").getByRole("button", { name: "Delete" }).click();
  await toastSays("Couldn't delete the template: PermissionError: The repository's folder is read-only.");
  await dialog().locator(".sqlt-card", { hasText: "Odd kind" }).waitFor();  // the gallery opens again on its group
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "sql_template_delete").at(-1).params, { place: "repo1", name: "broken.sql" });
});

test("a field whose kind the gallery doesn't know says so and the template can't be opened", { skip }, async () => {
  await open({ group: "sales-repo", id: "own:repo1:odd.sql" });
  await dialog().locator(".sqlt-detail h3", { hasText: "Odd kind" }).waitFor();
  assert.strictEqual(await dialog().locator(".sqlt-why").textContent(), "The kind blob isn't one the gallery knows.");
  assert.strictEqual(await dialog().getByRole("button", { name: "Open in a new tab" }).isDisabled(), true);
});

test("writing a template says what's missing, checks it with its defaults, and a save refused says why", { skip }, async () => {
  await open();
  await dialog().getByRole("button", { name: "New template" }).click();
  const why = dialog().locator(".sqlt-editor > [role=alert]");
  const check = dialog().locator(".sqlt-check");
  const save = dialog().getByRole("button", { name: "Save template" });
  assert.strictEqual(await why.textContent(), "Give the template a title.");
  await dialog().getByLabel("Title").fill("Recent orders");
  assert.strictEqual(await why.textContent(), "Write the template's SQL.");
  await dialog().getByLabel("Template SQL").fill("SELECT TOP <n, int, 10> * FROM [<t, sysname, Orders>]");
  assert.strictEqual(await why.textContent(), "");
  await check.getByText("With its defaults, the compatibility check says: No known problems.").waitFor();
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "sql_analyze").at(-1).params,
    { query: "SELECT TOP 10 * FROM [Orders]", target: "warehouse" });

  analyze = () => { throw new Error("The checker isn't running."); };
  await dialog().getByLabel("Template SQL").fill("SELECT TOP <n, int, 5> * FROM [<t, sysname, Orders>]");
  await page().waitForFunction(() => document.querySelector(".sqlt-check").textContent === "");

  analyze = () => ({ outcome: { status: "review", text: "Review it." }, diagnostics: [] });
  await dialog().getByLabel("Template SQL").fill("SELECT TOP <n, int, 7> * FROM [<t, sysname, Orders>]");
  await check.getByText("With its defaults, the compatibility check says: Review it.").waitFor();
  await dialog().getByLabel("Title").fill("");
  await page().waitForFunction(() => document.querySelector(".sqlt-check").textContent === "", null, { timeout: 5000 });
  assert.strictEqual(await save.isDisabled(), true);
  await dialog().getByLabel("Title").fill("Recent orders");

  await dialog().getByLabel("Save to").selectOption("repo1");
  await save.click();
  await why.filter({ hasText: "PermissionError: The repository's folder is read-only." }).waitFor();
  assert.strictEqual(await save.isDisabled(), false, "it can be saved again");
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "sql_template_save").at(-1).params, { place: "repo1", title: "Recent orders",
    sql: "SELECT TOP <n, int, 7> * FROM [<t, sysname, Orders>]", group: "", description: "", endpoint: false });
});
