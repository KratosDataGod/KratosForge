// #1296: Monitor > KratosForge > Operations > Agents, Queue and Run as, the three read-only panels
// (see harness.cjs for how to run it). Each shows every state the engine can send: Agents shows
// Running, Stopped reporting (with its last report as it was) and Stopped, and an agent opens on its
// own page and Back returns to the list; Queue shows Running, Queued, Missed and Next with who owns
// each row; Run as shows OK, Expires in N days, Expired, Last token request failed and Signed out.
// An empty answer and an engine error each show a plain message. No engine call but the three list
// methods (and the app's own startup calls) is made while they are open, and axe finds nothing
// serious on each panel.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const LISTS = ["agents_list", "queue_list", "run_as_list"];

// What the engine answers (incident_service.py, workloads/agents.py, queue.py and run_as.py), for
// four agents in Prod: etl-01 running, etl-02 stopped reporting two hours ago, etl-03 shut down
// cleanly, etl-04 running and sharing a schedule with etl-01; one schedule on this PC; one Fabric
// schedule; and nobody signed in on this PC. Times are seconds from now.
function answers(now = Date.now()) {
  const at = (s) => new Date(now + s * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
  const read = { source: "Agents", state: "live", label: "Live", collected_at: at(0).replace("Z", "+00:00"), age_seconds: 0, detail: "" };
  const envs = [{ env: "dev", count: 0, observation: read }, { env: "test", count: 0, observation: read }, { env: "prod", count: 4, observation: read }];
  const incidents = { enabled: true, seconds: 300, quiet_hours: null, quiet_except: [], escalate_after_minutes: 60, escalate_to: null, routes: [] };
  const pipeline = { enabled: true, entities: [], allow_backfill: false, require_run: false };
  const hourly = { id: "hourly", next: at(1800), entities: ["silver_orders"], when: { kind: "cron", every_minutes: 60 }, shared: false };
  const shared = { id: "orders-shared", next: at(5400), entities: ["silver_orders"], when: { kind: "cron", every_minutes: 60 }, shared: true };
  const agent = (name, state, beat, stale, prod, extra = {}) => ({
    agent: name, envs: { prod: { state, heartbeat_at: at(beat), counts: { queued: 0, running: 0 }, running: [], schedules: [], recent: [],
      pipeline, incidents, concurrency: 1, ...prod } },
    host: `srv-${name}`, version: "0.0.0", started_at: at(-86400), heartbeat_at: at(beat), stale_after: at(stale), status_seconds: 60,
    environments: ["prod"], owner: "Sales data team", identity: "service_principal", state, version_compare: "same", ...extra });
  const agents = {
    envs,
    agents: [
      agent("etl-01", "live", -20, 160, { counts: { queued: 2, running: 1 },
        running: [{ job_id: "a1b2c3d4e5", source: "schedule:hourly", attempts: 1, entities: ["silver_orders"], started_at: at(-300) }],
        schedules: [hourly, shared],
        recent: [{ job_id: "f1", state: "succeeded", source: "schedule:hourly", finished_at: at(-3600), error: null }] }),
      agent("etl-02", "offline", -7200, -7000, { counts: { queued: 1, running: 1 },
        running: [{ job_id: "b2", source: "pipeline", attempts: 2, entities: ["gold_sales"], started_at: at(-7500) }],
        schedules: [{ id: "nightly", next: at(-3600), entities: ["gold_sales"], when: { kind: "daily", times: ["02:00"] }, shared: false }] }),
      agent("etl-03", "stopped", -600, -420, {}, { identity: "managed_identity" }),
      agent("etl-04", "live", -30, 150, { schedules: [shared] }),
    ],
    app_version: "0.0.0",
    overlaps: [{ env: "prod", entities: ["silver_orders"], schedules: [
      { env: "prod", place: "agent", agent: "etl-01", state: "live", schedule: "hourly", text: "Every 60 minutes", next: at(1800), shared: false, entities: ["silver_orders"] },
      { env: "prod", place: "agent", agent: "etl-04", state: "live", schedule: "orders-shared", text: "Every 60 minutes", next: at(5400), shared: true, entities: ["silver_orders"] }] }],
    unknown: [],
    pc_schedules_read: true,
  };
  const fabric = { state: "read", at: at(-900) };
  const where = (name) => ({ kind: "agent", name });
  const row = (state, what, w, owner, when, more = {}) => ({ state, what, env: "prod", where: w, owner, at: when, text: "", started_by: "", note: "", ...more });
  const queue = {
    rows: [
      row("running", "silver_orders", where("etl-01"), "etl-01's queue", at(-300), { started_by: "schedule hourly", as_of: at(-20) }),
      row("unknown", "gold_sales", where("etl-02"), "etl-02's queue", at(-7500), { started_by: "pipeline",
        note: "From its last report. Picked up again when etl-02 is back.", as_of: at(-7200) }),
      row("unknown", "1 job", where("etl-02"), "etl-02's queue", null, { note: "From its last report", as_of: at(-7200) }),
      row("queued", "2 jobs", where("etl-01"), "etl-01's queue", null, { note: "The record counts queued jobs; it doesn't list them", as_of: at(-20) }),
      row("missed", "gold_sales", where("etl-02"), "etl-02's schedule nightly", at(-3600), { note: "Runs once when etl-02 is back", as_of: at(-7200) }),
      row("next", "silver_orders", where("etl-01"), "etl-01's schedule hourly", at(1800), { as_of: at(-20) }),
      row("next", "silver_orders", { kind: "agents", name: "etl-01, etl-04", names: ["etl-01", "etl-04"] }, "shared schedule orders-shared", at(5400),
        { note: "Shared by etl-01, etl-04: runs once, on whichever claims it", as_of: at(-20) }),
      row("next", "bronze_customers", { kind: "pc", name: "This PC" }, "Windows Task Scheduler", at(7200),
        { env: "dev", text: "Daily at 06:00", note: "Runs only while you are signed in to Windows" }),
      row("next", "Load sales", { kind: "fabric", name: "Sales Prod" }, "Fabric scheduler", null, { text: "Every day at 05:00", note: "Pipeline" }),
    ],
    sources: { agents: envs, pc: { ...read, source: "This PC" }, fabric_schedules: fabric },
    hours: 24,
  };
  const signedOut = { kind: "user", state: "signed_out", expires_at: null, last_token_at: null, error: "" };
  const nobody = { kind: "signed_out", name: "" };
  const sp = { kind: "service_principal", name: "Service principal" };
  const ra = (name, runsAs, credential, asOf, live) => ({ place: { kind: "agent", name }, env: "prod", runs_as: runsAs, credential,
    boundary: `Data on ${name}'s server (srv-${name}); tables go to Prod OneLake`, detail: "As set in its agent.toml; the app doesn't see its ids",
    as_of: at(asOf), live });
  const runAs = {
    rows: [
      { place: { kind: "pc", name: "This PC" }, env: null, runs_as: nobody, credential: signedOut,
        boundary: "Data is read and written on this PC; tables go to OneLake over HTTPS",
        detail: "Runs and schedules you start here. Windows schedules run only while you are signed in to Windows.", as_of: null, live: true },
      { place: { kind: "fabric_jobs", name: "Fabric jobs from this app" }, env: null, runs_as: nobody, credential: signedOut,
        boundary: "Runs on the workspace's capacity; this app sends the job request and data stays in Fabric",
        detail: "Run in Fabric and side-by-side runs", as_of: null, live: true, allowed: true },
      ra("etl-01", sp, { kind: "certificate", expires_at: at(10 * 86400 + 3600), last_token_at: at(-60), error: "", state: "expiring" }, -20, true),
      ra("etl-02", sp, { kind: "certificate", expires_at: at(-86400), last_token_at: at(-90000), error: "", state: "expired" }, -7200, false),
      ra("etl-03", { kind: "managed_identity", name: "Managed identity" }, { kind: "managed_identity", expires_at: null, last_token_at: at(-700),
        error: "AADSTS700016: app not found", error_at: at(-650), state: "failed" }, -600, false),
      ra("etl-04", sp, { kind: "certificate", expires_at: at(200 * 86400), last_token_at: at(-40), error: "", state: "ok" }, -30, true),
      { place: { kind: "fabric_schedule", name: "Sales Prod · Load sales" }, env: "prod", runs_as: { kind: "schedule_owner", name: "Dana Ruiz" },
        credential: { kind: "fabric", state: "not_visible", expires_at: null, last_token_at: null, error: "" },
        boundary: "Runs on the capacity of workspace Sales Prod; data stays in Fabric", detail: "Every day at 05:00", as_of: null, live: true },
    ],
    attention: [
      { place: { kind: "agent", name: "etl-02" }, state: "expired", expires_at: at(-86400), live: false },
      { place: { kind: "agent", name: "etl-03" }, state: "failed", expires_at: null, live: false },
      { place: { kind: "agent", name: "etl-01" }, state: "expiring", expires_at: at(10 * 86400 + 3600), live: true },
    ],
    expiring_days: 30,
    sources: { agents: envs, fabric_schedules: fabric },
  };
  // Nothing to show: no agent records, nothing queued. Run as always lists This PC; this is the bare answer.
  const empty = {
    agents_list: { envs, agents: [], app_version: "0.0.0", overlaps: [], unknown: [], pc_schedules_read: true },
    queue_list: { rows: [], sources: queue.sources, hours: 24 },
    run_as_list: { rows: [], attention: [], expiring_days: 30, sources: runAs.sources },
  };
  return { full: { agents_list: agents, queue_list: queue, run_as_list: runAs }, empty };
}

// "full", "empty" or "error" for each list method.
const mode = { agents_list: "full", queue_list: "full", run_as_list: "full" };
const reply = (method) => () => {
  if (mode[method] === "error") throw new Error("The Python sidecar stopped (exit code 1)");
  return answers()[mode[method]][method];
};

let baseline;  // the methods the app calls on its own, before any panel opens

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { readiness_check: () => [], ...Object.fromEntries(LISTS.map((m) => [m, reply(m)])) } });
  // The app's startup reads, and its first approval polls 5 s in, which repeat while it is open.
  await startupReads(app);
  for (const end = Date.now() + 15000; !["integrations_pending", "editor_pending"].every((m) => app.calls.some((c) => c.method === m));
    await new Promise((r) => setTimeout(r, 50))) {
    if (Date.now() > end) throw new Error("the app's startup polls never came");
  }
  baseline = new Set(app.calls.map((c) => c.method));
});
after(async () => { if (app) await app.close(); });

const open = (panel) => page().evaluate((p) => { monitorPage.panel = p; go("home"); go("monitor"); }, panel);

// The panel's rows once it has read its answer: each row's cells as text. Count and read in one step,
// since the page can draw again while it loads.
async function rows(panel, n) {
  await open(panel);
  const handle = await page().waitForFunction((n) => {
    const trs = [...document.querySelectorAll(".page.monitor .inc-panel tbody tr:not(.qu-group)")];
    return trs.length === n && trs.map((tr) => [...tr.children].map((td) => td.textContent.trim()));
  }, n);
  return handle.jsonValue();
}

// The one plain message a panel shows in place of its list, once it is no longer reading.
async function message(panel) {
  await open(panel);
  const handle = await page().waitForFunction(() => {
    const box = document.querySelector(".page.monitor .inc-panel");
    const p = box?.querySelector(".monitor-empty");
    return !!p && !/^Reading/.test(p.textContent) && { text: p.textContent.trim(), rows: box.querySelectorAll("tbody tr").length };
  });
  return handle.jsonValue();
}

const by = (list, name) => list.find((cells) => cells[0].startsWith(name));

test("Agents shows Running, Stopped reporting with its last report, and Stopped", { skip }, async () => {
  const list = await rows("agents", 4);
  assert.deepStrictEqual(list.map((c) => c[0].split("on ")[0]), ["etl-01", "etl-02", "etl-03", "etl-04"]);
  assert.match(by(list, "etl-01")[1], /^Runningheartbeat just now$/);
  assert.match(by(list, "etl-01")[3], /^1 running, 2 queuedsilver_orders since 5 min ago \(Prod\)$/);
  // Stopped reporting: its last report as it was, not as current.
  const gone = by(list, "etl-02");
  assert.match(gone[1], /^Stopped reportinglast seen 2 h ago, due again 2 h ago$/);
  assert.match(gone[3], /^Unknownits last report said 1 running, 1 queued$/);
  assert.match(gone[4], /^Won't run while it is downnightly due 1 h ago$/);
  assert.match(gone[6], /Not checking while it is down/);
  const stopped = by(list, "etl-03");
  assert.match(stopped[1], /^Stoppedshut down cleanly 10 min ago$/);
  assert.match(stopped[3], /^Nothing$/);
  assert.match(stopped[4], /^None while stopped$/);
  assert.match(by(list, "etl-04")[1], /^Running/);
  const text = await page().textContent(".page.monitor .inc-panel");
  assert.match(text, /Prod 4 agents, read just now/);
  assert.match(text, /Scheduled in two places/);
});

test("an agent opens on its own page and Back returns to the list", { skip }, async () => {
  await rows("agents", 4);
  await page().click(".page.monitor .inc-panel tbody tr:nth-child(2)");
  const handle = await page().waitForFunction(() => state.view === "agent" && document.querySelector(".page h1")?.textContent.includes("etl-02")
    && document.querySelector(".page")?.textContent);
  const text = await handle.jsonValue();
  assert.match(text, /etl-02 ?Stopped reporting/);
  assert.match(text, /Everything below is its last report, from 2 h ago\./);
  assert.match(text, /Doing at its last report/);
  assert.match(text, /This app can't restart an agent/);
  await page().click("#nav-back");
  const back = await page().waitForFunction(() => state.view === "monitor" && monitorPage.panel === "agents"
    && document.querySelectorAll(".page.monitor .inc-panel tbody tr").length);
  assert.strictEqual(await back.jsonValue(), 4);
  // The Agents crumb on the agent page goes back too.
  await page().click(".page.monitor .inc-panel tbody tr:nth-child(1)");
  await page().waitForFunction(() => state.view === "agent" && document.querySelector(".page h1")?.textContent.includes("etl-01"));
  await page().locator("button.crumb", { hasText: "Agents" }).click();
  await page().waitForFunction(() => state.view === "monitor" && monitorPage.panel === "agents"
    && document.querySelectorAll(".page.monitor .inc-panel tbody tr").length === 4);
});

test("Queue shows Running, Queued, Missed and Next, with who owns each row", { skip }, async () => {
  const list = await rows("queue", 9);
  // State, What, Environment, Where it runs, Owned by
  assert.deepStrictEqual(list.map((c) => c.slice(0, 5)), [
    ["Running", "silver_orders", "Prod", "Agent etl-01", "etl-01's queue"],
    ["Unknown", "gold_sales", "Prod", "Agent etl-02", "etl-02's queue"],
    ["Unknown", "1 job", "Prod", "Agent etl-02", "etl-02's queue"],
    ["Queued", "2 jobs", "Prod", "Agent etl-01", "etl-01's queue"],
    ["Missed", "gold_sales", "Prod", "Agent etl-02", "etl-02's schedule nightly"],
    ["Next", "silver_orders", "Prod", "Agent etl-01", "etl-01's schedule hourly"],
    ["Next", "silver_orders", "Prod", "Agents etl-01, etl-04", "shared schedule orders-shared"],
    ["Next", "bronze_customers", "Dev", "This PC", "Windows Task Scheduler"],
    ["Next", "Load sales", "Prod", "Fabric · Sales Prod", "Fabric scheduler"],
  ]);
  assert.strictEqual(list[0][5], "started 5 min ago");
  assert.strictEqual(list[4][5], "was due 1 h ago");
  assert.strictEqual(list[5][5], "in 30 min");
  assert.strictEqual(list[8][5], "Every day at 05:00");
  const text = await page().textContent(".page.monitor .inc-panel");
  assert.match(text, /This PC read just now/);
  assert.match(text, /Fabric schedules read 15 min ago/);
  const chips = await page().locator(".page.monitor .qu-chips button").allTextContents();
  assert.deepStrictEqual(chips, ["All · 9", "Running · 1", "Queued · 1", "Next 24 h · 5"]);
});

test("Run as shows OK, Expires in N days, Expired, Last token request failed and Signed out", { skip }, async () => {
  const list = await rows("runas", 7);
  // Each place's name and its state's pill
  const place = await page().locator(".page.monitor .inc-panel tbody tr td:nth-child(1) > :first-child").allTextContents();
  const pill = await page().locator(".page.monitor .inc-panel tbody tr td:nth-child(4) .pill-s").allTextContents();
  assert.deepStrictEqual(place.map((name, i) => [name, pill[i]]), [
    ["This PC", "Signed out"],
    ["Fabric jobs from this app", "Signed out"],
    ["Agent etl-01", "Expires in 10 days"],
    ["Agent etl-02", "Expired"],
    ["Agent etl-03", "Last token request failed"],
    ["Agent etl-04", "OK"],
    ["Sales Prod · Load sales", "Not visible"],
  ]);
  assert.match(list[2][3], /Ends /);
  assert.match(list[3][3], /Ended /);
  assert.match(list[4][3], /11 min ago, at its last report: AADSTS700016: app not found/);
  assert.strictEqual(list[1][1], "Nobody signed in");
  assert.match(list[6][1], /^Its schedule's ownerDana Ruiz · Fabric runs it as them$/);
  const note = await page().textContent(".page.monitor .ag-overlap");
  assert.match(note, /3 to look at\. etl-02's certificate has expired\. etl-03's last token request failed, at its last report\. etl-01's certificate expires in 10 days\./);
});

for (const [panel, method, words] of [
  ["agents", "agents_list", /^No agents serve these environments\./],
  ["queue", "queue_list", /^Nothing is running, queued or scheduled in the next day\.$/],
  ["runas", "run_as_list", /^No places to show\.$/],  // the engine always lists This PC, so this answer only comes from a bare reply
]) {
  test(`${panel}: an empty answer shows a plain message`, { skip }, async () => {
    mode[method] = "empty";
    try {
      const out = await message(panel);
      assert.match(out.text, words);
      assert.strictEqual(out.rows, 0);
    } finally { mode[method] = "full"; }
  });

  test(`${panel}: an engine error shows a plain message`, { skip }, async () => {
    mode[method] = "error";
    try {
      const out = await message(panel);
      assert.match(out.text, /^Couldn't read .+: The local engine stopped while doing this\./);
      assert.strictEqual(out.rows, 0);
    } finally { mode[method] = "full"; }
  });
}

test("only the three list methods reach the engine while these panels are open", { skip }, async () => {
  const from = app.calls.length;
  await rows("agents", 4);
  await page().click(".page.monitor .inc-panel tbody tr:nth-child(1)");
  await page().waitForFunction(() => state.view === "agent" && document.querySelector(".page h1")?.textContent.includes("etl-01"));
  await page().click("#nav-back");
  await rows("agents", 4);
  await rows("queue", 9);
  await page().locator(".page.monitor .qu-chips button", { hasText: "Running" }).click();
  await page().locator(".page.monitor .qu-chips button", { hasText: "All" }).click();
  await rows("runas", 7);
  await page().locator(".page.monitor .inc-panel button", { hasText: "Refresh" }).click();
  await rows("runas", 7);
  const made = app.calls.slice(from).map((c) => c.method);
  for (const m of LISTS) assert.ok(made.includes(m), `${m} was read`);
  assert.deepStrictEqual([...new Set(made.filter((m) => !LISTS.includes(m) && !baseline.has(m)))], []);
});

test("axe finds nothing serious on Agents, Queue and Run as", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  for (const [panel, n] of [["agents", 4], ["queue", 9], ["runas", 7]]) {
    await rows(panel, n);
    const found = await axeFindings(page(), ".page.monitor");
    assert.deepStrictEqual(found, [], panel);
  }
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
