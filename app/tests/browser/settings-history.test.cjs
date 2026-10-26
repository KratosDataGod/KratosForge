// Optimize > Settings history (#392, from quality's probe): opening the tab reads only the lakehouse
// history; Fabric is read only from the buttons; the first read asks before approve; hostile values
// stay text.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");
const skip = !playwright && "Playwright is not installed";
const CONN = { workspace_id: "w1", workspace_name: "P", lakehouse_id: "lh1", lakehouse_name: "FabricDesktop_Central", schemas: true, schema_version: 1, access: "write" };
let app, approved = false, tenantAnswer;
const HIST = { connected: true, last_read: { capacity: "2026-10-20T10:00:00+00:00" },
  gaps: [{ read_at: "2026-10-20T11:00:00+00:00", scope_type: "tenant", scope_id: "t1", outcome: "unreadable", reason: "<img src=x onerror=window.__pwn=1>" },
    { read_at: "2026-10-20T10:30:00+00:00", scope_type: "tenant", scope_id: "t1", outcome: "unreadable", reason: "older" }],
  changes: [
    { scope_type: "capacity", scope_id: "c1", setting: "displayName", changed_after: null, changed_by_time: "2026-10-20T10:00:00+00:00", old_json: null, new_json: "\"<script>window.__pwn=2</script>\"" },
    { scope_type: "tenant", scope_id: "t1", setting: "<b id=pwn3>x</b>", changed_after: "2026-10-19T10:00:00+00:00", changed_by_time: "2026-10-20T10:00:00+00:00", old_json: "{\"enabled\":true}", new_json: null },
  ] };
before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    optimize_status: () => ({ allowed: true, default_name: "FabricDesktop_Central", installation_id: "x", label: "", active: CONN, connections: [CONN] }),
    tenant_settings_history: () => HIST,
    capacity_settings_read: ({ approve, admin }) => {
      if (admin) return { outcome: "read", read_at: "2026-10-20T12:00:00+00:00", overrides: "unreadable", changes: [] };
      if (!approve) throw new Error("PermissionError: Settings history adds fdcap_snapshot, fdcap_change and fdcap_read to FabricDesktop_Central. Confirm to continue.");
      approved = true; return { outcome: "read", read_at: "2026-10-20T12:00:00+00:00", changes: [] };
    },
    tenant_settings_read: () => tenantAnswer,
    app_settings: () => ({}),
  } });
  await app.page.waitForFunction(() => typeof renderOptimize === "function");
});
after(async () => { await app?.close(); });
const p = () => app.page;
test("opening the tab reads only the history, and hostile values stay text", { skip }, async () => {
  await p().evaluate(() => { optimizePage.tab = "settings"; go("optimize"); });
  await p().getByText("Read capacities now").waitFor();
  const methods = app.calls.map((c) => c.method);
  assert.ok(methods.includes("tenant_settings_history"));
  assert.ok(!methods.includes("capacity_settings_read") && !methods.includes("tenant_settings_read"), methods.join(","));
  assert.strictEqual(await p().evaluate(() => window.__pwn), undefined);
  assert.strictEqual(await p().locator("#pwn3").count(), 0);
  assert.ok((await p().textContent("body")).includes("<script>"));
  assert.ok((await p().textContent("body")).includes("No longer returned"));
  const body = await p().textContent("body");
  assert.ok(body.includes("2 reads since the last good one") && !body.includes("older"), "repeated failed reads are one line");
  assert.ok(body.includes("Anyone who can read the central lakehouse sees this history"));
});
test("first read asks; Cancel never approves; OK approves once", { skip }, async () => {
  await p().getByText("Read capacities now").click();
  const dlg = p().locator("[role=dialog]").last();
  await dlg.waitFor();
  assert.match(await dlg.textContent(), /fdcap_snapshot/);
  await dlg.getByRole("button", { name: /Cancel/ }).click();
  await p().waitForTimeout(300);
  assert.strictEqual(approved, false);
  assert.ok(!app.calls.some((c) => c.method === "capacity_settings_read" && c.params?.approve));
  await p().getByText("Read capacities now").click();
  await p().locator("[role=dialog]").last().getByRole("button", { name: "Create and read" }).click();
  await p().waitForFunction(() => document.body.textContent.includes("nothing changed"));
  assert.strictEqual(approved, true);
  assert.strictEqual(app.calls.filter((c) => c.method === "capacity_settings_read" && c.params?.approve).length, 1);
});
test("a non-administrator's tenant read says why", { skip }, async () => {
  tenantAnswer = { outcome: "unreadable", reason: "Only a Fabric administrator can read tenant settings (Tenant.Read.All).", changes: [] };
  await p().getByText("Read tenant settings (administrators)").click();
  await p().waitForFunction(() => document.body.textContent.includes("Only a Fabric administrator"));
  assert.deepStrictEqual(app.errors, []);
});
test("Include capacity overrides asks Fabric for them only when ticked, and says when they weren't read", { skip }, async () => {
  const reads = () => app.calls.filter((c) => c.method === "capacity_settings_read");
  const before = reads().length;
  const box = p().getByLabel("Include capacity overrides (administrators)");
  assert.strictEqual(await box.isChecked(), false);
  assert.ok(reads().slice(0, before).every((c) => !c.params?.admin), "nothing asks for overrides unless ticked");
  await box.check();
  await p().getByText("Read capacities now").click();
  await p().waitForFunction(() => document.body.textContent.includes("Overrides weren't read"));
  assert.strictEqual(reads().length, before + 1);
  assert.strictEqual(reads().at(-1).params.admin, true);
  assert.deepStrictEqual(app.errors, []);
});
