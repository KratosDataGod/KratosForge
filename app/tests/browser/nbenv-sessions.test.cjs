// #1476: the notebook Environment and Sessions dialogs' writes that no other browser test reached (see
// harness.cjs for how to run it). Remove on an installed package asks first, as a danger, and sends
// exactly that environment's name and the one package; Cancel sends nothing. An install's progress
// poll (environment_job) stops once the job has ended, and a failed one says why in words. Share sends
// the notebook picked in the list as the guest and the open notebook as the host, after a danger
// confirm; Leave sends the open notebook. Engine refusals show as a sentence, without Python's
// exception name. Opening either dialog sends no write, and axe finds nothing serious in them.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const dialog = () => page().locator(".dialog");
const NB = "Daily sales";
const READS = ["notebook_environments", "kernel_sessions", "environment_job"];
const WRITES = ["environment_install", "environment_uninstall", "environment_create", "environment_delete", "environment_attach",
  "kernel_session_share", "kernel_session_leave", "stop_kernel_process"];

// Engine errors as server.py sends them: "<exception name>: <message>".
const BUSY = "RuntimeError: Data science is already installing packages. Wait for it to finish.";  // environments.py Environments.start
const LAKEHOUSES = `RuntimeError: Weekly report and ${NB} have different default lakehouses. Notebooks in a shared session use the same one, as in Fabric; change one of them first.`;  // service.py kernel_session_share
const UNKNOWN_JOB = "ValueError: That install isn't known. It may be from before the app restarted.";  // environments.py Environments.job
const TIMED_OUT = "pip didn't finish within 15 minutes, so it was stopped.";  // environments.py Environments._run

let envs, shared, jobs;
const fail = {};  // method -> the engine's error text for calls to it
function fresh() {
  // environments.py Environments.list: name, packages (the specs), installed (what's in its folder), notebooks, updated.
  envs = [{ name: "Data science", packages: ["scikit-learn==1.5.2", "great-expectations==1.2.0"],
    installed: [{ name: "great_expectations", version: "1.2.0" }, { name: "scikit-learn", version: "1.5.2" }],
    notebooks: [NB, "Other"], updated: 1761980000 }];
  shared = {};  // kernel_sessions.py KernelSessions.joined: guest -> host
  jobs = [];    // what environment_job answers, in turn
  for (const k of Object.keys(fail)) delete fail[k];
}
fresh();

const now = () => Date.now() / 1000;
// environments.py Environments.start: the job dict it returns (and job() returns a copy of).
const job = (state, more = {}) => ({ id: "a1b2c3d4e5f6", environment: "Data science", packages: ["scikit-learn==1.5.2"],
  remove: false, state, log: "", started: now() - 5, finished: state === "running" ? null : now(), ...more });
// kernel_sessions.py KernelSessions.describe: one row per notebook's session.
const row = (notebook, pid, last) => ({ notebook, process: true, status: "idle", pid, memory_bytes: 180e6, cells_run: 3,
  started: now() - 3600, last_used: now() - last, active: notebook === NB || shared[NB] === notebook,
  shared_with: Object.keys(shared).filter((g) => shared[g] === notebook).sort() });
const guard = (method, fn) => (p) => { if (fail[method]) throw new Error(fail[method]); return fn(p); };

const mocks = {
  save_notebook: () => ({ saved: true }),
  // environment_service.py EnvironmentMixin.notebook_environments
  notebook_environments: ({ notebook }) => ({ environments: JSON.parse(JSON.stringify(envs)),
    current: envs.find((e) => e.notebooks.includes(notebook))?.name || null, process: true, allowed: true }),
  environment_install: guard("environment_install", ({ packages }) => job("running", { packages })),
  environment_uninstall: guard("environment_uninstall", ({ packages }) => job("running", { packages, remove: true })),
  environment_job: guard("environment_job", () => jobs.length > 1 ? jobs.shift() : jobs[0]),
  // service.py kernel_sessions
  kernel_sessions: () => ({ process: true, sessions: [row(NB, 4120, 60), row("Other", 4188, 600), row("Weekly report", 4230, 1200)]
    .filter((r) => !shared[r.notebook]), shared: { ...shared }, notes: [], max_sessions: 5, idle_minutes: 30 }),
  // kernel_sessions.py KernelSessions.share / leave
  kernel_session_share: guard("kernel_session_share", ({ notebook, host }) => {
    shared[notebook] = host;
    return { notebook, host, shared_with: Object.keys(shared).filter((g) => shared[g] === host).sort() };
  }),
  kernel_session_leave: guard("kernel_session_leave", ({ notebook }) => {
    const left = shared[notebook] ?? null;
    delete shared[notebook];
    return { notebook, left };
  }),
};

before(async () => {
  if (skip) return;
  app = await openApp({ mocks });
  await page().evaluate((name) => {
    loadIntoState({ name, cells: [{ type: "code", cell_id: "c1", source: "x = 1", meta: {} }] });
    go("notebook");
  }, NB);
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  fresh();
  await page().evaluate(() => { closeDialog(); const t = document.getElementById("toast"); t.hidden = true; t.textContent = ""; });
});

const pause = (ms) => new Promise((r) => setTimeout(r, ms));
const writes = (from) => app.calls.slice(from).filter((c) => WRITES.includes(c.method)).map((c) => ({ method: c.method, params: c.params }));
const count = (from, method) => app.calls.slice(from).filter((c) => c.method === method).length;
const toastText = async () => { await page().locator("#toast:not([hidden])").waitFor(); return page().locator("#toast").innerText(); };
const sentence = (text) => {
  assert.doesNotMatch(text, /\b[A-Z][A-Za-z]*(Error|Exception):/, `no exception name: ${text}`);
  assert.match(text, /^[A-Za-z][^\n]* [^\n]*\.$/m, `a sentence: ${text}`);
};

async function openEnvironment() {
  await page().getByRole("button", { name: /^Environment/ }).click();
  await dialog().getByText("Packages this notebook adds").waitFor();
}
async function openSessions() {
  await page().getByRole("button", { name: /^(Sessions|Shared session)/ }).click();
  await dialog().locator(".sessions").waitFor();
}
const pkgRow = (name) => dialog().locator("tbody tr", { has: page().locator("td", { hasText: new RegExp(`^${name}$`) }) });

test("opening Environment and Sessions sends no write and doesn't poll", { skip }, async () => {
  const from = app.calls.length;
  await openEnvironment();
  assert.match(await dialog().innerText(), /scikit-learn\s+1\.5\.2/);
  await page().evaluate(() => closeDialog());
  await openSessions();
  await pause(1300);  // longer than the install poll's 1 s
  assert.deepStrictEqual(writes(from), []);
  assert.strictEqual(count(from, "environment_job"), 0);
});

test("Remove asks first as a danger, and Cancel sends nothing", { skip }, async () => {
  await openEnvironment();
  const from = app.calls.length;
  await pkgRow("scikit-learn").getByRole("button", { name: /Remove/ }).click();
  await dialog().getByText("Remove scikit-learn from Data science?").waitFor();
  assert.match(await dialog().innerText(), /deleted from this PC/);
  assert.strictEqual(await dialog().locator(".dialog-actions .danger-solid").count(), 1);
  assert.deepStrictEqual(writes(from), []);
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await dialog().getByText("Packages this notebook adds").waitFor();
  assert.deepStrictEqual(writes(from), []);
});

test("Remove sends exactly the environment and that one package, then follows the job until it ends", { skip }, async () => {
  jobs = [job("running", { remove: true }), job("done", { remove: true, log: "Removed scikit-learn" })];
  await openEnvironment();
  const from = app.calls.length;
  await pkgRow("scikit-learn").getByRole("button", { name: /Remove/ }).click();
  await dialog().locator(".dialog-actions .danger-solid").click();
  await dialog().getByText("Removing...").waitFor();
  assert.deepStrictEqual(writes(from), [{ method: "environment_uninstall", params: { name: "Data science", packages: ["scikit-learn"] } }]);
  await dialog().getByText("Done.").waitFor({ timeout: 8000 });
  assert.match(await dialog().locator(".env-log").innerText(), /Removed scikit-learn/);
  const polls = count(from, "environment_job");
  assert.strictEqual(polls, 2);
  await pause(2500);
  assert.strictEqual(count(from, "environment_job"), polls, "no poll after the job ended");
  assert.deepStrictEqual(app.calls.slice(from).filter((c) => c.method === "environment_job").map((c) => c.params), [{ job_id: "a1b2c3d4e5f6" }, { job_id: "a1b2c3d4e5f6" }]);
});

test("a failed install stops the poll and says why in a sentence", { skip }, async () => {
  jobs = [job("failed", { log: TIMED_OUT })];
  await openEnvironment();
  const from = app.calls.length;
  await page().fill('.dialog input[aria-label="Packages to install"]', "scikit-learn==1.5.2");
  await dialog().getByRole("button", { name: /Install/ }).click();
  await dialog().getByText("Install into Data science?").waitFor();
  await dialog().locator(".dialog-actions .tool.primary").click();
  await dialog().getByText("Installing...").waitFor();
  assert.ok(await dialog().getByRole("button", { name: /Install/ }).isDisabled(), "Install waits while pip runs");
  await dialog().getByText("pip failed:").waitFor({ timeout: 8000 });
  const log = await dialog().locator(".env-log").innerText();
  assert.strictEqual(log, TIMED_OUT);
  sentence(log);
  assert.ok(await dialog().getByRole("button", { name: /Install/ }).isEnabled(), "Install is offered again");
  await pause(2500);
  assert.strictEqual(count(from, "environment_job"), 1, "one poll, then none after it failed");
  assert.deepStrictEqual(writes(from), [{ method: "environment_install", params: { name: "Data science", packages: ["scikit-learn==1.5.2"] } }]);
});

test("the poll stops when the dialog is closed", { skip }, async () => {
  jobs = [job("running")];
  await openEnvironment();
  const from = app.calls.length;
  await page().fill('.dialog input[aria-label="Packages to install"]', "polars-ds");
  await dialog().getByRole("button", { name: /Install/ }).click();
  await dialog().locator(".dialog-actions .tool.primary").click();
  await dialog().getByText("Installing...").waitFor();
  await page().evaluate(() => closeDialog());
  await pause(2500);
  assert.strictEqual(count(from, "environment_job"), 0);
});

test("engine refusals of Remove and the poll read as a sentence", { skip }, async () => {
  fail.environment_uninstall = BUSY;
  await openEnvironment();
  await pkgRow("great_expectations").getByRole("button", { name: /Remove/ }).click();
  await dialog().locator(".dialog-actions .danger-solid").click();
  let text = await toastText();
  assert.match(text, /^Could not remove: Data science is already installing packages\. Wait for it to finish\./);
  sentence(text);
  await dialog().getByText("Packages this notebook adds").waitFor();

  await page().evaluate(() => { const t = document.getElementById("toast"); t.hidden = true; t.textContent = ""; });
  fail.environment_job = UNKNOWN_JOB;
  await page().fill('.dialog input[aria-label="Packages to install"]', "polars-ds");
  await dialog().getByRole("button", { name: /Install/ }).click();
  await dialog().locator(".dialog-actions .tool.primary").click();
  text = await toastText();
  assert.match(text, /^Could not check the install: That install isn't known\. It may be from before the app restarted\./);
  sentence(text);
});

test("Share sends the picked guest and this notebook as host, after a danger confirm; Cancel sends nothing", { skip }, async () => {
  await openSessions();
  const from = app.calls.length;
  const options = await dialog().locator("#share-with option").evaluateAll((os) => os.map((o) => o.value));
  assert.deepStrictEqual(options, ["Other", "Weekly report"]);
  await dialog().locator("#share-with").selectOption("Weekly report");
  await dialog().getByRole("button", { name: "Share", exact: true }).click();
  await dialog().getByText(`Share ${NB}'s session with Weekly report?`).waitFor();
  assert.strictEqual(await dialog().locator(".dialog-actions .danger-solid").count(), 1);
  await dialog().getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(writes(from), []);

  await openSessions();
  await dialog().locator("#share-with").selectOption("Weekly report");
  await dialog().getByRole("button", { name: "Share", exact: true }).click();
  await dialog().locator(".dialog-actions .danger-solid").click();
  assert.match(await toastText(), new RegExp(`^Weekly report now shares ${NB}'s session\\. Its variables were cleared\\.`));
  assert.deepStrictEqual(writes(from), [{ method: "kernel_session_share", params: { notebook: "Weekly report", host: NB } }]);
  await dialog().locator(".sessions tbody tr", { hasText: "Shared with Weekly report" }).waitFor();
});

test("Leave sends this notebook", { skip }, async () => {
  shared[NB] = "Other";
  await openSessions();
  assert.match(await dialog().innerText(), new RegExp(`${NB} shares Other's session`));
  const from = app.calls.length;
  await dialog().getByRole("button", { name: "Leave", exact: true }).click();
  assert.match(await toastText(), new RegExp(`^${NB} left Other's session\\. Its next cell starts a session of its own\\.`));
  assert.deepStrictEqual(writes(from), [{ method: "kernel_session_leave", params: { notebook: NB } }]);
  await dialog().locator("#share-with").waitFor();
});

test("an engine refusal of Share reads as a sentence and changes nothing", { skip }, async () => {
  fail.kernel_session_share = LAKEHOUSES;
  await openSessions();
  await dialog().locator("#share-with").selectOption("Weekly report");
  await dialog().getByRole("button", { name: "Share", exact: true }).click();
  await dialog().locator(".dialog-actions .danger-solid").click();
  const text = await toastText();
  assert.match(text, /^Could not share the session: Weekly report and Daily sales have different default lakehouses\./);
  sentence(text);
  assert.deepStrictEqual(shared, {});
});

test("axe finds nothing serious in the Environment, Remove and Sessions dialogs", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  await openEnvironment();
  assert.deepStrictEqual(await axeFindings(page(), ".dialog"), []);
  await pkgRow("scikit-learn").getByRole("button", { name: /Remove/ }).click();
  await dialog().getByText("Remove scikit-learn from Data science?").waitFor();
  assert.deepStrictEqual(await axeFindings(page(), ".dialog"), []);
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await page().evaluate(() => closeDialog());
  await openSessions();
  assert.deepStrictEqual(await axeFindings(page(), ".dialog"), []);
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
