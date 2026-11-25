// Home > Recent notebooks (home-notebooks.js), what home-notebooks.test.cjs leaves out: every Last run
// word, where each notebook is kept, the workspace link, Select all and Clear, a batch that runs, is
// stopped and ends with a notice, the confirm's other cases (a restriction, no workspace, nothing that
// can run, unsaved changes, the warning box not ticked, the parallel limit remembered), the Pinned card
// for items and notebooks, and Pin to Home from the command bar. The engine is mocked (see harness.cjs).
const { test, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
after(async () => { await app?.close(); });

const ago = (h) => new Date(Date.now() - h * 3600000).toISOString();
const NOTEBOOKS = [
  { name: "Load orders", modified: 1761480000, place: { root: "C:/src/sales", label: "sales" } },
  { name: "Clean orders", modified: 1761400000 },
  { name: "Fabric only", modified: 1761300000 },
  { name: "Old run", modified: 1761200000 },
];
const DETAILS = {
  "Load orders": { name: "Load orders", workspace_id: "w1", workspace_name: "Sales Dev", lakehouse: "bronze", compute: "default",
    last_run: { source: "pc", status: "skipped", started: ago(3) } },
  "Clean orders": { name: "Clean orders", workspace_id: "", workspace_name: "", lakehouse: "", compute: "local",
    last_run: { source: "pc", status: "stopped", ended: ago(5) } },
  "Fabric only": { name: "Fabric only", workspace_id: "", workspace_name: "", lakehouse: "", compute: "fabric",
    last_run: { source: "pc", status: "queued", started: null } },
  "Old run": { name: "Old run", workspace_id: "w2", workspace_name: "Ops", lakehouse: "silver", compute: "fabric", last_run: null },
};
const NEEDS_ACK = { id: "r9", result: "warning", label: "Warning", summary: "1 thing to know", needs_acknowledgement: true,
  findings: [{ check: "capacity", outcome: "warning", title: "The capacity is busy" }] };

let batches;  // the answers notebook_batch gives, in order; the last one repeats
async function home({ mocks = {}, pins = [], storage = {} } = {}) {
  await app?.close();
  let pinned = pins;
  batches = [];
  app = await openApp({ storage, mocks: {
    environment: () => ({ python: "Python 3 (test)", user: "chris@example.com" }),
    readiness_check: () => [],
    list_notebooks: () => NOTEBOOKS,
    notebook_details: ({ names }) => names.map((n) => DETAILS[n]),
    item_pins: () => pinned,
    set_item_pin: ({ kind, id, name, on }) => { pinned = on ? [...pinned, { kind, id, name, workspace_id: "", workspace_name: "" }] : pinned.filter((p) => p.id !== id); return pinned; },
    risk_check: ({ id }) => (id === "notebook:Old run" ? NEEDS_ACK : { id: "ok", result: "pass", label: "Pass", findings: [] }),
    risk_acknowledge: () => ({ ok: true }),
    notebook_batch_start: ({ names, parallel }) => ({ id: "b1", state: "starting", parallel, items: names.map((name) => ({ name, status: "waiting" })) }),
    notebook_batch: () => (batches.length > 1 ? batches.shift() : batches[0]),
    notebook_batch_stop: () => ({ id: "b1", state: "running", stopping: true, items: [{ name: "Clean orders", status: "running" }] }),
    notebook_batch_fabric: ({ name }) => ({ job_id: `j-${name}`, name, workspace_name: "Ops", url: "" }),
    open_notebook: ({ name }) => ({ name, cells: [{ type: "code", source: "x = 1" }] }),
    // Home reads the X-ray each time it draws, and again when a batch ends. Unanswered, that read fails
    // with an error toast that takes the batch's notice off the screen moments after it appears.
    xray: () => ({ findings: [], scanned_at: null }),
    ...mocks,
  } });
  await app.page.waitForFunction(() => state.view === "home" && homeCache.done, null, { timeout: 20000 });
  await app.page.waitForFunction(() => homeRun.details.size >= 4);
  return app.page;
}
const rowOf = (p, name) => p.locator(".home-notebooks tbody tr", { hasText: name });
const calls = (method) => app.calls.filter((c) => c.method === method).map((c) => c.params);

test("each row says how the last run ended, where it runs and where the notebook is kept", { skip }, async () => {
  const p = await home();
  const cells = async (name) => (await rowOf(p, name).locator("td").allTextContents()).map((t) => t.trim());
  const load = await cells("Load orders");
  assert.match(load[1], /^Load orderssales$/);
  assert.strictEqual(load[4], "This PC (environment)");
  assert.match(load[5], /^Skipped 3 h/);
  const clean = await cells("Clean orders");
  assert.match(clean[1], /This PC$/);
  assert.match(clean[5], /^Stopped 5 h/);
  assert.deepStrictEqual((await cells("Fabric only")).slice(2, 6), ["Not published", "None", "Fabric", "queued"]);
  // The workspace opens in the Fabric view without opening the notebook.
  await rowOf(p, "Load orders").getByRole("button", { name: "Sales Dev" }).click();
  assert.deepStrictEqual(await p.evaluate(() => ({ view: state.view, page: fabricPage.page })),
    { view: "fabric", page: "https://app.fabric.microsoft.com/groups/w1/list?experience=fabric-developer" });
  assert.deepStrictEqual(calls("open_notebook"), []);
  assert.deepStrictEqual(app.errors, []);
});

test("Select all ticks every notebook shown, and Clear unticks them", { skip }, async () => {
  const p = await home();
  await p.getByLabel("Select every notebook shown").check();
  assert.match(await p.locator(".home-run-bar").textContent(), /4 selected/);
  assert.strictEqual(await p.locator(".home-notebooks tbody tr.selected").count(), 4);
  await p.getByLabel("Select every notebook shown").uncheck();
  assert.match(await p.locator(".home-run-bar").textContent(), /Tick notebooks to run several at once\./);
  await p.getByLabel("Select Clean orders").check();
  assert.match(await p.locator(".home-run-bar button.primary").textContent(), /^Run 1 notebook(Preview)?$/);
  await p.getByRole("button", { name: "Clear" }).click();
  assert.strictEqual(await p.locator(".home-notebooks tbody tr.selected").count(), 0);
});

test("a batch shows each notebook's progress, can be stopped, and ends with a notice", { skip }, async () => {
  const p = await home({ storage: { "fd.home.parallel": "3" } });
  await p.getByLabel("Select Clean orders").check();
  await p.locator(".home-run-bar button.primary").click();
  const dialog = p.getByRole("dialog", { name: "Run Clean orders?" });
  assert.match(await dialog.innerText(), /On this PC \(1\)\s+Clean orders\s+No lakehouse/);
  assert.strictEqual(await dialog.locator("input.home-parallel").inputValue(), "3");
  await dialog.locator("input.home-parallel").fill("9");
  batches.push({ id: "b1", state: "running", items: [{ name: "Clean orders", status: "running", cell: 2, cells: 4 }] });
  await dialog.getByRole("button", { name: "Run Clean orders" }).click();
  assert.deepStrictEqual(calls("notebook_batch_start"), [{ names: ["Clean orders"], parallel: 4 }]);
  assert.strictEqual(await p.evaluate(() => localStorage.getItem("fd.home.parallel")), "4");
  await rowOf(p, "Clean orders").getByText("Running, cell 2 of 4").waitFor();
  assert.strictEqual(await rowOf(p, "Clean orders").locator(".progress .bar").evaluate((b) => b.style.width), "25%");
  assert.match(await p.locator(".home-run-bar").textContent(), /Running 1: 0 done, 1 running/);
  await p.locator(".home-run-bar").getByRole("button", { name: "Stop" }).click();
  await p.locator(".home-run-bar").getByText("Stopping").waitFor();
  assert.deepStrictEqual(calls("notebook_batch_stop"), [{ id: "b1" }]);
  batches.length = 0;
  batches.push({ id: "b1", state: "done", items: [{ name: "Clean orders", status: "stopped" }] });
  await p.waitForFunction(() => document.getElementById("toast").textContent === "Run 1 notebook: 0 succeeded, 1 stopped.", null, { timeout: 15000 });
  assert.ok(await p.evaluate(() => activity.items.some((i) => i.id === "batch:b1" && i.status === "ok")));
  assert.deepStrictEqual(app.errors, []);
});

test("a batch with a failure ends with an error naming it", { skip }, async () => {
  const p = await home();
  await p.getByLabel("Select Clean orders").check();
  await p.getByLabel("Select Load orders").check();
  await p.locator(".home-run-bar button.primary").click();
  batches.push({ id: "b1", state: "lost", items: [{ name: "Load orders", status: "ok" }, { name: "Clean orders", status: "failed" }, { name: "x", status: "skipped" }] });
  await p.getByRole("dialog", { name: "Run 2 notebooks?" }).getByRole("button", { name: "Run 2 notebooks" }).click();
  // The title counts what was started; the notice counts how each one ended.
  await p.waitForFunction(() => activity.items.some((i) => i.id === "batch:b1" && i.status === "failed"), null, { timeout: 15000 });
  await p.waitForFunction(() => document.getElementById("toast").textContent === "Run 2 notebooks: 1 succeeded, 1 failed (Clean orders), 1 not started.", null, { timeout: 15000 });
  assert.strictEqual(await p.locator("#toast.error").count(), 1);
});

test("the confirm leaves out what can't run and says why; nothing at all can run says so", { skip }, async () => {
  const p = await home({ mocks: { risk_check: () => ({ id: "rb", result: "blocked", label: "Blocked", findings: [{ check: "policy", outcome: "blocked", title: "Prod is frozen" }] }) } });
  await p.getByLabel("Select Fabric only").check();
  await p.getByLabel("Select Old run").check();
  await p.locator(".home-run-bar button.primary").click();
  const dialog = p.getByRole("dialog", { name: "Nothing can run" });
  await dialog.waitFor();
  const text = await dialog.innerText();
  assert.match(text, /Not run, the risk check found a restriction: Old run\. Open each to see why\./);
  assert.match(text, /Not run, they run on Fabric but have no workspace yet: Fabric only\. Attach a lakehouse/);
  await dialog.getByRole("button", { name: "Close" }).last().click();
  assert.deepStrictEqual(calls("notebook_batch_start"), []);
  assert.deepStrictEqual(calls("notebook_batch_fabric"), []);
});

test("a Fabric notebook whose warnings need a tick waits for it; unsaved changes are named", { skip }, async () => {
  const p = await home();
  await p.evaluate(() => { loadIntoState({ name: "Old run", cells: [{ type: "code", source: "x" }] }); state.notebook.dirty = true; go("home"); });
  await p.waitForFunction(() => state.view === "home" && document.querySelector(".home-notebooks"));
  await p.getByLabel("Select Old run").check();
  await p.getByLabel("Select Clean orders").check();
  await p.locator(".home-run-bar button.primary").click();
  const dialog = p.getByRole("dialog", { name: "Run 2 notebooks?" });
  assert.match(await dialog.innerText(), /Old run has unsaved changes\. The saved version runs\./);
  assert.match(await dialog.innerText(), /The capacity is busy/);
  await dialog.getByRole("button", { name: "Run 2 notebooks" }).click();
  await p.waitForFunction(() => document.getElementById("toast").textContent.startsWith("Tick \"I've looked at these\" first"));
  assert.deepStrictEqual(calls("notebook_batch_start"), []);
  await dialog.getByLabel("I've looked at these and want to run anyway").check();
  await dialog.getByRole("button", { name: "Run 2 notebooks" }).click();
  await p.waitForFunction(() => activity.items.some((i) => i.id === "fabric:j-Old run"));
  assert.deepStrictEqual(calls("risk_acknowledge").map((c) => c.risk_id), ["r9"]);
  assert.deepStrictEqual(calls("notebook_batch_start").map((c) => c.names), [["Clean orders"]]);
  assert.deepStrictEqual(app.errors, []);
});

test("a notebook pinned before its details are read is read first when it's run from Pinned", { skip }, async () => {
  const p = await home({ pins: [{ kind: "notebook", id: "Clean orders", name: "Clean orders", workspace_id: "", workspace_name: "" },
    { kind: "SemanticModel", id: "sm1", name: "Sales model", workspace_id: "w1", workspace_name: "Sales Dev" }] });
  const card = p.locator(".home-pins");
  await card.waitFor();
  assert.match(await card.innerText(), /Semantic model\s+Sales model\s+Sales Dev/);
  assert.match(await card.innerText(), /Clean orders\s+This PC/);
  await p.evaluate(() => homeRun.details.delete("Clean orders"));
  app.calls.length = 0;
  await card.locator(".home-pin", { hasText: "Clean orders" }).getByRole("button", { name: "Run" }).click();
  await p.getByRole("dialog", { name: "Run Clean orders?" }).waitFor();
  assert.deepStrictEqual(calls("notebook_details"), [{ names: ["Clean orders"] }]);
  await p.getByRole("dialog", { name: "Run Clean orders?" }).getByRole("button", { name: "Cancel" }).click();
  // The item opens in Fabric, the notebook opens here.
  await card.getByRole("button", { name: "Sales model" }).click();
  assert.match(await p.evaluate(() => fabricPage.page), /^https:\/\/app\.fabric\.microsoft\.com\/groups\/w1\/.*sm1/);
  await p.evaluate(() => go("home"));
  await p.locator(".home-pins").getByRole("button", { name: "Clean orders" }).click();
  await p.waitForFunction(() => state.view === "notebook" && state.notebook?.name === "Clean orders");
  assert.deepStrictEqual(calls("open_notebook"), [{ name: "Clean orders" }]);
  assert.deepStrictEqual(app.errors, []);
});

test("unpinning the last item on Pinned takes the card away", { skip }, async () => {
  const p = await home({ pins: [{ kind: "Lakehouse", id: "lh1", name: "bronze", workspace_id: "w1", workspace_name: "Sales Dev" }] });
  await p.locator(".home-pins").getByRole("button", { name: "Unpin from Home" }).click();
  await p.waitForFunction(() => !document.querySelector(".home-pins"));
  assert.deepStrictEqual(calls("set_item_pin").map((c) => [c.id, c.on]), [["lh1", false]]);
});

test("Pin to Home from the command bar pins the open notebook, and again unpins it", { skip }, async () => {
  const p = await home();
  await p.evaluate(() => { loadIntoState({ name: "Load orders", cells: [{ type: "code", source: "x" }] }); go("notebook"); });
  const command = async () => {
    await p.keyboard.press("Control+k");
    await p.locator("#palette").waitFor();
    await p.keyboard.type("Pin to Home or unpin");
    await p.waitForFunction(() => document.querySelector("#palette .palette-item.on")?.textContent.includes("Pin to Home or unpin"));
    await p.keyboard.press("Enter");
  };
  await command();
  await p.waitForFunction(() => document.getElementById("toast").textContent === "Load orders is pinned to Home.");
  await command();
  await p.waitForFunction(() => document.getElementById("toast").textContent === "Load orders is no longer pinned to Home.");
  assert.deepStrictEqual(calls("set_item_pin").map((c) => [c.kind, c.id, c.on]), [["notebook", "Load orders", true], ["notebook", "Load orders", false]]);
  assert.deepStrictEqual(app.errors, []);
});
