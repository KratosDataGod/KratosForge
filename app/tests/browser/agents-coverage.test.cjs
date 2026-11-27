// Agents (agents.js), the paths operations-panels and monitor-how leave out: an environment whose
// records can't be read and one with none, schedules in two places with this PC and an agent that
// stopped reporting, older agents that don't say what they schedule, this PC's schedules unread,
// Review schedules, a search that matches nothing, the guide's links, and an agent's own page for an
// older version, a clean shutdown and one that stopped reporting (shared and missed schedules, failed
// jobs, incident checks with quiet hours), one that has no record, and records that can't be read.
// agents_list is mocked with records shaped like workloads/agents.py's; what the person sees is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const at = (s) => new Date(Date.now() + s * 1000).toISOString();

const env = (more = {}) => ({ counts: { queued: 0, running: 0 }, running: [], schedules: [], recent: [], ...more });
const AGENTS = () => [
  { agent: "etl-05", state: "live", host: "etl-05.contoso.com", owner: "", identity: "service_principal", version: "0.9.0", version_compare: "older",
    heartbeat_at: at(-20), stale_after: at(140), status_seconds: 60,
    envs: { prod: env({ pipeline: { enabled: true, entities: [], allow_backfill: true, require_run: true }, concurrency: 2,
      schedules: [{ id: "hourly", next: at(1800) }, { id: "late-night", entities: ["gold.sales"], next: at(5 * 3600) }, { id: "weekly", entities: [], next: at(3 * 86400) }],
      recent: [{ job_id: "aaaaaaaa1111", state: "failed", source: "schedule hourly", error: "Path does not exist\nat line 2", finished_at: at(-7200) },
        { job_id: "bbbbbbbb2222", state: "failed", source: "pipeline", error: "Out of memory", finished_at: at(-600) },
        { job_id: "cccccccc3333", state: "done", source: "pipeline", finished_at: at(-300) }] }) } },
  { agent: "etl-06", state: "stopped", host: "", version: "1.0.0", version_compare: "same", heartbeat_at: at(-900), stale_after: at(-780),
    envs: { prod: env({ incidents: { enabled: false } }) } },
  { agent: "etl-07", state: "offline", host: "etl-07.contoso.com", owner: "bo@contoso.com", version: "1.0.0", version_compare: "same",
    heartbeat_at: at(-7200), stale_after: at(-7000),
    envs: { test: env({ counts: { queued: 2, running: 1 }, incidents: { enabled: true, seconds: 600, quiet_hours: "22:00-06:00" },
      running: [{ job_id: "dddddddd4444", entities: [], source: "schedule nightly", attempts: 2, started_at: at(-7300) }],
      schedules: [{ id: "orders-shared", shared: true, entities: ["silver.orders"], next: at(-3600) }, { id: "nightly", entities: ["gold.kpis"], next: at(-1800) },
        { id: "monthly", entities: ["gold.close"], next: at(20 * 86400) }] }) } },
];
const LIST = () => ({
  app_version: "1.0.0",
  envs: [{ env: "prod", count: null, observation: { label: "Can't read", detail: "403 from OneLake" } }, { env: "test", count: 0 }],
  agents: AGENTS(),
  overlaps: [{ env: "prod", entities: ["silver.orders", "silver.customers"], schedules: [
    { place: "pc", text: "Daily at 06:00" }, { place: "agent", agent: "etl-07", state: "offline", schedule: "nightly", text: "" }] }],
  unknown: [{ agent: "etl-old" }, { agent: "etl-older" }, { agent: "etl-old" }],
  pc_schedules_read: false,
});

const db = {};
let app;
const page = () => app.page;
const box = () => page().locator("#t-box");

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { agents_list: () => { if (db.list instanceof Error) throw db.list; return structuredClone(db.list); } } });
  await page().evaluate(() => {
    window.__docs = [];
    // The guide's links open docs through the shell; that call is kept here and every other passes on.
    const core = window.__TAURI__.core;
    const pass = core.invoke.bind(core);
    core.invoke = (cmd, args) => (cmd === "open_doc" ? (window.__docs.push([cmd, args]), Promise.resolve(null)) : pass(cmd, args));
  });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  db.list = LIST();
  await page().evaluate(() => { state.query = ""; window.__docs = []; go("home"); document.getElementById("t-box")?.replaceChildren(); });
  app.errors.length = 0;
});

const mount = () => page().evaluate(() => {
  let b = document.getElementById("t-box");
  if (!b) { b = document.createElement("div"); b.id = "t-box"; document.body.append(b); }
  b.replaceChildren(agentsPanel());
});
const rowOf = (name) => box().locator("tbody tr", { has: page().locator(`b:text-is("${name}")`) });

test("the list names what it couldn't read, schedules in two places, and each agent's state", { skip }, async () => {
  await mount();
  await box().locator(".inc-list").waitFor();
  const sources = await box().locator(".wl-sources > span").allInnerTexts();
  assert.deepStrictEqual(sources.map((s) => s.replace(/\s+/g, " ").trim()), ["Can't read Prod: 403 from OneLake", "Test no agents"]);
  const overlap = box().locator(".ag-overlap");
  assert.match(await overlap.innerText(), /silver\.orders, silver\.customers in Prod runs on a schedule on this PC \(daily at 06:00\) and on agent etl-07, which stopped reporting, \(nightly\)\. Both write the same tables\./);
  assert.match(await overlap.innerText(), /Can't tell what etl-old, etl-older schedule: they are an older version that doesn't say which tables their schedules run\./);
  assert.match(await overlap.innerText(), /This PC's schedules couldn't be read, so they weren't compared\./);

  const live = await rowOf("etl-05").innerText();
  assert.match(live, /Running/);
  assert.match(live, /in 30 min\s+hourly \(Prod\)/);
  assert.match(live, /2 failed, 1 done\s+Out of memory · /, "the newest failure's reason");
  assert.match(live, /Doesn't say \(an older version\)/);
  assert.match(live, /0\.9\.0\s+older than this app/);
  assert.match(await rowOf("etl-05").locator(".inc-escalated").innerText(), /older than this app/);
  const stopped = await rowOf("etl-06").innerText();
  assert.match(stopped, /Stopped\s+shut down cleanly/);
  assert.match(stopped, /None while stopped/);
  assert.match(stopped, /None yet/);
  assert.match(stopped, /\bNo\b/);
  const offline = await rowOf("etl-07").innerText();
  assert.match(offline, /Stopped reporting\s+last seen .+, due again /);
  assert.match(offline, /Unknown\s+its last report said 1 running, 2 queued/);
  assert.match(offline, /Won't run while it is down\s+orders-shared due /);
  assert.match(offline, /Test, every 10 min\s+Not checking while it is down/);
  assert.deepStrictEqual(app.errors, []);
});

test("Review schedules opens Monitor's schedules; the guide's links open agent.md; a search with no match says so", { skip }, async () => {
  await mount();
  await box().locator(".inc-list").waitFor();
  await page().evaluate(() => { window.__go = window.go; window.__went = []; window.go = (v) => window.__went.push([v, monitorPage.panel]); });
  await box().getByRole("button", { name: "Review schedules" }).click();
  assert.deepStrictEqual(await page().evaluate(() => { window.go = window.__go; return window.__went; }), [["monitor", "schedules"]]);
  // How this works opens as a drawer by default; its link is clicked where it is.
  await box().locator("a.learn-more", { hasText: "Status records" }).dispatchEvent("click");
  await page().waitForFunction(() => window.__docs.length === 1);
  assert.deepStrictEqual(await page().evaluate(() => window.__docs), [["open_doc", { page: "agent.md#status-records" }]]);

  await page().evaluate(() => { state.query = "no-such-agent"; });
  await box().getByRole("button", { name: "Refresh" }).click();
  await box().getByText("No agents match your search.").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("an older agent's page says how to update it and lists every job, newest first", { skip }, async () => {
  await page().evaluate(() => openAgent("etl-05"));
  await page().locator("h1", { hasText: "etl-05" }).waitFor();
  const sub = await page().locator(".sub").innerText();
  assert.match(sub, /Server etl-05\.contoso\.com · No owner set in agent\.toml · Signs in as a service principal · Version 0\.9\.0 \(this app is 1\.0\.0\)/);
  const runs = page().locator(".card", { hasText: "What it runs in Prod" });
  assert.match(await runs.innerText(), /Pipeline jobs for every entity · 2 at a time · backfill allowed · only runs it can check in Fabric/);
  const sched = await runs.locator("tbody tr").allInnerTexts();
  assert.match(sched[0], /^hourly\s+Doesn't say \(an older version\)\s+in 30 min/);
  assert.match(sched[1], /^late-night\s+gold\.sales\s+at /);
  assert.match(sched[2], /^weekly\s+Every table\s+\S/);
  const jobs = await page().locator(".card", { hasText: "Last jobs" }).locator("tbody tr").allInnerTexts();
  assert.deepStrictEqual(jobs.map((j) => j.split("\t")[2]), ["cccccccc", "bbbbbbbb", "aaaaaaaa"]);
  assert.match(jobs[2], /failed\s+Path does not exist$/);
  assert.match(await page().locator(".card", { hasText: "What you can do" }).innerText(), /It runs 0\.9\.0; this app is 1\.0\.0\. Update it the way it was installed\./);
  assert.deepStrictEqual(app.errors, []);
});

test("an agent that stopped reporting: its last report, shared and missed schedules, and what to do", { skip }, async () => {
  await page().evaluate(() => openAgent("etl-07"));
  await page().locator("h1", { hasText: "etl-07" }).waitFor();
  assert.match(await page().locator(".wl-head").innerText(), /Everything below is its last report, from /);
  const runs = page().locator(".card", { hasText: "What it runs in Test" });
  assert.match(await runs.innerText(), /Doesn't take pipeline jobs/);
  const sched = await runs.locator("tbody tr").allInnerTexts();
  assert.match(sched[0], /^orders-shared\s*Shared\s+silver\.orders\s+.+\s+Runs on the other agents that share it, if any are up$/);
  assert.match(sched[1], /Missed; runs once when the agent is back$/);
  assert.match(sched[2], /Won't run while it is down$/);
  const now = await page().locator(".card", { hasText: "Doing at its last report" }).innerText();
  assert.match(now, /1 running, 2 queued/);
  assert.match(now, /Job dddddddd · schedule nightly · attempt 2 · started /);
  assert.match(now, /Unknown since its last report\./);
  assert.match(now, /Yes, every 10 min, quiet 22:00-06:00/);
  assert.match(await page().locator(".card", { hasText: "What you can do" }).innerText(), /On etl-07\.contoso\.com, check the service, then run fabric-desktop agent status/);
  assert.deepStrictEqual(app.errors, []);
});

test("a cleanly stopped agent says nothing was lost; an unknown one and unreadable records say so", { skip }, async () => {
  await page().evaluate(() => openAgent("etl-06"));
  await page().locator("h1", { hasText: "etl-06" }).waitFor();
  assert.match(await page().locator(".card", { hasText: "What it runs in Prod" }).innerText(), /No schedules here\./);
  assert.match(await page().locator(".card", { hasText: "Doing at its last report" }).innerText(), /Checks incidents\s+No$/);
  assert.match(await page().locator(".card", { hasText: "Last jobs" }).innerText(), /No finished jobs in its reports yet\./);
  assert.match(await page().locator(".card", { hasText: "What you can do" }).innerText(), /It shut down cleanly, so nothing was lost\./);

  await page().evaluate(() => openAgent("ghost"));
  await page().getByText("No environment you can read has a status record from this agent.").waitFor();
  db.list = new Error("PermissionError: LH_Meta can't be read");
  await page().evaluate(() => openAgent("ghost"));
  await page().getByText("Its status couldn't be read.").waitFor();
  await page().evaluate(() => { window.__go = window.go; window.__went = []; window.go = (v) => window.__went.push([v, monitorPage.panel]); });
  await page().getByRole("button", { name: "Back to Agents" }).click();
  assert.deepStrictEqual(await page().evaluate(() => { window.go = window.__go; return window.__went; }), [["monitor", "agents"]]);

  await mount();
  await box().getByText("Couldn't read agents: LH_Meta can't be read").waitFor();
  assert.deepStrictEqual(app.errors, []);
});
