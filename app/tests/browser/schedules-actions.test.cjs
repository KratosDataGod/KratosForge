// #1466: the schedule writes in app/ui/schedules.js, sent from a real browser (see harness.cjs for how
// to run it). Adding a schedule on this PC sends the item and the timing typed. Run now on this PC sends
// that schedule's id; Run now in Fabric starts a Fabric job, so it asks the risk check and then a
// capacity confirm first, and Cancel at either sends nothing. On/Paused sends that schedule with the
// new state, on this PC and in Fabric. Remove in Fabric asks in a danger confirm: Cancel sends nothing,
// confirming sends that schedule. An engine refusal shows a plain message, never the raw exception or
// [object Object]. Opening both Schedules pages and the Schedule dialog sends no write, and axe finds
// nothing serious on them or on the confirms. (Remove on this PC: schedules-remove-confirm.test.cjs;
// Edit: schedules-edit.test.cjs.)
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const READS = ["all_schedules", "pc_schedules_for", "time_zones", "risk_check", "workload_preflight"];
const WRITES = ["add_pc_schedule", "run_pc_schedule_now", "set_pc_schedule_enabled", "delete_pc_schedule", "update_pc_schedule",
  "run_fabric_item_now", "set_fabric_schedule_enabled", "delete_fabric_schedule", "update_fabric_schedule", "risk_acknowledge"];
const DENIED = "HTTPError: 403 Client Error: Forbidden for url: https://api.fabric.microsoft.com/v1/workspaces/ws1/items/pl1/jobs/Pipeline/schedules/f1";
const copy = (x) => JSON.parse(JSON.stringify(x));
const later = (h) => new Date(Date.now() + h * 3600000).toISOString();

// This PC's schedules as LocalSchedules.listing returns them (local_schedules.py): the stored item
// (id, target, config, enabled, created) with label, text, next_run and last added.
const pcRow = (id, enabled, label, target, text, config) => ({ id, target, config, enabled, created: "2026-10-01T08:00:00+00:00",
  label, text, next_run: enabled ? later(5) : null, last: null });
// Fabric's schedules as ScheduleMixin._scan_fabric_schedules keeps them (schedule_service.py).
const fabRow = (id, enabled, name, item_type, type, job_type, item_id, text, configuration) => ({ id, enabled, text, owner: null,
  configuration, job_type, workspace_id: "ws1", workspace_name: "Sales Prod", item_id, name, type, item_type,
  last: { status: "Completed", started: "2026-10-31T02:00:05Z", ended: "2026-10-31T02:09:40Z", message: "" } });

function fresh() {
  return {
    pc: [
      pcRow("a1b2c3d4e5f6", true, "nb_sales", { kind: "notebook", name: "nb_sales" }, "Every day at 06:00",
        { type: "Daily", times: ["06:00"], localTimeZoneId: "Local" }),
      pcRow("f6e5d4c3b2a1", false, "Sales (dev)", { kind: "meta", env: "dev", entities: null }, "Mon, Wed at 18:00",
        { type: "Weekly", weekdays: ["Monday", "Wednesday"], times: ["18:00"], localTimeZoneId: "Local" }),
    ],
    fabric: [
      fabRow("f1", true, "pl_load_gold", "DataPipeline", "Pipeline", "Pipeline", "pl1", "Every day at 02:00 UTC",
        { type: "Daily", times: ["02:00"], localTimeZoneId: "UTC" }),
      fabRow("f2", false, "nb_orders", "Notebook", "Notebook", "RunNotebook", "nb2", "Every 30 minutes",
        { type: "Cron", interval: 30 }),
    ],
  };
}

let db = fresh();
const fail = {};  // method -> the engine's error text for calls to it
let risk = null;  // what risk_check answers; null passes
let listed = true;  // false: workload_preflight answers not_in_inventory, as the engine does for an item it doesn't list
const pcFind = (id) => {
  const s = db.pc.find((x) => x.id === id);
  if (!s) throw new Error(`LookupError: There is no schedule ${id} on this PC`);  // local_schedules.py
  return s;
};
const refuse = (method) => { if (fail[method]) throw new Error(fail[method]); };

const mocks = {
  readiness_check: () => [],
  all_schedules: () => ({ pc: copy(db.pc), windows: true, runs: [], pc_set_aside: [],
    fabric: { pending: false, items: copy(db.fabric), all_workspaces: false, workspaces: 1, scanned: "2026-11-01T08:00:00+00:00" } }),
  pc_schedules_for: ({ kind, name }) => copy(db.pc.filter((s) => s.target.kind === kind && s.target.name === name)),
  // schedule_service.py add_pc_schedule: {schedule, windows}
  add_pc_schedule: ({ target, schedule }) => {
    refuse("add_pc_schedule");
    const item = pcRow("0a0b0c0d0e0f", true, target.name, target, "Every day at 07:30", { type: "Daily", times: schedule.times, localTimeZoneId: "Local" });
    db.pc.push(item);
    return { schedule: copy(item), windows: true };
  },
  run_pc_schedule_now: ({ schedule_id }) => { refuse("run_pc_schedule_now"); pcFind(schedule_id); return { started: schedule_id }; },
  set_pc_schedule_enabled: ({ schedule_id, enabled }) => {
    refuse("set_pc_schedule_enabled");
    const s = pcFind(schedule_id);
    s.enabled = !!enabled;
    s.next_run = enabled ? later(5) : null;
    return copy(s);
  },
  // workload_service.py workload_preflight: an item the inventory doesn't list is refused with this code
  workload_preflight: ({ id }) => listed ? { allowed: true, id }
    : { allowed: false, code: "not_in_inventory", reason: "That workload isn't in the inventory any more. It may have been renamed, "
      + "deleted, or be in a workspace this app no longer reads." },
  // risk.py analyze; a pass shows nothing (risk.js riskGate). It looks the workload up the same way
  // (workload_service.py _wl_record), so for an item the inventory doesn't list it raises NotInInventory.
  risk_check: ({ id, action }) => {
    if (!listed) throw new Error("NotInInventory: That workload isn't in the inventory any more. It may have been renamed, "
      + "deleted, or be in a workspace this app no longer reads.");
    return risk || { id: "r-pass", result: "pass", label: "Pass", summary: "Nothing should stop or slow this run",
      workload_id: id, action, findings: [], can_proceed: true, needs_acknowledgement: false, enforced: false };
  },
  // schedule_service.py run_fabric_item_now -> cloud_service.py _log_fabric_run
  run_fabric_item_now: (p) => {
    refuse("run_fabric_item_now");
    return { status: "NotStarted", started_at: "2026-11-01T08:00:00+00:00", finished_at: null, message: "", kind: "Scheduled item run",
      name: p.name, workspace_id: p.workspace_id, workspace_name: p.workspace_name, item_id: p.item_id, job_id: "job-1", engine: "spark" };
  },
  set_fabric_schedule_enabled: ({ schedule_id, enabled, configuration }) => {
    refuse("set_fabric_schedule_enabled");
    const s = db.fabric.find((x) => x.id === schedule_id);
    s.enabled = !!enabled;
    return { id: schedule_id, enabled: !!enabled, configuration };  // Fabric's Update Item Schedule answer
  },
  delete_fabric_schedule: ({ schedule_id }) => {
    refuse("delete_fabric_schedule");
    db.fabric = db.fabric.filter((x) => x.id !== schedule_id);
    return { deleted: schedule_id };
  },
};

let baseline;  // the methods the app calls on its own

before(async () => {
  if (skip) return;
  app = await openApp({ mocks });
  // The app's startup reads, and its first approval polls about 5 s in.
  await startupReads(app);
  for (const end = Date.now() + 15000; !["integrations_pending", "editor_pending"].every((m) => app.calls.some((c) => c.method === m));
    await new Promise((r) => setTimeout(r, 50))) {
    if (Date.now() > end) throw new Error("the app's startup polls never came");
  }
  baseline = new Set(app.calls.map((c) => c.method));
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  db = fresh();
  risk = null;
  listed = true;
  for (const k of Object.keys(fail)) delete fail[k];
  await page().evaluate(() => { closeMenu(); closeDialog(); const t = document.getElementById("toast"); t.hidden = true; t.textContent = ""; go("home"); });
});

const writes = (from) => app.calls.slice(from).filter((c) => WRITES.includes(c.method)).map((c) => ({ method: c.method, params: c.params }));
const pause = (ms) => new Promise((r) => setTimeout(r, ms));
const toastText = async () => (await page().waitForFunction(() => { const t = document.getElementById("toast"); return !t.hidden && t.textContent; })).jsonValue();

// A Schedules page with its rows drawn; n rows in its table.
async function schedules(panel, n) {
  await page().evaluate((p) => { monitorPage.panel = p; go("home"); go("monitor"); }, panel);
  await page().waitForFunction((n) => document.querySelectorAll(".page.monitor .schedules-panel table.list tbody tr").length === n, n);
}
const row = (name) => page().locator(".page.monitor .schedules-panel tbody tr", { has: page().locator("b", { hasText: name }) });

// The Schedule dialog for a notebook, as scheduleNotebook opens it (app.js), on This PC.
async function scheduleDialog() {
  await page().evaluate(() => openScheduleDialog({ title: "Schedule nb_sales", what: "nb_sales",
    pc: { target: { kind: "notebook", name: "nb_sales" }, query: { kind: "notebook", name: "nb_sales" }, intro: "This PC runs the saved notebook." },
    fabric: { unavailable: "Attach a lakehouse first." } }));
  const d = page().getByRole("dialog", { name: "Schedule nb_sales" });
  await d.locator("tr", { hasText: "Every day at 06:00" }).waitFor();
  return d;
}

test("Add schedule on this PC sends that notebook and exactly the timing entered", { skip }, async () => {
  const d = await scheduleDialog();
  await d.getByRole("combobox").first().selectOption("weekly");
  await d.getByRole("textbox", { name: /Times of day/ }).fill("07:30, 19:15");
  for (const day of ["Mon", "Wed", "Fri"]) await d.getByRole("checkbox", { name: day }).uncheck();  // Tue and Thu stay
  const from = app.calls.length;
  await page().locator(".dialog-actions").getByRole("button", { name: "Add schedule" }).click();
  assert.match(await toastText(), /Scheduled on this PC/);
  const sent = writes(from);
  assert.strictEqual(sent.length, 1, JSON.stringify(sent));
  const [{ method, params }] = sent;
  assert.strictEqual(method, "add_pc_schedule");
  assert.deepStrictEqual(params.target, { kind: "notebook", name: "nb_sales" });
  assert.deepStrictEqual({ ...params.schedule, times: params.schedule.times.map((t) => t.trim()), time_zone: undefined },
    { kind: "weekly", times: ["07:30", "19:15"], every_minutes: 60, weekdays: ["Tuesday", "Thursday"], time_zone: undefined });
  // The list reads again and shows the new one.
  await d.locator("tr", { hasText: "Every day at 07:30" }).waitFor();
});

test("Add schedule every few minutes sends the minutes entered", { skip }, async () => {
  const d = await scheduleDialog();
  await d.getByRole("combobox").first().selectOption("cron");
  await d.getByRole("spinbutton", { name: "Minutes between runs" }).fill("45");
  const from = app.calls.length;
  await page().locator(".dialog-actions").getByRole("button", { name: "Add schedule" }).click();
  await toastText();
  const [{ method, params }] = writes(from);
  assert.strictEqual(method, "add_pc_schedule");
  assert.deepStrictEqual([params.schedule.kind, params.schedule.every_minutes], ["cron", 45]);
});

test("Run now on this PC sends that schedule's id", { skip }, async () => {
  await schedules("schedules", 2);
  const from = app.calls.length;
  await row("Sales (dev)").getByRole("button", { name: "Run now" }).click();
  assert.match(await toastText(), /^Sales \(dev\) started on this PC/);
  assert.deepStrictEqual(writes(from), [{ method: "run_pc_schedule_now", params: { schedule_id: "f6e5d4c3b2a1" } }]);
});

test("Paused and On on this PC send that schedule and the new state", { skip }, async () => {
  await schedules("schedules", 2);
  let from = app.calls.length;
  await row("nb_sales").getByRole("button", { name: "On", exact: true }).click();
  await row("nb_sales").getByRole("button", { name: "Paused", exact: true }).waitFor();
  assert.deepStrictEqual(writes(from), [{ method: "set_pc_schedule_enabled", params: { schedule_id: "a1b2c3d4e5f6", enabled: false } }]);
  from = app.calls.length;
  await row("Sales (dev)").getByRole("button", { name: "Paused", exact: true }).click();
  await row("Sales (dev)").getByRole("button", { name: "On", exact: true }).waitFor();
  assert.deepStrictEqual(writes(from), [{ method: "set_pc_schedule_enabled", params: { schedule_id: "f6e5d4c3b2a1", enabled: true } }]);
});

const RUN_FABRIC = { method: "run_fabric_item_now", params: { workspace_id: "ws1", item_id: "pl1", job_type: "Pipeline",
  name: "pl_load_gold", workspace_name: "Sales Prod" } };

test("Run now in Fabric checks the risk, asks before using capacity, then starts that item's job", { skip }, async () => {
  await schedules("fabric_schedules", 2);
  const from = app.calls.length;
  await row("pl_load_gold").getByRole("button", { name: "Run now" }).click();
  const confirm = page().getByRole("dialog", { name: "Run pl_load_gold now?" });
  await confirm.waitFor();
  assert.match(await confirm.innerText(), /Sales Prod, which uses that workspace's capacity/);
  const checked = app.calls.slice(from).filter((c) => c.method === "risk_check").map((c) => c.params);
  assert.deepStrictEqual(checked, [{ id: "fabric:ws1:pl1", action: "run_item", env: null, refresh: false }]);
  assert.deepStrictEqual(writes(from), [], "nothing starts before the confirm");
  await confirm.getByRole("button", { name: "Run now" }).click();
  assert.match(await toastText(), /^pl_load_gold started in Fabric/);
  assert.deepStrictEqual(writes(from), [RUN_FABRIC]);
});

test("Run now in Fabric: Cancel at the confirm sends nothing", { skip }, async () => {
  await schedules("fabric_schedules", 2);
  const from = app.calls.length;
  await row("pl_load_gold").getByRole("button", { name: "Run now" }).click();
  const confirm = page().getByRole("dialog", { name: "Run pl_load_gold now?" });
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await confirm.waitFor({ state: "detached" });
  await pause(300);
  assert.deepStrictEqual(writes(from), []);
});

test("Run now in Fabric: a blocked risk check stops it, and nothing is sent", { skip }, async () => {
  risk = { id: "r-blocked", result: "blocked", label: "Blocked", summary: "The capacity is paused", findings: [
    { check: "capacity", outcome: "blocked", label: "Blocked", title: "Capacity F8 is paused", detail: "Fabric can't start runs on a paused capacity.",
      evidence: ["Read 2 min ago"], remedy: "Resume it in the Azure portal.", can_override: false }],
    can_proceed: false, needs_acknowledgement: false, enforced: false, note: "" };
  await schedules("fabric_schedules", 2);
  const from = app.calls.length;
  await row("pl_load_gold").getByRole("button", { name: "Run now" }).click();
  const gate = page().getByRole("dialog", { name: "Run pl_load_gold now?" });
  await gate.getByText("Capacity F8 is paused").waitFor();
  assert.strictEqual(await gate.getByRole("button", { name: "Run now" }).count(), 0, "a blocked run has no Run now");
  await gate.locator(".dialog-actions").getByRole("button", { name: "Close" }).click();
  await gate.waitFor({ state: "detached" });
  await pause(300);
  assert.deepStrictEqual(writes(from), []);
});

test("Run now in Fabric for an item the app doesn't list: one confirm that says the risk check wasn't run", { skip }, async () => {
  listed = false;
  try {
    await schedules("fabric_schedules", 2);
    const from = app.calls.length;
    await row("pl_load_gold").getByRole("button", { name: "Run now" }).click();
    const confirm = page().getByRole("dialog", { name: "Run pl_load_gold now?" });
    await confirm.waitFor();
    await pause(300);
    assert.strictEqual(await page().getByRole("dialog").count(), 1, "only the capacity confirm");
    const text = await confirm.innerText();
    assert.match(text, /Sales Prod, which uses that workspace's capacity/);
    assert.match(text, /The risk check wasn't run: it covers only workloads in the workspaces this app works in/);
    assert.doesNotMatch(text, /deleted|couldn't run|inventory/i);
    const looked = app.calls.slice(from).filter((c) => c.method === "workload_preflight").map((c) => c.params);
    assert.deepStrictEqual(looked, [{ id: "fabric:ws1:pl1", action: "run_item", env: null, assess: false }]);
    assert.strictEqual(app.calls.slice(from).filter((c) => c.method === "risk_check").length, 0, "the check isn't asked");
    await confirm.getByRole("button", { name: "Run now" }).click();
    assert.match(await toastText(), /^pl_load_gold started in Fabric/);
    assert.deepStrictEqual(writes(from), [RUN_FABRIC]);
  } finally {
    listed = true;
  }
});

test("Paused and On in Fabric send that schedule, its configuration and the new state", { skip }, async () => {
  await schedules("fabric_schedules", 2);
  let from = app.calls.length;
  await row("pl_load_gold").getByRole("button", { name: "On", exact: true }).click();
  await row("pl_load_gold").getByRole("button", { name: "Paused", exact: true }).waitFor();
  assert.deepStrictEqual(writes(from), [{ method: "set_fabric_schedule_enabled", params: { workspace_id: "ws1", item_id: "pl1",
    job_type: "Pipeline", schedule_id: "f1", enabled: false, configuration: { type: "Daily", times: ["02:00"], localTimeZoneId: "UTC" } } }]);
  from = app.calls.length;
  await row("nb_orders").getByRole("button", { name: "Paused", exact: true }).click();
  await row("nb_orders").getByRole("button", { name: "On", exact: true }).waitFor();
  assert.deepStrictEqual(writes(from), [{ method: "set_fabric_schedule_enabled", params: { workspace_id: "ws1", item_id: "nb2",
    job_type: "RunNotebook", schedule_id: "f2", enabled: true, configuration: { type: "Cron", interval: 30 } } }]);
});

async function removeFabric() {
  await schedules("fabric_schedules", 2);
  await row("nb_orders").getByRole("button", { name: "Remove" }).click();
  const d = page().getByRole("dialog", { name: "Remove the schedule for nb_orders?" });
  await d.waitFor();
  return d;
}

test("Remove in Fabric asks in red, and Cancel sends nothing", { skip }, async () => {
  const from = app.calls.length;
  const d = await removeFabric();
  assert.match(await d.innerText(), /Every 30 minutes\. Fabric deletes the schedule/);
  assert.match(await d.getByRole("button", { name: "Remove" }).getAttribute("class"), /danger-solid/);
  await d.getByRole("button", { name: "Cancel" }).click();
  await d.waitFor({ state: "detached" });
  await pause(300);
  assert.deepStrictEqual(writes(from), []);
  assert.strictEqual(await row("nb_orders").count(), 1);
});

test("confirming Remove in Fabric sends that schedule, and it leaves the list", { skip }, async () => {
  const from = app.calls.length;
  const d = await removeFabric();
  await d.getByRole("button", { name: "Remove" }).click();
  await page().waitForFunction(() => document.querySelectorAll(".page.monitor .schedules-panel tbody tr").length === 1);
  assert.deepStrictEqual(writes(from), [{ method: "delete_fabric_schedule", params: { workspace_id: "ws1", item_id: "nb2",
    job_type: "RunNotebook", schedule_id: "f2" } }]);
  assert.strictEqual(await row("pl_load_gold").count(), 1);
});

// Each write refused by the engine: the toast is the label and a sentence, never the exception's name,
// the URL or [object Object], and the list stays as it was.
const REFUSALS = [
  { name: "Run now on this PC", method: "run_pc_schedule_now", panel: "schedules", rows: 2, row: "nb_sales", button: "Run now",
    error: "LookupError: There is no schedule a1b2c3d4e5f6 on this PC", label: "Not started" },
  { name: "Pause on this PC", method: "set_pc_schedule_enabled", panel: "schedules", rows: 2, row: "nb_sales", button: "On",
    error: "OSError: Task Scheduler refused the task", label: "Not paused" },
  { name: "Run now in Fabric", method: "run_fabric_item_now", panel: "fabric_schedules", rows: 2, row: "pl_load_gold", button: "Run now",
    confirm: "Run now", error: DENIED, label: "Not started" },
  { name: "Turn on in Fabric", method: "set_fabric_schedule_enabled", panel: "fabric_schedules", rows: 2, row: "nb_orders", button: "Paused",
    error: DENIED, label: "Not turned on" },
  { name: "Remove in Fabric", method: "delete_fabric_schedule", panel: "fabric_schedules", rows: 2, row: "nb_orders", button: "Remove",
    confirm: "Remove", error: DENIED, label: "Not removed" },
];
for (const r of REFUSALS) {
  test(`${r.name}: an engine refusal shows a plain message and changes nothing`, { skip }, async () => {
    fail[r.method] = r.error;
    await schedules(r.panel, r.rows);
    const before = JSON.stringify(db);
    await row(r.row).getByRole("button", { name: r.button, exact: true }).click();
    if (r.confirm) await page().locator(".dialog").getByRole("button", { name: r.confirm, exact: true }).click();
    const said = (await toastText()).split("\n")[0];
    assert.ok(said.startsWith(`${r.label}: `), said);
    assert.ok(said.length > r.label.length + 10, said);
    assert.doesNotMatch(said, /\[object Object\]|undefined|Error:|HTTPError|https?:\/\//);
    assert.strictEqual(JSON.stringify(db), before);
    assert.strictEqual(await row(r.row).getByRole("button", { name: r.button, exact: true }).count(), 1, "the row stays as it was");
  });
}

test("Add schedule refused: a plain message, and the dialog keeps what was typed", { skip }, async () => {
  fail.add_pc_schedule = "ValueError: 25:00 isn't a time of day (use HH:MM, 24-hour)";
  const d = await scheduleDialog();
  await d.getByRole("textbox", { name: /Times of day/ }).fill("25:00");
  await page().locator(".dialog-actions").getByRole("button", { name: "Add schedule" }).click();
  const said = (await toastText()).split("\n")[0];
  assert.strictEqual(said, "Schedule not added: 25:00 isn't a time of day (use HH:MM, 24-hour)");
  assert.strictEqual(await d.getByRole("textbox", { name: /Times of day/ }).inputValue(), "25:00");
  assert.strictEqual(db.pc.length, 2);
});

test("opening both Schedules pages and the Schedule dialog sends no write", { skip }, async () => {
  const from = app.calls.length;
  await schedules("schedules", 2);
  await schedules("fabric_schedules", 2);
  await page().locator("[aria-label=Workspaces] button", { hasText: "All workspaces" }).click();
  await schedules("schedules", 2);
  const d = await scheduleDialog();
  await d.getByRole("combobox").first().selectOption("weekly");
  await page().locator(".dialog-actions").getByRole("button", { name: "Close" }).click();
  await pause(500);
  const made = app.calls.slice(from).map((c) => c.method);
  for (const m of ["all_schedules", "pc_schedules_for"]) assert.ok(made.includes(m), `${m} was read`);
  assert.deepStrictEqual(made.filter((m) => WRITES.includes(m)), []);
  assert.deepStrictEqual([...new Set(made.filter((m) => !READS.includes(m) && !baseline.has(m)))], []);
});

test("axe finds nothing serious on the Schedules pages, the Schedule dialog and the confirms", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  const check = async (where, selector) => assert.deepStrictEqual(await axeFindings(page(), selector), [], where);
  await schedules("schedules", 2);
  await check("this PC", ".page.monitor");
  await schedules("fabric_schedules", 2);
  await check("Fabric", ".page.monitor");
  await row("pl_load_gold").getByRole("button", { name: "Run now" }).click();
  await page().getByRole("dialog", { name: "Run pl_load_gold now?" }).waitFor();
  await check("run confirm", "#dialog-root .dialog");
  await page().evaluate(() => closeDialog());
  await removeFabric();
  await check("remove confirm", "#dialog-root .dialog");
  await page().evaluate(() => closeDialog());
  await scheduleDialog();
  await check("schedule dialog", "#dialog-root .dialog");
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
