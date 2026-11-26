// Source control > Bundles (dbx-bundles.js), the paths source-databricks-bundles leaves out: a
// databricks.yml that can't be read or has no targets, no Databricks CLI profile on this PC, switching
// profile, a validate or plan that fails, a plan with nothing to change, and a deploy that first
// asks to run the bundle's commands, one whose plan changed meanwhile, and one that fails. The
// tab is drawn into a box with the engine's replies mocked; what the person sees and what was sent is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const HOST = "https://adb-1234567890123456.7.azuredatabricks.net";
const REPO = "C:/src/sales-etl";
const PLAN = { ok: true, target: "dev", digest: "d-1", destructive: 0, production: false, needs_typed_confirm: false,
  rows: [{ resource: "resources.jobs.nightly", kind: "job", name: "nightly", action: "update" }] };
const CLI = [{ key: "cli:dev", label: "dev (Databricks CLI)", source: "cli", host: HOST },
  { key: "cli:ops", label: "ops (Databricks CLI)", source: "cli", host: HOST },
  { key: "pat:x", label: "A token", source: "pat", host: HOST }];

const db = { conns: CLI };  // set before the app opens, in case it asks for connections at start-up
let app;
const page = () => app.page;
const tab = () => page().locator("#t-box");
const dialog = (name) => page().getByRole("dialog", { name });
const sent = (m) => app.calls.filter((c) => c.method === m).map((c) => c.params);
const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test(document.getElementById("toast")?.textContent || ""), re.source);

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    engine_connections: () => ({ connections: db.conns }),
    databricks_bundle_targets: () => { if (db.targetsFail) throw new Error(db.targetsFail); return { name: "sales-etl", file: `${REPO}/databricks.yml`, targets: db.targets }; },
    databricks_bundle_validate: () => db.validate,
    databricks_bundle_plan: () => db.plan,
    databricks_bundle_deploy: (p) => db.deploy(p),
  } });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  Object.assign(db, { conns: CLI, targetsFail: null, targets: [{ name: "dev", mode: "development", default: true }],
    validate: { ok: true, warnings: [] }, plan: PLAN, deploy: () => ({ ok: true, changed: 1 }) });
  await page().evaluate(() => {
    closeDialog(); closeDialog();
    Object.assign(dbxBundle, { root: "", folder: "", info: null, target: "", connection: "", plan: null, validated: null, busy: "", allow: "" });
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    document.getElementById("t-box")?.remove();
  });
  app.calls.length = 0;
  app.errors.length = 0;
});

const mount = () => page().evaluate((root) => {
  const b = document.createElement("div"); b.id = "t-box"; document.body.append(b);
  b.append(gitBundlesTab({ root }));
}, REPO);

test("a databricks.yml that can't be read, one with no targets, and no CLI profile each say so", { skip }, async () => {
  db.targetsFail = "No databricks.yml in C:/src/sales-etl.";
  await mount();
  await tab().getByText("No databricks.yml in C:/src/sales-etl.").waitFor();
  db.targetsFail = null;
  db.targets = [];
  await tab().getByRole("button", { name: "Read" }).click();
  await tab().getByText("databricks.yml has no targets to deploy to.").waitFor();
  db.targets = [{ name: "dev", default: true }];
  db.conns = [CLI[2]];
  await tab().getByRole("button", { name: "Read" }).click();
  await tab().getByText("Bundles run with the Databricks CLI as one of its profiles, and there's none on this PC yet.").waitFor();
  assert.strictEqual(await tab().getByRole("combobox", { name: "Databricks CLI profile" }).count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("another profile is used from then on; a failed validate and plan show why, a plan with nothing to do says so", { skip }, async () => {
  await mount();
  const profile = tab().getByRole("combobox", { name: "Databricks CLI profile" });
  await profile.waitFor();
  assert.deepStrictEqual(await profile.locator("option").allInnerTexts(), ["dev (Databricks CLI)", "ops (Databricks CLI)"]);
  await profile.selectOption("cli:ops");
  db.validate = { ok: false, error: "Error: unknown target 'dev' in databricks.yml" };
  await tab().getByRole("button", { name: "Validate" }).click();
  await tab().getByRole("alert").getByText("Error: unknown target 'dev' in databricks.yml").waitFor();
  assert.strictEqual(sent("databricks_bundle_validate")[0].connection, "cli:ops");
  db.plan = { ok: false, error: "Error: cannot read the workspace's state" };
  await tab().getByRole("button", { name: "Plan" }).click();
  await tab().getByRole("alert").filter({ hasText: "cannot read the workspace's state" }).waitFor();
  assert.ok(await tab().getByRole("button", { name: "Deploy to dev…" }).isDisabled());
  db.plan = { ...PLAN, rows: [] };
  await tab().getByRole("button", { name: "Plan" }).click();
  await tab().getByText("Nothing to change on dev: it already matches these files.").waitFor();
  assert.ok(await tab().getByRole("button", { name: "Deploy to dev…" }).isDisabled());
  assert.deepStrictEqual(app.errors, []);
});

test("a deploy that runs the bundle's commands asks first, then deploys with them allowed", { skip }, async () => {
  db.deploy = (p) => (p.allow_commands === "c-7" ? { ok: true, changed: 2 }
    : { ok: false, needs_confirm: true, local_commands: ["databricks.yml: artifacts.wheel.build: python -m build"], commands_digest: "c-7" });
  await mount();
  await tab().getByRole("button", { name: "Plan" }).click();
  await tab().locator("table.dbx-plan").waitFor();
  await tab().getByRole("button", { name: "Deploy to dev…" }).click();
  await dialog("Deploy to dev?").getByRole("button", { name: "Deploy" }).click();
  const allow = dialog("Run this bundle's commands on this PC?");
  assert.strictEqual(await allow.locator(".dbx-commands").innerText(), "databricks.yml: artifacts.wheel.build: python -m build");
  await allow.getByRole("button", { name: "Allow and run" }).click();
  await toastSays(/^Deployed to dev: 2 changes\.$/);
  assert.deepStrictEqual(sent("databricks_bundle_deploy").map((p) => p.allow_commands), ["", "c-7"]);
  await tab().getByText("Plan to see what a deploy would change.", { exact: false }).waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("a deploy whose plan changed shows the new plan; one that fails keeps the plan with why", { skip }, async () => {
  const NEW = { ...PLAN, digest: "d-2", rows: [...PLAN.rows, { resource: "resources.jobs.extra", kind: "job", name: "extra", action: "create" }] };
  db.deploy = () => ({ ok: false, error: "The plan changed since you looked at it. Look at it again, then deploy.", replanned: NEW });
  await mount();
  await tab().getByRole("button", { name: "Plan" }).click();
  await tab().locator("table.dbx-plan").waitFor();
  await tab().getByRole("button", { name: "Deploy to dev…" }).click();
  await dialog("Deploy to dev?").getByRole("button", { name: "Deploy" }).click();
  await toastSays(/^The plan changed since you looked at it/);
  await page().waitForFunction(() => document.querySelectorAll("#t-box table.dbx-plan tbody tr").length === 2);
  db.deploy = () => ({ ok: false, error: "Error: PERMISSION_DENIED on job extra" });
  await tab().getByRole("button", { name: "Deploy to dev…" }).click();
  assert.match(await dialog("Deploy to dev?").innerText(), /This changes 1 to create, 1 to update in Databricks, as dev\./);
  await dialog("Deploy to dev?").getByRole("button", { name: "Deploy" }).click();
  await toastSays(/^The deploy didn't finish: see the plan area\./);
  await tab().getByRole("alert").getByText("Error: PERMISSION_DENIED on job extra").waitFor();
  assert.deepStrictEqual(sent("databricks_bundle_deploy").map((p) => p.digest), ["d-1", "d-2"]);
  assert.deepStrictEqual(app.errors, []);
});
