// App Studio's Publish dialog (appstudio.js, AS-13) against a mocked engine: the check comes first, Publish stays
// off until the workspace's name is typed and, when a report of that name is there, Replace is ticked; a
// check that went stale is shown again; a publish says where it went. Nothing reaches Fabric.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const CHECK = { ready: true, problems: [], warnings: ["Visual titles aren't compiled yet."], name: "Sales overview", workspace_id: "w1",
  workspace_name: "Sales Dev", confirm_text: "Sales Dev", model: { id: "m1", name: "Sales model" }, conflict: { id: "r1", ours: false },
  unfinished: null, fingerprint: "f1", parts: ["definition.pbir"] };
let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["app_settings"],
    mocks: {
      list_workspaces: () => [{ id: "w1", displayName: "Sales Dev" }, { id: "w2", displayName: "Sales Test" }],
      appstudio_publish_check: () => CHECK,
      appstudio_publish: (p) => (p.fingerprint === "stale" ? { ...CHECK, stale: true, done: false, fingerprint: "f2" }
        : { ...CHECK, done: true, replaced: true, report_id: "11111111-2222-3333-4444-555555555555",
          url: "https://app.powerbi.com/groups/w1/reports/11111111-2222-3333-4444-555555555555" }),
    },
  });
  await page().waitForFunction(() => state.settings && "show_preview" in state.settings);
});

after(async () => { await app?.close(); });

const open = () => page().evaluate(() => {
  state.user = state.user || { name: "Ana" };
  appStudio.project = "Sales overview.KFApp";
  appStudioPublishDialog({ valid: true, spec: { name: "Sales overview", pages: [], sources: [{ kind: "semantic-model", workspace_id: "w1", item_id: "m1" }] } });
});

test("Publish waits for the typed workspace name and the Replace tick", { skip }, async () => {
  await open();
  const dialog = page().locator(".dialog[aria-label='Publish to Power BI']");
  const confirm = dialog.locator("input[aria-label='Type Sales Dev to publish']");
  await confirm.waitFor();
  const go = dialog.locator(".dialog-actions button", { hasText: "Replace and publish" });
  assert.match(await dialog.textContent(), /may be someone else's/);
  assert.ok(await go.isDisabled());
  await confirm.fill("sales dev");
  assert.ok(await go.isDisabled(), "the name must match exactly");
  await confirm.fill("Sales Dev");
  assert.ok(await go.isDisabled(), "replacing needs its own tick");
  await dialog.locator(".check", { hasText: "Replace that report" }).locator("input").check();
  assert.ok(await go.isEnabled());
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "appstudio-publish.png") });
  await go.click();
  await dialog.locator(".callout.ok").waitFor();
  assert.match(await dialog.textContent(), /Replaced Sales overview in Sales Dev/);
  const sent = app.calls.filter((c) => c.method === "appstudio_publish").at(-1).params;
  assert.deepStrictEqual([sent.fingerprint, sent.confirm_name, sent.workspace_id, sent.replace], ["f1", "Sales Dev", "w1", true]);
  await dialog.locator("button", { hasText: "Close" }).first().click();
});

test("a check that went stale is shown again before anything is written", { skip }, async () => {
  CHECK.fingerprint = "stale";
  CHECK.conflict = null;
  await open();
  const dialog = page().locator(".dialog[aria-label='Publish to Power BI']");
  await dialog.locator("input[aria-label='Type Sales Dev to publish']").fill("Sales Dev");
  await dialog.locator(".dialog-actions button", { hasText: "Publish" }).click();
  await dialog.locator(".callout", { hasText: "Something changed since the check" }).waitFor();
  assert.ok(await dialog.locator(".dialog-actions button", { hasText: "Publish" }).isDisabled(), "the name is typed again for the new check");
  assert.deepStrictEqual(app.errors, []);
});
