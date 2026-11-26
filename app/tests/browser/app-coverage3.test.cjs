// app/ui/app.js beyond app-coverage and the page tests: the notebook's Runs on menu with Preview
// features hidden (This PC, Same as Dev, the OneLake writes options and opening their folder), a
// notebook changed on disk into something that isn't a notebook (Keep mine saves, the copy is
// named), Alt+Left and Alt+Right (ignored while typing or with a dialog open), Monitor's rows for this
// PC's schedule runs, Home's failed Fabric jobs and Coming up, closing a notebook with edits from the
// rail, the compute note (only in Fabric, and how a Fabric Run all works), and the start page of an
// item page that reopens on its rail page. Engine calls are mocked with their own reply shapes.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const NB = { name: "Sales prep", cells: [{ type: "code", cell_id: "a1", source: "x = 1", meta: {} }] };
// fabric_service.runs: Fabric's job runs, newest first. Failed in the last day, they're on Home.
const FABRIC_RUNS = () => [
  { name: "Load sales", kind: "Notebook", status: "Failed", started_at: new Date().toISOString(), message: "Out of capacity",
    url: "https://app.fabric.microsoft.com/groups/w1/runs/1", workspace_name: "Sales" },
  { name: "Old job", kind: "Pipeline", status: "Failed", started_at: new Date().toISOString(), workspace_name: "Ops" },
  { name: "Last week", kind: "Notebook", status: "Failed", started_at: "2026-01-01T00:00:00Z", workspace_name: "Ops" },
];
const calls = (method) => app.calls.filter((c) => c.method === method);

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    save_notebook: () => ({ saved: true, disk_stamp: 9 }),
    local_writes_reveal: () => ({ shown: "C:\\Users\\me\\KratosForge\\local-writes" }),
    kernel_variables: () => [],
    notebook_dependencies: () => ({}),
    open_notebook: ({ name }) => ({ name, cells: [{ type: "code", cell_id: "d1", source: "print(1)", meta: {} }] }),
    // The Monitor page's reads: only this PC's schedule runs have rows here (schedule_service.runs).
    history: () => [], meta_runs_all: () => ({ runs: [], unavailable: {} }), fabric_runs: () => FABRIC_RUNS(),
    pc_schedule_runs: () => [
      { started: "2026-11-26T08:00:00Z", status: "ok", label: "Daily load", trigger: "home", seconds: 12.5, rows: 40, message: "Wrote 40 rows." },
      { started: "2026-11-26T07:00:00Z", status: "skipped", label: "Hourly sync", trigger: "manual", seconds: 0, message: "The PC was asleep." },
      { started: "2026-11-26T06:00:00Z", status: "failed", label: "Nightly", trigger: "timer", seconds: 3, message: "Table missing.", alert_error: "The alert email couldn't be sent" },
    ],
    // schedule_service.schedules_for: this PC's schedules, enabled or not, with their next run.
    pc_schedules_for: () => [
      { label: "Nightly", enabled: true, next_run: "2099-01-02T02:00:00Z" },
      { label: "Daily load", enabled: true, next_run: "2099-01-01T06:00:00Z" },
      { label: "Paused one", enabled: false, next_run: "2099-01-01T01:00:00Z" },
    ],
    meta_project: () => null,
    // settings_service.app_settings: each setting as {value, default}.
    app_settings: () => ({ show_preview: { value: false, default: true }, onelake_writes: { value: "local_first", default: "local_first" } }),
  } });
  await page().evaluate(() => loadSettings());
  await page().waitForFunction(() => state.settings && "show_preview" in state.settings);
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  await page().evaluate((nb) => {
    closeDialog();
    closeMenu();
    state.settings.show_preview = { ...(state.settings.show_preview || {}), value: false };
    loadIntoState(JSON.parse(JSON.stringify(nb)));
    go("notebook");
  }, NB);
  await page().locator(".compute-btn").waitFor();
});

const menuItems = () => page().locator("#menu [role=menuitem]").allInnerTexts();

test("with Preview hidden, Runs on lists this PC, Same as Dev and the OneLake writes, and opens their folder", { skip }, async () => {
  await page().locator(".compute-btn").click();
  await page().locator("#menu").waitFor();
  const items = (await menuItems()).map((t) => t.trim());
  assert.deepStrictEqual(items, ["This PC (no Fabric job)", "Same as Dev (this PC)", "Write here, then upload in the background",
    "Straight to OneLake (fast upload line)", "Same as Settings (Write here, then upload)",
    "Open the folder of local writes", "Restart kernel"], "no Fabric option while Preview is hidden and the notebook isn't set to Fabric");
  assert.match(await page().locator("#menu .info").first().innerText(), /Where Run all runs this notebook\./);

  await page().locator("#menu [role=menuitem]", { hasText: "Open the folder of local writes" }).click();
  await page().locator("#toast", { hasText: "Opened C:\\Users\\me\\KratosForge\\local-writes in File Explorer." }).waitFor();
  assert.deepStrictEqual(calls("local_writes_reveal").map((c) => c.params), [{}]);
  assert.deepStrictEqual(app.errors, []);
});

test("choosing This PC saves the notebook with it, and the button still says This PC", { skip }, async () => {
  const from = calls("save_notebook").length;
  await page().locator(".compute-btn").click();
  await page().locator("#menu [role=menuitem]", { hasText: "This PC (no Fabric job)" }).click();
  await page().waitForFunction(() => state.notebook.compute === "local");
  await page().locator(".compute-btn", { hasText: "Runs on: This PC" }).waitFor();
  assert.ok(calls("save_notebook").length > from, "the choice is saved with the notebook");
  // Set to Fabric, the menu offers Fabric even with Preview hidden, named for attaching a lakehouse.
  await page().evaluate(() => { state.notebook.compute = "fabric"; renderNotebook(); });
  await page().locator(".compute-btn").click();
  const items = (await menuItems()).map((t) => t.trim());
  assert.ok(items.includes("Fabric (attach a lakehouse first) · Preview"), items.join(" | "));
  assert.ok(!items.includes("Open the folder of local writes"), "a Fabric run writes no tables on this PC");
  await page().evaluate(() => closeMenu());
  assert.deepStrictEqual(app.errors, []);
});

test("a notebook changed on disk into something unreadable offers only Keep mine, which saves and names the copy", { skip }, async () => {
  await page().evaluate(() => nbDiskConflict(state.notebook, { unreadable: true, outside_copy: "C:\\nb\\Sales prep.conflict.ipynb", disk_stamp: 7 }));
  const dialog = page().locator("#dialog-root .dialog");
  await dialog.waitFor();
  const text = await dialog.innerText();
  assert.match(text, /Sales prep was changed by another program and isn't a notebook KratosForge can read any more/);
  assert.match(text, /Copy: C:\\nb\\Sales prep\.conflict\.ipynb/);
  assert.strictEqual(await dialog.getByRole("button", { name: "Load its version" }).count(), 0, "nothing to load");
  // Asked again while it shows: one dialog only.
  await page().evaluate(() => nbDiskConflict(state.notebook, { unreadable: true }));
  assert.strictEqual(await page().locator("#dialog-root .dialog").count(), 1);

  const from = calls("save_notebook").length;
  await dialog.getByRole("button", { name: "Keep mine" }).click();
  await page().locator("#toast", { hasText: "Saved Sales prep. The other version is copied beside its versions." }).waitFor();
  assert.strictEqual(calls("save_notebook").length, from + 1);
  assert.deepStrictEqual(await page().evaluate(() => [state.notebook.diskConflict, state.notebook.diskConflictOpen]), [false, false]);
  assert.deepStrictEqual(app.errors, []);
});

test("Alt+Left and Alt+Right move through pages, but not while typing or with a dialog open", { skip }, async () => {
  await page().evaluate(() => go("home"));
  await page().waitForFunction(() => state.view === "home");
  const alt = (key, target) => page().evaluate(([k, t]) => {
    const el = t ? document.querySelector(t) : document.body;
    return navKey({ key: k, altKey: true, ctrlKey: false, metaKey: false, shiftKey: false, target: el, preventDefault() {} });
  }, [key, target]);

  await page().evaluate(() => document.body.append(Object.assign(document.createElement("input"), { id: "kf-typing" })));
  assert.strictEqual(await alt("ArrowLeft", "#kf-typing"), false, "typing in a box");
  await page().evaluate(() => document.getElementById("kf-typing").remove());
  await page().evaluate(() => showDialog("Hold on", h("p", {}, "x"), []));
  assert.strictEqual(await alt("ArrowLeft"), false, "a dialog is open");
  await page().evaluate(() => closeDialog());
  assert.strictEqual(await page().evaluate(() => state.view), "home");

  assert.strictEqual(await alt("ArrowLeft"), true);
  assert.strictEqual(await page().evaluate(() => state.view), "notebook");
  assert.strictEqual(await alt("ArrowRight"), true);
  assert.strictEqual(await page().evaluate(() => state.view), "home");
  await alt("ArrowRight");  // nothing ahead: the key is taken, and the page stays
  assert.strictEqual(await page().evaluate(() => state.view), "home");
  assert.deepStrictEqual(app.errors, []);
});

test("Monitor lists this PC's schedule runs by how they started, and a Home run opens its notebook, saving edits first", { skip }, async () => {
  await page().evaluate(() => { monitorPage.panel = "runs"; go("monitor"); });
  const row = (text) => page().locator("tr.click", { hasText: text });
  await row("Daily load").waitFor();
  assert.match(await row("Daily load").innerText(), /Run from Home · Wrote 40 rows\./);
  assert.match(await row("Hourly sync").innerText(), /Schedule, run now/);
  assert.match(await row("Nightly").innerText(), /Scheduled run[\s\S]*Table missing\. The alert email couldn't be sent/);
  assert.strictEqual(await row("Hourly sync").locator(".reason").innerText(), "The PC was asleep");

  await page().evaluate(() => { state.notebook.dirty = true; });
  const saves = calls("save_notebook").length;
  await row("Daily load").click();
  await page().waitForFunction(() => state.view === "notebook" && state.notebook.name === "Daily load");
  assert.strictEqual(calls("save_notebook").length, saves + 1, "the open notebook's edits are saved first");
  assert.deepStrictEqual(calls("open_notebook").map((c) => c.params), [{ name: "Daily load" }]);
  assert.deepStrictEqual(app.errors, []);
});

test("Home lists Fabric jobs that failed in the last day, with Fabric's reason and a link when there is one", { skip }, async () => {
  await page().evaluate(() => go("home"));
  const card = (text) => page().locator(".card.attention", { hasText: text });
  await card("Load sales").waitFor();
  assert.match(await card("Load sales").innerText(), /Load sales · Notebook in Fabric[\s\S]*Out of capacity/);
  assert.strictEqual(await card("Load sales").getByRole("button", { name: "Open in Fabric" }).count(), 1);
  assert.match(await card("Old job").innerText(), /Old job · Pipeline in Fabric[\s\S]*Failed in Fabric/);
  assert.strictEqual(await card("Old job").getByRole("button", { name: "Open in Fabric" }).count(), 0, "no link without a url");
  assert.strictEqual(await card("Last week").count(), 0, "older than a day");
  assert.deepStrictEqual(app.errors, []);
});

test("Home's Coming up lists this PC's enabled schedules, soonest first", { skip }, async () => {
  await page().evaluate(() => go("home"));
  const item = page().locator(".home-item");
  await item.first().waitFor();
  const texts = await item.allInnerTexts();
  assert.strictEqual(texts.length, 2, "a disabled schedule isn't coming up");
  assert.match(texts[0], /^Daily load · This PC/);
  assert.match(texts[1], /^Nightly · This PC/);
  assert.ok(await page().getByRole("button", { name: "All schedules" }).isVisible());
  assert.deepStrictEqual(app.errors, []);
});

test("closing a notebook with edits from the rail saves it first, then shows Home", { skip }, async () => {
  await page().evaluate(() => { state.notebook.dirty = true; renderChrome(); });
  const saves = calls("save_notebook").length;
  await page().getByRole("button", { name: "Close Sales prep" }).click();
  await page().waitForFunction(() => state.notebook === null && state.view === "home");
  assert.strictEqual(calls("save_notebook").length, saves + 1);
  assert.strictEqual(await page().getByRole("button", { name: "Close Sales prep" }).count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("a notebook that runs only in Fabric says why and offers Run in Fabric; set to Fabric it says how Run all works", { skip }, async () => {
  await page().evaluate(() => {
    state.notebook.check = {
      local: { reasons: [{ cell: 1, text: "uses Spark", code: "spark.sql" }], unknown: [{ cell: 2, text: "uses a name no cell defines" }] },
      fabric: { ok: true, reasons: [] },
    };
    renderNotebook();
  });
  const note = page().locator(".compute-note-slot .problems");
  await note.waitFor();
  const text = await note.innerText();
  assert.match(text, /This notebook only runs in Fabric/);
  assert.match(text, /Cell 1 uses Spark: spark\.sql/);
  assert.match(text, /The check also can't tell for 1 cell:[\s\S]*Cell 2 uses a name no cell defines/);

  await note.getByRole("button", { name: "Run in Fabric" }).click();
  await page().locator("#toast", { hasText: "Sales prep now runs in Fabric. Run all publishes it and starts it there." }).waitFor();
  await page().evaluate(() => {
    state.notebook.check = { fabric: { ok: true, reasons: [] }, local: { ok: true, reasons: [] } };
    state.notebook.fabricRun = { status: "NotStarted", url: "https://app.fabric.microsoft.com/x" };
    renderNotebook();
  });
  const callout = page().locator(".compute-callout");
  await callout.waitFor();
  assert.match(await callout.innerText(), /Run all publishes this notebook and runs it on the workspace's Fabric capacity\./);
  assert.strictEqual(await callout.locator("a").innerText(), "Last run: starting");
  await page().evaluate(() => { state.notebook.compute = "local"; state.notebook.fabricRun = null; });
  assert.deepStrictEqual(app.errors, []);
});

test("an item page reopens on the rail page it sits under; an unknown page opens Home", { skip }, async () => {
  const [got, want, unknown] = await page().evaluate(() => {
    const parent = features.list.find((f) => f.rail && (f.rail.also || []).some((v) => navShown(v) && !Object.keys(f.views).includes(v)
      && !features.list.some((g) => g.rail && Object.keys(g.views)[0] === v)));
    const view = parent.rail.also.find((v) => navShown(v) && !Object.keys(parent.views).includes(v)
      && !features.list.some((g) => g.rail && Object.keys(g.views)[0] === v));
    return [navStartView(view), Object.keys(parent.views)[0], navStartView("no-such-page")];
  });
  assert.strictEqual(got, want);
  assert.strictEqual(unknown, "home");
  assert.deepStrictEqual(app.errors, []);
});
