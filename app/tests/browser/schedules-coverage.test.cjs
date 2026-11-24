// Schedules (schedules.js), the paths the schedules-* tests leave out. Monitor > KratosForge > Schedules:
// a system that isn't Windows, a run that failed with its reason and its alert's failure, a skipped
// run, and a search that matches nothing. Monitor > Fabric > Schedules: still reading (and read again by
// itself), Fabric's schedules that can't be read, a run going on with its message, Open, and a search
// that matches nothing. The Schedule dialog: Fabric's list that can't be read, an item not in Fabric
// yet, editing one of Fabric's schedules, adding one in Fabric with the plain messages, adding one on a
// PC that isn't Windows, and the summary of an every-N-minutes schedule. The engine's replies are
// mocked; what the person sees and what was sent is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const later = (h) => new Date(Date.now() + h * 3600000).toISOString();
const PC = [
  { id: "p1", target: { kind: "notebook", name: "nb_sales" }, config: { type: "Daily", times: ["06:00"], localTimeZoneId: "Local" }, enabled: true,
    label: "nb_sales", text: "Every day at 06:00", next_run: later(5),
    last: { status: "failed", started: "2026-11-01T06:00:00Z", message: "Table orders not found", alert_error: "The alert email couldn't be sent." } },
  { id: "p2", target: { kind: "pipeline", name: "pl" }, config: { type: "Daily", times: ["07:00"], localTimeZoneId: "Local" }, enabled: true,
    label: "pl", text: "Every day at 07:00", next_run: later(6), last: { status: "skipped", started: "2026-11-01T07:00:00Z", message: "The PC was busy" } },
];
const FAB = [{ id: "f1", enabled: true, text: "Every day at 02:00 UTC", configuration: { type: "Daily", times: ["02:00"], localTimeZoneId: "UTC" },
  job_type: "Pipeline", workspace_id: "ws1", workspace_name: "Sales Prod", item_id: "pl1", name: "pl_load_gold", type: "Pipeline", item_type: "DataPipeline",
  last: { status: "InProgress", started: "2026-11-01T02:00:00Z", message: "Copying 3 of 7 tables" } }];

// Set before the app opens; each test changes what it needs.
const fresh = () => ({ windows: false, fabric: { pending: false, items: FAB, workspaces: 1, scanned: "2026-11-01T08:00:00Z" },
  fabricList: { published: false, schedules: [] }, fabricListFail: null, pc: [] });
const db = fresh();
let app;
const page = () => app.page;
const panel = () => page().locator(".page.monitor .schedules-panel");
const sent = (m) => app.calls.filter((c) => c.method === m).map((c) => c.params);
const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test(document.getElementById("toast")?.textContent || ""), re.source);

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    readiness_check: () => [],
    all_schedules: () => ({ pc: structuredClone(PC), windows: db.windows, runs: [], pc_set_aside: [], fabric: structuredClone(db.fabric) }),
    pc_schedules_for: () => db.pc,
    add_pc_schedule: ({ target }) => ({ schedule: { id: "p9", target }, windows: db.windows }),
    fabric_list: () => { if (db.fabricListFail) throw new Error(db.fabricListFail); return db.fabricList; },
    fabric_add: () => ({ ok: true }),
    update_fabric_schedule: () => ({ ok: true }),
    time_zones: () => ["UTC", "W. Europe Standard Time"],
  } });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  Object.assign(db, fresh());
  await page().evaluate(() => {
    closeMenu(); closeDialog(); closeDialog();
    state.query = "";
    window.__user = state.user; state.user = "chris@contoso.com";
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    go("home");
  });
  app.calls.length = 0;
  app.errors.length = 0;
});

const monitor = (p) => page().evaluate((p) => { monitorPage.panel = p; go("home"); go("monitor"); }, p);
const redrawWithQuery = (q) => page().evaluate((q) => { state.query = q; drawSchedules(document.querySelector(".schedules-panel")); }, q);
const row = (name) => panel().locator("tbody tr", { has: page().locator("b", { hasText: name }) });

test("this PC's schedules: not Windows, a failed run with both reasons, a skipped one, and a search with no match", { skip }, async () => {
  await monitor("schedules");
  await panel().getByText("Only Windows starts these. On this system they are saved but nothing runs them.").waitFor();
  assert.match(await panel().locator(".schedule-summary").innerText(), /2 of 2 on\s*1 failed last time/);
  const failed = row("nb_sales");
  assert.deepStrictEqual(await failed.locator(".reason").allInnerTexts(), ["Table orders not found", "The alert email couldn't be sent."]);
  assert.match(await row("pl").innerText(), /Skipped/i);
  await redrawWithQuery("zzz");
  await panel().getByText("No schedule on this PC matches the search.").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("Fabric's schedules: read again by itself while pending, then a run going on, Open, and no match", { skip }, async () => {
  db.fabric = { pending: true, items: [], workspaces: 0 };
  await monitor("fabric_schedules");
  await page().getByText("Reading schedules from Fabric...").waitFor();
  db.fabric = fresh().fabric;
  await row("pl_load_gold").waitFor({ timeout: 10000 });
  assert.ok(sent("all_schedules").length >= 2);
  assert.match(await row("pl_load_gold").innerText(), /Running[\s\S]*Copying 3 of 7 tables/);
  await page().evaluate(() => { window.__oif = window.openInFabric; window.__opened = []; window.openInFabric = (u) => window.__opened.push(u); });
  await row("pl_load_gold").getByRole("button", { name: "Open" }).click();
  const opened = await page().evaluate(() => { window.openInFabric = window.__oif; return window.__opened; });
  assert.strictEqual(opened.length, 1);
  assert.match(JSON.stringify(opened[0]), /pl1/);
  await redrawWithQuery("zzz");
  await panel().getByText("No Fabric schedule matches the search.").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("Fabric's schedules that can't be read say why", { skip }, async () => {
  db.fabric = { pending: false, items: [], error: "HTTPError: 500 Server Error", workspaces: 0 };
  await monitor("fabric_schedules");
  await panel().getByText("Couldn't read Fabric's schedules:", { exact: false }).waitFor();
  await panel().getByText("No schedules in these workspaces.").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

// The Schedule dialog with a Fabric side whose list, add and remove go to fabric_list / fabric_add.
const dialogFor = (mode, plain = false) => page().evaluate(([mode, plain]) => {
  openScheduleDialog({ title: "Schedule nb_sales", what: "nb_sales", mode,
    pc: { target: { kind: "notebook", name: "nb_sales" }, query: { kind: "notebook", name: "nb_sales" }, intro: "This PC runs the saved notebook." },
    fabric: { unavailable: "", intro: "Fabric runs the published notebook.", ...(plain ? {} : { adding: "Publishing...", added: "Scheduled." }),
      list: () => rpc("fabric_list"), add: (s) => rpc("fabric_add", { schedule: s }), remove: () => rpc("fabric_list") } });
}, [mode, plain]);
const d = () => page().getByRole("dialog", { name: "Schedule nb_sales" });

test("in the dialog, Fabric's list that can't be read leaves it empty; an item not in Fabric yet says adding publishes it", { skip }, async () => {
  db.fabricListFail = "HTTPError: 403 Forbidden";
  await dialogFor("fabric");
  await toastSays(/^Could not read schedules/);
  await page().waitForFunction(() => document.querySelector(".dialog .schedule-list")?.childElementCount === 0);
  db.fabricListFail = null;
  await d().getByRole("button", { name: "This PC" }).click();
  await d().getByText("No schedules on this PC yet.").waitFor();
  await d().getByRole("button", { name: "Fabric" }).click();
  await d().getByText("Not in Fabric yet. Adding a schedule publishes it.").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("Edit on one of Fabric's schedules saves it in place; adding in Fabric with no own messages uses the plain ones", { skip }, async () => {
  db.fabricList = { published: true, workspace_id: "ws1", item_id: "nb1", schedules: [{ id: "s1", enabled: true, text: "Every 30 minutes",
    configuration: { type: "Cron", interval: 30 } }] };
  await dialogFor("fabric", true);
  await d().locator("tr", { hasText: "Every 30 minutes" }).getByRole("button", { name: "Edit" }).click();
  await d().getByText("Edit the schedule: Every 30 minutes").waitFor();
  await page().locator(".dialog-actions").getByRole("button", { name: "Save changes" }).click();
  await toastSays(/^Schedule changed\.$/);
  const [u] = sent("update_fabric_schedule");
  assert.deepStrictEqual([u.workspace_id, u.item_id, u.job_type, u.schedule_id, u.enabled, u.schedule.kind, u.schedule.every_minutes],
    ["ws1", "nb1", "RunNotebook", "s1", true, "cron", 30]);
  await page().locator(".dialog-actions").getByRole("button", { name: "Add schedule" }).click();
  await toastSays(/^Scheduled in Fabric\.$/);
  assert.strictEqual(sent("fabric_add").length, 1);
  assert.deepStrictEqual(app.errors, []);
});

test("adding on a PC that isn't Windows says it's saved but won't start", { skip }, async () => {
  await dialogFor("pc");
  await d().getByText("No schedules on this PC yet.").waitFor();
  await page().locator(".dialog-actions").getByRole("button", { name: "Add schedule" }).click();
  await toastSays(/^Saved, but only Windows can start schedules on this PC\.$/);
  assert.strictEqual(sent("add_pc_schedule").length, 1);
  assert.deepStrictEqual(app.errors, []);
});

test("an every-N-minutes schedule is summed up as such before it is saved", { skip }, async () => {
  assert.deepStrictEqual(await page().evaluate(() => [scheduleSummary({ kind: "cron", every_minutes: 15, times: [], weekdays: [], time_zone: "UTC" }, true),
    scheduleSummary({ kind: "daily", every_minutes: 60, times: ["06:00"], weekdays: [], time_zone: "UTC" }, true)]),
  ["every 15 minutes", "every day at 06:00, UTC"]);
  assert.deepStrictEqual(app.errors, []);
});
