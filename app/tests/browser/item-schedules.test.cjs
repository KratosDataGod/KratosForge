// #1483: the Fabric schedules of one item, from the Schedule dialog in a real browser (see harness.cjs
// for how to run it): a notebook's (scheduleNotebook in app.js) and a metadata environment's
// (scheduleMetaRuns in meta.js). Add schedule saves the notebook and sends add_notebook_schedule with
// the notebook's publish payload and exactly the timing typed; for an environment it sends
// meta_add_schedule with that environment and the timing. Remove asks first in a danger confirm: Cancel
// sends nothing, confirming sends that schedule's id (delete_notebook_schedule, meta_delete_schedule).
// An engine refusal reads as a sentence, never the exception's name, a URL or [object Object]. Opening
// both dialogs sends no write, and axe finds nothing serious on them or on the confirm. (The dialog's
// This PC side and Monitor's Schedules pages: schedules-actions.test.cjs; Edit: schedules-edit.test.cjs.)
// Known gap, left as todo tests: the Remove confirm takes the Schedule dialog's place, so after it the
// dialog is gone; the fix belongs in schedules.js, which another open PR owns.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
// The schedule listings and zones, and what the notebook page reads when the notebook opens.
const READS = ["notebook_schedules", "meta_schedules", "time_zones", "pc_schedules_for",
  "git_notebook_info", "notebook_inputs", "notebook_comments_list", "notebook_syntax"];
const WRITES = ["save_notebook", "add_notebook_schedule", "delete_notebook_schedule", "meta_add_schedule", "meta_delete_schedule",
  "update_fabric_schedule", "add_pc_schedule", "delete_pc_schedule"];
const DENIED = "HTTPError: 403 Client Error: Forbidden for url: https://api.fabric.microsoft.com/v1/workspaces/ws-sales/items/nb-1/jobs/RunNotebook/schedules/s1";
const copy = (x) => JSON.parse(JSON.stringify(x));

const LAKEHOUSE = { workspace_id: "ws-sales", workspace_name: "Sales", lakehouse_id: "lh-1", name: "Sales_LH" };
const NOTEBOOK = { name: "nb_sales", lakehouse: LAKEHOUSE, compute: "fabric",
  cells: [{ type: "code", cell_id: "c1", source: "df = spark.read.table('orders')", meta: {} },
    { type: "markdown", cell_id: "c2", source: "# Orders", meta: {} }] };
// The metadata project's environments as meta/project.py keeps them: Test and Prod run in Fabric.
const ENVIRONMENTS = { dev: { compute: "local" }, test: { compute: "fabric", workspace_id: "ws-test" },
  prod: { compute: "fabric", workspace_id: "ws-prod" } };

// A schedule as Fabric's List Item Schedules returns it (fabric_api.py list_schedules), with the text
// cloud_service.py adds from fabric_run.py describe_schedule.
const describe = (c) => c.type === "Cron" ? `Every ${c.interval} minutes`
  : c.type === "Daily" ? `Daily at ${c.times.join(", ")} ${c.localTimeZoneId}`
  : `${c.weekdays.map((d) => d.slice(0, 3)).join(", ")} at ${c.times.join(", ")} ${c.localTimeZoneId}`;
const sched = (id, enabled, configuration) => ({ id, enabled, createdDateTime: "2026-10-01T08:00:00Z",
  owner: { id: "u1", type: "User" }, text: describe(configuration),
  configuration: { startDateTime: "2026-10-01T08:00:00", endDateTime: "2036-09-29T08:00:00", ...configuration } });

function fresh() {
  return {
    notebook: [sched("s1", true, { type: "Daily", times: ["06:00"], localTimeZoneId: "UTC" }),
      sched("s2", false, { type: "Cron", interval: 30, localTimeZoneId: "UTC" })],
    test: [sched("m1", true, { type: "Weekly", weekdays: ["Monday", "Thursday"], times: ["05:30"], localTimeZoneId: "UTC" })],
    prod: [sched("m2", true, { type: "Daily", times: ["02:00"], localTimeZoneId: "UTC" }),
      sched("m3", true, { type: "Daily", times: ["14:00"], localTimeZoneId: "UTC" })],
  };
}

let db = fresh();
const fail = {};  // method -> the engine's error text for calls to it
const refuse = (method) => { if (fail[method]) throw new Error(fail[method]); };

// fabric_run.py schedule_configuration: what Fabric is sent for the form's values (its checks too).
function configuration(s) {
  const times = (s.times || []).map((t) => t.trim()).filter(Boolean);
  for (const t of times) if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(t)) throw new Error(`ValueError: Times are HH:MM in 24-hour form, like 06:00 (got '${t}')`);
  const base = { startDateTime: "2026-11-01T08:00:00", endDateTime: "2036-10-30T08:00:00", localTimeZoneId: s.time_zone || "UTC" };
  if (s.kind === "cron") return { ...base, type: "Cron", interval: Number(s.every_minutes) };
  if (s.kind === "daily") return { ...base, type: "Daily", times };
  return { ...base, type: "Weekly", weekdays: s.weekdays, times };
}

// cloud_service.py notebook_schedules: the published notebook's item and its schedules.
const nbListing = () => ({ published: true, item_id: "nb-1", workspace_id: "ws-sales", workspace_name: "Sales",
  schedules: copy(db.notebook) });
// cloud_service.py meta_schedules: the runner notebook's schedules in the environment's workspace.
const metaListing = (env) => ENVIRONMENTS[env].compute !== "fabric" ? { env, compute: "local", published: false, schedules: [] }
  : { env, compute: "fabric", published: true, workspace_id: ENVIRONMENTS[env].workspace_id, item_id: `runner-${env}`, schedules: copy(db[env]) };
let nextId = 1;

const mocks = {
  readiness_check: () => [],
  // time_zones.py time_zones: every Windows zone and this PC's.
  time_zones: () => ({ zones: [{ id: "UTC", name: "(UTC) Coordinated Universal Time" },
    { id: "GMT Standard Time", name: "(UTC+00:00) Dublin, Edinburgh, Lisbon, London" },
    { id: "Pacific Standard Time", name: "(UTC-08:00) Pacific Time (US & Canada)" }], here: "UTC" }),
  // service.py save_notebook
  save_notebook: () => ({ saved: true, disk_stamp: "stamp-2" }),
  notebook_schedules: () => nbListing(),
  // cloud_service.py add_notebook_schedule: publish, add the schedule, then the listing.
  add_notebook_schedule: ({ schedule }) => {
    refuse("add_notebook_schedule");
    db.notebook.push(sched(`new${nextId++}`, true, configuration(schedule)));
    return nbListing();
  },
  // cloud_service.py delete_notebook_schedule: the listing without it.
  delete_notebook_schedule: ({ schedule_id }) => {
    refuse("delete_notebook_schedule");
    db.notebook = db.notebook.filter((s) => s.id !== schedule_id);
    return nbListing();
  },
  meta_schedules: ({ env }) => metaListing(env),
  // cloud_service.py meta_add_schedule: refused unless the environment runs in Fabric.
  meta_add_schedule: ({ env, schedule }) => {
    refuse("meta_add_schedule");
    if (ENVIRONMENTS[env]?.compute !== "fabric") throw new Error(`ValueError: Set ${env[0].toUpperCase()}${env.slice(1)} to run in Fabric first: scheduled runs need Fabric compute.`);
    db[env].push(sched(`new${nextId++}`, true, configuration(schedule)));
    return metaListing(env);
  },
  meta_delete_schedule: ({ env, schedule_id }) => {
    refuse("meta_delete_schedule");
    db[env] = db[env].filter((s) => s.id !== schedule_id);
    return metaListing(env);
  },
};

let baseline;  // the methods the app calls on its own

before(async () => {
  if (skip) return;
  app = await openApp({ mocks });
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
  for (const k of Object.keys(fail)) delete fail[k];
  await page().evaluate(() => { closeMenu(); closeDialog(); const t = document.getElementById("toast"); t.hidden = true; t.textContent = ""; go("home"); });
});

const writes = (from) => app.calls.slice(from).filter((c) => WRITES.includes(c.method)).map((c) => ({ method: c.method, params: c.params }));
const pause = (ms) => new Promise((r) => setTimeout(r, ms));
// Once the app has sent `method` (and its answer has had time to land), or a 5 s time-out.
async function sent(from, method) {
  for (const end = Date.now() + 5000; !app.calls.slice(from).some((c) => c.method === method); await pause(25)) {
    if (Date.now() > end) throw new Error(`${method} was never sent`);
  }
  await pause(300);
}
// The toast once it reads `pattern` (Add shows "Publishing..." first, then how it went).
const toastSays = async (pattern) => (await page().waitForFunction((src) => {
  const t = document.getElementById("toast");
  return !t.hidden && new RegExp(src).test(t.textContent) && t.textContent;
}, pattern.source)).jsonValue();
const rowOf = (d, text) => d.locator(".schedule-list tr", { hasText: text });

// The notebook open and signed in, then its Schedule dialog (app.js scheduleNotebook) on Fabric.
async function notebookDialog() {
  await page().evaluate((nb) => {
    state.user ||= "chris@contoso.com";
    loadIntoState(nb);
    go("notebook");
    scheduleNotebook();
  }, NOTEBOOK);
  const d = page().getByRole("dialog", { name: "Schedule nb_sales" });
  await rowOf(d, "Daily at 06:00 UTC").waitFor();
  return d;
}

// An environment's Schedule dialog (meta.js scheduleMetaRuns) for a project whose Test and Prod run in Fabric.
async function metaDialog(env) {
  await page().evaluate(([env, environments]) => {
    state.user ||= "chris@contoso.com";
    const was = state.meta;
    state.meta = { ...(was || {}), env, project: { ...(was?.project || {}), environments } };
    try { scheduleMetaRuns(env); } finally { state.meta = was; }
  }, [env, ENVIRONMENTS]);
  const d = page().getByRole("dialog", { name: `Schedule ${env[0].toUpperCase()}${env.slice(1)} runs` });
  await rowOf(d, db[env][0].text).waitFor();
  return d;
}

// Weekly on Tue and Thu at 07:30 and 19:15, Pacific time.
async function typeWeekly(d) {
  await d.getByRole("combobox").first().selectOption("weekly");
  await d.getByRole("textbox", { name: /Times of day/ }).fill("07:30, 19:15");
  for (const day of ["Mon", "Wed", "Fri"]) await d.getByRole("checkbox", { name: day }).uncheck();
  await d.getByRole("searchbox", { name: "Search time zones" }).fill("pacific");
  await page().waitForFunction(() => document.querySelector("#dialog-root select[aria-label='Time zone']")?.value === "Pacific Standard Time");
}
const WEEKLY = { kind: "weekly", times: ["07:30", "19:15"], every_minutes: 60, weekdays: ["Tuesday", "Thursday"], time_zone: "Pacific Standard Time" };
const trimmed = (s) => ({ ...s, times: s.times.map((t) => t.trim()) });
const addButton = () => page().locator(".dialog-actions").getByRole("button", { name: "Add schedule" });

test("notebook: Add schedule saves it, then sends its publish payload and exactly the timing typed", { skip }, async () => {
  const d = await notebookDialog();
  await typeWeekly(d);
  const from = app.calls.length;
  await addButton().click();
  assert.match(await toastSays(/^Scheduled\./), /Fabric runs the published notebook/);
  const sent = writes(from);
  assert.deepStrictEqual(sent.map((w) => w.method), ["save_notebook", "add_notebook_schedule"], "saved first, then scheduled");
  const { params } = sent[1];
  assert.deepStrictEqual(trimmed(params.schedule), WEEKLY);
  assert.strictEqual(params.name, "nb_sales");
  assert.deepStrictEqual(params.lakehouse_ref, LAKEHOUSE);
  assert.strictEqual(params.compute, "fabric");
  assert.strictEqual(params.workspace, null);
  assert.strictEqual(params.location_ref, null);
  assert.deepStrictEqual(params.cells.map((c) => [c.type, c.cell_id, c.source]),
    NOTEBOOK.cells.map((c) => [c.type, c.cell_id, c.source]));
  // The listing the engine answers with is shown, with the new one in it.
  await rowOf(d, "Tue, Thu at 07:30, 19:15 Pacific Standard Time").waitFor();
});

test("notebook: Add schedule every few minutes sends the minutes typed", { skip }, async () => {
  const d = await notebookDialog();
  await d.getByRole("combobox").first().selectOption("cron");
  await d.getByRole("spinbutton", { name: "Minutes between runs" }).fill("45");
  const from = app.calls.length;
  await addButton().click();
  await toastSays(/^Scheduled\./);
  const [add] = writes(from).filter((w) => w.method === "add_notebook_schedule");
  assert.deepStrictEqual([add.params.schedule.kind, add.params.schedule.every_minutes], ["cron", 45]);
  await rowOf(d, "Every 45 minutes").waitFor();
});

test("environment: Add schedule sends that environment and exactly the timing typed", { skip }, async () => {
  const d = await metaDialog("prod");
  await typeWeekly(d);
  const from = app.calls.length;
  await addButton().click();
  assert.match(await toastSays(/^Scheduled\./), /Fabric runs every entity/);
  const sent = writes(from);
  assert.strictEqual(sent.length, 1, JSON.stringify(sent));
  assert.strictEqual(sent[0].method, "meta_add_schedule");
  assert.deepStrictEqual(Object.keys(sent[0].params).sort(), ["env", "schedule"]);
  assert.strictEqual(sent[0].params.env, "prod");
  assert.deepStrictEqual(trimmed(sent[0].params.schedule), WEEKLY);
  await rowOf(d, "Tue, Thu at 07:30, 19:15 Pacific Standard Time").waitFor();
});

// Remove on a listed schedule: the confirm it opens.
async function askRemove(d, text, what) {
  await rowOf(d, text).getByRole("button", { name: "Remove" }).click();
  const c = page().getByRole("dialog", { name: `Remove the schedule for ${what}?` });
  await c.waitFor();
  return c;
}

const REMOVES = [
  { name: "notebook", open: () => notebookDialog(), what: "nb_sales", text: "Every 30 minutes", keep: "Daily at 06:00 UTC",
    sent: { method: "delete_notebook_schedule", params: { name: "nb_sales", lakehouse_ref: LAKEHOUSE, workspace: null, schedule_id: "s2" } } },
  { name: "environment", open: () => metaDialog("prod"), what: "Prod runs", text: "Daily at 14:00 UTC", keep: "Daily at 02:00 UTC",
    sent: { method: "meta_delete_schedule", params: { env: "prod", schedule_id: "m3" } } },
];
for (const r of REMOVES) {
  test(`${r.name}: Remove asks first in a danger confirm, and Cancel sends nothing`, { skip }, async () => {
    const d = await r.open();
    const from = app.calls.length;
    const c = await askRemove(d, r.text, r.what);
    assert.deepStrictEqual(writes(from), [], "nothing is sent before the confirm");
    assert.match(await c.innerText(), new RegExp(`${r.text}\\. Fabric deletes the schedule and stops starting runs from it`));
    assert.match(await c.getByRole("button", { name: "Remove" }).getAttribute("class"), /danger-solid/);
    await c.getByRole("button", { name: "Cancel" }).click();
    await c.waitFor({ state: "detached" });
    await pause(300);
    assert.deepStrictEqual(writes(from), []);
    // The confirm takes the Schedule dialog's place (see the todo test below), so it is opened again.
    assert.strictEqual(await rowOf(await r.open(), r.text).count(), 1, "the schedule is still listed");
  });

  test(`${r.name}: confirming Remove sends that schedule's id, and it leaves the list`, { skip }, async () => {
    const d = await r.open();
    const from = app.calls.length;
    const c = await askRemove(d, r.text, r.what);
    await c.getByRole("button", { name: "Remove" }).click();
    await c.waitFor({ state: "detached" });
    await sent(from, r.sent.method);
    assert.deepStrictEqual(writes(from), [r.sent]);
    const again = await r.open();
    assert.strictEqual(await rowOf(again, r.text).count(), 0, "it leaves the list");
    assert.strictEqual(await rowOf(again, r.keep).count(), 1, "the other schedule stays");
  });

  // openScheduleDialog (schedules.js) asks with confirmDialog, which replaces the Schedule dialog, so
  // after Cancel or Remove the person is back on the page and the list it redraws is never shown.
  // schedules.js belongs to an open PR (#1467), so this is left for a follow-up.
  test(`${r.name}: after the Remove confirm, the Schedule dialog is still open`, { skip,
    todo: "schedules.js: the Remove confirm replaces the Schedule dialog" }, async () => {
    const d = await r.open();
    const c = await askRemove(d, r.text, r.what);
    await c.getByRole("button", { name: "Cancel" }).click();
    await c.waitFor({ state: "detached" });
    await page().getByRole("dialog", { name: /^Schedule / }).waitFor({ timeout: 1000 });
  });
}

// Each write refused by the engine: the toast is the label and a sentence, and the list stays as it was.
const REFUSALS = [
  { name: "notebook Add", method: "add_notebook_schedule", open: () => notebookDialog(), label: "Schedule not added",
    act: (d) => d.getByRole("textbox", { name: /Times of day/ }).fill("25:00").then(() => addButton().click()),
    said: "Schedule not added: Times are HH:MM in 24-hour form, like 06:00 (got '25:00')" },
  { name: "notebook Remove", method: "delete_notebook_schedule", open: () => notebookDialog(), label: "Not removed", error: DENIED,
    act: async (d) => (await askRemove(d, "Every 30 minutes", "nb_sales")).getByRole("button", { name: "Remove" }).click() },
  { name: "environment Add", method: "meta_add_schedule", open: () => metaDialog("test"), label: "Schedule not added",
    error: "ValueError: The Test workspace has no LH_Meta lakehouse yet. Run the environment once, then add the schedule.",
    act: () => addButton().click(), said: "Schedule not added: The Test workspace has no LH_Meta lakehouse yet. Run the environment once, then add the schedule." },
  { name: "environment Remove", method: "meta_delete_schedule", open: () => metaDialog("test"), label: "Not removed",
    error: "LookupError: There is no runner notebook in the Test workspace, so it has no schedules there. Nothing was removed.",  // cloud_service.py meta_delete_schedule
    act: async (d) => (await askRemove(d, "Mon, Thu at 05:30 UTC", "Test runs")).getByRole("button", { name: "Remove" }).click() },
];
for (const r of REFUSALS) {
  test(`${r.name}: an engine refusal reads as a sentence and changes nothing`, { skip }, async () => {
    if (r.error) fail[r.method] = r.error;
    const d = await r.open();
    const before = JSON.stringify(db);
    const listed = await d.locator(".schedule-list tr").count();
    await r.act(d);
    const said = (await toastSays(new RegExp(`^${r.label}: `))).split("\n")[0];
    if (r.said) assert.strictEqual(said, r.said);
    assert.ok(said.length > r.label.length + 20, said);
    assert.match(said, /[.)]$/, `a sentence: ${said}`);
    assert.doesNotMatch(said, /\[object Object\]|undefined|Error:|HTTPError|https?:\/\//);
    assert.strictEqual(JSON.stringify(db), before);
    const open = await page().getByRole("dialog", { name: /^Schedule / }).count();  // a refused Remove left from its confirm
    const shown = open ? page().getByRole("dialog") : await r.open();
    assert.strictEqual(await shown.locator(".schedule-list tr").count(), listed, "the list stays as it was");
  });
}

test("opening the notebook's and an environment's Schedule dialogs sends no write", { skip }, async () => {
  const from = app.calls.length;
  let d = await notebookDialog();
  await d.getByRole("combobox").first().selectOption("weekly");
  await d.getByRole("searchbox", { name: "Search time zones" }).fill("london");
  await page().locator(".dialog-actions").getByRole("button", { name: "Close" }).click();
  d = await metaDialog("test");
  await d.getByRole("combobox").first().selectOption("cron");
  await page().locator(".dialog-actions").getByRole("button", { name: "Close" }).click();
  await pause(500);
  const made = app.calls.slice(from).map((c) => c.method);
  for (const m of ["notebook_schedules", "meta_schedules"]) assert.ok(made.includes(m), `${m} was read`);
  assert.deepStrictEqual(made.filter((m) => WRITES.includes(m)), []);
  assert.deepStrictEqual([...new Set(made.filter((m) => !READS.includes(m) && !baseline.has(m)))], []);
});

test("axe finds nothing serious on both Schedule dialogs and the Remove confirm", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  const check = async (where) => assert.deepStrictEqual(await axeFindings(page(), "#dialog-root .dialog"), [], where);
  const d = await notebookDialog();
  await check("notebook dialog");
  await askRemove(d, "Every 30 minutes", "nb_sales");
  await check("remove confirm");
  await page().evaluate(() => closeDialog());
  await metaDialog("prod");
  await check("environment dialog");
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
