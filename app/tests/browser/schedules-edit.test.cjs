// PB-048: Edit changes a schedule's times in place, on this PC and in Fabric, from Monitor and from the
// Schedule dialog (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

let app;
const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const calls = [];
const pcRow = { id: "s1", enabled: false, label: "nb_sales", text: "Mon, Tue, Wed, Thu, Fri at 06:00", next_run: null,
  target: { kind: "notebook", name: "nb_sales" }, last: null,
  config: { type: "Weekly", weekdays: ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"], times: ["06:00"], localTimeZoneId: "Local" } };
const fabRow = { id: "f1", enabled: true, name: "pl_load_gold", type: "Pipeline", item_type: "DataPipeline", job_type: "Pipeline",
  workspace_id: "ws1", workspace_name: "Sales Prod", item_id: "pl1", text: "Daily at 02:00 Tasmania Standard Time", last: null,
  configuration: { type: "Daily", times: ["02:00"], localTimeZoneId: "Tasmania Standard Time" } };

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    readiness_check: () => [],
    all_schedules: () => ({ pc: [pcRow], windows: true, fabric: { pending: false, items: [fabRow], workspaces: 1 }, runs: [], pc_set_aside: [] }),
    pc_schedules_for: () => [pcRow],
    update_pc_schedule: (p) => { calls.push(["pc", p]); return { ...pcRow }; },
    update_fabric_schedule: (p) => { calls.push(["fabric", p]); return { id: p.schedule_id }; },
  } });
});
after(async () => { if (app) await app.close(); });

const open = async (panel, row) => {
  await page().evaluate((p) => { monitorPage.panel = p; go("monitor"); }, panel);
  await page().click(`tr:has-text('${row}') button:has-text('Edit')`);
  await page().waitForSelector(`.dialog:has-text('Edit the schedule for ${row}')`);
};

test("Edit on this PC opens the schedule filled in and saves new times under the same id", { skip }, async () => {
  calls.length = 0;
  await open("schedules", "nb_sales");
  const d = page().locator(".dialog");
  assert.strictEqual(await d.locator("select").first().inputValue(), "weekly");
  assert.strictEqual(await d.locator("input[placeholder='06:00, 18:00']").inputValue(), "06:00");
  assert.ok(!(await d.locator("label:has-text('Sat') input").isChecked()) && await d.locator("label:has-text('Fri') input").isChecked());
  assert.ok(await d.locator(".zone-field").isHidden(), "this PC runs in its own time zone");
  await d.locator("input[placeholder='06:00, 18:00']").fill("06:00, 13:00");
  assert.match(await d.textContent(), /After saving: Mon, Tue, Wed, Thu, Fri at 06:00 and 13:00/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "schedule-edit-pc.png") });
  await d.locator("button:has-text('Save changes')").click();
  await page().waitForFunction(() => !document.querySelector(".dialog"));
  const [[where, p]] = calls;
  assert.strictEqual(where, "pc");
  assert.strictEqual(p.schedule_id, "s1");
  assert.deepStrictEqual(p.schedule.times.map((t) => t.trim()), ["06:00", "13:00"]);
  assert.strictEqual(p.schedule.kind, "weekly");
});

test("Edit in Fabric keeps its time zone, even one the list doesn't have, and keeps it on", { skip }, async () => {
  calls.length = 0;
  await open("fabric_schedules", "pl_load_gold");
  const d = page().locator(".dialog");
  assert.strictEqual(await d.locator("select[aria-label='Time zone']").inputValue(), "Tasmania Standard Time");
  assert.match(await d.textContent(), /in Fabric \(Sales Prod\)/);
  await d.locator("input[placeholder='06:00, 18:00']").fill("03:30");
  await d.locator("button:has-text('Save changes')").click();
  await page().waitForFunction(() => !document.querySelector(".dialog"));
  const [[where, p]] = calls;
  assert.strictEqual(where, "fabric");
  assert.deepStrictEqual({ ws: p.workspace_id, item: p.item_id, job: p.job_type, id: p.schedule_id, on: p.enabled },
    { ws: "ws1", item: "pl1", job: "Pipeline", id: "f1", on: true });
  assert.deepStrictEqual(p.schedule.times, ["03:30"]);
  assert.strictEqual(p.schedule.time_zone, "Tasmania Standard Time");
});

test("Cancel saves nothing", { skip }, async () => {
  calls.length = 0;
  await open("schedules", "nb_sales");
  await page().click(".dialog button:has-text('Cancel')");
  assert.deepStrictEqual(calls, []);
});

test("In the Schedule dialog, Edit fills the form below and Add becomes Save changes", { skip }, async () => {
  calls.length = 0;
  await page().evaluate(() => openScheduleDialog({ title: "Schedule nb_sales", what: "nb_sales",
    pc: { target: { kind: "notebook", name: "nb_sales" }, query: { kind: "notebook", name: "nb_sales" }, intro: "Runs it." },
    fabric: { unavailable: "Attach a lakehouse first." } }));
  const d = page().locator(".dialog");
  await d.locator("tr:has-text('Mon, Tue') button:has-text('Edit')").click();
  assert.match(await d.textContent(), /Edit the schedule: Mon, Tue, Wed, Thu, Fri at 06:00/);
  await d.locator("input[placeholder='06:00, 18:00']").fill("07:15");
  await page().click(".dialog-actions button:has-text('Save changes')");
  await page().waitForFunction(() => document.querySelector(".dialog-actions .primary")?.textContent.includes("Add schedule"));
  const [[where, p]] = calls;
  assert.strictEqual(where, "pc");
  assert.deepStrictEqual([p.schedule_id, p.schedule.times], ["s1", ["07:15"]]);
  await page().click(".dialog button:has-text('Close')");
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
