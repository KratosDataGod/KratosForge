// Source control > Bundles (Preview, dbx-bundles.js): the bundle's targets come from databricks.yml,
// Validate and Plan change nothing, Deploy is off until there is a plan, a safe plan asks a plain
// confirm, and a plan that deletes or rebuilds asks for the target's name typed (Cancel sends nothing).
// Deploy sends the digest of the plan shown, so a changed plan is shown again instead of deployed.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const SHOTS = process.env.SCREENSHOT_DIR;
const HOST = "https://adb-1234567890123456.7.azuredatabricks.net";
const REPO = "C:/src/sales-etl";
const STATUS = { commit: "a".repeat(40), branch: "main", upstream: null, ahead: 0, behind: 0, root: REPO, name: "sales-etl",
  staged: [], unstaged: [], untracked: [], conflicts: [], changed: 0, dirty: false, merging: false, stashes: 0, remotes: [],
  fetched_at: null, links: null, summary: "main, no uncommitted changes." };
const SAFE = { ok: true, target: "dev", rows: [{ resource: "resources.jobs.nightly_sales", kind: "job", name: "nightly_sales", action: "update" }],
  digest: "d-safe", destructive: 0, production: false, needs_typed_confirm: false };
const RISKY = { ok: true, target: "prod", digest: "d-risky", destructive: 1, production: true, needs_typed_confirm: true, rows: [
  { resource: "resources.jobs.legacy_export", kind: "job", name: "legacy_export", action: "delete" },
  { resource: "resources.pipelines.orders_cdc", kind: "pipeline", name: "orders_cdc", action: "create" }] };

let app;
const page = () => app.page;
const sent = (m) => app.calls.filter((c) => c.method === m).map((c) => c.params);

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1280, height: 900 },
    engine: ["app_settings"],
    mocks: {
      readiness_check: () => [],
      git_info: () => ({ installed: true, version: "2.47.1", repos: [{ path: REPO, name: "sales-etl", exists: true, kind: "notebooks", suggested: null }],
        current: REPO, kinds: { notebooks: { label: "Notebooks", holds: "Notebooks.", for: "For one team." } }, notebooks_folder: "C:/nb", clone_folder: "C:/src" }),
      git_status: () => STATUS,
      engine_connections: () => ({ connections: [{ key: "cli:dev", label: "dev (Databricks CLI)", source: "cli", host: HOST }] }),
      databricks_bundle_targets: () => ({ name: "sales-etl", file: `${REPO}/databricks.yml`, targets: [
        { name: "dev", mode: "development", default: true }, { name: "prod", mode: "production", default: false }] }),
      databricks_bundle_validate: (p) => (p.folder.endsWith("scripted") && p.allow_commands !== "c-1"
        ? { ok: false, needs_confirm: true, local_commands: ["databricks.yml: experimental.scripts.prebuild: make wheel"], commands_digest: "c-1", error: "x" }
        : { ok: true, warnings: ["Warning: unknown field: foo"] }),
      databricks_bundle_plan: (p) => (p.target === "prod" ? RISKY : SAFE),
      databricks_bundle_deploy: (p) => ({ ok: true, target: p.target, changed: p.target === "prod" ? 2 : 1, log: "Deployment complete!" }),
    },
  });
  await page().waitForFunction(() => typeof go === "function" && typeof gitBundlesTab === "function" && !!document.querySelector(".rail-item"));
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

test("the Bundles tab reads the targets; Validate and Plan send no deploy", { skip }, async () => {
  await page().evaluate(() => { gitPage.tab = "bundles"; go("source"); });
  await page().locator(".dbx-bundles button", { hasText: "Validate" }).waitFor({ timeout: 10000 });
  assert.match(await page().locator('.subtabs [role="tab"][aria-selected="true"]').textContent(), /Bundles/);
  assert.strictEqual(await page().locator('.dbx-bundles select[aria-label="Target"]').inputValue(), "dev");
  assert.ok(await page().locator(".dbx-bundles button", { hasText: "Deploy to dev" }).isDisabled());
  await page().locator(".dbx-bundles button", { hasText: "Validate" }).click();
  // The tab can be drawn again while a result arrives: wait for the result's text, then read it (as below).
  await page().locator(".dbx-valid", { hasText: /1 warning.*unknown field/ }).waitFor();
  assert.match(await page().locator(".dbx-valid").textContent(), /1 warning.*unknown field/);
  await page().locator(".dbx-bundles button", { hasText: "Plan" }).click();
  await page().locator(".dbx-plan tbody tr", { hasText: "nightly_sales" }).waitFor();
  assert.deepStrictEqual(await page().locator(".dbx-plan tbody tr").allTextContents(), ["Updatejobnightly_sales"]);
  assert.deepStrictEqual(sent("databricks_bundle_plan"), [{ folder: REPO, target: "dev", connection: "cli:dev", allow_commands: "" }]);
  assert.deepStrictEqual(sent("databricks_bundle_deploy"), []);
});

test("a safe plan deploys after a plain confirm, with the plan's digest", { skip }, async () => {
  await page().locator(".dbx-bundles button", { hasText: "Deploy to dev" }).click();
  await page().locator("#dialog-root .dialog").waitFor();
  assert.match(await page().locator("#dialog-root .dialog").textContent(), /1 to update in Databricks, as dev\. No job or pipeline runs\./);
  await page().locator("#dialog-root button", { hasText: /^Deploy$/ }).click();
  await page().locator(".dbx-plan").waitFor({ state: "detached" });
  assert.deepStrictEqual(sent("databricks_bundle_deploy"), [{ folder: REPO, target: "dev", connection: "cli:dev", allow_commands: "", digest: "d-safe", typed: "" }]);
});

test("a plan that deletes, on production, needs the target typed; Cancel sends nothing", { skip }, async () => {
  await page().locator('.dbx-bundles select[aria-label="Target"]').selectOption("prod");
  await page().locator(".dbx-bundles button", { hasText: "Plan" }).click();
  // The tab can be drawn again after a deploy ("Reading databricks.yml…"): wait for the plan's warning, then read it.
  await page().locator(".dbx-bundles", { hasText: /prod is a production target.*deletes or rebuilds 1 resource/ }).waitFor();
  assert.match(await page().locator(".dbx-bundles").textContent(), /prod is a production target.*deletes or rebuilds 1 resource/);
  await page().locator(".dbx-bundles button", { hasText: "Deploy to prod" }).click();
  const ok = page().locator("#dialog-root button", { hasText: "Deploy to prod" });
  assert.ok(await ok.isDisabled());
  await page().locator("#dialog-root input").fill("pro");
  assert.ok(await ok.isDisabled());
  await page().locator("#dialog-root button", { hasText: "Cancel" }).click();
  assert.strictEqual(sent("databricks_bundle_deploy").length, 1);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "source-databricks-bundles.png"), fullPage: true });
  await page().locator(".dbx-bundles button", { hasText: "Deploy to prod" }).click();
  await page().locator("#dialog-root input").fill("prod");
  await page().locator("#dialog-root button", { hasText: "Deploy to prod" }).click();
  await page().locator(".dbx-plan").waitFor({ state: "detached" });
  assert.deepStrictEqual(sent("databricks_bundle_deploy").at(-1), { folder: REPO, target: "prod", connection: "cli:dev", allow_commands: "", digest: "d-risky", typed: "prod" });
});

test("a bundle that runs commands here lists them first; Cancel runs nothing, Allow sends their digest", { skip }, async () => {
  await page().locator('.dbx-bundles input[aria-label="Bundle folder"]').fill(`${REPO}/scripted`);
  await page().locator(".dbx-bundles button", { hasText: "Read" }).click();
  await page().locator(".dbx-bundles button", { hasText: "Validate" }).waitFor();
  await page().locator(".dbx-bundles button", { hasText: "Validate" }).click();
  await page().locator("#dialog-root .dialog").waitFor();
  assert.match(await page().locator("#dialog-root .dialog").textContent(), /Run this bundle's commands on this PC\?.*experimental\.scripts\.prebuild: make wheel/);
  await page().locator("#dialog-root button", { hasText: "Cancel" }).click();
  await page().locator(".dbx-bundles button", { hasText: /^Validate$/ }).waitFor();
  assert.deepStrictEqual(sent("databricks_bundle_validate").map((p) => p.allow_commands), ["", ""]);
  await page().locator(".dbx-bundles button", { hasText: "Validate" }).click();
  await page().locator("#dialog-root button", { hasText: "Allow and run" }).click();
  await page().locator(".dbx-valid").waitFor();
  assert.deepStrictEqual(sent("databricks_bundle_validate").map((p) => p.allow_commands), ["", "", "", "c-1"]);
});

test("the Bundles tab has no serious axe findings", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  assert.deepStrictEqual(await axeFindings(page(), "#view .dbx-bundles"), []);
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
