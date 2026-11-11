// App Studio's Describe and Ask for a change (appstudio.js, AS-10/11) against a mocked engine: without a model
// connection both point to Model connections; Describe says what it sends, shows the plan as the engine kept
// it (with what was left out) and builds only on Build it; Ask shows the change plan and applies it only on
// Apply, Discard drops it. No model and nothing in Fabric is reached.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const CONN = { id: "c1", name: "My Claude", provider: "anthropic", model: "claude-sonnet-5-5", key_ref: "credman:KratosForge/c",
  consent: { context: true, sample_rows: false }, refused: [], usage: {} };
const CONNS = { policy: { enabled: true, sample_rows: false }, providers: [], connections: [CONN], default: "c1" };
const SPEC = { name: "Sales overview", target: "powerbi-report", sources: [{ kind: "sample", id: "src_1" }], measures: [],
  pages: [{ id: "pg_1", title: "Overview", components: [{ id: "c_1", type: "bar", title: "Revenue by region", measures: [], fields: [], layout: { x: 0, y: 0, w: 6, h: 4 } }] }] };
const OPENED = { project: "Sales overview.KFApp", spec: SPEC, valid: true, problems: [], can_undo: false, can_redo: false, components: ["bar", "column"] };
const PLAN = { name: "Sales overview", purpose: "Revenue for leads", audience: ["Sales leads"], questions: ["Which fiscal year?"],
  pages: [{ title: "Overview", components: [{ type: "card", title: "Revenue", measure: "Revenue", field: "" }] }] };
let conns = { ...CONNS, connections: [] };
let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["app_settings"],
    mocks: {
      appstudio_home: () => ({ templates: [{ id: "sales", title: "Sales overview", text: "Revenue" }], projects: [],
        targets: [{ id: "powerbi-report", label: "Power BI report", components: [], interactions: [] }] }),
      appstudio_connections: () => conns,
      appstudio_plan: () => ({ plan_id: "p1", plan: PLAN, left_out: ["Overview: 3D (hologram isn't available for a Power BI report)"],
        sent: { tables: 2, columns: 5, measures: 2, expressions: false, rows: 0, description_chars: 40 },
        connection: { name: "My Claude", provider: "anthropic", model: "claude-sonnet-5-5" }, usage: { input_tokens: 900, output_tokens: 100 }, warning: "" }),
      appstudio_create_from_plan: () => ({ ...OPENED, left_out: [] }),
      appstudio_open: () => OPENED,
      appstudio_preview: () => ({ note: "", page: { title: "Overview" }, components: [] }),
      appstudio_change_plan: () => ({ change_id: "ch1", summary: "Show the region chart as columns", done: ["Show c_1 as a column"],
        left_out: [], questions: [], changed: ["c_1"], spec: SPEC, sent: {}, connection: {}, usage: {}, warning: "" }),
      appstudio_apply_change: () => ({ ...OPENED, can_undo: true }),
    },
  });
  await page().waitForFunction(() => state.settings && "show_preview" in state.settings);
});

after(async () => { await app?.close(); });

test("without a model connection, Describe points to Model connections", { skip }, async () => {
  await page().evaluate(() => { appStudioLeave(); go("appStudio"); });
  const card = page().locator(".as-describe");
  await card.waitFor();
  assert.match(await card.textContent(), /Add your own AI model[\s\S]*Model connections/);
  assert.strictEqual(await card.locator("textarea").count(), 0);
});

test("Describe says what it sends, shows the plan and builds only on Build it", { skip }, async () => {
  conns = CONNS;
  await page().evaluate(() => { appStudioLeave(); render(); });
  const card = page().locator(".as-describe");
  await card.locator("textarea").fill("An executive sales dashboard");
  assert.match(await card.textContent(), /Sends your description and the sample data's field names to My Claude\. Never rows\./);
  await card.locator("button", { hasText: "Make a plan" }).click();
  const plan = page().locator(".as-plan");
  await plan.waitFor();
  assert.match(await plan.textContent(), /Plan: Sales overview[\s\S]*Card · Revenue · Revenue[\s\S]*Left out \(1\)[\s\S]*hologram[\s\S]*Which fiscal year\?[\s\S]*No rows/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "appstudio-plan.png") });
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#view"), []);
  assert.ok(!app.calls.some((c) => c.method === "appstudio_create_from_plan"), "nothing is made before Build it");
  await plan.locator("input[aria-label='App name']").fill("Exec sales");
  await plan.locator("button", { hasText: "Build it" }).click();
  await page().locator(".as-ask").waitFor();
  const sent = app.calls.find((c) => c.method === "appstudio_create_from_plan").params;
  assert.deepStrictEqual([sent.plan_id, sent.name, sent.plan.pages.length], ["p1", "Exec sales", 1]);
});

test("Ask shows the change plan and changes nothing until Apply", { skip }, async () => {
  const ask = page().locator(".as-ask");
  await ask.locator("textarea").fill("Make the region chart a column chart");
  await ask.locator("button", { hasText: "Plan the change" }).click();
  await ask.locator(".as-change").waitFor();
  assert.match(await ask.locator(".as-change").textContent(), /Show "Revenue by region" as a column[\s\S]*1 item change\. Nothing else is touched/);
  assert.ok(!app.calls.some((c) => c.method === "appstudio_apply_change"));
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "appstudio-ask.png") });
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#view"), []);
  await ask.locator("button", { hasText: "Apply" }).click();
  await page().waitForFunction(() => document.getElementById("toast")?.textContent.includes("Applied. Undo puts it back."));
  const sent = app.calls.find((c) => c.method === "appstudio_apply_change").params;
  assert.deepStrictEqual([sent.project_name, sent.change_id], ["Sales overview.KFApp", "ch1"]);
  await page().locator(".as-ask textarea").waitFor();
  assert.strictEqual(await page().locator(".as-change").count(), 0, "the applied change is gone from the panel");
  assert.deepStrictEqual(app.errors, []);
});
