// #1318: Monitor > KratosForge > Operations > Incidents and the actions on one incident (see harness.cjs
// for how to run it). The list shows Open, Acknowledged and Resolved incidents in two environments,
// and an incident opens on its own page. Acknowledge, Assign, Add note and Resolve each ask for their
// text in a dialog, send exactly that incident's environment and id with the typed text, and the list
// or page then shows the new state, also after a reload. Cancel sends nothing. An engine error shows a
// plain message and the incident stays as it was. Opening the list and an incident sends no write,
// and axe finds nothing serious on the list, the incident page and an action dialog.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");


let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const ME = "chris@contoso.com";
const READS = ["incidents_summary", "incidents_list", "incident_impact"];
const WRITES = ["incident_acknowledge", "incident_assign", "incident_note", "incident_resolve", "incidents_evaluate",
  "incident_tell_impacted", "incident_set_priority", "incident_report"];
const ENVS = ["dev", "test", "prod"];  // meta/project.py ENVIRONMENTS
const KIND_LABELS = { run_failed: "Run failed", needs_decision: "Needs a decision", late: "Late", objective: "Objective missed",
  agent_offline: "Agent offline", cant_evaluate: "Can't evaluate", reported: "Reported" };  // workloads/incidents.py
const DENIED = "HTTPError: 403 Client Error: Forbidden for url: https://onelake.dfs.fabric.microsoft.com/a1/b2/Files/_control/incidents/incidents.json";
const PLAIN = "Not saved: You don't have permission for that in this workspace. A workspace admin can make you a Contributor or above.";

const at = (s) => new Date(Date.now() + s * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");

// One record as workloads/incidents.py keeps it in incidents/incidents.json (contract 1).
function record(env, number, id, kind, workload, name, title, more = {}) {
  const opened = more.opened_at || at(-7200);
  return { id, key: `${kind}:${workload || "-"}:${env}`, kind, workload, name, env, title, text: "", evidence: {}, evidence_id: "",
    state: "open", opened_at: opened, last_seen_at: opened, occurrences: 1, opened_by: "agent etl-01",
    owner: "", acknowledged_by: null, acknowledged_at: null, resolved_at: null, resolved_by: null, resolution: null,
    team: "Sales data team", criticality: "high", classification: null, escalated_at: null, escalated_to: null,
    notes: [], number, source: "detected", priority: "P2", priority_source: "policy",
    events: [{ at: opened, by: "agent etl-01", what: "opened", text: title }], ...more };
}

// Prod: an open failed run, an acknowledged late table and a resolved offline agent. Dev: an open
// failed run and a recovered objective. Test has none.
const A = "inc-0a1b2c3d4e5f", B = "inc-1b2c3d4e5f6a", C = "inc-2c3d4e5f6a7b", D = "inc-3d4e5f6a7b8c", E = "inc-4e5f6a7b8c9d";
function fresh() {
  return {
    dev: [
      record("dev", "INC-DEV-0002", D, "run_failed", "meta:sales:bronze.customers", "bronze.customers", "bronze.customers failed",
        { text: "Py4JJavaError: Path does not exist: Files/landing/customers", evidence_id: "run-d1", priority: "P4",
          evidence: { source: "the run log", run_id: "run-d1", started_at: at(-3700) }, opened_at: at(-3600) }),
      record("dev", "INC-DEV-0001", E, "objective", "meta:sales:silver.returns", "silver.returns", "silver.returns missed its objective",
        { state: "resolved", resolved_at: at(-1800), resolved_by: "KratosForge (chris@contoso.com)", resolution: "recovered",
          opened_at: at(-90000), priority: "P4" }),
    ],
    test: [],
    prod: [
      record("prod", "INC-PROD-0003", A, "run_failed", "meta:sales:silver.orders", "silver.orders", "silver.orders failed",
        { text: "Gateway timeout reading the orders source", evidence_id: "run-a1", evidence: { source: "the run log", run_id: "run-a1" },
          escalated_at: at(-3600), escalated_to: "on-call",
          notes: [{ at: at(-3600), by: "agent etl-01", text: "Escalated to on-call: not acknowledged in 60 min" }] }),
      record("prod", "INC-PROD-0002", B, "late", "meta:sales:gold.sales", "gold.sales", "gold.sales is late",
        { text: "No good run since yesterday 06:00", state: "acknowledged", owner: ME, acknowledged_by: ME, acknowledged_at: at(-80000),
          opened_at: at(-90000) }),
      record("prod", "INC-PROD-0001", C, "agent_offline", null, "etl-02", "Agent etl-02 stopped reporting",
        { state: "resolved", resolved_at: at(-86400 * 2), resolved_by: ME, resolution: "resolved", opened_at: at(-86400 * 3), priority: "P3" }),
    ],
  };
}

let db = fresh();
const fail = {};  // method -> the engine's error text, for the next calls to it
const copy = (x) => JSON.parse(JSON.stringify(x));
const labelled = (i) => ({ ...copy(i), kind_label: KIND_LABELS[i.kind] || i.kind });
const ACTIVE = ["open", "acknowledged"];
// IncidentLog.list: active newest first, then resolved newest first.
const sorted = (items) => [...items.filter((i) => ACTIVE.includes(i.state)).sort((a, b) => b.opened_at.localeCompare(a.opened_at)),
  ...items.filter((i) => !ACTIVE.includes(i.state)).sort((a, b) => (b.resolved_at || "").localeCompare(a.resolved_at || ""))];
const live = () => ({ source: "Incidents", state: "live", label: "Live", collected_at: at(0).replace("Z", "+00:00"), age_seconds: 0, detail: "" });
const checking = { enabled: true, seconds: 300, quiet_hours: null, quiet_except: [], escalate_after_minutes: 60, escalate_to: "on-call", routes: [] };

// A change as IncidentLog._manual makes it: the record after the change, or the engine's error.
const change = (method, fn) => (p) => {
  if (fail[method]) throw new Error(fail[method]);
  if (!ENVS.includes(p.env)) throw new Error("ValueError: env is one of dev, test, prod");
  const inc = db[p.env].find((i) => i.id === p.id);
  if (!inc) throw new Error("LookupError: That incident isn't in the list any more.");
  const now = at(0);
  const event = (what, text = "") => inc.events.push({ at: now, by: ME, what, text });
  fn(inc, p, now, event);
  return copy(inc);
};

const mocks = {
  readiness_check: () => [],
  // incident_service.py
  incidents_summary: () => ({ envs: ENVS.map((env) => {
    const items = sorted(db[env]).filter((i) => ACTIVE.includes(i.state)).map(labelled);
    return { env, count: items.length, incidents: items, observation: live() };
  }), user: ME }),
  incidents_list: ({ env }) => ({ env, incidents: sorted(db[env]).map(labelled), observation: live(), user: ME,
    agents: { readable: true, agents: env === "prod" ? [{ agent: "etl-01", host: "srv-etl-01", version: "0.0.0", state: "live",
      heartbeat_at: at(-30), incidents: checking }] : [] } }),
  incident_acknowledge: change("incident_acknowledge", (inc, { note = "" }, now, event) => {
    if (inc.state === "resolved") throw new Error("ValueError: It's resolved already.");
    Object.assign(inc, { state: "acknowledged", acknowledged_by: ME, acknowledged_at: now });
    event("acknowledged", note);
    if (note) inc.notes.push({ at: now, by: ME, text: note });
  }),
  incident_assign: change("incident_assign", (inc, { owner }, now, event) => {
    const was = inc.owner || "nobody";
    inc.owner = owner.trim();
    inc.notes.push({ at: now, by: ME, text: `Assigned: ${was} → ${owner.trim() || "nobody"}` });
    event("assigned", `${was} → ${owner.trim() || "nobody"}`);
  }),
  incident_note: change("incident_note", (inc, { text }, now, event) => {
    if (!text.trim()) throw new Error("ValueError: Write a note first.");
    inc.notes.push({ at: now, by: ME, text: text.trim() });
    event("note", text);
  }),
  incident_resolve: change("incident_resolve", (inc, { note = "" }, now, event) => {
    if (inc.state === "resolved") throw new Error("ValueError: It's resolved already.");
    Object.assign(inc, { state: "resolved", resolved_at: now, resolved_by: ME, resolution: "resolved" });
    event("resolved", note);
    if (note) inc.notes.push({ at: now, by: ME, text: note });
  }),
  // incident_desk.py: no data product is built on these tables.
  incident_impact: ({ env, id }) => {
    const inc = db[env].find((i) => i.id === id);
    if (!inc?.workload?.startsWith("meta:")) return { available: false, reason: "This incident isn't about a table, so no data product is built on it." };
    const [, project, entity] = inc.workload.split(":");
    return { project, entity, env, products: [], consumers: [], people: [], unreachable: [], available: true, told: null, followers_hidden: [] };
  },
};

let baseline;  // the methods the app calls on its own, before any incident opens

before(async () => {
  if (skip) return;
  app = await openApp({ mocks });
  // The app's startup reads (with its own incidents check), and its first approval polls 5 s in.
  await startupReads(app);
  for (const end = Date.now() + 15000; !["incidents_watch", "integrations_pending", "editor_pending"].every((m) => app.calls.some((c) => c.method === m));
    await new Promise((r) => setTimeout(r, 50))) {
    if (Date.now() > end) throw new Error("the app's startup polls never came");
  }
  baseline = new Set(app.calls.map((c) => c.method));
});
after(async () => { if (app) await app.close(); });

// Each test starts from the same incidents, no dialog and no message showing.
beforeEach(async () => {
  if (skip) return;
  db = fresh();
  for (const k of Object.keys(fail)) delete fail[k];
  await page().evaluate(() => { closeMenu(); closeDialog(); const t = document.getElementById("toast"); t.hidden = true; t.textContent = ""; go("home"); });
});

const writes = (from) => app.calls.slice(from).filter((c) => WRITES.includes(c.method)).map((c) => ({ method: c.method, params: c.params }));
const pause = (ms) => new Promise((r) => setTimeout(r, ms));

// The list for one environment and filter, read afresh: each row's cells as text. Count and read in
// one step, since the list draws again while it loads.
async function listed(env, filter, n) {
  await page().evaluate(([env, filter]) => {
    incPage.env = env; incPage.filter = filter; monitorPage.panel = "incidents"; go("home"); go("monitor");
  }, [env, filter]);
  return rowsNow(env, n);
}

async function rowsNow(env, n, title = null, cell = null, want = null) {
  const handle = await page().waitForFunction(([env, n, title, cell, want]) => {
    const box = document.querySelector(".page.monitor .inc-panel");
    const on = box?.querySelector('[aria-label="Environment"] .chip.on')?.textContent || "";
    const trs = [...(box?.querySelectorAll("tbody tr") || [])];
    const rows = trs.map((tr) => [...tr.children].map((td) => td.textContent.trim()));
    if (!on.startsWith(`${ENV_LABEL[env]} ·`) || rows.length !== n) return false;
    if (title && rows.find((r) => r[1].startsWith(title))?.[cell] !== want) return false;
    return rows;
  }, [env, n, title, cell, want]);
  return handle.jsonValue();
}

const rowOf = (list, title) => list.find((cells) => cells[1].startsWith(title));
const listRow = (title) => page().locator(".page.monitor .inc-panel tbody tr", { has: page().locator("b", { hasText: title }) });

// The incident's page once drawn for that incident; with pill, once its state pill reads that.
async function incidentNow(id, pill = null) {
  const handle = await page().waitForFunction(([id, pill]) => {
    const h1 = document.querySelector(".page.wl-page h1");
    if (state.view !== "incident" || incPage.detail?.id !== id || !h1 || !document.querySelector(".page .inc-grid")) return false;
    const shown = h1.querySelector(".pill-s")?.textContent;
    if (pill && shown !== pill) return false;
    return { title: h1.firstChild.textContent.trim(), pill: shown,
      buttons: [...document.querySelectorAll(".page .wl-head button")].map((b) => b.textContent.trim()).filter(Boolean),
      history: [...document.querySelectorAll(".page .wl-timeline .wl-event > div")].map((e) => e.textContent),
      text: document.querySelector(".page").innerText };
  }, [id, pill]);
  return handle.jsonValue();
}

async function openPage(env, id) {
  await page().evaluate(([env, id]) => openIncident({ env, id }), [env, id]);
  return incidentNow(id);
}

// Each action, where it is started, and what it asks for.
const ACTIONS = {
  acknowledge: { method: "incident_acknowledge", env: "dev", id: D, title: "bronze.customers failed", where: "row", button: "Acknowledge",
    dialog: "Acknowledge bronze.customers failed?", field: "Note (optional)", param: "note", text: "Checking the landing folder" },
  assign: { method: "incident_assign", env: "prod", id: A, title: "silver.orders failed", where: "menu", button: "Assign",
    dialog: "Assign silver.orders failed?", field: "Owner (required)", param: "owner", text: "dana@contoso.com" },
  note: { method: "incident_note", env: "dev", id: E, title: "silver.returns missed its objective", where: "page", button: "Add note",
    dialog: "Add a note to silver.returns missed its objective?", field: "Note (required)", param: "text", text: "Late upstream file, now on time" },
  resolve: { method: "incident_resolve", env: "prod", id: B, title: "gold.sales is late", where: "page", button: "Resolve",
    dialog: "Resolve gold.sales is late?", field: "Note (optional)", param: "note", text: "Source moved to 05:00" },
};
const ROWS = { dev: 1, prod: 2 };  // active rows in each environment as the test starts

// Opens the action's dialog and types its text; returns the dialog.
async function start(a) {
  if (a.where === "page") {
    await openPage(a.env, a.id);
    await page().locator(".page .wl-head").getByRole("button", { name: a.button, exact: true }).click();
  } else {
    await listed(a.env, "active", ROWS[a.env]);
    if (a.where === "menu") {
      await listRow(a.title).getByRole("button", { name: "More" }).click();
      await page().getByRole("menuitem", { name: a.button }).click();
    } else {
      await listRow(a.title).getByRole("button", { name: a.button, exact: true }).click();
    }
  }
  const dialog = page().getByRole("dialog", { name: a.dialog });
  await dialog.waitFor();
  await dialog.getByRole("textbox", { name: a.field }).fill(a.text);
  return dialog;
}

const sentFor = (a) => [{ method: a.method, params: { env: a.env, id: a.id, [a.param]: a.text } }];

test("the list shows Open, Acknowledged and Resolved in each environment, and an incident opens on its own page", { skip }, async () => {
  const prod = await listed("prod", "active", 2);
  // State, Problem, Kind, Opened, Seen, Owner, Escalation
  assert.deepStrictEqual(prod.map((c) => [c[0], c[1].split(/failed|is late/)[0].trim(), c[2], c[5]]), [
    ["Open", "silver.orders", "Run failed", "No owner"],
    ["Acknowledged", "gold.sales", "Late", ME],
  ]);
  assert.match(rowOf(prod, "silver.orders")[6], /^Escalated 1 h agoto on-call$/);
  assert.strictEqual(rowOf(prod, "gold.sales")[6], "Not needed: acknowledged");
  const chips = await page().locator(".page.monitor .inc-panel [aria-label=Environment] button").allTextContents();
  assert.deepStrictEqual(chips, ["Dev · 1", "Test · 0", "Prod · 2"]);
  assert.match(await page().textContent(".page.monitor .inc-watchers"), /Checked by agent etl-01 every 5 min/);
  const prodDone = await listed("prod", "resolved", 1);
  assert.deepStrictEqual(prodDone.map((c) => [c[0], c[2]]), [["Resolved", "Agent offline"]]);
  assert.match(prodDone[0][1], /^Agent etl-02 stopped reportingAgent etl-02/);
  assert.strictEqual(prodDone[0][6], "None");
  const dev = await listed("dev", "active", 1);
  assert.deepStrictEqual(dev.map((c) => c[0]), ["Open"]);
  assert.match(dev[0][1], /^bronze\.customers failedPy4JJavaError: Path does not exist/);
  const devDone = await listed("dev", "resolved", 1);
  assert.deepStrictEqual(devDone.map((c) => c[0]), ["Recovered"]);
  await listed("test", "active", 0);
  assert.match(await page().textContent(".page.monitor .inc-panel"), /No active incidents in Test\./);
  // A row opens the incident on its own page, and the Incidents crumb goes back.
  await listed("prod", "active", 2);
  await listRow("gold.sales is late").locator("td").nth(1).click();
  const shown = await incidentNow(B);
  assert.strictEqual(shown.title, "gold.sales is late");
  assert.strictEqual(shown.pill, "Acknowledged");
  assert.deepStrictEqual(shown.buttons, ["Assign", "Add note", "Resolve", "Open workload"]);
  assert.match(shown.text, /Late · High · Prod · opened yesterday by agent etl-01/);
  assert.match(shown.text, /No good run since yesterday 06:00/);
  assert.deepStrictEqual(shown.history.slice(0, 1), [`Acknowledged by ${ME}`]);
  await page().locator("button.crumb", { hasText: "Incidents" }).click();
  await page().waitForFunction(() => state.view === "monitor" && monitorPage.panel === "incidents"
    && document.querySelectorAll(".page.monitor .inc-panel tbody tr").length === 2);
});

test("Acknowledge on a row asks for a note, sends that incident with it, and the row shows Acknowledged", { skip }, async () => {
  const a = ACTIONS.acknowledge;
  const from = app.calls.length;
  const dialog = await start(a);
  assert.match(await dialog.innerText(), /Marks it acknowledged in Dev's incident list for everyone/);
  await dialog.getByRole("button", { name: "Acknowledge", exact: true }).click();
  await dialog.waitFor({ state: "detached" });
  assert.deepStrictEqual(writes(from), sentFor(a));
  const now = await rowsNow("dev", 1, a.title, 0, "Acknowledged");
  assert.strictEqual(now[0][6], "Not needed: acknowledged");
  assert.strictEqual(await listRow(a.title).getByRole("button", { name: "Acknowledge" }).count(), 0);
  // Read again, and on its page.
  assert.strictEqual(rowOf(await listed("dev", "active", 1), a.title)[0], "Acknowledged");
  const shown = await openPage(a.env, a.id);
  assert.strictEqual(shown.pill, "Acknowledged");
  assert.deepStrictEqual(shown.history.slice(0, 1), [`Acknowledged by ${ME}`]);
  assert.ok(shown.history.includes(`${ME}: ${a.text}`), shown.history.join("\n"));
});

test("Assign from a row's More menu asks for an owner, sends that incident with it, and the row shows the owner", { skip }, async () => {
  const a = ACTIONS.assign;
  const from = app.calls.length;
  const dialog = await start(a);
  await dialog.getByRole("button", { name: "Assign", exact: true }).click();
  await dialog.waitFor({ state: "detached" });
  assert.deepStrictEqual(writes(from), sentFor(a));
  const now = await rowsNow("prod", 2, a.title, 5, a.text);
  assert.strictEqual(rowOf(now, "gold.sales")[5], ME, "the other incident keeps its owner");
  assert.strictEqual(rowOf(await listed("prod", "active", 2), a.title)[5], a.text);
  const shown = await openPage(a.env, a.id);
  assert.match(shown.text, /Owner\s+dana@contoso\.com/);
  assert.ok(shown.history.includes(`${ME}: Assigned: nobody → ${a.text}`), shown.history.join("\n"));
});

test("Add note on the incident page asks for the note, sends that incident with it, and History shows it", { skip }, async () => {
  const a = ACTIONS.note;
  const from = app.calls.length;
  const dialog = await start(a);
  await dialog.getByRole("button", { name: "Add note", exact: true }).click();
  await dialog.waitFor({ state: "detached" });
  assert.deepStrictEqual(writes(from), sentFor(a));
  const handle = await page().waitForFunction((want) => [...document.querySelectorAll(".page .wl-timeline .wl-event > div")]
    .some((e) => e.textContent === want) && document.querySelector(".page h1 .pill-s")?.textContent, `${ME}: ${a.text}`);
  assert.strictEqual(await handle.jsonValue(), "Recovered", "a note doesn't change the state");
  await page().evaluate(() => go("home"));
  const shown = await openPage(a.env, a.id);
  assert.deepStrictEqual(shown.history.slice(0, 2), [`${ME}: ${a.text}`, "Recovered by KratosForge (chris@contoso.com)"]);
});

test("Resolve on the incident page asks for a note, sends that incident with it, and it shows Resolved", { skip }, async () => {
  const a = ACTIONS.resolve;
  const from = app.calls.length;
  const dialog = await start(a);
  assert.match(await dialog.innerText(), /It opens again only on new evidence/);
  await dialog.getByRole("button", { name: "Resolve", exact: true }).click();
  await dialog.waitFor({ state: "detached" });
  assert.deepStrictEqual(writes(from), sentFor(a));
  const shown = await incidentNow(a.id, "Resolved");
  assert.deepStrictEqual(shown.buttons, ["Add note", "Open workload"]);
  assert.deepStrictEqual(shown.history.slice(0, 2).sort(), [`Resolved by ${ME}`, `${ME}: ${a.text}`]);
  // Gone from Active, listed under Resolved; the other open incident is untouched.
  const active = await listed("prod", "active", 1);
  assert.deepStrictEqual(active.map((c) => [c[0], c[1].slice(0, 20)]), [["Open", "silver.orders failed"]]);
  const done = await listed("prod", "resolved", 2);
  assert.deepStrictEqual(done.map((c) => [c[0], c[1].slice(0, 18)]), [["Resolved", "gold.sales is late"], ["Resolved", "Agent etl-02 stopp"]]);
});

for (const [name, a] of Object.entries(ACTIONS)) {
  test(`${name}: Cancel sends nothing and changes nothing`, { skip }, async () => {
    const before = JSON.stringify(db);
    const from = app.calls.length;
    const dialog = await start(a);
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await dialog.waitFor({ state: "detached" });
    await pause(300);  // nothing should happen: give it the chance
    assert.deepStrictEqual(writes(from), []);
    assert.strictEqual(JSON.stringify(db), before);
  });

  test(`${name}: an engine error shows a plain message and the incident stays as it was`, { skip }, async () => {
    fail[a.method] = DENIED;
    const before = JSON.stringify(db);
    const from = app.calls.length;
    const dialog = await start(a);
    await dialog.getByRole("button", { name: a.button, exact: true }).click();
    const handle = await page().waitForFunction(() => { const t = document.getElementById("toast"); return !t.hidden && t.textContent; });
    const said = await handle.jsonValue();
    assert.strictEqual(said.split("\n")[0], PLAIN);
    assert.doesNotMatch(said, /HTTPError|403|onelake/);
    assert.strictEqual(await dialog.count(), 1, "the dialog stays open with what was typed");
    assert.strictEqual(await dialog.getByRole("textbox", { name: a.field }).inputValue(), a.text);
    assert.deepStrictEqual(writes(from), sentFor(a));
    assert.strictEqual(JSON.stringify(db), before);
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await dialog.waitFor({ state: "detached" });
    // Read again: as it was.
    const was = fresh()[a.env].find((i) => i.id === a.id);
    const shown = await openPage(a.env, a.id);
    assert.strictEqual(shown.pill, { open: "Open", acknowledged: "Acknowledged", resolved: "Recovered" }[was.state]);
    assert.strictEqual(shown.history.length, 1 + (was.acknowledged_at ? 1 : 0) + was.notes.length + (was.resolved_at ? 1 : 0));
  });
}

// The incident list changed under every try (workloads/incidents.py raises Conflict): the sentence,
// without the exception's name in front.
test("a write that keeps losing to other changes says so, without the exception's name", { skip }, async () => {
  const a = ACTIONS.acknowledge;
  fail[a.method] = "Conflict: The incident list kept changing while it was being updated; try again.";
  try {
    const dialog = await start(a);
    await dialog.getByRole("button", { name: a.button, exact: true }).click();
    const handle = await page().waitForFunction(() => { const t = document.getElementById("toast"); return !t.hidden && t.textContent; });
    assert.strictEqual((await handle.jsonValue()).split("\n")[0], "Not saved: The incident list kept changing while it was being updated; try again.");
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await dialog.waitFor({ state: "detached" });
  } finally { delete fail[a.method]; }
});

test("opening the list and an incident sends no write", { skip }, async () => {
  const from = app.calls.length;
  await listed("prod", "active", 2);
  await listed("prod", "resolved", 1);
  await listed("dev", "active", 1);
  await page().locator(".page.monitor .inc-panel [aria-label=Show] button", { hasText: "Mine" }).click();
  await rowsNow("dev", 0);
  await listed("prod", "active", 2);
  await listRow("silver.orders failed").locator("td").nth(1).click();
  await incidentNow(A);
  await page().locator(".inc-impact .muted-text", { hasText: "No published data product" }).waitFor();
  await openPage("prod", C);
  await openPage("dev", E);
  await page().locator("button.crumb", { hasText: "Incidents" }).click();
  await rowsNow("prod", 2);
  const made = app.calls.slice(from).map((c) => c.method);
  for (const m of READS) assert.ok(made.includes(m), `${m} was read`);
  assert.deepStrictEqual(made.filter((m) => WRITES.includes(m)), []);
  assert.deepStrictEqual([...new Set(made.filter((m) => !READS.includes(m) && !baseline.has(m)))], []);
});

test("axe finds nothing serious on the list, the incident page and an action dialog", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  // axeFindings settles the page first: axe judges contrast on the colors as drawn.
  const check = async (where, selector) => assert.deepStrictEqual(await axeFindings(page(), selector), [], where);
  await listed("prod", "active", 2);
  await check("list", ".page.monitor");
  await listed("prod", "resolved", 1);
  await check("resolved list", ".page.monitor");
  await openPage("prod", A);
  await page().locator(".inc-impact .muted-text", { hasText: "No published data product" }).waitFor();
  await check("incident page", ".page.wl-page");
  await start(ACTIONS.resolve);
  await check("dialog", "#dialog-root .dialog");
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
